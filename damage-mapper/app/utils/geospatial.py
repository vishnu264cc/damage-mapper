from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Incident


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000
    latitude_1, latitude_2 = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(latitude_1)
        * math.cos(latitude_2)
        * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(math.sqrt(value))


def nearby_incidents(
    session: Session,
    latitude: float,
    longitude: float,
    radius_m: float = 150,
    max_age_hours: float = 48,
    reference_time: datetime | None = None,
) -> list[Incident]:
    window = timedelta(hours=max_age_hours)
    window_end = reference_time + window if reference_time else datetime.now(timezone.utc)
    window_start = window_end - window if reference_time is None else reference_time - window
    if session.get_bind().dialect.name == "postgresql":
        ids = session.execute(
            text(
                """
                SELECT id
                FROM incidents
                WHERE created_at >= :window_start
                  AND created_at <= :window_end
                  AND ST_DWithin(
                    ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography,
                    ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                    :radius_m
                  )
                ORDER BY created_at DESC
                """
            ),
            {
                "latitude": latitude,
                "longitude": longitude,
                "radius_m": radius_m,
                "window_start": window_start,
                "window_end": window_end,
            },
        ).scalars().all()
        if not ids:
            return []
        incidents = session.scalars(select(Incident).where(Incident.id.in_(ids))).all()
        by_id = {incident.id: incident for incident in incidents}
        return [by_id[incident_id] for incident_id in ids if incident_id in by_id]

    candidates = session.scalars(
        select(Incident).where(
            Incident.created_at >= window_start,
            Incident.created_at <= window_end,
        )
    ).all()
    return [
        incident
        for incident in candidates
        if haversine_m(latitude, longitude, incident.latitude, incident.longitude) <= radius_m
    ]
