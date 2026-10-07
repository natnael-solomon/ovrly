"""Durable hosted speech reservations and recoverable outcomes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012_asr_requests"
down_revision: str | Sequence[str] | None = "0011_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asr_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("audio_seconds", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("result", JSONB),
        sa.Column("retry_index", sa.Integer(), nullable=False),
        sa.Column("retry_after", sa.Integer()),
    )
    op.create_index("ix_asr_requests_quota", "asr_requests", ["account_id", "model", "created_at"])
    op.create_index("ix_asr_requests_job", "asr_requests", ["job_id"])


def downgrade() -> None:
    op.drop_table("asr_requests")
