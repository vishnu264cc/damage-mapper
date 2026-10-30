"""Normalize historical model confidence values to the 0..1 API scale.

Revision ID: 20261030_0002
Revises: 20261030_0001
"""
from alembic import op
from sqlalchemy import text

revision = "20261030_0002"
down_revision = "20261030_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE incidents "
            "SET model_confidence = model_confidence / 100 "
            "WHERE model_confidence > 1"
        )
    )


def downgrade() -> None:
    # Legacy rows mixed fractions and percentages, so reversing the scale is ambiguous.
    pass
