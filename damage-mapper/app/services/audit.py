from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, Incident, VerificationHistory


def record_audit(
    session: Session,
    action: str,
    details: dict[str, Any],
    incident_id: str | None = None,
    actor_user_id: str | None = None,
) -> None:
    session.add(
        AuditLog(
            incident_id=incident_id,
            actor_user_id=actor_user_id,
            action=action,
            details=details,
        )
    )


def record_verification(
    session: Session,
    incident: Incident,
    previous_status: str | None,
    note: str | None = None,
    changed_by_user_id: str | None = None,
) -> None:
    session.add(
        VerificationHistory(
            incident_id=incident.id,
            status=incident.verification_status,
            previous_status=previous_status,
            changed_by_user_id=changed_by_user_id,
            note=note,
        )
    )
