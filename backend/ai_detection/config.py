from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Model and artifact directories
MODEL_DIR = ROOT / "models" / "mobilenetv3"
MODEL_PATH = Path(os.getenv("AI_MODEL_PATH", str(MODEL_DIR / "best_model.pth")))
CLASSES_PATH = Path(os.getenv("AI_CLASSES_PATH", str(MODEL_DIR / "classes.json")))
CONFIG_PATH = Path(os.getenv("AI_CONFIG_PATH", str(MODEL_DIR / "config.json")))
EVAL_REPORT_PATH = Path(os.getenv("AI_EVAL_REPORT_PATH", str(MODEL_DIR / "evaluation_report.json")))

# Dataset configuration
DATASET_DIR = Path(os.getenv("AI_DATASET_DIR", str(ROOT / "datasets" / "electronic_components" / "images")))

# Inference configuration
CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.55"))
AMBIGUITY_DELTA = float(os.getenv("AI_AMBIGUITY_DELTA", "0.10"))
IMAGE_SIZE = int(os.getenv("AI_IMAGE_SIZE", "224"))

# Normalization constants (Standard ImageNet values matching MobileNetV3 pretraining)
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

# Device selection
def get_device() -> str:
    env_device = os.getenv("AI_DEVICE")
    if env_device:
        return env_device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"

# Catalog file paths
COMPONENT_CATALOG_PATH = ROOT / "data" / "component_catalog.json"
DEVICE_CATALOG_PATH = ROOT / "data" / "device_salvage_catalog.json"
