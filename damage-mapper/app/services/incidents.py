from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, time, timedelta, timezone
from math import ceil

from sqlalchemy import Numeric, String, case, cast, func, or_, select, text
from sqlalchemy.engine import ScalarResult
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import (
    AuditLog,
    DuplicateCandidate,
    Incident,
    IncidentImage,
    ResponderAssignment,
    User,
    VerificationHistory,
)
from app.schemas.incident import (
    IncidentAnalyticsFilters,
    IncidentAnalyticsResponse,
    IncidentMapPoint,
    IncidentMapResponse,
    IncidentPage,
    IncidentRead,
    IncidentUpdate,
)
from app.services.audit import record_audit, record_verification
from app.services.ai import analyze_image
from app.services.prioritization.assessment import build_incident_assessment
from app.services.prioritization.duplicates import (
    find_duplicate_candidates,
    record_duplicate_candidates,
)
from app.services.prioritization.priority import (
    population_exposure,
)
from app.services.prioritization.severity import estimate_severity
from app.services.prioritization.settings import get_weights_record
from app.services.uploads import StoredUpload, materialize_upload, remove_upload
from app.utils.geospatial import haversine_m

logger = logging.getLogger(__name__)


class IncidentNotFoundError(LookupError):
    pass


def _incident_query():
    return select(Incident).options(selectinload(Incident.images))


def get_incident(session: Session, incident_id: str) -> Incident:
    incident = session.scalar(
        _incident_query().where(Incident.id == incident_id)
    )
    if incident is None:
        raise IncidentNotFoundError("Incident not found")
    return incident


def list_incidents(
    session: Session,
    *,
    page: int,
    page_size: int,
    q: str | None = None,
    verification_status: str | None = None,
    incident_status: str | None = None,
    severity: str | None = None,
    damage_type: str | None = None,
    sort_by: str = "priority_score",
    sort_order: str = "desc",
    reporting_user_id: str | None = None,
) -> IncidentPage:
    filters = []
    if reporting_user_id is not None:
        filters.append(Incident.reporting_user_id == reporting_user_id)
    if q:
        needle = f"%{q.strip().lower()}%"
        filters.append(
            or_(
                func.lower(Incident.id).like(needle),
                func.lower(Incident.description).like(needle),
                func.lower(Incident.damage_type).like(needle),
                func.lower(func.coalesce(Incident.reporter, "")).like(needle),
                func.lower(func.coalesce(Incident.assigned_to, "")).like(needle),
            )
        )
    if verification_status:
        filters.append(Incident.verification_status == verification_status)
    if incident_status:
        filters.append(Incident.incident_status == incident_status)
    if severity:
        filters.append(Incident.severity == severity)
    if damage_type:
        filters.append(Incident.damage_type == damage_type)

    total = session.scalar(
        select(func.count()).select_from(Incident).where(*filters)
    ) or 0
    sort_columns = {
        "priority_score": Incident.priority_score,
        "created_at": Incident.created_at,
        "severity_score": Incident.severity_score,
    }
    sort_column = sort_columns[sort_by]
    sort_expression = sort_column.desc() if sort_order == "desc" else sort_column.asc()
    statement = (
        _incident_query()
        .where(*filters)
        .order_by(sort_expression, Incident.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records = session.scalars(statement).all()
    return IncidentPage(
        items=[IncidentRead.model_validate(record) for record in records],
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total else 0,
    )


def get_map_points(
    session: Session,
    *,
    limit: int,
    offset: int = 0,
    severity: str | None = None,
    damage_type: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_m: float | None = None,
) -> IncidentMapResponse:
    invalid_coordinate_filters = []
    if severity:
        invalid_coordinate_filters.append(Incident.severity == severity)
    if damage_type:
        invalid_coordinate_filters.append(Incident.damage_type == damage_type)
    invalid_coordinate_count = session.scalar(
        select(func.count())
        .select_from(Incident)
        .where(
            *invalid_coordinate_filters,
            or_(
                Incident.latitude.is_(None),
                Incident.longitude.is_(None),
                Incident.latitude < -90,
                Incident.latitude > 90,
                Incident.longitude < -180,
                Incident.longitude > 180,
            ),
        )
    ) or 0
    filters = [
        Incident.latitude.is_not(None),
        Incident.longitude.is_not(None),
        Incident.latitude.between(-90, 90),
        Incident.longitude.between(-180, 180),
    ]
    if severity:
        filters.append(Incident.severity == severity)
    if damage_type:
        filters.append(Incident.damage_type == damage_type)

    statement = select(Incident).where(*filters)
    if radius_m is not None and latitude is not None and longitude is not None:
        if session.get_bind().dialect.name == "postgresql":
            statement = statement.where(
                text(
                    """
                    ST_DWithin(
                        ST_SetSRID(ST_MakePoint(incidents.longitude, incidents.latitude), 4326)::geography,
                        ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                        :radius_m
                    )
                    """
                )
            )
            matching = session.scalars(
                statement.order_by(
                    Incident.priority_score.desc(), Incident.created_at.desc()
                ).offset(offset).limit(limit),
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius_m": radius_m,
                },
            ).all()
            total = session.scalar(
                select(func.count()).select_from(Incident).where(
                    *filters,
                    text(
                        """
                        ST_DWithin(
                            ST_SetSRID(ST_MakePoint(incidents.longitude, incidents.latitude), 4326)::geography,
                            ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                            :radius_m
                        )
                        """
                    ),
                ),
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius_m": radius_m,
                },
            ) or 0
        else:
            latitude_delta = radius_m / 111_320
            statement = statement.where(
                Incident.latitude.between(
                    max(-90, latitude - latitude_delta),
                    min(90, latitude + latitude_delta),
                )
            )
            longitude_delta = radius_m / max(
                111_320 * abs(math.cos(math.radians(latitude))), 1
            )
            if longitude_delta < 180:
                west, east = longitude - longitude_delta, longitude + longitude_delta
                if west < -180:
                    statement = statement.where(
                        or_(
                            Incident.longitude >= west + 360,
                            Incident.longitude <= east,
                        )
                    )
                elif east > 180:
                    statement = statement.where(
                        or_(
                            Incident.longitude >= west,
                            Incident.longitude <= east - 360,
                        )
                    )
                else:
                    statement = statement.where(
                        Incident.longitude.between(west, east)
                    )
            candidates = session.scalars(statement).all()
            matching = [
                incident
                for incident in candidates
                if haversine_m(
                    latitude, longitude, incident.latitude, incident.longitude
                )
                <= radius_m
            ]
            total = len(matching)
            matching.sort(
                key=lambda incident: (
                    incident.priority_score,
                    incident.created_at,
                ),
                reverse=True,
            )
            matching = matching[offset : offset + limit]
    else:
        total = session.scalar(
            select(func.count()).select_from(Incident).where(*filters)
        ) or 0
        matching = session.scalars(
            statement.order_by(
                Incident.priority_score.desc(), Incident.created_at.desc()
            ).offset(offset).limit(limit)
        ).all()

    incident_ids = [incident.id for incident in matching]
    duplicate_ids: set[str] = set()
    if incident_ids:
        duplicate_pairs = session.execute(
            select(
                DuplicateCandidate.incident_id,
                DuplicateCandidate.candidate_incident_id,
            ).where(
                DuplicateCandidate.status.in_(("candidate", "confirmed")),
                or_(
                    DuplicateCandidate.incident_id.in_(incident_ids),
                    DuplicateCandidate.candidate_incident_id.in_(incident_ids),
                ),
            )
        ).all()
        duplicate_ids = {
            incident_id
            for pair in duplicate_pairs
            for incident_id in pair
        }

    return IncidentMapResponse(
        items=[
            IncidentMapPoint(
                id=incident.id,
                latitude=incident.latitude,
                longitude=incident.longitude,
                description=incident.description,
                damage_type=incident.damage_type,
                severity=incident.severity,
                priority_score=incident.priority_score,
                verification_status=incident.verification_status,
                incident_status=incident.incident_status,
                reported_at=incident.created_at,
                duplicate_of=(
                    incident.duplicate_of
                    if incident.id in duplicate_ids
                    or (incident.duplicate_of and not incident.duplicate_group_id)
                    else None
                ),
                duplicate_group_id=incident.duplicate_group_id,
                possible_duplicate=(
                    incident.id in duplicate_ids
                    or (bool(incident.duplicate_of) and not incident.duplicate_group_id)
                ),
                assigned_to=incident.assigned_to,
            )
            for incident in matching
        ],
        total=total,
        returned=len(matching),
        offset=offset,
        has_more=offset + len(matching) < total,
        invalid_coordinates=invalid_coordinate_count,
    )


def create_incident(
    session: Session,
    *,
    description: str,
    latitude: float,
    longitude: float,
    urgency: int,
    people_affected: int,
    access_blocked: bool,
    reporter: str | None,
    upload: StoredUpload,
    reporting_user_id: str | None = None,
) -> Incident:
    try:
        with materialize_upload(upload.storage_path) as image_path:
            assessment = analyze_image(image_path)
        category = assessment.predicted_category or "Needs review"
        weights, _, _ = get_weights_record(session)
        severity_estimate = estimate_severity(
            urgency=urgency,
            people_affected=people_affected,
            access_blocked=access_blocked,
            visual_category=category if assessment.status == "complete" else None,
            visual_confidence=assessment.confidence,
            visual_detections=[
                {"label": item.label, "confidence": item.confidence}
                for item in assessment.detected_objects
            ],
            confidence_threshold=assessment.confidence_threshold,
        )
        incident = Incident(
            id=str(uuid.uuid4()),
            description=description.strip(),
            latitude=latitude,
            longitude=longitude,
            reporter=(reporter.strip() or None) if reporter else None,
            reporting_user_id=reporting_user_id,
            damage_type=category,
            model_confidence=assessment.confidence or 0.0,
            severity=severity_estimate.severity,
            severity_score=severity_estimate.score,
            urgency=urgency,
            people_affected=people_affected,
            access_blocked=access_blocked,
            exposure_score=round(population_exposure(people_affected), 1),
            priority_score=0,
            verification_status="Pending",
            incident_status="Reported",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            model_name=assessment.model_name,
            model_analysis_status=assessment.status,
            model_processing_ms=assessment.processing_time_ms,
            model_confidence_threshold=assessment.confidence_threshold,
            model_candidates=[
                {"category": item.category, "confidence": item.confidence}
                for item in assessment.candidate_classes
            ],
            model_detected_objects=[
                {"label": item.label, "confidence": item.confidence, "box": item.box}
                for item in assessment.detected_objects
            ],
            model_review_required=assessment.review_required,
            model_detector_status=assessment.detector_status,
            model_detector_name=assessment.detector_model_name,
        )
        session.add(incident)
        session.flush()
        incident.images.append(
            IncidentImage(
                storage_path=upload.storage_path,
                original_filename=upload.original_filename,
                content_type=upload.content_type,
                file_size=upload.file_size,
                sha256=upload.sha256,
                perceptual_hash=upload.perceptual_hash,
                is_primary=True,
            )
        )
        duplicate_matches = find_duplicate_candidates(
            session,
            incident_id=incident.id,
            latitude=latitude,
            longitude=longitude,
            created_at=incident.created_at,
            image_hash=upload.perceptual_hash,
        )
        record_duplicate_candidates(session, incident, duplicate_matches)
        session.flush()
        for match in duplicate_matches:
            build_incident_assessment(session, match.incident, weights=weights)
        decision_assessment = build_incident_assessment(
            session, incident, weights=weights
        )
        record_verification(
            session,
            incident,
            previous_status=None,
            note="Report submitted",
            changed_by_user_id=reporting_user_id,
        )
        record_audit(
            session,
            "incident_created",
            {
                "severity": decision_assessment.severity.severity,
                "severity_score": decision_assessment.severity.score,
                "priority_score": decision_assessment.priority.score,
                "damage_type": category,
                "preliminary_decision_support": True,
            },
            incident.id,
            actor_user_id=reporting_user_id,
        )
        session.commit()
        return get_incident(session, incident.id)
    except Exception:
        session.rollback()
        remove_upload(upload.storage_path)
        raise


def update_incident(
    session: Session,
    incident_id: str,
    payload: IncidentUpdate,
    *,
    actor_user_id: str | None = None,
) -> Incident:
    incident = get_incident(session, incident_id)
    changes: dict[str, dict[str, str | None]] = {}
    fields = payload.model_fields_set
    original_incident_status = incident.incident_status

    if "verification_status" in fields:
        if payload.verification_status is None:
            raise ValueError("verification_status cannot be null")
        previous = incident.verification_status
        if payload.verification_status != previous:
            incident.verification_status = payload.verification_status
            record_verification(
                session,
                incident,
                previous,
                changed_by_user_id=actor_user_id,
            )
            changes["verification_status"] = {
                "from": previous,
                "to": payload.verification_status,
            }

    if "incident_status" in fields:
        if payload.incident_status is None:
            raise ValueError("incident_status cannot be null")
        previous = incident.incident_status
        if payload.incident_status != previous:
            incident.incident_status = payload.incident_status
            changes["incident_status"] = {
                "from": previous,
                "to": payload.incident_status,
            }

    if "assigned_to" in fields:
        assigned_to = (payload.assigned_to or "").strip() or None
        previous = incident.assigned_to
        if assigned_to != previous:
            incident.assigned_to = assigned_to
            responder = None
            if assigned_to:
                responders = session.scalars(
                    select(User).where(
                        User.is_active.is_(True),
                        User.role == "responder",
                        or_(
                            func.lower(User.email) == assigned_to.lower(),
                            func.lower(User.display_name) == assigned_to.lower(),
                            User.id == assigned_to,
                        ),
                    )
                ).all()
                if len(responders) != 1:
                    raise ValueError("Assignment must target an active responder account")
                responder = responders[0]
                incident.assigned_to = responder.display_name
            active_assignments = session.scalars(
                select(ResponderAssignment).where(
                    ResponderAssignment.incident_id == incident.id,
                    ResponderAssignment.active.is_(True),
                )
            ).all()
            for assignment in active_assignments:
                assignment.active = False
            if assigned_to:
                session.add(
                    ResponderAssignment(
                        incident_id=incident.id,
                        responder_user_id=responder.id if responder else None,
                        assigned_by_user_id=actor_user_id,
                        assigned_to=responder.display_name if responder else assigned_to,
                        active=True,
                    )
                )
            changes["assigned_to"] = {
                "from": previous,
                "to": responder.display_name if responder else None,
            }
            if "incident_status" not in fields:
                incident.incident_status = "Assigned" if assigned_to else "Reported"
                changes["incident_status"] = {
                    "from": original_incident_status,
                    "to": incident.incident_status,
                }

    if not fields:
        raise ValueError("No supported fields supplied")
    if changes:
        incident.updated_at = datetime.now(timezone.utc)
        record_audit(
            session,
            "incident_updated",
            changes,
            incident.id,
            actor_user_id=actor_user_id,
        )
    session.commit()
    return get_incident(session, incident.id)


def delete_incident(
    session: Session, incident_id: str, *, actor_user_id: str | None = None
) -> None:
    incident = get_incident(session, incident_id)
    storage_paths = [image.storage_path for image in incident.images]
    record_audit(
        session,
        "incident_deleted",
        {"deleted_incident_id": incident.id, "description": incident.description},
        actor_user_id=actor_user_id,
    )
    session.delete(incident)
    session.commit()
    for storage_path in storage_paths:
        still_referenced = session.scalar(
            select(IncidentImage.id).where(IncidentImage.storage_path == storage_path).limit(1)
        )
        if still_referenced is None:
            remove_upload(storage_path)


def get_incident_history(session: Session, incident_id: str) -> dict:
    incident = get_incident(session, incident_id)
    logs = session.scalars(
        select(AuditLog)
        .where(AuditLog.incident_id == incident_id)
        .order_by(AuditLog.created_at.desc())
    ).all()
    return {
        "incident_id": incident.id,
        "verification_history": incident.verification_history,
        "audit_log": logs,
    }


def get_stats(session: Session) -> dict[str, int]:
    total = session.scalar(select(func.count()).select_from(Incident)) or 0
    pending = session.scalar(
        select(func.count()).select_from(Incident).where(
            Incident.verification_status == "Pending"
        )
    ) or 0
    critical = session.scalar(
        select(func.count()).select_from(Incident).where(
            Incident.severity == "Critical",
            Incident.verification_status != "Rejected",
        )
    ) or 0
    resolved = session.scalar(
        select(func.count()).select_from(Incident).where(
            Incident.incident_status == "Resolved"
        )
    ) or 0
    return {"total": total, "pending": pending, "critical": critical, "resolved": resolved}


def _count_by(
    session: Session, column, filters: tuple = ()
) -> dict[str, int]:
    rows = session.execute(
        select(column, func.count()).where(*filters).group_by(column)
    ).all()
    return {str(key or "Unspecified"): count for key, count in rows}


def _analytics_conditions(
    filters: IncidentAnalyticsFilters,
) -> list:
    conditions = []
    if filters.date_from is not None:
        start = datetime.combine(filters.date_from, time.min, tzinfo=timezone.utc)
        conditions.append(Incident.created_at >= start)
    if filters.date_to is not None:
        end_exclusive = datetime.combine(
            filters.date_to + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        conditions.append(Incident.created_at < end_exclusive)
    if filters.min_latitude is not None:
        conditions.extend(
            (
                Incident.latitude >= filters.min_latitude,
                Incident.latitude <= filters.max_latitude,
                Incident.longitude >= filters.min_longitude,
                Incident.longitude <= filters.max_longitude,
            )
        )
    if filters.damage_type is not None:
        conditions.append(Incident.damage_type == filters.damage_type)
    if filters.severity is not None:
        conditions.append(Incident.severity == filters.severity)
    if filters.verification_status is not None:
        conditions.append(
            Incident.verification_status == filters.verification_status
        )
    if filters.incident_status is not None:
        conditions.append(Incident.incident_status == filters.incident_status)
    return conditions


def _trend_granularity(filters: IncidentAnalyticsFilters) -> str:
    if filters.date_from is None:
        return "month"
    end = filters.date_to or datetime.now(timezone.utc).date()
    return "day" if (end - filters.date_from).days <= 90 else "month"


def _trend_period_expression(granularity: str):
    date_text = cast(Incident.created_at, String)
    return func.substr(date_text, 1, 10 if granularity == "day" else 7)


def _average_verification_seconds(
    session: Session, filters: list
) -> tuple[float | None, int]:
    first_review = (
        select(
            VerificationHistory.incident_id.label("incident_id"),
            func.min(VerificationHistory.created_at).label("reviewed_at"),
        )
        .join(Incident, Incident.id == VerificationHistory.incident_id)
        .where(
            *filters,
            VerificationHistory.status.in_(("Verified", "Rejected")),
        )
        .group_by(VerificationHistory.incident_id)
        .subquery()
    )
    if session.get_bind().dialect.name == "sqlite":
        elapsed_seconds = (
            func.julianday(first_review.c.reviewed_at)
            - func.julianday(Incident.created_at)
        ) * 86_400
    else:
        elapsed_seconds = func.extract(
            "epoch", first_review.c.reviewed_at - Incident.created_at
        )
    average, count = session.execute(
        select(func.avg(elapsed_seconds), func.count())
        .select_from(Incident)
        .join(first_review, first_review.c.incident_id == Incident.id)
        .where(*filters)
    ).one()
    return (
        round(float(average), 1) if average is not None else None,
        int(count),
    )


def get_analytics(
    session: Session,
    filters: IncidentAnalyticsFilters | None = None,
) -> IncidentAnalyticsResponse:
    filters = filters or IncidentAnalyticsFilters()
    conditions = _analytics_conditions(filters)
    incident_count = func.count()
    high_priority_threshold = get_settings().high_risk_priority_threshold
    (
        total,
        verified,
        pending,
        critical_high,
        high_priority,
        resolved,
        active,
        mean_priority,
        duplicate_candidates,
        model_analysed,
        mean_confidence,
        needs_review,
    ) = session.execute(
        select(
            func.count(Incident.id),
            func.count(case((Incident.verification_status == "Verified", Incident.id))),
            func.count(case((Incident.verification_status == "Pending", Incident.id))),
            func.count(case((Incident.severity.in_(("Critical", "High")), Incident.id))),
            func.count(case((Incident.priority_score >= high_priority_threshold, Incident.id))),
            func.count(case((Incident.incident_status == "Resolved", Incident.id))),
            func.count(case((Incident.incident_status != "Resolved", Incident.id))),
            func.avg(Incident.priority_score),
            func.count(case((Incident.duplicate_of.is_not(None), Incident.id))),
            func.count(case((Incident.model_analysis_status == "complete", Incident.id))),
            func.avg(
                case(
                    (
                        Incident.model_analysis_status == "complete",
                        Incident.model_confidence,
                    )
                )
            ),
            func.count(case((Incident.model_review_required.is_(True), Incident.id))),
        )
        .select_from(Incident)
        .where(*conditions)
    ).one()
    granularity = _trend_granularity(filters)
    period = _trend_period_expression(granularity)
    trend_rows = session.execute(
        select(period.label("period"), incident_count.label("count"))
        .select_from(Incident)
        .where(*conditions)
        .group_by(period)
        .order_by(period)
    ).all()
    latitude_cell = func.round(cast(Incident.latitude, Numeric), 2)
    longitude_cell = func.round(cast(Incident.longitude, Numeric), 2)
    concentration_rows = session.execute(
        select(
            latitude_cell.label("latitude"),
            longitude_cell.label("longitude"),
            incident_count.label("count"),
        )
        .select_from(Incident)
        .where(
            *conditions,
            Incident.latitude.between(-90, 90),
            Incident.longitude.between(-180, 180),
        )
        .group_by(latitude_cell, longitude_cell)
        .order_by(incident_count.desc(), latitude_cell, longitude_cell)
        .limit(20)
    ).all()
    average_verification_seconds, verification_time_count = (
        _average_verification_seconds(session, conditions)
    )
    return IncidentAnalyticsResponse(
        total=int(total or 0),
        verified=int(verified or 0),
        pending=int(pending or 0),
        critical_high=int(critical_high or 0),
        high_priority=int(high_priority or 0),
        high_priority_threshold=high_priority_threshold,
        resolved=int(resolved or 0),
        active=int(active or 0),
        mean_priority=round(mean_priority or 0, 1),
        average_verification_seconds=average_verification_seconds,
        verification_time_count=verification_time_count,
        duplicate_candidates=int(duplicate_candidates or 0),
        severity_counts=_count_by(session, Incident.severity, tuple(conditions)),
        verification_counts=_count_by(
            session, Incident.verification_status, tuple(conditions)
        ),
        response_counts=_count_by(
            session, Incident.incident_status, tuple(conditions)
        ),
        category_counts=_count_by(session, Incident.damage_type, tuple(conditions)),
        trend_granularity=granularity,
        trends=[
            {"period": str(row.period), "count": int(row.count)}
            for row in trend_rows
        ],
        geographic_concentrations=[
            {
                "latitude": float(row.latitude),
                "longitude": float(row.longitude),
                "count": int(row.count),
            }
            for row in concentration_rows
        ],
        model_analysed=int(model_analysed or 0),
        mean_model_confidence=round(mean_confidence, 4) if mean_confidence is not None else None,
        needs_review=int(needs_review or 0),
    )


def iter_analytics_export_records(
    session: Session,
    filters: IncidentAnalyticsFilters,
) -> ScalarResult[Incident]:
    return session.scalars(
        select(Incident)
        .where(*_analytics_conditions(filters))
        .order_by(Incident.created_at.desc(), Incident.id)
        .execution_options(yield_per=500)
    )
