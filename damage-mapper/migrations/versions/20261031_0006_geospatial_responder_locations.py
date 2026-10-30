"""Add secure responder location storage for geospatial operations.

Revision ID: 20261031_0006
Revises: 20261030_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "20261031_0006"
down_revision = "20261030_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "responder_locations",
        sa.Column("responder_name", sa.String(length=200), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name=op.f("ck_responder_locations_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name=op.f("ck_responder_locations_longitude_range"),
        ),
        sa.PrimaryKeyConstraint(
            "responder_name", name=op.f("pk_responder_locations")
        ),
    )


def downgrade() -> None:
    op.drop_table("responder_locations")
