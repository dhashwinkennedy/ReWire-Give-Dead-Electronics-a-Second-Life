from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

import torch
from PIL import Image

from ai_detection.config import (
    AMBIGUITY_DELTA,
    CLASSES_PATH,
    CONFIDENCE_THRESHOLD,
    IMAGE_SIZE,
    MODEL_PATH,
    get_device,
)
from ai_detection.dataset import get_transforms
from ai_detection.model import load_model

logger = logging.getLogger("ewaste.ai.inference")


class ComponentClassifier:
    """
    Inference service for single-object electronic component classification using MobileNetV3.
    Loads the trained model checkpoint and class mapping once upon initialization.
    """

    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        classes_path: Path = CLASSES_PATH,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        ambiguity_delta: float = AMBIGUITY_DELTA,
        image_size: int = IMAGE_SIZE,
        device: str | None = None,
    ):
        self.model_path = Path(model_path)
        self.classes_path = Path(classes_path)
        self.confidence_threshold = confidence_threshold
        self.ambiguity_delta = ambiguity_delta
        self.image_size = image_size
        self.device = device or get_device()

        self.class_names: list[str] = []
        self.class_to_idx: dict[str, int] = {}
        self.idx_to_class: dict[int, str] = {}
        self.model: torch.nn.Module | None = None

        _, self.transform = get_transforms(image_size=self.image_size)
        self._load_artifacts()

    def _load_artifacts(self) -> None:
        if not self.classes_path.exists():
            raise FileNotFoundError(
                f"Class mapping file not found at {self.classes_path}. "
                "Please ensure classes.json exists or train the model first."
            )
        with open(self.classes_path, "r", encoding="utf-8") as f:
            classes_data = json.load(f)
            if isinstance(classes_data, list):
                self.class_names = classes_data
            elif isinstance(classes_data, dict):
                # could be {"class_names": [...]} or {"0": "name"} or {"name": 0}
                if "class_names" in classes_data:
                    self.class_names = classes_data["class_names"]
                elif all(isinstance(v, int) for v in classes_data.values()):
                    # {"name": 0}
                    self.class_names = [k for k, _ in sorted(classes_data.items(), key=lambda x: x[1])]
                else:
                    # {"0": "name"}
                    self.class_names = [v for _, v in sorted(classes_data.items(), key=lambda x: int(x[0]))]

        self.idx_to_class = {idx: name for idx, name in enumerate(self.class_names)}
        self.class_to_idx = {name: idx for idx, name in enumerate(self.class_names)}

        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model checkpoint not found at {self.model_path}. "
                "Please run training (python -m ai_detection.train) or supply best_model.pth."
            )

        logger.info(f"Loading MobileNetV3 from {self.model_path} on {self.device}...")
        self.model = load_model(
            num_classes=len(self.class_names),
            checkpoint_path=self.model_path,
            device=self.device,
        )
        logger.info(f"MobileNetV3 loaded successfully ({len(self.class_names)} classes).")

    def predict(self, image_input: Image.Image | bytes | io.BytesIO) -> dict[str, Any]:
        """
        Runs MobileNetV3 inference on a single image.
        Returns:
        {
            "top_label": str,
            "confidence": float,
            "is_ambiguous": bool,
            "is_low_confidence": bool,
            "top2_label": str | None,
            "top2_confidence": float | None,
            "all_scores": dict[str, float]
        }
        """
        if self.model is None:
            raise RuntimeError("Classifier model is not loaded.")

        if isinstance(image_input, (bytes, bytearray)):
            image = Image.open(io.BytesIO(image_input))
        elif isinstance(image_input, io.BytesIO):
            image = Image.open(image_input)
        elif isinstance(image_input, Image.Image):
            image = image_input
        else:
            raise ValueError(f"Unsupported image input type: {type(image_input)}")

        image_rgb = image.convert("RGB")
        tensor = self.transform(image_rgb).unsqueeze(0).to(self.device)

        with torch.no_grad():
            outputs = self.model(tensor)
            probabilities = torch.softmax(outputs, dim=1)[0]

        top_probs, top_indices = torch.topk(probabilities, k=min(2, len(self.class_names)))
        top1_prob = float(top_probs[0].item())
        top1_idx = int(top_indices[0].item())
        top1_label = self.idx_to_class[top1_idx]

        top2_label = None
        top2_prob = None
        if len(top_probs) > 1:
            top2_prob = float(top_probs[1].item())
            top2_idx = int(top_indices[1].item())
            top2_label = self.idx_to_class[top2_idx]

        # Check ambiguity: if top1 and top2 are very close and confidence is not overwhelming
        is_ambiguous = False
        if top2_prob is not None and (top1_prob - top2_prob) < self.ambiguity_delta and top1_prob < 0.85:
            is_ambiguous = True

        is_low_confidence = top1_prob < self.confidence_threshold

        all_scores = {
            self.idx_to_class[i]: round(float(p.item()), 4)
            for i, p in enumerate(probabilities)
        }

        return {
            "top_label": top1_label,
            "confidence": round(top1_prob, 4),
            "is_ambiguous": is_ambiguous,
            "is_low_confidence": is_low_confidence,
            "top2_label": top2_label,
            "top2_confidence": round(top2_prob, 4) if top2_prob is not None else None,
            "all_scores": all_scores,
        }


# Global singleton instance holder for API lifecycle
_classifier_instance: ComponentClassifier | None = None


def get_classifier(reload: bool = False) -> ComponentClassifier:
    """Returns singleton ComponentClassifier instance, initializing if needed."""
    global _classifier_instance
    if _classifier_instance is None or reload:
        _classifier_instance = ComponentClassifier()
    return _classifier_instance


def set_classifier(classifier: ComponentClassifier | None) -> None:
    """Allows setting or mocking the classifier instance."""
    global _classifier_instance
    _classifier_instance = classifier
