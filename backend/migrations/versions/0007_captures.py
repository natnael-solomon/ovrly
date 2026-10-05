"""Durable live-capture manifests and chunk reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0007_captures"
down_revision = "0006_job_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_sessions",
        sa.Column(
            "id",
            sa.Uuid(),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("chunk_duration_ms", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("continue_research", sa.Boolean(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_capture_owner_request"),
    )
    op.create_table(
        "capture_chunks",
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("capture_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("modality", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.create_index("ix_capture_chunks_job_id", "capture_chunks", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_capture_chunks_job_id", table_name="capture_chunks")
    op.drop_table("capture_chunks")
    op.drop_table("capture_sessions")
