from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.dependencies import get_db
from app.core.config import get_settings
from app.models import AuditLog, AuthSession, User
from app.services.auth import hash_token
from tests.test_incidents_api import create_report


def token_headers(auth_response: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_response['access_token']}"}


def test_registration_password_hash_session_and_logout(
    anonymous_client: TestClient,
) -> None:
    response = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": " Reporter@Example.Test ",
            "display_name": "Reporter",
            "password": "correct-horse-battery-42",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["user"]["role"] == "citizen"
    assert payload["user"]["email"] == "reporter@example.test"
    assert payload["access_token"]

    generator = anonymous_client.app.dependency_overrides[get_db]()
    session = next(generator)
    user = session.scalar(select(User).where(User.email == "reporter@example.test"))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert user.password_hash != "correct-horse-battery-42"
    assert session.get(AuthSession, hash_token(payload["access_token"])) is not None
    session.close()
    generator.close()

    current = anonymous_client.get("/api/auth/me", headers=token_headers(payload))
    assert current.status_code == 200
    assert current.json()["id"] == payload["user"]["id"]
    logged_out = anonymous_client.post(
        "/api/auth/logout", headers=token_headers(payload)
    )
    assert logged_out.status_code == 204
    assert anonymous_client.get(
        "/api/auth/me", headers=token_headers(payload)
    ).status_code == 401


def test_login_rate_limit_and_public_role_escalation(
    anonymous_client: TestClient, monkeypatch
) -> None:
    escalation = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "elevated@example.test",
            "display_name": "Untrusted Admin",
            "password": "community-password-123",
            "role": "admin",
        },
    )
    assert escalation.status_code == 422
    response = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "citizen@example.test",
            "display_name": "Reporter",
            "password": "community-password-123",
        },
    )
    assert response.status_code == 201
    monkeypatch.setattr(get_settings(), "auth_login_limit", 1)

    credentials = {
        "email": "citizen@example.test",
        "password": "incorrect-password-123",
    }
    assert anonymous_client.post("/api/auth/login", json=credentials).status_code == 401
    limited = anonymous_client.post("/api/auth/login", json=credentials)
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 900

    invalid_password = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "weak@example.test",
            "display_name": "Weak",
            "password": "short",
        },
    )
    assert invalid_password.status_code == 422


def test_citizens_cannot_access_operational_analytics_or_exports(
    anonymous_client: TestClient,
) -> None:
    registration = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "analytics-citizen@example.test",
            "display_name": "Citizen",
            "password": "community-password-123",
        },
    )
    headers = token_headers(registration.json())
    assert registration.status_code == 201
    assert anonymous_client.get("/api/analytics", headers=headers).status_code == 403
    assert anonymous_client.get(
        "/api/analytics/export.csv", headers=headers
    ).status_code == 403


def test_expired_session_is_rejected(
    anonymous_client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "auth_token_ttl_minutes", 0)
    auth = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "expired@example.test",
            "display_name": "Expired Session",
            "password": "expiration-password-123",
        },
    ).json()
    assert anonymous_client.get(
        "/api/auth/me", headers=token_headers(auth)
    ).status_code == 401


def test_bootstrap_admin_is_secret_gated_and_one_time(
    client: TestClient,
    anonymous_client: TestClient,
    monkeypatch,
) -> None:
    generator = client.app.dependency_overrides[get_db]()
    session = next(generator)
    session.execute(update(User).where(User.role == "admin").values(role="responder"))
    session.commit()
    session.close()
    generator.close()

    monkeypatch.setattr(get_settings(), "auth_bootstrap_secret", "s" * 40)
    payload = {
        "email": "first-admin@example.test",
        "display_name": "Emergency Administrator",
        "password": "admin-password-secure-123",
    }
    denied = anonymous_client.post("/api/auth/bootstrap-admin", json=payload)
    assert denied.status_code == 503
    invalid_secret = anonymous_client.post(
        "/api/auth/bootstrap-admin",
        json=payload,
        headers={"X-Admin-Bootstrap-Secret": "wrong-secret"},
    )
    assert invalid_secret.status_code == 403
    bootstrapped = anonymous_client.post(
        "/api/auth/bootstrap-admin",
        json=payload,
        headers={"X-Admin-Bootstrap-Secret": "s" * 40},
    )
    assert bootstrapped.status_code == 201, bootstrapped.text
    assert bootstrapped.json()["user"]["role"] == "admin"
    duplicate = anonymous_client.post(
        "/api/auth/bootstrap-admin",
        json={**payload, "email": "second-admin@example.test"},
        headers={"X-Admin-Bootstrap-Secret": "s" * 40},
    )
    assert duplicate.status_code == 409


def test_citizen_can_only_read_own_reports_and_cannot_operate(
    client: TestClient,
    anonymous_client: TestClient,
    png_image: bytes,
) -> None:
    another_users_report = create_report(client, png_image)
    citizen = anonymous_client.post(
        "/api/auth/register",
        json={
            "email": "owner@example.test",
            "display_name": "Report Owner",
            "password": "owner-password-secure-123",
        },
    ).json()
    headers = token_headers(citizen)
    own_report = anonymous_client.post(
        "/api/incidents",
        headers=headers,
        data={
            "description": "My street is flooded",
            "latitude": "12.97",
            "longitude": "77.59",
        },
        files={"image": ("report.png", png_image, "image/png")},
    )
    assert own_report.status_code == 201, own_report.text
    assert own_report.json()["reporter"] == "Report Owner"
    assert anonymous_client.get(
        "/api/incidents", headers=headers
    ).json()["total"] == 1
    assert anonymous_client.get(
        f"/api/incidents/{another_users_report['id']}", headers=headers
    ).status_code == 404
    assert anonymous_client.get(
        f"/api/incidents/{another_users_report['id']}/image",
        headers=headers,
    ).status_code == 404
    assert anonymous_client.get("/api/stats", headers=headers).status_code == 403
    assert anonymous_client.patch(
        f"/api/incidents/{own_report.json()['id']}",
        headers=headers,
        json={"verification_status": "Verified"},
    ).status_code == 403
    assert anonymous_client.delete(
        f"/api/incidents/{own_report.json()['id']}", headers=headers
    ).status_code == 403


def test_responder_and_admin_workflow_permissions_and_audit(
    client: TestClient,
    anonymous_client: TestClient,
    png_image: bytes,
) -> None:
    provisioned = client.post(
        "/api/auth/users",
        json={
            "email": "field@example.test",
            "display_name": "Field Responder",
            "password": "field-password-secure-123",
            "role": "responder",
        },
    )
    assert provisioned.status_code == 201
    report = create_report(client, png_image)
    assignment = client.patch(
        f"/api/incidents/{report['id']}",
        json={"assigned_to": "field@example.test"},
    )
    assert assignment.status_code == 200, assignment.text
    credentials = anonymous_client.post(
        "/api/auth/login",
        json={
            "email": "field@example.test",
            "password": "field-password-secure-123",
        },
    ).json()
    headers = token_headers(credentials)
    changed = anonymous_client.patch(
        f"/api/incidents/{report['id']}",
        headers=headers,
        json={"incident_status": "In progress"},
    )
    assert changed.status_code == 200
    assert changed.json()["incident_status"] == "In progress"
    assert anonymous_client.patch(
        f"/api/incidents/{report['id']}",
        headers=headers,
        json={"verification_status": "Verified"},
    ).status_code == 403
    assert anonymous_client.put(
        "/api/prioritization/weights",
        headers=headers,
        json={"severity": 0.4, "urgency": 0.25, "exposure": 0.2, "accessibility": 0.15},
    ).status_code == 403

    verified = client.patch(
        f"/api/incidents/{report['id']}",
        json={"verification_status": "Verified"},
    )
    assert verified.status_code == 200
    generator = client.app.dependency_overrides[get_db]()
    session = next(generator)
    audit = session.scalar(
        select(AuditLog).where(
            AuditLog.incident_id == report["id"],
            AuditLog.action == "incident_updated",
        )
    )
    assert audit is not None
    assert audit.actor_user_id is not None
    session.close()
    generator.close()

    disabled = client.patch(
        f"/api/auth/users/{provisioned.json()['id']}",
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    assert anonymous_client.get(
        "/api/auth/me", headers=headers
    ).status_code == 401
    self_demotion = client.patch(
        f"/api/auth/users/{client.get('/api/auth/me').json()['id']}",
        json={"role": "citizen"},
    )
    assert self_demotion.status_code == 409
    self_deactivation = client.patch(
        f"/api/auth/users/{client.get('/api/auth/me').json()['id']}",
        json={"is_active": False},
    )
    assert self_deactivation.status_code == 409


def test_sensitive_endpoints_require_bearer_authentication(
    anonymous_client: TestClient,
    png_image: bytes,
) -> None:
    assert anonymous_client.get("/api/incidents").status_code == 401
    assert anonymous_client.get("/api/incidents/map").status_code == 401
    assert anonymous_client.get("/api/duplicates").status_code == 401
    assert anonymous_client.get("/api/responders/locations").status_code == 401
    assert anonymous_client.get("/uploads/untrusted.png").status_code == 401
    assert anonymous_client.post(
        "/api/ai/analyze",
        files={"image": ("evidence.png", png_image, "image/png")},
    ).status_code == 401


def test_ai_uploads_are_rate_limited_per_account(
    client: TestClient,
    png_image: bytes,
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "auth_upload_limit", 1)
    first = client.post(
        "/api/ai/analyze",
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    assert first.status_code == 200, first.text
    limited = client.post(
        "/api/ai/analyze",
        files={"image": ("evidence.png", png_image, "image/png")},
    )
    assert limited.status_code == 429
