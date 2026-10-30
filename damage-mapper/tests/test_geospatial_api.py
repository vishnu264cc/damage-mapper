from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Incident
from app.services.incidents import get_map_points


def test_map_query_excludes_and_counts_invalid_legacy_coordinates() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
    try:
        with Session(engine) as session:
            session.add(
                Incident(
                    id="invalid-coordinate",
                    description="Legacy record with invalid GPS",
                    latitude=95,
                    longitude=181,
                    damage_type="Needs review",
                    model_confidence=0,
                    severity="Low",
                    severity_score=0,
                    urgency=0,
                    people_affected=0,
                    access_blocked=False,
                    exposure_score=0,
                    priority_score=0,
                    verification_status="Pending",
                    incident_status="Reported",
                )
            )
            session.commit()

            result = get_map_points(session, limit=10)

        assert result.items == []
        assert result.total == result.returned == 0
        assert result.invalid_coordinates == 1
    finally:
        engine.dispose()


def test_responder_location_api_requires_administrator(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.get("/api/responders/locations")
    assert response.status_code == 401


def test_responder_locations_require_roles_and_return_persisted_gps(
    client: TestClient,
) -> None:
    invalid = client.put(
        "/api/responders/locations",
        json={
            "responder_name": "Team Alpha",
            "latitude": 91,
            "longitude": 0,
        },
    )
    assert invalid.status_code == 422

    saved = client.put(
        "/api/responders/locations",
        json={
            "responder_name": "  Team Alpha  ",
            "latitude": 12.9716,
            "longitude": 77.5946,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["responder_name"] == "Team Alpha"

    listed = client.get("/api/responders/locations")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["latitude"] == 12.9716

    updated = client.put(
        "/api/responders/locations",
        json={
            "responder_name": "Team Alpha",
            "latitude": 13,
            "longitude": 78,
        },
    )
    assert updated.status_code == 200
    assert client.get("/api/responders/locations").json()["total"] == 1
