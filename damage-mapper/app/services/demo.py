from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.config import UPLOADS
from app.models import Incident, IncidentImage
from app.services.audit import record_audit, record_verification
from app.services.prioritization.assessment import build_incident_assessment
from app.services.uploads import _difference_hash

logger = logging.getLogger(__name__)
SAMPLES = [
    (
        "Flooded residential street; residents report water entering ground floors.",
        12.9716, 77.5946, "Flood", 92, True, 120,
    ),
    (
        "Road partly submerged and vehicles cannot pass safely.",
        12.9750, 77.6010, "Flood", 75, True, 45,
    ),
    (
        "Tree fallen across a local access road after strong winds.",
        12.9650, 77.5900, "Cyclone damage", 55, True, 12,
    ),
]


def seed_demo(
    session: Session, *, actor_user_id: str | None = None
) -> dict[str, list[str] | str]:
    image_path = UPLOADS / "demo-placeholder.png"
    if not image_path.exists():
        UPLOADS.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 360), (190, 205, 215)).save(image_path)
    try:
        image_hash = _difference_hash(image_path)
    except (OSError, UnidentifiedImageError):
        logger.exception("Could not read the demo placeholder image")
        raise
    image_bytes = image_path.read_bytes()
    created: list[str] = []
    now = datetime.now(timezone.utc)
    try:
        for index, sample in enumerate(SAMPLES, start=1):
            (
                description,
                latitude,
                longitude,
                category,
                urgency,
                access_blocked,
                people_affected,
            ) = sample
            incident_id = f"demo-{index}"
            exists = session.get(Incident, incident_id)
            if exists:
                continue
            incident = Incident(
                id=incident_id,
                description=description,
                latitude=latitude,
                longitude=longitude,
                reporter="Demo scenario",
                damage_type=category,
                model_confidence=0.0,
                severity="Low",
                severity_score=0,
                urgency=urgency,
                people_affected=people_affected,
                access_blocked=access_blocked,
                exposure_score=0,
                priority_score=0,
                verification_status="Verified",
                incident_status="Reported",
                created_at=now,
                updated_at=now,
                model_name="Synthetic demo data",
                model_analysis_status="synthetic",
                model_processing_ms=0,
                model_confidence_threshold=0.55,
                model_candidates=[],
                model_detected_objects=[],
                model_review_required=True,
                model_detector_status="not_run",
            )
            session.add(incident)
            session.flush()
            session.add(
                IncidentImage(
                    incident_id=incident_id,
                    storage_path=image_path.name,
                    original_filename="demo-placeholder.png",
                    content_type="image/png",
                    file_size=len(image_bytes),
                    sha256=hashlib.sha256(image_bytes).hexdigest(),
                    perceptual_hash=image_hash,
                    is_primary=True,
                )
            )
            build_incident_assessment(session, incident)
            record_verification(session, incident, None, "Synthetic demo report")
            record_audit(
                session,
                "demo_incident_created",
                {"synthetic": True},
                incident_id,
                actor_user_id=actor_user_id,
            )
            created.append(incident_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {
        "created": created,
        "message": "Synthetic demo incidents added. They are not real emergency reports.",
    }
