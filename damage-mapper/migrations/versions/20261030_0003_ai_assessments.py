"""Persist structured image assessment results.

Revision ID: 20261030_0003
Revises: 20261030_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261030_0003"
down_revision = "20261030_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column(
            "model_analysis_status",
            sa.String(20),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "incidents",
        sa.Column("model_processing_ms", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "model_confidence_threshold",
            sa.Float(),
            nullable=False,
            server_default="0.55",
        ),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "model_candidates",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "model_detected_objects",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "model_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "model_detector_status",
            sa.String(20),
            nullable=False,
            server_default="not_run",
        ),
    )
    op.add_column("incidents", sa.Column("model_detector_name", sa.String(200)))
    op.execute("UPDATE incidents SET model_confidence_threshold = 0.55")


def downgrade() -> None:
    op.drop_column("incidents", "model_detector_name")
    op.drop_column("incidents", "model_detector_status")
    op.drop_column("incidents", "model_review_required")
    op.drop_column("incidents", "model_detected_objects")
    op.drop_column("incidents", "model_candidates")
    op.drop_column("incidents", "model_confidence_threshold")
    op.drop_column("incidents", "model_processing_ms")
    op.drop_column("incidents", "model_analysis_status")
