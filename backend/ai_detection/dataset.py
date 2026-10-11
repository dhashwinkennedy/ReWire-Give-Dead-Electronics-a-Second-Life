from __future__ import annotations

import logging
import random
from collections import defaultdict
from pathlib import Path
from typing import Callable, Literal

import torch
from PIL import Image
from torch.utils.data import Dataset
from torchvision import transforms

from ai_detection.config import DATASET_DIR, IMAGE_SIZE, IMAGENET_MEAN, IMAGENET_STD

logger = logging.getLogger("ewaste.ai.dataset")

# Mapping from Kaggle dataset folder names to canonical electronic component class labels
DEFAULT_KAGGLE_CLASS_MAP: dict[str, str] = {
    "Bypass-capacitor": "Capacitor",
    "Electrolytic-capacitor": "Electrolytic Capacitor",
    "Integrated-micro-circuit": "IC Chip",
    "LED": "LED",
    "PNP-transistor": "Transistor",
    "cartridge-fuse": "Fuse",
    "electric-relay": "Relay",
    "heat-sink": "Heat Sink",
    "induction-coil": "Inductor",
    "junction-transistor": "BJT Transistor",
    "memory-chip": "IC Chip",
    "microchip": "IC Chip",
    "microprocessor": "IC Chip",
    "multiplexer": "IC Chip",
    "potential-divider": "Potentiometer",
    "potentiometer": "Potentiometer",
    "relay": "Relay",
    "rheostat": "Potentiometer",
    "semiconductor-diode": "Diode",
    "solenoid": "Solenoid",
    "step-down-transformer": "Transformer",
    "step-up-transformer": "Transformer",
    "transistor": "Transistor",
}

VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def get_transforms(image_size: int = IMAGE_SIZE) -> tuple[transforms.Compose, transforms.Compose]:
    """
    Returns (train_transform, eval_transform).
    Train uses mild, realistic augmentations suitable for electronic hardware.
    Eval uses deterministic resize + ImageNet normalization.
    """
    train_transform = transforms.Compose([
        transforms.Resize((image_size + 32, image_size + 32)),
        transforms.RandomCrop(image_size),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.RandomRotation(degrees=15),
        transforms.ColorJitter(brightness=0.15, contrast=0.15),
        transforms.ToTensor(),
        transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
    ])

    eval_transform = transforms.Compose([
        transforms.Resize((image_size, image_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
    ])

    return train_transform, eval_transform


class ElectronicComponentsDataset(Dataset):
    """PyTorch Dataset loading images on the fly with RGB conversion and transforms."""

    def __init__(
        self,
        samples: list[tuple[Path, int]],
        transform: Callable | None = None,
    ):
        self.samples = samples
        self.transform = transform

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        img_path, label = self.samples[idx]
        try:
            with Image.open(img_path) as img:
                image = img.convert("RGB")
        except Exception as e:
            logger.warning(f"Error loading image {img_path}: {e}")
            # Fallback to black image if corrupted
            image = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), (0, 0, 0))

        if self.transform is not None:
            image = self.transform(image)

        return image, label


def prepare_dataset_splits(
    dataset_dir: Path = DATASET_DIR,
    class_map: dict[str, str] | None = None,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    max_samples_per_class: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Scans dataset_dir, maps folders to target classes, splits into train/val/test without leakage.
    Returns:
    {
        "train_samples": [(Path, class_idx), ...],
        "val_samples": [(Path, class_idx), ...],
        "test_samples": [(Path, class_idx), ...],
        "class_names": ["BJT Transistor", ...],
        "class_to_idx": {"BJT Transistor": 0, ...},
        "stats": {...}
    }
    """
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset directory not found at {dataset_dir}")

    mapping = class_map if class_map is not None else DEFAULT_KAGGLE_CLASS_MAP

    # Collect valid images by target class
    images_by_class: dict[str, list[Path]] = defaultdict(list)
    total_scanned = 0
    skipped_unmapped = 0

    for folder in dataset_dir.iterdir():
        if not folder.is_dir():
            continue
        folder_name = folder.name
        target_class = mapping.get(folder_name)
        if target_class is None:
            skipped_unmapped += 1
            continue

        for file_path in folder.iterdir():
            if file_path.suffix.lower() in VALID_IMAGE_EXTS:
                images_by_class[target_class].append(file_path)
                total_scanned += 1

    if not images_by_class:
        raise ValueError(f"No valid images found in {dataset_dir} matching the class mapping")

    class_names = sorted(images_by_class.keys())
    class_to_idx = {cls_name: idx for idx, cls_name in enumerate(class_names)}

    rng = random.Random(seed)

    train_samples: list[tuple[Path, int]] = []
    val_samples: list[tuple[Path, int]] = []
    test_samples: list[tuple[Path, int]] = []

    per_class_counts: dict[str, dict[str, int]] = {}

    for cls_name in class_names:
        img_list = images_by_class[cls_name]
        # Sort first for deterministic ordering, then shuffle with fixed seed
        img_list = sorted(img_list, key=lambda p: p.name)
        rng.shuffle(img_list)

        if max_samples_per_class is not None and len(img_list) > max_samples_per_class:
            img_list = img_list[:max_samples_per_class]

        n = len(img_list)
        n_train = int(n * train_ratio)
        n_val = int(n * val_ratio)
        # Remaining goes to test to guarantee full partition
        train_files = img_list[:n_train]
        val_files = img_list[n_train : n_train + n_val]
        test_files = img_list[n_train + n_val :]

        cls_idx = class_to_idx[cls_name]
        train_samples.extend((p, cls_idx) for p in train_files)
        val_samples.extend((p, cls_idx) for p in val_files)
        test_samples.extend((p, cls_idx) for p in test_files)

        per_class_counts[cls_name] = {
            "total": n,
            "train": len(train_files),
            "val": len(val_files),
            "test": len(test_files),
        }

    # Final shuffle of split lists
    rng.shuffle(train_samples)
    rng.shuffle(val_samples)
    rng.shuffle(test_samples)

    return {
        "train_samples": train_samples,
        "val_samples": val_samples,
        "test_samples": test_samples,
        "class_names": class_names,
        "class_to_idx": class_to_idx,
        "stats": {
            "total_images": len(train_samples) + len(val_samples) + len(test_samples),
            "num_classes": len(class_names),
            "train_count": len(train_samples),
            "val_count": len(val_samples),
            "test_count": len(test_samples),
            "per_class": per_class_counts,
        },
    }
