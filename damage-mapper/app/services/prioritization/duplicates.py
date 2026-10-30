from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    DuplicateCandidate,
    DuplicateGroup,
    Incident,
    IncidentImage,
)
from app.services.audit import record_audit
from app.services.uploads import _difference_hash, materialize_upload
from app.utils.geospatial import haversine_m, nearby_incidents

logger = logging.getLogger(__name__)
HASH_WEIGHT = 0.50
GEO_WEIGHT = 0.30
TIME_WEIGHT = 0.20


@dataclass(frozen=True)
class DuplicateMatch:
    incident: Incident
    hash_distance: int
    distance_m: float
    time_delta_seconds: float
    match_score: float


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def find_duplicate_candidates(
    session: Session,
    *,
    incident_id: str | None,
    latitude: float,
    longitude: float,
    created_at: datetime,
    image_hash: str,
) -> list[DuplicateMatch]:
    settings = get_settings()
    radius = settings.duplicate_radius_m
    window_seconds = settings.duplicate_window_hours * 3600
    new_hash = int(image_hash, 16)
    matches: list[DuplicateMatch] = []
    nearby = nearby_incidents(
        session,
        latitude,
        longitude,
        radius_m=radius,
        max_age_hours=settings.duplicate_window_hours,
        reference_time=created_at,
    )
    for candidate in nearby:
        if candidate.id == incident_id:
            continue
        image = session.scalar(
            select(IncidentImage).where(
                IncidentImage.incident_id == candidate.id,
                IncidentImage.is_primary.is_(True),
            )
        )
        if image is None:
            continue
        try:
            if image.perceptual_hash is not None:
                old_hash = int(image.perceptual_hash, 16)
            else:
                with materialize_upload(image.storage_path) as path:
                    if not path.is_file():
                        continue
                    old_hash = int(_difference_hash(path), 16)
                image.perceptual_hash = f"{old_hash:016x}"
        except (OSError, ValueError):
            logger.warning(
                "Could not compute duplicate image hash for incident %s",
                candidate.id,
                exc_info=True,
            )
            continue

        hash_distance = (new_hash ^ old_hash).bit_count()
        if hash_distance > settings.duplicate_hash_distance:
            continue
        distance = haversine_m(
            latitude, longitude, candidate.latitude, candidate.longitude
        )
        if distance > radius:
            continue
        time_delta = abs(
            (_aware(created_at) - _aware(candidate.created_at)).total_seconds()
        )
        if time_delta > window_seconds:
            continue
        hash_similarity = 1 - hash_distance / 64
        geo_similarity = 1 - distance / radius
        time_similarity = 1 - time_delta / window_seconds
        score = (
            HASH_WEIGHT * hash_similarity
            + GEO_WEIGHT * geo_similarity
            + TIME_WEIGHT * time_similarity
        )
        matches.append(
            DuplicateMatch(
                incident=candidate,
                hash_distance=hash_distance,
                distance_m=round(distance, 2),
                time_delta_seconds=round(time_delta, 2),
                match_score=round(min(1.0, max(0.0, score)), 4),
            )
        )
    return sorted(matches, key=lambda match: match.match_score, reverse=True)


def record_duplicate_candidates(
    session: Session, incident: Incident, matches: list[DuplicateMatch]
) -> list[DuplicateCandidate]:
    if not matches:
        return []

    existing_group_ids = list(
        dict.fromkeys(
            match.incident.duplicate_group_id
            for match in matches
            if match.incident.duplicate_group_id
        )
    )
    group = None
    if existing_group_ids:
        group = session.get(DuplicateGroup, existing_group_ids[0])
    if group is None:
        group = DuplicateGroup(status="candidate")
        session.add(group)
        session.flush()

    if existing_group_ids:
        for old_group_id in existing_group_ids:
            if old_group_id == group.id:
                continue
            session.query(Incident).filter(
                Incident.duplicate_group_id == old_group_id
            ).update(
                {Incident.duplicate_group_id: group.id},
                synchronize_session="fetch",
            )
            session.query(DuplicateCandidate).filter(
                DuplicateCandidate.group_id == old_group_id
            ).update(
                {DuplicateCandidate.group_id: group.id},
                synchronize_session="fetch",
            )
            old_group = session.get(DuplicateGroup, old_group_id)
            if old_group is not None:
                session.delete(old_group)

    incident.duplicate_group_id = group.id
    recorded: list[DuplicateCandidate] = []
    for match in matches:
        match.incident.duplicate_group_id = group.id
        if incident.duplicate_of is None:
            incident.duplicate_of = match.incident.id
        incident_id, candidate_incident_id = sorted(
            (incident.id, match.incident.id)
        )
        existing = session.scalar(
            select(DuplicateCandidate).where(
                DuplicateCandidate.incident_id == incident_id,
                DuplicateCandidate.candidate_incident_id == candidate_incident_id,
            )
        )
        if existing is not None:
            recorded.append(existing)
            continue
        pair = DuplicateCandidate(
            id=str(uuid.uuid4()),
            group_id=group.id,
            incident_id=incident_id,
            candidate_incident_id=candidate_incident_id,
            match_score=match.match_score,
            hash_distance=match.hash_distance,
            distance_m=match.distance_m,
            time_delta_seconds=match.time_delta_seconds,
            status="candidate",
        )
        session.add(pair)
        recorded.append(pair)
        record_audit(
            session,
            "duplicate_candidate_detected",
            {
                "candidate_incident_id": match.incident.id,
                "duplicate_group_id": group.id,
                "match_score": match.match_score,
                "hash_distance": match.hash_distance,
                "distance_m": match.distance_m,
                "time_delta_seconds": match.time_delta_seconds,
                "automatic_deletion": False,
            },
            incident.id,
        )
    session.flush()
    statuses = session.scalars(
        select(DuplicateCandidate.status).where(
            DuplicateCandidate.group_id == group.id
        )
    ).all()
    if "candidate" in statuses:
        group.status = "candidate"
    elif "confirmed" in statuses:
        group.status = "confirmed"
    else:
        group.status = "rejected"
    return recorded


def find_existing_incident_candidates(
    session: Session, incident: Incident
) -> list[DuplicateCandidate]:
    primary = next(
        (image for image in incident.images if image.is_primary), None
    )
    if primary is None:
        raise ValueError("Incident has no primary image for duplicate analysis")
    if primary.perceptual_hash:
        image_hash = primary.perceptual_hash
    else:
        with materialize_upload(primary.storage_path) as image_path:
            if not image_path.is_file():
                raise FileNotFoundError(
                    "Incident image is unavailable for duplicate analysis"
                )
            image_hash = _difference_hash(image_path)
    primary.perceptual_hash = image_hash
    matches = find_duplicate_candidates(
        session,
        incident_id=incident.id,
        latitude=incident.latitude,
        longitude=incident.longitude,
        created_at=incident.created_at,
        image_hash=image_hash,
    )
    pairs = record_duplicate_candidates(session, incident, matches)
    if pairs:
        from app.services.prioritization.assessment import build_incident_assessment
        from app.services.prioritization.settings import get_weights_record

        session.flush()
        weights, _, _ = get_weights_record(session)
        related_incidents = {incident.id: incident}
        for pair in pairs:
            related_incidents[pair.incident_id] = pair.incident
            related_incidents[pair.candidate_incident_id] = pair.candidate_incident
        for related_incident in related_incidents.values():
            build_incident_assessment(session, related_incident, weights=weights)
    session.commit()
    return pairs


def review_duplicate_candidate(
    session: Session,
    candidate_id: str,
    *,
    status: str,
    review_note: str | None,
    actor_user_id: str | None = None,
) -> DuplicateCandidate:
    candidate = session.get(DuplicateCandidate, candidate_id)
    if candidate is None:
        raise LookupError("Duplicate candidate not found")
    candidate.status = status
    candidate.review_note = review_note.strip() if review_note else None
    candidate.reviewed_at = datetime.now(timezone.utc)
    group = session.get(DuplicateGroup, candidate.group_id)
    if group is not None:
        statuses = session.scalars(
            select(DuplicateCandidate.status).where(
                DuplicateCandidate.group_id == group.id
            )
        ).all()
        if "candidate" in statuses:
            group.status = "candidate"
        elif "confirmed" in statuses:
            group.status = "confirmed"
        else:
            group.status = "rejected"
    record_audit(
        session,
        "duplicate_candidate_reviewed",
        {
            "duplicate_candidate_id": candidate.id,
            "status": status,
            "review_note": candidate.review_note,
            "reports_retained": True,
        },
        candidate.incident_id,
        actor_user_id=actor_user_id,
    )
    session.commit()
    return candidate


def duplicate_candidates_for_incident(
    session: Session, incident_id: str
) -> list[DuplicateCandidate]:
    return session.scalars(
        select(DuplicateCandidate)
        .where(
            or_(
                DuplicateCandidate.incident_id == incident_id,
                DuplicateCandidate.candidate_incident_id == incident_id,
            )
        )
        .order_by(DuplicateCandidate.match_score.desc())
    ).all()
