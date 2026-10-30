from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_report_to_response_workflow_and_frontend_contract(
    client: TestClient,
    anonymous_client: TestClient,
    png_image: bytes,
) -> None:
    page = anonymous_client.get("/")
    assert page.status_code == 200
    assert "Damage Mapper" in page.text
    assert 'id="reportForm"' in page.text
    assert 'data-action="apply-analytics"' in page.text
    assert "/api/analytics/export.csv" in page.text

    citizen_auth = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "e2e-citizen@example.test",
            "display_name": "E2E Citizen",
            "password": "citizen-e2e-password-123",
        },
    )
    assert citizen_auth.status_code == 201, citizen_auth.text
    citizen_token = citizen_auth.json()["access_token"]
    citizen_headers = _headers(citizen_token)

    report_response = anonymous_client.post(
        "/api/incidents",
        headers=citizen_headers,
        data={
            "description": "Flood water around the community clinic",
            "latitude": "12.9716",
            "longitude": "77.5946",
            "urgency": "75",
            "people_affected": "24",
            "access_blocked": "true",
        },
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    assert report_response.status_code == 201, report_response.text
    report = report_response.json()
    report_id = report["id"]
    assert report["ai_analysis"]["status"] == "unavailable"
    assert report["ai_analysis"]["predicted_category"] is None
    assert report["ai_analysis"]["confidence"] is None
    assert report["verification_status"] == "Pending"
    assert report["severity"] in {"Critical", "High", "Moderate", "Low"}
    assert report["decision_assessment"]["preliminary"] is True
    assert 0 <= report["priority_score"] <= 100

    stored_image = anonymous_client.get(
        report["image_url"], headers=citizen_headers
    )
    assert stored_image.status_code == 200
    assert stored_image.headers["cache-control"] == "private, no-store"
    assert anonymous_client.get("/api/analytics", headers=citizen_headers).status_code == 403
    assert anonymous_client.get("/api/analytics/export.csv", headers=citizen_headers).status_code == 403
    assert anonymous_client.get(
        f"/api/incidents/{report_id}/image"
    ).status_code == 401

    duplicate_response = anonymous_client.post(
        "/api/incidents",
        headers=citizen_headers,
        data={
            "description": "Another report of the same flooding",
            "latitude": "12.9720",
            "longitude": "77.5946",
            "urgency": "60",
            "people_affected": "5",
            "access_blocked": "false",
        },
        files={"image": ("same-scene.png", png_image, "image/png")},
    )
    assert duplicate_response.status_code == 201, duplicate_response.text
    duplicate = duplicate_response.json()
    assert duplicate["decision_assessment"]["duplicate_candidate_count"] >= 1
    assert client.get("/api/duplicates").json()["total"] >= 1
    assert client.get(f"/api/incidents/{report_id}").status_code == 200
    assert client.get(f"/api/incidents/{duplicate['id']}").status_code == 200

    account_response = client.post(
        "/api/auth/users",
        json={
            "email": "e2e-responder@example.test",
            "display_name": "E2E Responder",
            "password": "responder-e2e-password-123",
            "role": "responder",
        },
    )
    assert account_response.status_code == 201, account_response.text
    responder_auth = anonymous_client.post(
        "/api/auth/login",
        json={
            "email": "e2e-responder@example.test",
            "password": "responder-e2e-password-123",
        },
    )
    assert responder_auth.status_code == 200, responder_auth.text
    responder_headers = _headers(responder_auth.json()["access_token"])

    assignment = client.patch(
        f"/api/incidents/{report_id}",
        json={
            "verification_status": "Verified",
            "assigned_to": "e2e-responder@example.test",
            "incident_status": "Assigned",
        },
    )
    assert assignment.status_code == 200, assignment.text
    assert assignment.json()["assigned_to"] == "E2E Responder"
    assert assignment.json()["verification_status"] == "Verified"

    response_update = anonymous_client.patch(
        f"/api/incidents/{report_id}",
        headers=responder_headers,
        json={"incident_status": "In progress"},
    )
    assert response_update.status_code == 200, response_update.text
    assert response_update.json()["incident_status"] == "In progress"
    resolved = anonymous_client.patch(
        f"/api/incidents/{report_id}",
        headers=responder_headers,
        json={"incident_status": "Resolved"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["incident_status"] == "Resolved"

    date_filter = datetime.now(timezone.utc).date().isoformat()
    query = {
        "date_from": date_filter,
        "date_to": date_filter,
        "min_latitude": "12",
        "max_latitude": "13",
        "min_longitude": "77",
        "max_longitude": "78",
    }
    analytics = client.get("/api/analytics", params=query)
    assert analytics.status_code == 200, analytics.text
    analytics_data = analytics.json()
    assert analytics_data["total"] == 2
    assert analytics_data["verified"] == 1
    assert analytics_data["pending"] == 1
    assert analytics_data["resolved"] == 1
    assert analytics_data["active"] == 1
    assert analytics_data["average_verification_seconds"] is not None
    assert sum(point["count"] for point in analytics_data["trends"]) == 2
    assert sum(
        point["count"]
        for point in analytics_data["geographic_concentrations"]
    ) == 2
    exported = client.get("/api/analytics/export.csv", params=query)
    assert exported.status_code == 200
    assert report_id in exported.text
    assert duplicate["id"] in exported.text

    citizen_reports = anonymous_client.get(
        "/api/incidents", headers=citizen_headers
    )
    assert citizen_reports.status_code == 200
    assert {item["id"] for item in citizen_reports.json()["items"]} == {
        report_id,
        duplicate["id"],
    }
