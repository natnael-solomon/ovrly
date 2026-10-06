"""Shared provider rate-limit buckets for the evidence stages (BE-09, #27)."""

import sqlalchemy as sa
from alembic import op

revision = "0010_provider_buckets"
down_revision = "0009_reanalysis_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_buckets",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("provider_buckets")
