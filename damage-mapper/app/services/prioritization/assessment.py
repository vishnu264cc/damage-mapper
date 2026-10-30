from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import DuplicateCandidate, Incident
from app.schemas.prioritization import (
    IncidentDecisionAssessmentRead,
    PriorityWeights,
)
from app.services.prioritization.priority import (
    calculate_priority,
    population_exposure,
)
from app.services.prioritization.severity import estimate_severity
from app.services.prioritization.settings import get_weights_record


def build_incident_assessment(
    session: Session,
    incident: Incident,
    *,
    weights: PriorityWeights | None = None,
) -> IncidentDecisionAssessmentRead:
    if weights is None:
        weights, _, _ = get_weights_record(session)
    settings = get_settings()
    is_model_prediction = incident.model_analysis_status == "complete"
    severity_assessment = estimate_severity(
        urgency=incident.urgency,
        people_affected=incident.people_affected,
        access_blocked=incident.access_blocked,
        visual_detections=incident.model_detected_objects or [],
        confidence_threshold=incident.model_confidence_threshold,
    )
    priority_assessment = calculate_priority(
        severity_score=severity_assessment.score,
        urgency=incident.urgency,
        people_affected=incident.people_affected,
        access_blocked=incident.access_blocked,
        severity=severity_assessment.severity,
        model_review_required=incident.model_review_required
        or not is_model_prediction,
        weights=weights,
        high_risk_threshold=settings.high_risk_priority_threshold,
    )
    duplicate_count = session.scalar(
        select(func.count())
        .select_from(DuplicateCandidate)
        .where(
            or_(
                DuplicateCandidate.incident_id == incident.id,
                DuplicateCandidate.candidate_incident_id == incident.id,
            )
        )
    ) or 0
    assessment = IncidentDecisionAssessmentRead(
        severity=severity_assessment.as_dict(),
        priority=priority_assessment.as_dict(),
        duplicate_candidate_count=duplicate_count,
        duplicate_group_id=incident.duplicate_group_id,
    )
    incident.severity = severity_assessment.severity
    incident.severity_score = severity_assessment.score
    incident.exposure_score = round(population_exposure(incident.people_affected), 1)
    incident.priority_score = priority_assessment.score
    incident.decision_assessment = assessment.model_dump(mode="json")
    return assessment


def refresh_incident_assessment(
    session: Session,
    incident: Incident,
    *,
    weights: PriorityWeights | None = None,
) -> IncidentDecisionAssessmentRead:
    assessment = build_incident_assessment(session, incident, weights=weights)
    session.commit()
    session.refresh(incident)
    return assessment


def refresh_all_assessments(session: Session, weights: PriorityWeights) -> int:
    incidents = session.scalars(select(Incident)).all()
    for incident in incidents:
        build_incident_assessment(session, incident, weights=weights)
    return len(incidents)
