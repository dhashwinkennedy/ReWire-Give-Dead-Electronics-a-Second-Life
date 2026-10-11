from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class NormalizedInventory(BaseModel):
    name: str = Field(..., description="Canonical catalog identifier of the item")
    type: Literal["component", "device"] = Field(..., description="Classification category: component or device")
    kind: str | None = Field(default=None, description="Optional alias field mirroring type for backward compatibility")


class AlternativeMatch(BaseModel):
    label: str
    confidence: float
    matched_id: str | None = None
    type: str | None = None


class DetectionResponse(BaseModel):
    detected_label: str = Field(..., description="Label predicted by the MobileNetV3 classifier or 'unknown'")
    confidence: float = Field(..., description="Confidence score from the AI model (0.0 to 1.0)")
    resolved: bool = Field(..., description="Whether the prediction successfully resolved to a canonical catalog item")
    resolved_type: Literal["component", "device"] | None = Field(
        default=None, description="Type of catalog entity: 'component' or 'device'"
    )
    resolved_id: str | None = Field(
        default=None, description="Canonical catalog ID from component_catalog or device_salvage_catalog"
    )
    resolution_confidence: float | None = Field(
        default=None, description="Confidence score of mapping the label to the catalog (0.0 to 1.0)"
    )
    normalized_inventory: NormalizedInventory | None = Field(
        default=None, description="Inventory object compatible with the manual-input inventory pipeline"
    )
    reason: str | None = Field(
        default=None, description="Explanation when object could not be resolved or confidence was insufficient"
    )
    alternatives: list[AlternativeMatch] | None = Field(
        default=None, description="Top alternative predictions / catalog matches if applicable"
    )
