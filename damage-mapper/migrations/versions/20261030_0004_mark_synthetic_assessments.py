"""Mark seeded demo incidents as synthetic, not model predictions.

Revision ID: 20261030_0004
Revises: 20261030_0003
"""
from alembic import op

revision = "20261030_0004"
down_revision = "20261030_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE incidents "
        "SET model_analysis_status = 'synthetic', model_confidence = 0, "
        "model_review_required = TRUE "
        "WHERE model_name = 'Synthetic demo data'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE incidents "
        "SET model_analysis_status = 'legacy', model_confidence = model_confidence * 100 "
        "WHERE model_name = 'Synthetic demo data'"
    )
