from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import get_db
from app.api.auth_dependencies import AdminUser, ResponderOrAdmin
from app.core.config import get_settings
from app.models import DuplicateCandidate
from app.schemas.prioritization import (
    DuplicateCandidateRead,
    DuplicateCandidatesRead,
    DuplicateReviewRead,
    DuplicateReviewUpdate,
    IncidentDecisionAssessmentRead,
    PriorityWeights,
    PriorityWeightsRead,
)
from app.services.audit import record_audit
from app.services.prioritization.assessment import (
    refresh_all_assessments,
    refresh_incident_assessment,
)
from app.services.prioritization.duplicates import (
    duplicate_candidates_for_incident,
    find_existing_incident_candidates,
    review_duplicate_candidate,
)
from app.services.prioritization.settings import get_weights_record, save_weights
from app.services.incidents import IncidentNotFoundError, get_incident

router = APIRouter(prefix="/api", tags=["Prioritization"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.get(
    "/incidents/{incident_id}/assessment",
    response_model=IncidentDecisionAssessmentRead,
    summary="Get preliminary severity, priority and duplicate assessments",
)
def get_decision_assessment(
    incident_id: str,
    session: DatabaseSession,
    _: ResponderOrAdmin,
) -> IncidentDecisionAssessmentRead:
    try:
        incident = get_incident(session, incident_id)
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return refresh_incident_assessment(session, incident)


@router.post(
    "/incidents/{incident_id}/duplicates/analyze",
    response_model=DuplicateCandidatesRead,
    summary="Find nearby image-hash duplicate candidates for human review",
)
def analyze_incident_duplicates(
    incident_id: str,
    session: DatabaseSession,
    _: ResponderOrAdmin,
) -> DuplicateCandidatesRead:
    try:
        incident = get_incident(session, incident_id)
    except IncidentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    try:
        find_existing_incident_candidates(session, incident)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    pairs = duplicate_candidates_for_incident(session, incident_id)
    items = [DuplicateCandidateRead.model_validate(pair) for pair in pairs]
    return DuplicateCandidatesRead(items=items, total=len(items), returned=len(items))


@router.get(
    "/duplicates",
    response_model=DuplicateCandidatesRead,
    summary="List duplicate candidate pairs grouped by review group",
)
def list_duplicate_candidates(
    session: DatabaseSession,
    _: ResponderOrAdmin,
    status: Literal["candidate", "confirmed", "rejected"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DuplicateCandidatesRead:
    count_statement = select(func.count()).select_from(DuplicateCandidate)
    statement = select(DuplicateCandidate).options(
        selectinload(DuplicateCandidate.group)
    )
    if status:
        count_statement = count_statement.where(DuplicateCandidate.status == status)
        statement = statement.where(DuplicateCandidate.status == status)
    total = session.scalar(count_statement) or 0
    pairs = session.scalars(
        statement.order_by(
            DuplicateCandidate.match_score.desc(),
            DuplicateCandidate.created_at.desc(),
        ).limit(limit)
    ).all()
    items = [DuplicateCandidateRead.model_validate(pair) for pair in pairs]
    return DuplicateCandidatesRead(items=items, total=total, returned=len(items))


@router.patch(
    "/duplicates/{candidate_id}",
    response_model=DuplicateReviewRead,
    summary="Confirm or reject a possible duplicate without deleting either report",
)
def review_duplicate(
    candidate_id: str,
    payload: DuplicateReviewUpdate,
    session: DatabaseSession,
    actor: AdminUser,
) -> DuplicateReviewRead:
    try:
        pair = review_duplicate_candidate(
            session,
            candidate_id,
            status=payload.status,
            review_note=payload.review_note,
            actor_user_id=actor.id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return DuplicateReviewRead.model_validate(pair)


@router.get("/prioritization/weights", response_model=PriorityWeightsRead)
def read_priority_weights(
    session: DatabaseSession, _: ResponderOrAdmin
) -> PriorityWeightsRead:
    weights, updated_by, updated_at = get_weights_record(session)
    return PriorityWeightsRead(
        weights=weights,
        high_risk_threshold=get_settings().high_risk_priority_threshold,
        updated_by=updated_by,
        updated_at=updated_at,
    )


@router.put(
    "/prioritization/weights",
    response_model=PriorityWeightsRead,
    summary="Update priority weights and recalculate stored incident scores",
)
def update_priority_weights(
    payload: PriorityWeights,
    session: DatabaseSession,
    administrator: AdminUser,
) -> PriorityWeightsRead:
    record = save_weights(session, payload, updated_by=administrator.email)
    recalculated = refresh_all_assessments(session, payload)
    record_audit(
        session,
        "prioritization_weights_updated",
        {
            "weights": payload.model_dump(),
            "incidents_recalculated": recalculated,
            "preliminary_decision_support": True,
        },
        actor_user_id=administrator.id,
    )
    session.commit()
    return PriorityWeightsRead(
        weights=payload,
        high_risk_threshold=get_settings().high_risk_priority_threshold,
        updated_by=record.updated_by,
        updated_at=record.updated_at,
    )
