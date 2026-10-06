"""Opt-in per-principal admission counters and shared provider request slots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_quotas"
down_revision = "0010_provider_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quota_usage",
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("checks", sa.Integer(), nullable=False),
        sa.Column("upload_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "provider_slots",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("provider_slots")
    op.drop_table("quota_usage")
