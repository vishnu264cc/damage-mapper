from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SeverityFactorRead(BaseModel):
    factor: str
    value: float
    weight: float
    contribution: float
    evidence: str


class SeverityAssessmentRead(BaseModel):
    severity: Literal["Low", "Moderate", "High", "Critical"]
    score: float = Field(ge=0, le=100)
    factors: list[SeverityFactorRead]
    evidence: list[str]
    uncertainty: list[str]
    preliminary: Literal[True] = True
    label: str = "Preliminary decision-support assessment"


class PriorityWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: float = Field(ge=0, le=1)
    urgency: float = Field(ge=0, le=1)
    exposure: float = Field(ge=0, le=1)
    accessibility: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> PriorityWeights:
        if not math.isclose(
            self.severity + self.urgency + self.exposure + self.accessibility,
            1.0,
            abs_tol=0.0001,
        ):
            raise ValueError("Priority weights must sum to 1")
        return self


class PriorityFactorRead(BaseModel):
    factor: str
    value: float = Field(ge=0, le=100)
    weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=100)
    explanation: str


class PriorityAssessmentRead(BaseModel):
    score: float = Field(ge=0, le=100)
    factors: list[PriorityFactorRead]
    weights: PriorityWeights
    high_risk: bool
    high_risk_review_required: bool
    review_required: bool
    high_risk_threshold: float = Field(ge=0, le=100)
    preliminary: Literal[True] = True
    label: str = "Preliminary decision-support assessment"


class IncidentDecisionAssessmentRead(BaseModel):
    severity: SeverityAssessmentRead
    priority: PriorityAssessmentRead
    duplicate_candidate_count: int = Field(ge=0)
    duplicate_group_id: str | None
    preliminary: Literal[True] = True
    label: str = "Preliminary decision-support assessment"


class PriorityWeightsRead(BaseModel):
    weights: PriorityWeights
    high_risk_threshold: float = Field(ge=0, le=100)
    updated_by: str | None
    updated_at: datetime | None
    preliminary: Literal[True] = True
    label: str = "Preliminary decision-support configuration"


class DuplicateCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    incident_id: str
    candidate_incident_id: str
    match_score: float = Field(ge=0, le=1)
    hash_distance: int = Field(ge=0, le=64)
    distance_m: float = Field(ge=0)
    time_delta_seconds: float = Field(ge=0)
    status: Literal["candidate", "confirmed", "rejected"]
    review_note: str | None
    created_at: datetime
    reviewed_at: datetime | None


class DuplicateCandidatesRead(BaseModel):
    items: list[DuplicateCandidateRead]
    total: int
    returned: int
    preliminary: Literal[True] = True
    label: str = "Possible duplicate candidates; human review required"


class DuplicateReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "rejected"]
    review_note: str | None = Field(default=None, max_length=1000)


class DuplicateReviewRead(DuplicateCandidateRead):
    pass
