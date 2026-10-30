from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import PrioritizationSettings
from app.schemas.prioritization import PriorityWeights
from app.services.prioritization.priority import DEFAULT_PRIORITY_WEIGHTS


def get_weights_record(session: Session) -> tuple[PriorityWeights, str | None, datetime | None]:
    record = session.get(PrioritizationSettings, "default")
    if record is None:
        return PriorityWeights(**DEFAULT_PRIORITY_WEIGHTS), None, None
    return PriorityWeights(**record.weights), record.updated_by, record.updated_at


def save_weights(
    session: Session,
    weights: PriorityWeights,
    *,
    updated_by: str,
) -> PrioritizationSettings:
    record = session.get(PrioritizationSettings, "default")
    if record is None:
        record = PrioritizationSettings(
            id="default",
            weights=weights.model_dump(),
            updated_by=updated_by,
            updated_at=datetime.now(timezone.utc),
        )
        session.add(record)
    else:
        record.weights = weights.model_dump()
        record.updated_by = updated_by
        record.updated_at = datetime.now(timezone.utc)
    return record


def current_high_risk_threshold() -> float:
    return get_settings().high_risk_priority_threshold
