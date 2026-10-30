from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def create_report(client: TestClient, png_image: bytes, **data) -> dict:
    payload = {
        "description": "Flood water around the community clinic",
        "latitude": "12.9716",
        "longitude": "77.5946",
        "urgency": "75",
        "people_affected": "24",
        "access_blocked": "true",
        "reporter": "Field team",
    }
    payload.update(data)
    response = client.post(
        "/api/incidents",
        data=payload,
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health_and_openapi_have_typed_responses(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    render_health = client.get("/healthz")
    assert render_health.status_code == 200
    assert render_health.json()["database"] == "ok"

    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/incidents"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/IncidentPage")
    assert schema["paths"]["/api/incidents"]["post"]["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/IncidentRead")
    assert schema["paths"]["/api/incidents/map"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/IncidentMapResponse")
    assert "/api/responders/locations" in schema["paths"]
    assert "/api/ai/analyze" in schema["paths"]
    assert "/api/incidents/{incident_id}" in schema["paths"]
    assert "/api/analytics" in schema["paths"]
    assert "/api/analytics/export.csv" in schema["paths"]
    analytics_parameters = {
        parameter["name"]
        for parameter in schema["paths"]["/api/analytics"]["get"]["parameters"]
    }
    assert {"date_from", "date_to", "min_latitude", "max_latitude"} <= analytics_parameters
    assert "text/csv" in schema["paths"]["/api/analytics/export.csv"]["get"]["responses"]["200"]["content"]


def test_render_health_check_reports_database_outage_as_unhealthy(
    client: TestClient, monkeypatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.api.dependencies import get_db
    from app.main import app

    class UnavailableSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, OSError("database unavailable"))

    monkeypatch.setitem(
        app.dependency_overrides, get_db, lambda: UnavailableSession()
    )

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_incident_create_list_search_filter_and_pagination(
    client: TestClient, png_image: bytes
) -> None:
    first = create_report(client, png_image)
    second = create_report(
        client,
        png_image,
        description="Road obstruction near the bridge",
        latitude="40.7128",
        longitude="-74.0060",
        urgency="30",
        access_blocked="false",
    )

    assert first["image_url"] == f"/api/incidents/{first['id']}/image"
    assert first["description"] == "Flood water around the community clinic"
    assert first["verification_status"] == "Pending"
    assert first["incident_status"] == "Reported"
    assert first["model_name"] == "Demo fallback (model disabled)"
    assert 0 <= first["model_confidence"] <= 1
    assert first["ai_analysis"]["status"] == "unavailable"
    assert first["ai_analysis"]["predicted_category"] is None
    assert first["ai_analysis"]["confidence"] is None
    from app.services.uploads import UPLOADS

    private_image = client.get(first["image_url"])
    assert private_image.status_code == 200
    assert private_image.headers["cache-control"] == "private, no-store"
    legacy_storage_name = next(UPLOADS.iterdir()).name
    assert client.get(f"/uploads/{legacy_storage_name}").status_code == 200

    page = client.get("/api/incidents", params={"page": 1, "page_size": 1}).json()
    assert page["total"] == 2
    assert page["page"] == 1
    assert page["pages"] == 2
    assert len(page["items"]) == 1
    assert client.get(
        "/api/incidents", params={"q": "community clinic"}
    ).json()["items"][0]["id"] == first["id"]
    assert client.get(
        "/api/incidents", params={"status": "Pending"}
    ).json()["total"] == 2
    assert client.get(
        "/api/incidents", params={"severity": first["severity"]}
    ).json()["total"] == 1
    assert client.get(
        f"/api/incidents/{second['id']}"
    ).json()["id"] == second["id"]
    assert client.get("/api/incidents/map").json()["returned"] == 2


def test_incident_update_audit_history_and_delete(
    client: TestClient, png_image: bytes
) -> None:
    incident = create_report(client, png_image)
    incident_id = incident["id"]
    responder = client.post(
        "/api/auth/users",
        json={
            "email": "rescue@example.test",
            "display_name": "Rescue team A",
            "password": "responder-password-123",
            "role": "responder",
        },
    )
    assert responder.status_code == 201, responder.text

    updated = client.patch(
        f"/api/incidents/{incident_id}",
        json={
            "verification_status": "Verified",
            "assigned_to": "rescue@example.test",
            "incident_status": "In progress",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["verification_status"] == "Verified"
    assert updated.json()["assigned_to"] == "Rescue team A"
    assert updated.json()["incident_status"] == "In progress"

    history = client.get(f"/api/incidents/{incident_id}/history")
    assert history.status_code == 200
    assert [entry["status"] for entry in history.json()["verification_history"]] == [
        "Pending",
        "Verified",
    ]
    assert {entry["action"] for entry in history.json()["audit_log"]} >= {
        "incident_created",
        "incident_updated",
    }

    from app.services.uploads import UPLOADS

    image_path = next(UPLOADS.iterdir())
    assert client.get(incident["image_url"]).status_code == 200
    delete_response = client.delete(f"/api/incidents/{incident_id}")
    assert delete_response.status_code == 204
    assert client.get(f"/api/incidents/{incident_id}").status_code == 404
    assert client.get("/api/stats").json()["total"] == 0

    assert not image_path.exists()


def test_invalid_upload_coordinates_and_update_fields(
    client: TestClient, png_image: bytes
) -> None:
    invalid_type = client.post(
        "/api/incidents",
        data={"description": "Invalid type", "latitude": "0", "longitude": "0"},
        files={"image": ("evidence.txt", b"not an image", "text/plain")},
    )
    assert invalid_type.status_code == 400

    mismatched_type = client.post(
        "/api/incidents",
        data={"description": "Mismatched type", "latitude": "0", "longitude": "0"},
        files={"image": ("evidence.jpg", png_image, "image/jpeg")},
    )
    assert mismatched_type.status_code == 400

    invalid_coordinates = client.post(
        "/api/incidents",
        data={"description": "Bad location", "latitude": "91", "longitude": "0"},
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    assert invalid_coordinates.status_code == 422

    incident = create_report(client, png_image)
    invalid_update = client.patch(
        f"/api/incidents/{incident['id']}",
        json={"incident_status": "Teleporting"},
    )
    assert invalid_update.status_code == 422
    unknown_field = client.patch(
        f"/api/incidents/{incident['id']}",
        json={"assigned_to": "Team", "is_admin": True},
    )
    assert unknown_field.status_code == 422
    assert client.delete("/api/incidents/missing").status_code == 404


def test_upload_size_limit_is_enforced(client: TestClient, png_image: bytes, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 32)
    response = client.post(
        "/api/incidents",
        data={"description": "Too large", "latitude": "0", "longitude": "0"},
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    assert response.status_code == 400
    assert "10 MB" in response.json()["detail"]


def test_analytics_and_map_filters_use_database_records(
    client: TestClient, png_image: bytes
) -> None:
    incident = create_report(client, png_image)
    analytics = client.get("/api/analytics")
    assert analytics.status_code == 200
    assert analytics.json()["total"] == 1
    assert analytics.json()["verification_counts"] == {"Pending": 1}
    assert analytics.json()["needs_review"] == 1
    assert analytics.json()["pending"] == 1
    assert analytics.json()["trends"][0]["count"] == 1
    assert analytics.json()["geographic_concentrations"][0]["count"] == 1
    data = analytics.json()
    assert data["critical_high"] == sum(
        data["severity_counts"].get(label, 0) for label in ("Critical", "High")
    )
    assert data["high_priority"] == int(
        incident["priority_score"] >= data["high_priority_threshold"]
    )
    severity = client.get("/api/incidents").json()["items"][0]["severity"]
    assert client.get(
        "/api/incidents/map", params={"severity": severity}
    ).json()["returned"] == 1


def test_analytics_filters_and_csv_export_share_database_records(
    client: TestClient, png_image: bytes
) -> None:
    first = create_report(
        client,
        png_image,
        description='=HYPERLINK("https://example.test","Report")',
    )
    second = create_report(
        client,
        png_image,
        description="Flood damage in another region",
        latitude="40.7128",
        longitude="-74.0060",
    )
    updated = client.patch(
        f"/api/incidents/{first['id']}",
        json={"verification_status": "Verified", "incident_status": "Resolved"},
    )
    assert updated.status_code == 200, updated.text

    params = {
        "date_from": first["created_at"][:10],
        "date_to": first["created_at"][:10],
        "min_latitude": "12",
        "max_latitude": "13",
        "min_longitude": "77",
        "max_longitude": "78",
        "verification_status": "Verified",
        "incident_status": "Resolved",
    }
    analytics = client.get("/api/analytics", params=params)
    assert analytics.status_code == 200, analytics.text
    data = analytics.json()
    assert data["total"] == 1
    assert data["verified"] == 1
    assert data["pending"] == 0
    assert data["resolved"] == 1
    assert data["active"] == 0
    assert data["verification_time_count"] == 1
    assert data["average_verification_seconds"] is not None
    assert sum(data["category_counts"].values()) == 1
    assert sum(point["count"] for point in data["trends"]) == 1
    assert data["geographic_concentrations"] == [
        {"latitude": 12.97, "longitude": 77.59, "count": 1}
    ]

    exported = client.get("/api/analytics/export.csv", params=params)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    assert first["id"] in exported.text
    assert second["id"] not in exported.text
    exported_rows = csv.DictReader(io.StringIO(exported.text))
    exported_row = next(exported_rows)
    assert exported_row["description"].startswith("'=")

    invalid_bounds = client.get(
        "/api/analytics", params={"min_latitude": "12"}
    )
    assert invalid_bounds.status_code == 422
    invalid_dates = client.get(
        "/api/analytics",
        params={
            "date_from": datetime.now(timezone.utc).date().isoformat(),
            "date_to": "2000-01-01",
        },
    )
    assert invalid_dates.status_code == 422


def test_map_api_filters_live_points_by_category_severity_and_radius(
    client: TestClient, png_image: bytes
) -> None:
    nearby = create_report(client, png_image)
    create_report(
        client,
        png_image,
        description="Remote road obstruction",
        latitude="40.7128",
        longitude="-74.0060",
    )

    filtered = client.get(
        "/api/incidents/map",
        params={
            "damage_type": nearby["damage_type"],
            "severity": nearby["severity"],
            "latitude": nearby["latitude"],
            "longitude": nearby["longitude"],
            "radius_m": 1000,
        },
    )
    assert filtered.status_code == 200, filtered.text
    result = filtered.json()
    assert result["total"] == result["returned"] == 1
    assert result["items"][0]["id"] == nearby["id"]
    point = result["items"][0]
    assert point["reported_at"]
    assert point["verification_status"] == "Pending"
    assert point["priority_score"] == nearby["priority_score"]
    assert result["has_more"] is False

    first_page = client.get("/api/incidents/map", params={"limit": 1})
    assert first_page.status_code == 200
    assert first_page.json()["returned"] == 1
    assert first_page.json()["has_more"] is True
    next_page = client.get(
        "/api/incidents/map", params={"limit": 1, "offset": 1}
    )
    assert next_page.status_code == 200
    assert next_page.json()["returned"] == 1
    assert next_page.json()["items"][0]["id"] != first_page.json()["items"][0]["id"]

    incomplete_radius = client.get(
        "/api/incidents/map", params={"latitude": 12.97, "radius_m": 1000}
    )
    assert incomplete_radius.status_code == 422
    invalid_radius = client.get(
        "/api/incidents/map",
        params={"latitude": 0, "longitude": 0, "radius_m": 500001},
    )
    assert invalid_radius.status_code == 422


def test_demo_seed_is_idempotent_and_records_are_synthetic(client: TestClient) -> None:
    first = client.post("/api/demo-seed")
    second = client.post("/api/demo-seed")
    assert first.status_code == 200
    assert len(first.json()["created"]) == 3
    assert second.json()["created"] == []
    assert client.get("/api/incidents").json()["total"] == 3
    incidents = client.get("/api/incidents").json()["items"]
    assert all(item["ai_analysis"]["status"] == "synthetic" for item in incidents)
    assert all(item["decision_assessment"]["preliminary"] for item in incidents)


def test_demo_seed_can_be_disabled(client: TestClient, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "demo_enabled", False)
    assert client.post("/api/demo-seed").status_code == 404
    assert client.get("/api/demo-seed").status_code == 404
    assert client.get("/api/incidents").json()["total"] == 0
