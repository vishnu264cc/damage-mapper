from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL

from app.core.config import get_settings


def test_legacy_sqlite_records_are_migrated_and_confidence_is_normalized(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE incidents (
            id TEXT PRIMARY KEY, description TEXT NOT NULL, image_path TEXT NOT NULL,
            latitude REAL NOT NULL, longitude REAL NOT NULL, reporter TEXT,
            damage_type TEXT NOT NULL, model_confidence REAL NOT NULL,
            severity TEXT NOT NULL, severity_score REAL NOT NULL,
            urgency INTEGER NOT NULL, people_affected INTEGER NOT NULL,
            access_blocked INTEGER NOT NULL, exposure_score REAL NOT NULL,
            priority_score REAL NOT NULL, duplicate_of TEXT,
            verification_status TEXT NOT NULL DEFAULT 'Pending',
            incident_status TEXT NOT NULL DEFAULT 'Reported', assigned_to TEXT,
            created_at TEXT NOT NULL, model_name TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO incidents VALUES (
            'demo-1', 'Synthetic flood scenario', 'uploads/demo.png', 12.6, 77.6,
            'Demo scenario', 'Flood', 91, 'Critical', 90, 80, 20, 1, 60, 85,
            NULL, 'Verified', 'Reported', NULL,
            '2026-10-30T12:00:00+00:00', 'Synthetic demo data'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO incidents VALUES (
            'legacy-1', 'Flood report', 'uploads/legacy.png', 12.5, 77.5,
            'Field team', 'Flood', 91, 'Critical', 90, 80, 20, 1, 60, 85,
            NULL, 'Verified', 'Assigned', 'Response team',
            '2026-10-30T12:00:00+00:00', 'Legacy CLIP'
        )
        """
    )
    connection.commit()
    connection.close()

    database_url = URL.create("sqlite", database=str(database)).render_as_string(
        hide_password=False
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    root = Path(__file__).parents[1]
    monkeypatch.chdir(root)
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, "head")
    get_settings.cache_clear()

    migrated = sqlite3.connect(database)
    assert migrated.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 2
    assert migrated.execute("SELECT COUNT(*) FROM incidents_legacy").fetchone()[0] == 2
    assert migrated.execute(
        "SELECT model_confidence, assigned_to FROM incidents WHERE id='legacy-1'"
    ).fetchone() == (0.91, "Response team")
    assert migrated.execute(
        "SELECT model_analysis_status, model_review_required "
        "FROM incidents WHERE id='legacy-1'"
    ).fetchone() == ("legacy", 1)
    assert migrated.execute(
        "SELECT model_analysis_status, model_confidence FROM incidents WHERE id='demo-1'"
    ).fetchone() == ("synthetic", 0.0)
    assert migrated.execute(
        "SELECT action FROM audit_logs WHERE incident_id='legacy-1'"
    ).fetchone() == ("legacy_import",)
    assert migrated.execute(
        "SELECT assigned_to, active FROM responder_assignments WHERE incident_id='legacy-1'"
    ).fetchone() == ("Response team", 1)
    assert migrated.execute(
        "SELECT decision_assessment FROM incidents WHERE id='legacy-1'"
    ).fetchone() == (None,)
    assert migrated.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='duplicate_candidates'"
    ).fetchone() == ("duplicate_candidates",)
    assert migrated.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prioritization_settings'"
    ).fetchone() == ("prioritization_settings",)
    assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    migrated.close()
