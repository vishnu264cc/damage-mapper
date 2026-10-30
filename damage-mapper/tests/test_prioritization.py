from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.models import Incident, IncidentImage
from app.schemas.prioritization import PriorityWeights
from app.services.prioritization.duplicates import find_duplicate_candidates
from app.services.prioritization.priority import (
    DEFAULT_PRIORITY_WEIGHTS,
    calculate_priority,
    population_exposure,
)
from app.services.prioritization.severity import estimate_severity
from tests.test_incidents_api import create_report


def test_severity_records_reported_evidence_and_uncertainty() -> None:
    assessment = estimate_severity(
        urgency=50,
        people_affected=10,
        access_blocked=False,
    )

    assert assessment.preliminary is True
    assert assessment.severity == "Low"
    assert assessment.score == 31.8
    assert any("urgency" in item.lower() for item in assessment.evidence)
    assert any("self-reported" in item for item in assessment.uncertainty)
    assert any("No usable visual" in item for item in assessment.uncertainty)


def test_classifier_confidence_is_not_used_as_severity_or_priority() -> None:
    no_visual = estimate_severity(
        urgency=75,
        people_affected=24,
        access_blocked=True,
    )
    low_confidence_visual = estimate_severity(
        urgency=75,
        people_affected=24,
        access_blocked=True,
        visual_detections=[{"label": "fire", "confidence": 0.2}],
    )
    assert low_confidence_visual.score == no_visual.score

    priority = calculate_priority(
        severity_score=95,
        urgency=100,
        people_affected=5000,
        access_blocked=True,
        severity="Critical",
        model_review_required=False,
        weights=PriorityWeights(**DEFAULT_PRIORITY_WEIGHTS),
    )
    assert priority.score == 98.0
    assert priority.high_risk is True
    assert priority.high_risk_review_required is True
    assert priority.review_required is True
    assert sum(factor.contribution for factor in priority.factors) == priority.score
    assert priority.factors[2].value == population_exposure(5000) == 100


def test_priority_weights_require_a_unit_sum() -> None:
    weights = PriorityWeights(**DEFAULT_PRIORITY_WEIGHTS)
    assert sum(weights.model_dump().values()) == 1

    try:
        PriorityWeights(
            severity=0.5,
            urgency=0.3,
            exposure=0.2,
            accessibility=0.2,
        )
    except ValueError as error:
        assert "sum to 1" in str(error)
    else:
        raise AssertionError("Invalid weights must be rejected")


def test_duplicate_candidates_are_grouped_and_never_delete_reports(
    client: TestClient, png_image: bytes
) -> None:
    first = create_report(client, png_image)
    second = create_report(
        client,
        png_image,
        latitude="12.9720",
        longitude="77.5946",
    )

    assert second["decision_assessment"]["duplicate_candidate_count"] == 1
    first_after_match = client.get(f"/api/incidents/{first['id']}").json()
    assert first_after_match["decision_assessment"]["duplicate_candidate_count"] == 1
    assert (
        first_after_match["decision_assessment"]["duplicate_group_id"]
        == second["decision_assessment"]["duplicate_group_id"]
    )
    map_points = client.get("/api/incidents/map").json()["items"]
    assert all(
        point["possible_duplicate"] is True
        for point in map_points
        if point["id"] in {first["id"], second["id"]}
    )

    analyzed = client.post(
        f"/api/incidents/{first['id']}/duplicates/analyze"
    )
    assert analyzed.status_code == 200
    assert analyzed.json()["total"] == 1
    assert client.get("/api/duplicates").json()["total"] == 1

    candidates = client.get("/api/duplicates", params={"status": "candidate"})
    assert candidates.status_code == 200
    assert candidates.json()["total"] == 1
    candidate_id = candidates.json()["items"][0]["id"]

    reviewed = client.patch(
        f"/api/duplicates/{candidate_id}",
        json={"status": "confirmed", "review_note": "Same scene"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "confirmed"
    rejected = client.patch(
        f"/api/duplicates/{candidate_id}",
        json={"status": "rejected", "review_note": "Separate reports"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    map_points = client.get("/api/incidents/map").json()["items"]
    assert all(
        point["possible_duplicate"] is False
        for point in map_points
        if point["id"] in {first["id"], second["id"]}
    )
    assert client.get(f"/api/incidents/{first['id']}").status_code == 200
    assert client.get(f"/api/incidents/{second['id']}").status_code == 200


def test_duplicate_matching_requires_hash_location_and_time_proximity(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    settings = get_settings()
    monkeypatch.setattr(settings, "duplicate_radius_m", 150.0)
    monkeypatch.setattr(settings, "duplicate_window_hours", 48.0)
    monkeypatch.setattr(settings, "duplicate_hash_distance", 10)
    reference = datetime.now(timezone.utc)

    def add_incident(
        session: Session,
        incident_id: str,
        latitude: float,
        longitude: float,
        created_at: datetime,
        perceptual_hash: str,
    ) -> None:
        incident = Incident(
            id=incident_id,
            description="Reported scene",
            latitude=latitude,
            longitude=longitude,
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
            created_at=created_at,
        )
        incident.images.append(
            IncidentImage(
                storage_path=f"{incident_id}.png",
                content_type="image/png",
                file_size=10,
                perceptual_hash=perceptual_hash,
                is_primary=True,
            )
        )
        session.add(incident)

    try:
        with Session(engine) as session:
            add_incident(session, "near", 12.0005, 77.0, reference, "0000000000000000")
            add_incident(session, "far", 13.0, 77.0, reference, "0000000000000000")
            add_incident(
                session,
                "old",
                12.0,
                77.0,
                reference - timedelta(hours=72),
                "0000000000000000",
            )
            add_incident(session, "different-hash", 12.0, 77.0, reference, "ffffffffffffffff")
            session.commit()

            matches = find_duplicate_candidates(
                session,
                incident_id=None,
                latitude=12.0,
                longitude=77.0,
                created_at=reference,
                image_hash="0000000000000000",
            )

        assert [match.incident.id for match in matches] == ["near"]
    finally:
        engine.dispose()


def test_priority_weight_update_is_role_gated_and_recalculates_incidents(
    client: TestClient, png_image: bytes
) -> None:
    incident = create_report(client, png_image)
    assessment = client.get(f"/api/incidents/{incident['id']}/assessment")
    assert assessment.status_code == 200
    assert assessment.json()["preliminary"] is True
    assert assessment.json()["priority"]["label"] == (
        "Preliminary decision-support assessment"
    )
    old_score = incident["priority_score"]
    payload = {
        "severity": 0.5,
        "urgency": 0.2,
        "exposure": 0.2,
        "accessibility": 0.1,
    }

    updated = client.put("/api/prioritization/weights", json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["weights"] == payload

    refreshed = client.get(f"/api/incidents/{incident['id']}").json()
    assert refreshed["priority_score"] != old_score
    assert refreshed["decision_assessment"]["priority"]["weights"] == payload
