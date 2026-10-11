from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepurposeItem(APIModel):
    name: str = Field(min_length=1, max_length=80, examples=["old nokia"])
    kind: Literal["component", "device"] | None = None
    quantity: int = Field(default=1, ge=1, le=999)
    condition: Literal["working", "defective", "unknown"] = "working"
    defective_components: list[str] | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must contain non-whitespace characters")
        return value

    @field_validator("defective_components")
    @classmethod
    def trim_defects(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [part.strip() for part in value if part.strip()]

    @model_validator(mode="after")
    def defects_only_for_devices(self) -> "RepurposeItem":
        if self.defective_components and self.kind != "device":
            raise ValueError("defective_components is valid only when kind is device")
        return self


class RepurposeOptions(APIModel):
    max_results: int = Field(default=10, ge=1, le=50)
    min_score: float = Field(default=40, ge=0, le=100)
    max_difficulty: Literal["low", "medium", "high"] = "high"
    detail: Literal["summary", "full"] = "summary"
    include_best_combination: bool = True


class RepurposeRequest(APIModel):
    items: list[RepurposeItem] = Field(default_factory=list, max_length=50)
    options: RepurposeOptions = Field(default_factory=RepurposeOptions)

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{
                "items": [
                    {"name": "old nokia", "kind": "device", "quantity": 1, "condition": "defective", "defective_components": ["battery"]},
                    {"name": "ldr", "kind": "component", "quantity": 2, "condition": "working"},
                    {"name": "pot", "quantity": 1},
                ],
                "options": {"max_results": 10, "min_score": 40, "max_difficulty": "high", "detail": "summary", "include_best_combination": True},
            }]
        },
    )


class Alternative(APIModel):
    matched_id: str
    display_name: str
    kind: Literal["component", "device"]
    preferred: bool = False
    confidence: float = Field(ge=0, le=1)


class UnitGroup(APIModel):
    component_id: str
    qty: int = Field(ge=0)
    estimated: bool = False
    condition_factor: float = Field(ge=0, le=1)
    extraction_difficulty: str | None = None
    availability: str | None = None
    reuse_suitability: str | None = None
    from_source: str | None = None
    notes: list[str] = Field(default_factory=list)


class Exclusion(APIModel):
    component_id: str
    qty: int = 1
    reason: str
    disposal_note: str | None = None


class ResolvedInventoryItem(APIModel):
    input: str
    status: Literal["exact", "alias", "fuzzy", "suggested", "ambiguous", "unrecognized"]
    matched_id: str | None = None
    kind: Literal["component", "device"] | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    alternatives: list[Alternative] = Field(default_factory=list)
    units: list[UnitGroup] = Field(default_factory=list)
    excluded_defective: list[Exclusion] = Field(default_factory=list)
    skipped_for_safety: list[Exclusion] = Field(default_factory=list)
    prompt_user_to_specify_defects: bool = False
    defect_suggestions: list[str] = Field(default_factory=list)
    suggestions: list[Alternative] = Field(default_factory=list)


class UnrecognizedItem(APIModel):
    input: str
    suggestions: list[Alternative] = Field(default_factory=list)


class SatisfiedUnit(APIModel):
    component_id: str
    from_source: str
    qty: int = 1
    credit: float = Field(ge=0, le=1)
    via: Literal["exact", "substitution"]
    note: str | None = None
    estimated: bool = False


class PossibleSubstitute(APIModel):
    component_id: str
    display_name: str
    note: str


class RequirementResult(APIModel):
    requirement_id: str
    role: str
    critical: bool
    needed: int
    fill: float | None = Field(default=None, ge=0, le=1)
    status: Literal["have", "substitute", "missing", "defective", "unscored"]
    satisfied_by: list[SatisfiedUnit] = Field(default_factory=list)
    possible_substitutes: list[PossibleSubstitute] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ShoppingItem(APIModel):
    component_id: str
    qty: int
    tier: Literal["free", "cheap", "moderate", "expensive"]
    alternatives: list[str] = Field(default_factory=list)
    for_requirement: str


class WasteBySource(APIModel):
    component_id: str
    qty: int
    grams: float
    from_source: str


class WasteReduction(APIModel):
    grams: float
    pct_of_input: float
    label: Literal["estimate"] = "estimate"
    by_source: list[WasteBySource] = Field(default_factory=list)


class DataQuality(APIModel):
    needs_review: bool = False
    confidence: str = "unknown"
    review_reasons: list[str] = Field(default_factory=list)


class BuildStep(APIModel):
    model_config = ConfigDict(extra="allow")
    step: int
    title: str
    instruction: str | None = None
    check: str | None = None


class BuildWiring(APIModel):
    model_config = ConfigDict(extra="allow")
    from_: str = Field(alias="from")
    to: str
    note: str | None = None
    source_ids: list[int | str] = Field(default_factory=list)


class BuildVideo(APIModel):
    model_config = ConfigDict(extra="allow")
    title: str | None = None
    url: str | None = None


class BuildInstructions(APIModel):
    model_config = ConfigDict(extra="allow")
    prerequisites: list[str] = Field(default_factory=list)
    wiring: list[BuildWiring] = Field(default_factory=list)
    steps: list[BuildStep] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    safety: list[str] | str | None = None
    code_outline: str | None = None
    common_mistakes: list[str] = Field(default_factory=list)
    testing: list[str] | dict[str, object] | str | None = None


class BuildSource(APIModel):
    source_id: int | str | None = None
    title: str
    url: str
    type: str | None = None
    used_for: list[str] = Field(default_factory=list)


class BuildInfo(APIModel):
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[BuildStep] = Field(default_factory=list)
    wiring: list[BuildWiring] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    full_instructions: BuildInstructions | None = None
    sources: list[BuildSource] = Field(default_factory=list)
    youtube_videos: list[BuildVideo | str] = Field(default_factory=list)


class Recommendation(APIModel):
    project_id: str
    name: str
    description: str = ""
    category: str
    difficulty: str
    estimated_build_time_hours: float
    safety_level: str
    tier: Literal["ready", "almost", "partial", "not_viable", "unscored"]
    feasibility_score: float | None = None
    score_if_buying_cheap_parts: float | None = None
    rank: float | None = None
    requirements: list[RequirementResult]
    shopping_list: list[ShoppingItem] = Field(default_factory=list)
    waste_reduction: WasteReduction
    warnings: list[str] = Field(default_factory=list)
    data_quality: DataQuality
    build: BuildInfo


class LeftoverGroup(APIModel):
    component_id: str
    qty: int
    from_source: str
    estimated: bool = False
    condition_factor: float = 1


class CombinationProject(APIModel):
    project_id: str
    name: str
    feasibility_score: float
    waste_grams: float
    tier: str


class BestCombination(APIModel):
    projects: list[CombinationProject] = Field(default_factory=list)
    total_waste_grams: float
    reusable_leftovers: list[LeftoverGroup] = Field(default_factory=list)
    not_reusable: list[Exclusion] = Field(default_factory=list)


class Summary(APIModel):
    items_recognized: int
    projects_ready: int
    projects_almost: int
    top_waste_reduction_grams: float


class ResponseMeta(APIModel):
    catalog_version: str
    generated_at: str
    mode: Literal["match", "browse"]


class RepurposeResponse(APIModel):
    meta: ResponseMeta
    resolved_inventory: list[ResolvedInventoryItem]
    unrecognized: list[UnrecognizedItem] = Field(default_factory=list)
    safety_banners: list[str] = Field(default_factory=list)
    recommendations: list[Recommendation]
    best_combination: BestCombination | None = None
    summary: Summary
