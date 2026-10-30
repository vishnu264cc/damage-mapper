"""Add password credentials, revocable bearer sessions and persistent rate limits.

Revision ID: 20261031_0007
Revises: 20261031_0006
"""
import sqlalchemy as sa
from alembic import op

revision = "20261031_0007"
down_revision = "20261031_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE users
        SET role = CASE lower(trim(role))
            WHEN 'citizen' THEN 'citizen'
            WHEN 'responder' THEN 'responder'
            WHEN 'field responder' THEN 'responder'
            WHEN 'field_responder' THEN 'responder'
            WHEN 'admin' THEN 'admin'
            WHEN 'administrator' THEN 'admin'
            WHEN 'emergency administrator' THEN 'admin'
            WHEN 'emergency_administrator' THEN 'admin'
            ELSE 'citizen'
        END
        """
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_auth_sessions")),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"])
    op.create_index(
        "ix_auth_sessions_user_expires", "auth_sessions", ["user_id", "expires_at"]
    )
    op.create_table(
        "auth_rate_limits",
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bucket_key", name=op.f("pk_auth_rate_limits")),
    )
    op.create_table(
        "auth_bootstrap_claims",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_bootstrap_claims")),
        sa.UniqueConstraint("user_id", name=op.f("uq_auth_bootstrap_claims_user_id")),
    )


def downgrade() -> None:
    op.drop_table("auth_bootstrap_claims")
    op.drop_table("auth_rate_limits")
    op.drop_index("ix_auth_sessions_user_expires", table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_column("users", "password_hash")
