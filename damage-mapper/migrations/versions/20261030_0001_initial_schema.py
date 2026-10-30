"""Create normalized incident schema and import the legacy SQLite records.

Revision ID: 20261030_0001
Revises:
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261030_0001"
down_revision = None
branch_labels = None
depends_on = None


def _created_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _safe_storage_path(value: object) -> str:
    original = str(value or "")
    return PureWindowsPath(original.replace("/", "\\")).name or PurePosixPath(original).name


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    has_legacy = inspector.has_table("incidents")
    if has_legacy and not inspector.has_table("incidents_legacy"):
        op.rename_table("incidents", "incidents_legacy")

    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(40), nullable=False, server_default="responder"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "duplicate_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="candidate"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("reporter", sa.String(200)),
        sa.Column("reporting_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("damage_type", sa.String(100), nullable=False),
        sa.Column("model_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("severity", sa.String(30), nullable=False),
        sa.Column("severity_score", sa.Float(), nullable=False),
        sa.Column("urgency", sa.Integer(), nullable=False),
        sa.Column("people_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("access_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exposure_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("priority_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duplicate_of", sa.String(100), sa.ForeignKey("incidents.id", ondelete="SET NULL")),
        sa.Column("duplicate_group_id", sa.String(36), sa.ForeignKey("duplicate_groups.id", ondelete="SET NULL")),
        sa.Column("verification_status", sa.String(30), nullable=False, server_default="Pending"),
        sa.Column("incident_status", sa.String(30), nullable=False, server_default="Reported"),
        sa.Column("assigned_to", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False, server_default="Needs review"),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_incidents_latitude_range"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_incidents_longitude_range"),
        sa.CheckConstraint("urgency >= 0 AND urgency <= 100", name="ck_incidents_urgency_range"),
        sa.CheckConstraint("people_affected >= 0", name="ck_incidents_people_affected_nonnegative"),
        sa.CheckConstraint("severity_score >= 0 AND severity_score <= 100", name="ck_incidents_severity_score_range"),
        sa.CheckConstraint("priority_score >= 0 AND priority_score <= 100", name="ck_incidents_priority_score_range"),
    )
    op.create_table(
        "incident_images",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(100), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_path", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255)),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("perceptual_hash", sa.String(16)),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("file_size >= 0", name="ck_incident_images_file_size_nonnegative"),
    )
    op.create_table(
        "verification_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(100), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("previous_status", sa.String(30)),
        sa.Column("changed_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("note", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "responder_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(100), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("responder_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("assigned_to", sa.String(200), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(100), sa.ForeignKey("incidents.id", ondelete="SET NULL")),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    indexes = {
        "users": [],
        "incidents": [
            ("ix_incidents_created_at", ["created_at"], False),
            ("ix_incidents_duplicate_of", ["duplicate_of"], False),
            ("ix_incidents_duplicate_group_id", ["duplicate_group_id"], False),
            ("ix_incidents_priority_created", ["priority_score", "created_at"], False),
            ("ix_incidents_verification_status", ["verification_status"], False),
            ("ix_incidents_incident_status", ["incident_status"], False),
            ("ix_incidents_severity", ["severity"], False),
        ],
        "incident_images": [
            ("ix_incident_images_incident_id", ["incident_id"], False),
            ("ix_incident_images_sha256", ["sha256"], False),
        ],
        "verification_history": [
            ("ix_verification_history_incident_id", ["incident_id"], False),
            ("ix_verification_history_incident_created", ["incident_id", "created_at"], False),
        ],
        "responder_assignments": [
            ("ix_responder_assignments_incident_id", ["incident_id"], False),
            ("ix_responder_assignments_incident_created", ["incident_id", "created_at"], False),
        ],
        "audit_logs": [
            ("ix_audit_logs_incident_id", ["incident_id"], False),
            ("ix_audit_logs_incident_created", ["incident_id", "created_at"], False),
        ],
    }
    for table_name, table_indexes in indexes.items():
        for index_name, columns, unique in table_indexes:
            op.create_index(index_name, table_name, columns, unique=unique)

    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE INDEX ix_incidents_geography
            ON incidents USING GIST (
                (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography)
            )
            """
        )

    if not has_legacy:
        return

    legacy_rows = bind.execute(text("SELECT * FROM incidents_legacy")).mappings().all()
    valid_ids = {row["id"] for row in legacy_rows}
    for row in legacy_rows:
        created_at = _created_at(row["created_at"])
        values = {
            "id": row["id"],
            "description": row["description"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "reporter": row["reporter"],
            "damage_type": row["damage_type"],
            "model_confidence": (
                row["model_confidence"] / 100
                if row["model_confidence"] > 1
                else row["model_confidence"]
            ),
            "severity": row["severity"],
            "severity_score": row["severity_score"],
            "urgency": row["urgency"],
            "people_affected": row["people_affected"],
            "access_blocked": bool(row["access_blocked"]),
            "exposure_score": row["exposure_score"],
            "priority_score": row["priority_score"],
            "duplicate_of": row["duplicate_of"] if row["duplicate_of"] in valid_ids else None,
            "verification_status": row["verification_status"],
            "incident_status": row["incident_status"],
            "assigned_to": row["assigned_to"] or None,
            "created_at": created_at,
            "updated_at": created_at,
            "model_name": row["model_name"],
        }
        bind.execute(
            text(
                """
                INSERT INTO incidents (
                    id, description, latitude, longitude, reporter, damage_type,
                    model_confidence, severity, severity_score, urgency, people_affected,
                    access_blocked, exposure_score, priority_score, duplicate_of,
                    verification_status, incident_status, assigned_to, created_at,
                    updated_at, model_name
                ) VALUES (
                    :id, :description, :latitude, :longitude, :reporter, :damage_type,
                    :model_confidence, :severity, :severity_score, :urgency, :people_affected,
                    :access_blocked, :exposure_score, :priority_score, :duplicate_of,
                    :verification_status, :incident_status, :assigned_to, :created_at,
                    :updated_at, :model_name
                )
                """
            ),
            values,
        )
        raw_path = str(row["image_path"] or "")
        storage_path = _safe_storage_path(raw_path)
        if storage_path:
            bind.execute(
                text(
                    """
                    INSERT INTO incident_images (
                        id, incident_id, storage_path, original_filename, content_type,
                        file_size, sha256, is_primary, created_at
                    ) VALUES (
                        :id, :incident_id, :storage_path, :original_filename, :content_type,
                        :file_size, :sha256, :is_primary, :created_at
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "incident_id": row["id"],
                    "storage_path": storage_path,
                    "original_filename": storage_path,
                    "content_type": (
                        "image/jpeg" if storage_path.lower().endswith((".jpg", ".jpeg"))
                        else "image/webp" if storage_path.lower().endswith(".webp")
                        else "image/png"
                    ),
                    "file_size": 0,
                    "sha256": None,
                    "is_primary": True,
                    "created_at": created_at,
                },
            )
        bind.execute(
            text(
                """
                INSERT INTO verification_history (
                    id, incident_id, status, previous_status, note, created_at
                ) VALUES (:id, :incident_id, :status, NULL, :note, :created_at)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "incident_id": row["id"],
                "status": row["verification_status"],
                "note": "Imported from legacy incident database",
                "created_at": created_at,
            },
        )
        if row["assigned_to"]:
            bind.execute(
                text(
                    """
                    INSERT INTO responder_assignments (
                        id, incident_id, assigned_to, active, created_at
                    ) VALUES (:id, :incident_id, :assigned_to, :active, :created_at)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "incident_id": row["id"],
                    "assigned_to": row["assigned_to"],
                    "active": True,
                    "created_at": created_at,
                },
            )
        audit_table = sa.table(
            "audit_logs",
            sa.column("id", sa.String(36)),
            sa.column("incident_id", sa.String(100)),
            sa.column("action", sa.String(80)),
            sa.column("details", sa.JSON()),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        bind.execute(
            sa.insert(audit_table).values(
                {
                    "id": str(uuid.uuid4()),
                    "incident_id": row["id"],
                    "action": "legacy_import",
                    "details": {"source": "incidents_legacy"},
                    "created_at": created_at,
                }
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_incidents_geography")
    for table_name in (
        "audit_logs",
        "responder_assignments",
        "verification_history",
        "incident_images",
        "incidents",
        "duplicate_groups",
        "users",
    ):
        op.drop_table(table_name)
    if inspect(bind).has_table("incidents_legacy"):
        op.rename_table("incidents_legacy", "incidents")
