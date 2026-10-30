from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.ai import AIAnalysisRead
from app.schemas.prioritization import IncidentDecisionAssessmentRead

VerificationStatus = Literal["Pending", "Verified", "Rejected"]
ResponseStatus = Literal["Reported", "Assigned", "In progress", "Resolved"]
Severity = Literal["Critical", "High", "Moderate", "Low"]


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    description: str
    latitude: float
    longitude: float
    reporter: str | None
    damage_type: str
    model_confidence: float = Field(ge=0, le=1)
    severity: str
    severity_score: float
    urgency: int
    people_affected: int
    access_blocked: bool
    exposure_score: float
    priority_score: float
    duplicate_of: str | None
    verification_status: str
    incident_status: str
    assigned_to: str | None
    created_at: datetime
    updated_at: datetime
    model_name: str
    ai_analysis: AIAnalysisRead
    decision_assessment: IncidentDecisionAssessmentRead | None = Field(
        validation_alias="preliminary_assessment"
    )
    image_url: str


class IncidentPage(BaseModel):
    items: list[IncidentRead]
    total: int
    page: int
    page_size: int
    pages: int


class IncidentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_status: VerificationStatus | None = None
    incident_status: ResponseStatus | None = None
    assigned_to: str | None = Field(default=None, max_length=200)


class IncidentMapPoint(BaseModel):
    id: str
    latitude: float
    longitude: float
    description: str
    damage_type: str
    severity: str
    priority_score: float
    verification_status: str
    incident_status: str
    reported_at: datetime
    duplicate_of: str | None
    duplicate_group_id: str | None
    possible_duplicate: bool
    assigned_to: str | None


class IncidentMapResponse(BaseModel):
    items: list[IncidentMapPoint]
    total: int
    returned: int
    offset: int = 0
    has_more: bool = False
    invalid_coordinates: int = 0


class ResponderLocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responder_name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @field_validator("responder_name")
    @classmethod
    def normalize_responder_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Responder name cannot be blank")
        return normalized


class ResponderLocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    responder_name: str
    latitude: float
    longitude: float
    updated_at: datetime


class ResponderLocationList(BaseModel):
    items: list[ResponderLocationRead]
    total: int


class MapLocationFilters(BaseModel):
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    radius_m: float | None = Field(default=None, gt=0, le=500_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_complete_radius(self) -> MapLocationFilters:
        supplied = (self.latitude is not None, self.longitude is not None, self.radius_m is not None)
        if any(supplied) and not all(supplied):
            raise ValueError("latitude, longitude, and radius_m must be supplied together")
        return self


class VerificationHistoryRead(BaseModel):
    status: str
    previous_status: str | None
    note: str | None
    created_at: datetime


class AuditLogRead(BaseModel):
    action: str
    details: dict
    created_at: datetime


class IncidentHistoryResponse(BaseModel):
    incident_id: str
    verification_history: list[VerificationHistoryRead]
    audit_log: list[AuditLogRead]


class IncidentStatsResponse(BaseModel):
    total: int
    pending: int
    critical: int
    resolved: int


class IncidentAnalyticsResponse(BaseModel):
    total: int
    verified: int
    pending: int
    critical_high: int
    high_priority: int
    high_priority_threshold: float
    resolved: int
    active: int
    mean_priority: float
    average_verification_seconds: float | None
    verification_time_count: int
    duplicate_candidates: int
    severity_counts: dict[str, int]
    verification_counts: dict[str, int]
    response_counts: dict[str, int]
    category_counts: dict[str, int]
    trend_granularity: Literal["day", "month"]
    trends: list[IncidentTrendPoint]
    geographic_concentrations: list[IncidentGeographicConcentration]
    model_analysed: int
    mean_model_confidence: float | None
    needs_review: int


class IncidentTrendPoint(BaseModel):
    period: str
    count: int


class IncidentGeographicConcentration(BaseModel):
    latitude: float
    longitude: float
    count: int


class IncidentAnalyticsFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    min_latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    max_latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    min_longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    max_longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    damage_type: str | None = Field(default=None, min_length=1, max_length=100)
    severity: Severity | None = None
    verification_status: VerificationStatus | None = None
    incident_status: ResponseStatus | None = None

    @field_validator("damage_type")
    @classmethod
    def normalize_damage_type(cls, value: str | None) -> str | None:
        normalized = value.strip() if value is not None else None
        if normalized == "":
            raise ValueError("damage_type cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_ranges(self) -> IncidentAnalyticsFilters:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        location = (
            self.min_latitude,
            self.max_latitude,
            self.min_longitude,
            self.max_longitude,
        )
        if any(value is not None for value in location) and any(
            value is None for value in location
        ):
            raise ValueError(
                "All four geographic bounds must be supplied together"
            )
        if self.min_latitude is not None and self.min_latitude >= self.max_latitude:
            raise ValueError("min_latitude must be less than max_latitude")
        if self.min_longitude is not None and self.min_longitude >= self.max_longitude:
            raise ValueError("min_longitude must be less than max_longitude")
        return self


class HealthResponse(BaseModel):
    status: str
    app: str
    model: str
    model_error: str | None
    note: str


class DemoSeedResponse(BaseModel):
    created: list[str]
    message: str
