from __future__ import annotations

import argparse
import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import classification_report, confusion_matrix
from torch.utils.data import DataLoader

from ai_detection.config import (
    DATASET_DIR,
    IMAGE_SIZE,
    IMAGENET_MEAN,
    IMAGENET_STD,
    MODEL_DIR,
    get_device,
)
from ai_detection.dataset import (
    ElectronicComponentsDataset,
    get_transforms,
    prepare_dataset_splits,
)
from ai_detection.model import build_mobilenetv3, save_checkpoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ewaste.ai.train")


def set_seed(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def evaluate(
    model: nn.Module,
    dataloader: DataLoader,
    criterion: nn.Module,
    device: str,
) -> tuple[float, float, list[int], list[int]]:
    """Evaluates model on dataloader, returns (loss, accuracy, all_preds, all_targets)."""
    model.eval()
    total_loss = 0.0
    correct = 0
    total = 0
    all_preds: list[int] = []
    all_targets: list[int] = []

    with torch.no_grad():
        for inputs, targets in dataloader:
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            loss = criterion(outputs, targets)

            total_loss += loss.item() * inputs.size(0)
            preds = torch.argmax(outputs, dim=1)
            correct += (preds == targets).sum().item()
            total += targets.size(0)

            all_preds.extend(preds.cpu().tolist())
            all_targets.extend(targets.cpu().tolist())

    avg_loss = total_loss / max(total, 1)
    acc = correct / max(total, 1)
    return avg_loss, acc, all_preds, all_targets


def train(
    dataset_dir: Path = DATASET_DIR,
    output_dir: Path = MODEL_DIR,
    epochs: int = 10,
    batch_size: int = 32,
    lr: float = 0.001,
    freeze_epochs: int = 3,
    use_class_weights: bool = True,
    device: str | None = None,
    max_samples_per_class: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Two-stage transfer learning training and evaluation pipeline for MobileNetV3.
    Stage 1 (epochs 1..freeze_epochs): Train classification head with frozen backbone.
    Stage 2 (epochs freeze_epochs+1..epochs): Fine-tune all layers with reduced learning rate.
    """
    set_seed(seed)
    device = device or get_device()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Maximize CPU core utilization on Windows
    if device == "cpu":
        cores = min(8, os.cpu_count() or 4)
        torch.set_num_threads(cores)
        logger.info(f"Using {cores} CPU threads for PyTorch.")

    logger.info(f"Preparing dataset from {dataset_dir} (device={device}, seed={seed})...")
    data_dict = prepare_dataset_splits(
        dataset_dir=dataset_dir,
        max_samples_per_class=max_samples_per_class,
        seed=seed,
    )

    class_names = data_dict["class_names"]
    class_to_idx = data_dict["class_to_idx"]
    num_classes = len(class_names)
    stats = data_dict["stats"]
    logger.info(f"Target classes ({num_classes}): {class_names}")

    # Inspect and log class distribution & imbalance
    per_class_stats = stats["per_class"]
    logger.info("Class distribution in training split:")
    train_counts_list = []
    for cls_name in class_names:
        c_train = per_class_stats[cls_name]["train"]
        train_counts_list.append(c_train)
        logger.info(f"  {cls_name:24}: {c_train:4d} train images ({per_class_stats[cls_name]['total']:4d} total)")

    train_transform, eval_transform = get_transforms(image_size=IMAGE_SIZE)

    train_ds = ElectronicComponentsDataset(data_dict["train_samples"], transform=train_transform)
    val_ds = ElectronicComponentsDataset(data_dict["val_samples"], transform=eval_transform)
    test_ds = ElectronicComponentsDataset(data_dict["test_samples"], transform=eval_transform)

    # Windows compatible num_workers=0 to avoid IPC overhead
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False, num_workers=0)

    logger.info(f"DataLoaders ready: Train={len(train_ds)}, Val={len(val_ds)}, Test={len(test_ds)}")

    # Loss function with optional balanced class weighting
    if use_class_weights:
        train_counts = np.array(train_counts_list, dtype=np.float32)
        total_train = np.sum(train_counts)
        class_weights = total_train / (num_classes * train_counts)
        # Normalize weights so mean is 1.0
        class_weights = class_weights / np.mean(class_weights)
        weights_tensor = torch.tensor(class_weights, dtype=torch.float32).to(device)
        criterion = nn.CrossEntropyLoss(weight=weights_tensor)
        logger.info(f"Class weighting enabled. Weights min={class_weights.min():.2f}, max={class_weights.max():.2f}")
    else:
        criterion = nn.CrossEntropyLoss()

    logger.info(f"Instantiating MobileNetV3 Small (pretrained=True, num_classes={num_classes})...")
    # Stage 1: start with frozen backbone if freeze_epochs > 0
    start_frozen = freeze_epochs > 0
    model = build_mobilenetv3(num_classes=num_classes, pretrained=True, freeze_backbone=start_frozen)
    model.to(device)

    # Optimizer for head only initially
    current_lr = lr
    optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=current_lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5, patience=1)

    best_val_acc = 0.0
    best_epoch = 0
    history: list[dict[str, Any]] = []

    logger.info(f"Starting training for {epochs} epochs (freeze_epochs={freeze_epochs}, lr={lr})...")
    for epoch in range(1, epochs + 1):
        # Stage 2: Unfreeze backbone when freeze_epochs reached
        if freeze_epochs > 0 and epoch == freeze_epochs + 1:
            logger.info("--> Unfreezing MobileNetV3 backbone for end-to-end fine-tuning...")
            for param in model.features.parameters():
                param.requires_grad = True
            # Fine-tune all layers with a lower learning rate
            current_lr = lr * 0.2
            optimizer = torch.optim.Adam(model.parameters(), lr=current_lr)
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5, patience=1)

        model.train()
        running_loss = 0.0
        correct_train = 0
        total_train = 0

        for batch_idx, (inputs, targets) in enumerate(train_loader):
            inputs, targets = inputs.to(device), targets.to(device)
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()

            running_loss += loss.item() * inputs.size(0)
            preds = torch.argmax(outputs, dim=1)
            correct_train += (preds == targets).sum().item()
            total_train += targets.size(0)

        train_loss = running_loss / max(total_train, 1)
        train_acc = correct_train / max(total_train, 1)

        val_loss, val_acc, _, _ = evaluate(model, val_loader, criterion, device)
        scheduler.step(val_acc)

        epoch_record = {
            "epoch": epoch,
            "train_loss": round(train_loss, 4),
            "train_accuracy": round(train_acc, 4),
            "val_loss": round(val_loss, 4),
            "val_accuracy": round(val_acc, 4),
            "stage": "head_only" if (epoch <= freeze_epochs) else "fine_tune",
        }
        history.append(epoch_record)
        logger.info(
            f"Epoch {epoch:2d}/{epochs} [{epoch_record['stage']}] - "
            f"Train Loss: {train_loss:.4f}, Train Acc: {train_acc:.4f} | "
            f"Val Loss: {val_loss:.4f}, Val Acc: {val_acc:.4f}"
        )

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_epoch = epoch
            save_checkpoint(
                model,
                output_dir / "best_model.pth",
                metadata={"best_epoch": epoch, "val_accuracy": val_acc, "num_classes": num_classes},
            )
            logger.info(f"--> Best model checkpoint updated (val_acc={val_acc:.4f})")

    # If training completed, ensure checkpoint exists
    if not (output_dir / "best_model.pth").exists():
        save_checkpoint(model, output_dir / "best_model.pth")

    # Load best model for final evaluation on test set
    logger.info(f"Loading best model (from epoch {best_epoch}, val_acc={best_val_acc:.4f}) for test evaluation...")
    from ai_detection.model import load_model

    best_model = load_model(num_classes, output_dir / "best_model.pth", device=device)
    test_loss, test_acc, test_preds, test_targets = evaluate(best_model, test_loader, criterion, device)

    # Scikit-learn classification report
    report_dict = classification_report(
        test_targets,
        test_preds,
        target_names=class_names,
        output_dict=True,
        zero_division=0,
    )
    conf_matrix = confusion_matrix(test_targets, test_preds).tolist()

    # Per-class metrics
    per_class_metrics = {
        cls_name: {
            "precision": round(report_dict[cls_name]["precision"], 4),
            "recall": round(report_dict[cls_name]["recall"], 4),
            "f1_score": round(report_dict[cls_name]["f1-score"], 4),
            "support": int(report_dict[cls_name]["support"]),
        }
        for cls_name in class_names
    }

    class_f1_scores = [
        {"class": cls_name, "f1_score": per_class_metrics[cls_name]["f1_score"]}
        for cls_name in class_names
    ]
    class_f1_scores.sort(key=lambda x: x["f1_score"])
    low_performing_classes = [c for c in class_f1_scores if c["f1_score"] < 0.60]

    evaluation_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "architecture": "mobilenet_v3_small",
        "num_classes": num_classes,
        "classes": class_names,
        "epochs": epochs,
        "batch_size": batch_size,
        "best_epoch": best_epoch,
        "best_val_accuracy": round(best_val_acc, 4),
        "final_val_accuracy": round(history[-1]["val_accuracy"] if history else 0.0, 4),
        "test_loss": round(test_loss, 4),
        "test_accuracy": round(test_acc, 4),
        "macro_precision": round(report_dict["macro avg"]["precision"], 4),
        "macro_recall": round(report_dict["macro avg"]["recall"], 4),
        "macro_avg_f1": round(report_dict["macro avg"]["f1-score"], 4),
        "weighted_avg_f1": round(report_dict["weighted avg"]["f1-score"], 4),
        "history": history,
        "per_class_metrics": per_class_metrics,
        "confusion_matrix": conf_matrix,
        "low_performing_classes": low_performing_classes,
        "lowest_3_classes": class_f1_scores[:3],
    }

    # Save artifacts
    classes_path = output_dir / "classes.json"
    with open(classes_path, "w", encoding="utf-8") as f:
        json.dump(class_names, f, indent=2)

    config_path = output_dir / "config.json"
    training_config = {
        "architecture": "mobilenet_v3_small",
        "num_classes": num_classes,
        "image_size": IMAGE_SIZE,
        "normalization": {
            "mean": IMAGENET_MEAN,
            "std": IMAGENET_STD,
        },
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": lr,
        "freeze_epochs": freeze_epochs,
        "use_class_weights": use_class_weights,
        "device": device,
        "seed": seed,
        "dataset_split": {"train": 0.70, "val": 0.15, "test": 0.15},
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(training_config, f, indent=2)

    eval_path = output_dir / "evaluation_report.json"
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(evaluation_report, f, indent=2)

    logger.info("==================================================")
    logger.info("           TRAINING & EVALUATION SUMMARY          ")
    logger.info("==================================================")
    logger.info(f"Best Val Accuracy:  {best_val_acc * 100:.2f}% (Epoch {best_epoch})")
    logger.info(f"Final Val Accuracy: {evaluation_report['final_val_accuracy'] * 100:.2f}%")
    logger.info(f"Test Accuracy:      {test_acc * 100:.2f}%")
    logger.info(f"Macro Precision:    {evaluation_report['macro_precision']:.4f}")
    logger.info(f"Macro Recall:       {evaluation_report['macro_recall']:.4f}")
    logger.info(f"Macro F1 Score:     {evaluation_report['macro_avg_f1']:.4f}")
    logger.info(f"Saved best model:   {output_dir / 'best_model.pth'}")
    logger.info(f"Saved classes:      {classes_path}")
    logger.info(f"Saved config:       {config_path}")
    logger.info(f"Saved report:       {eval_path}")

    return evaluation_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train MobileNetV3 on Electronic Components Dataset")
    parser.add_argument("--epochs", type=int, default=10, help="Number of training epochs (default: 10)")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size (default: 32)")
    parser.add_argument("--lr", type=float, default=0.001, help="Learning rate (default: 0.001)")
    parser.add_argument("--freeze-epochs", type=int, default=3, help="Epochs to train head with frozen backbone (default: 3)")
    parser.add_argument("--no-class-weights", action="store_true", help="Disable class weighting in loss")
    parser.add_argument("--dataset-dir", type=str, default=str(DATASET_DIR), help="Path to images directory")
    parser.add_argument("--output-dir", type=str, default=str(MODEL_DIR), help="Output directory for model artifacts")
    parser.add_argument("--device", type=str, default=None, help="Device to use ('cpu' or 'cuda')")
    parser.add_argument(
        "--max-samples-per-class",
        type=int,
        default=None,
        help="Optional max samples per class (default: None = full dataset)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    train(
        dataset_dir=Path(args.dataset_dir),
        output_dir=Path(args.output_dir),
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        freeze_epochs=args.freeze_epochs,
        use_class_weights=not args.no_class_weights,
        device=args.device,
        max_samples_per_class=args.max_samples_per_class,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
