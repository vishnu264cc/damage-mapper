from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="citizen")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    assignments: Mapped[list[ResponderAssignment]] = relationship(
        back_populates="responder",
        foreign_keys="ResponderAssignment.responder_user_id",
    )
    incidents: Mapped[list[Incident]] = relationship(back_populates="reporting_user")


class DuplicateGroup(Base):
    __tablename__ = "duplicate_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    incidents: Mapped[list[Incident]] = relationship(back_populates="duplicate_group")


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        UniqueConstraint(
            "incident_id",
            "candidate_incident_id",
            name="uq_duplicate_candidates_pair",
        ),
        CheckConstraint("match_score >= 0 AND match_score <= 1", name="match_score_range"),
        Index("ix_duplicate_candidates_group_status", "group_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("duplicate_groups.id", ondelete="CASCADE"), index=True
    )
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    candidate_incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    match_score: Mapped[float] = mapped_column(Float)
    hash_distance: Mapped[int] = mapped_column(Integer)
    distance_m: Mapped[float] = mapped_column(Float)
    time_delta_seconds: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    review_note: Mapped[str | None] = mapped_column(String(1000))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    group: Mapped[DuplicateGroup] = relationship()
    incident: Mapped[Incident] = relationship(foreign_keys=[incident_id])
    candidate_incident: Mapped[Incident] = relationship(foreign_keys=[candidate_incident_id])


class PrioritizationSettings(Base):
    __tablename__ = "prioritization_settings"

    id: Mapped[str] = mapped_column(String(30), primary_key=True, default="default")
    weights: Mapped[dict[str, float]] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResponderLocation(Base):
    __tablename__ = "responder_locations"
    __table_args__ = (
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="latitude_range"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="longitude_range"),
    )

    responder_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthBootstrapClaim(Base):
    __tablename__ = "auth_bootstrap_claims"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="latitude_range"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="longitude_range"),
        CheckConstraint("urgency >= 0 AND urgency <= 100", name="urgency_range"),
        CheckConstraint("people_affected >= 0", name="people_affected_nonnegative"),
        CheckConstraint("severity_score >= 0 AND severity_score <= 100", name="severity_score_range"),
        CheckConstraint("priority_score >= 0 AND priority_score <= 100", name="priority_score_range"),
        Index("ix_incidents_priority_created", "priority_score", "created_at"),
        Index("ix_incidents_verification_status", "verification_status"),
        Index("ix_incidents_incident_status", "incident_status"),
        Index("ix_incidents_severity", "severity"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True, default=new_id)
    description: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    reporter: Mapped[str | None] = mapped_column(String(200))
    reporting_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    damage_type: Mapped[str] = mapped_column(String(100))
    model_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str] = mapped_column(String(30))
    severity_score: Mapped[float] = mapped_column(Float)
    urgency: Mapped[int] = mapped_column(Integer)
    people_affected: Mapped[int] = mapped_column(Integer, default=0)
    access_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    exposure_score: Mapped[float] = mapped_column(Float, default=0.0)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    duplicate_of: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), index=True
    )
    duplicate_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("duplicate_groups.id", ondelete="SET NULL"), index=True
    )
    verification_status: Mapped[str] = mapped_column(String(30), default="Pending")
    incident_status: Mapped[str] = mapped_column(String(30), default="Reported")
    assigned_to: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    model_name: Mapped[str] = mapped_column(String(200), default="Needs review")
    model_analysis_status: Mapped[str] = mapped_column(
        String(20), default="legacy", server_default="legacy"
    )
    model_processing_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    model_confidence_threshold: Mapped[float] = mapped_column(
        Float, default=0.55, server_default="0.55"
    )
    model_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model_detected_objects: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model_review_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    model_detector_status: Mapped[str] = mapped_column(
        String(20), default="not_run", server_default="not_run"
    )
    model_detector_name: Mapped[str | None] = mapped_column(String(200))
    decision_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    reporting_user: Mapped[User | None] = relationship(back_populates="incidents")
    duplicate_group: Mapped[DuplicateGroup | None] = relationship(back_populates="incidents")
    images: Mapped[list[IncidentImage]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="IncidentImage.created_at"
    )
    verification_history: Mapped[list[VerificationHistory]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="VerificationHistory.created_at"
    )
    assignments: Mapped[list[ResponderAssignment]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="ResponderAssignment.created_at"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="incident")

    @property
    def image_url(self) -> str:
        primary = next((image for image in self.images if image.is_primary), None)
        image = primary or (self.images[0] if self.images else None)
        return f"/api/incidents/{self.id}/image" if image else ""

    @property
    def ai_analysis(self) -> dict[str, Any]:
        is_current_prediction = self.model_analysis_status == "complete"
        return {
            "status": self.model_analysis_status,
            "predicted_category": self.damage_type if is_current_prediction else None,
            "confidence": self.model_confidence if is_current_prediction else None,
            "model_name": self.model_name,
            "processing_time_ms": self.model_processing_ms,
            "candidate_classes": self.model_candidates or [],
            "detected_objects": self.model_detected_objects or [],
            "review_required": self.model_review_required,
            "confidence_threshold": self.model_confidence_threshold,
            "detector_status": self.model_detector_status,
            "detector_model_name": self.model_detector_name,
        }

    @property
    def preliminary_assessment(self) -> dict[str, Any] | None:
        return self.decision_assessment


class IncidentImage(Base):
    __tablename__ = "incident_images"
    __table_args__ = (
        CheckConstraint("file_size >= 0", name="file_size_nonnegative"),
        Index("ix_incident_images_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    storage_path: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    perceptual_hash: Mapped[str | None] = mapped_column(String(16))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    incident: Mapped[Incident] = relationship(back_populates="images")


class VerificationHistory(Base):
    __tablename__ = "verification_history"
    __table_args__ = (Index("ix_verification_history_incident_created", "incident_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    previous_status: Mapped[str | None] = mapped_column(String(30))
    changed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    incident: Mapped[Incident] = relationship(back_populates="verification_history")


class ResponderAssignment(Base):
    __tablename__ = "responder_assignments"
    __table_args__ = (Index("ix_responder_assignments_incident_created", "incident_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    responder_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_to: Mapped[str] = mapped_column(String(200))
    assigned_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    incident: Mapped[Incident] = relationship(back_populates="assignments")
    responder: Mapped[User | None] = relationship(
        back_populates="assignments",
        foreign_keys=[responder_user_id],
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_incident_created", "incident_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    incident: Mapped[Incident | None] = relationship(back_populates="audit_logs")
