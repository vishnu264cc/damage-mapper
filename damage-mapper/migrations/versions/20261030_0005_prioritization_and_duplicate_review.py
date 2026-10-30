"""Persist transparent prioritization and duplicate review assessments.

Revision ID: 20261030_0005
Revises: 20261030_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261030_0005"
down_revision = "20261030_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("decision_assessment", sa.JSON(), nullable=True),
    )
    op.create_table(
        "prioritization_settings",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prioritization_settings")),
    )
    op.create_table(
        "duplicate_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("incident_id", sa.String(length=100), nullable=False),
        sa.Column("candidate_incident_id", sa.String(length=100), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("hash_distance", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("time_delta_seconds", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("review_note", sa.String(length=1000), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "match_score >= 0 AND match_score <= 1",
            name=op.f("ck_duplicate_candidates_match_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_incident_id"],
            ["incidents.id"],
            name=op.f(
                "fk_duplicate_candidates_candidate_incident_id_incidents"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["duplicate_groups.id"],
            name=op.f("fk_duplicate_candidates_group_id_duplicate_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_duplicate_candidates_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duplicate_candidates")),
        sa.UniqueConstraint(
            "incident_id",
            "candidate_incident_id",
            name="uq_duplicate_candidates_pair",
        ),
    )
    op.create_index(
        "ix_duplicate_candidates_candidate_incident_id",
        "duplicate_candidates",
        ["candidate_incident_id"],
    )
    op.create_index(
        "ix_duplicate_candidates_group_id", "duplicate_candidates", ["group_id"]
    )
    op.create_index(
        "ix_duplicate_candidates_group_status",
        "duplicate_candidates",
        ["group_id", "status"],
    )
    op.create_index(
        "ix_duplicate_candidates_incident_id", "duplicate_candidates", ["incident_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_duplicate_candidates_incident_id", table_name="duplicate_candidates"
    )
    op.drop_index(
        "ix_duplicate_candidates_group_status", table_name="duplicate_candidates"
    )
    op.drop_index(
        "ix_duplicate_candidates_group_id", table_name="duplicate_candidates"
    )
    op.drop_index(
        "ix_duplicate_candidates_candidate_incident_id",
        table_name="duplicate_candidates",
    )
    op.drop_table("duplicate_candidates")
    op.drop_table("prioritization_settings")
    op.drop_column("incidents", "decision_assessment")
