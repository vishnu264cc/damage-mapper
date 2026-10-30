from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("DATABASE_URL", " ", "DATABASE_URL"),
        ("DM_MAX_UPLOAD_BYTES", "0", "Upload byte and pixel limits"),
        ("DM_MAX_IMAGE_PIXELS", "-1", "Upload byte and pixel limits"),
        ("DM_AI_REVIEW_THRESHOLD", "nan", "DM_AI_REVIEW_THRESHOLD"),
        ("DM_HIGH_RISK_PRIORITY_THRESHOLD", "nan", "DM_HIGH_RISK_PRIORITY_THRESHOLD"),
        ("DM_DUPLICATE_RADIUS_M", "inf", "Duplicate search radius"),
        ("DM_DUPLICATE_WINDOW_HOURS", "nan", "Duplicate search radius"),
    ],
)
def test_invalid_runtime_configuration_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        Settings()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("DM_STORAGE_BACKEND", "disk", "DM_STORAGE_BACKEND"),
        ("DM_STORAGE_BACKEND", "supabase", "SUPABASE_URL"),
        ("DM_CORS_ORIGINS", "https://example.com/app", "DM_CORS_ORIGINS"),
        ("DM_TRUSTED_HOSTS", "*", "DM_TRUSTED_HOSTS"),
    ],
)
def test_invalid_storage_and_origin_settings_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)
    if name == "DM_STORAGE_BACKEND" and value == "supabase":
        monkeypatch.setenv("SUPABASE_URL", "")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    with pytest.raises(ValueError, match=message):
        Settings()


def test_production_settings_require_postgres_private_storage_and_host_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DM_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:pass@db.example.test/app"
    )
    monkeypatch.setenv("DM_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "server-only-secret")
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "incident-evidence")
    monkeypatch.setenv("DM_ENABLE_DEMO", "0")
    monkeypatch.setenv("DM_TRUSTED_HOSTS", "damage-mapper-api.onrender.com")

    settings = Settings()

    assert settings.environment == "production"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.storage_backend == "supabase"
    assert settings.trusted_hosts == ["damage-mapper-api.onrender.com"]


def test_production_rejects_sqlite_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DM_ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./damage_mapper.db")

    with pytest.raises(ValueError, match="Production requires a PostgreSQL"):
        Settings()


def test_cors_origins_are_parsed_as_exact_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DM_CORS_ORIGINS", "https://dashboard.example.test, https://ops.example.test/"
    )

    settings = Settings()

    assert settings.cors_origins == [
        "https://dashboard.example.test",
        "https://ops.example.test",
    ]
