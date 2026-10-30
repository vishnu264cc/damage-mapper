from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DamageCategory = Literal[
    "Flood damage",
    "Building damage",
    "Fire and smoke",
    "Landslide",
    "Cyclone damage",
    "Road and infrastructure obstruction",
    "Other / unclear",
]


class CandidateClassRead(BaseModel):
    category: DamageCategory
    confidence: float = Field(ge=0, le=1)


class DetectedObjectRead(BaseModel):
    label: str
    confidence: float = Field(ge=0, le=1)
    box: tuple[float, float, float, float] = Field(
        description="Normalized [x1, y1, x2, y2] coordinates in the 0..1 image range"
    )


class AIAnalysisRead(BaseModel):
    status: Literal["complete", "unavailable", "legacy", "synthetic"]
    predicted_category: DamageCategory | None
    confidence: float | None = Field(default=None, ge=0, le=1)
    model_name: str
    processing_time_ms: float = Field(ge=0)
    candidate_classes: list[CandidateClassRead]
    detected_objects: list[DetectedObjectRead]
    review_required: bool
    confidence_threshold: float = Field(ge=0, le=1)
    detector_status: Literal["available", "disabled", "unavailable", "not_run"]
    detector_model_name: str | None
    note: str = (
        "Model confidence is an uncalibrated classification score, not a "
        "validated probability, severity, or verification."
    )
