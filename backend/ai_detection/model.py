from __future__ import annotations

import logging
from pathlib import Path

import torch
import torch.nn as nn
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

logger = logging.getLogger("ewaste.ai.model")


def build_mobilenetv3(
    num_classes: int,
    pretrained: bool = True,
    freeze_backbone: bool = False,
) -> nn.Module:
    """
    Builds a lightweight MobileNetV3-Small classifier adapted for num_classes.
    Transfer learning replaces the final Linear classification layer.
    """
    weights = MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
    model = mobilenet_v3_small(weights=weights)

    if freeze_backbone:
        for param in model.features.parameters():
            param.requires_grad = False

    # In MobileNetV3 Small, classifier is:
    # (0): Linear(in_features=576, out_features=1024)
    # (1): Hardswish()
    # (2): Dropout(p=0.2)
    # (3): Linear(in_features=1024, out_features=num_classes)
    in_features = model.classifier[3].in_features
    model.classifier[3] = nn.Linear(in_features, num_classes)

    return model


def save_checkpoint(
    model: nn.Module,
    checkpoint_path: Path,
    metadata: dict | None = None,
) -> None:
    checkpoint_path = Path(checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state_dict": model.state_dict(),
        "metadata": metadata or {},
    }
    torch.save(payload, checkpoint_path)
    logger.info(f"Model saved successfully to {checkpoint_path}")


def load_model(
    num_classes: int,
    checkpoint_path: Path,
    device: str = "cpu",
) -> nn.Module:
    checkpoint_path = Path(checkpoint_path)
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Model checkpoint not found at {checkpoint_path}")

    model = build_mobilenetv3(num_classes=num_classes, pretrained=False)
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=True)
    if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
        model.load_state_dict(checkpoint["state_dict"])
    else:
        model.load_state_dict(checkpoint)

    model.to(device)
    model.eval()
    return model
