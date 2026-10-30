from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DM_DISABLE_MODEL"] = "1"

from app.db.base import Base
from app.db.session import get_db
from app.core.config import get_settings
from app.models import AuthSession, User
from app.main import app
from app.services import demo, uploads
from app.services.auth import hash_token


@pytest.fixture
def client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        with test_sessions() as session:
            yield session

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(uploads, "UPLOADS", upload_dir)
    monkeypatch.setattr(demo, "UPLOADS", upload_dir)
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 10 * 1024 * 1024)
    app.dependency_overrides[get_db] = override_get_db
    test_token = "test-admin-session-token"
    with test_sessions() as session:
        admin = User(
            email="admin@example.test",
            display_name="Test Administrator",
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.flush()
        session.add(
            AuthSession(
                token_hash=hash_token(test_token),
                user_id=admin.id,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
            )
        )
        session.commit()
    with TestClient(
        app,
        headers={"Authorization": f"Bearer {test_token}"},
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def anonymous_client(client: TestClient) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def png_image() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (80, 60), (220, 100, 80)).save(buffer, format="PNG")
    return buffer.getvalue()
