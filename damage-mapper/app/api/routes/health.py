from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.models import Incident
from app.schemas.incident import HealthResponse
from app.services.classification import model_error, model_name

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(get_db)) -> HealthResponse:
    try:
        session.execute(select(Incident.id, Incident.updated_at).limit(1))
        status = "ok"
    except SQLAlchemyError:
        status = "degraded"
    return HealthResponse(
        status=status,
        app="Damage Mapper",
        model=model_name(),
        model_error=model_error(),
        note="AI output is preliminary and requires human verification.",
    )
