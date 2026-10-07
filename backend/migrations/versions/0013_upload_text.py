"""Bounded completed-upload device text, with durable job-deletion fences."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013_upload_text"
down_revision = "0012_asr_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_text",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column(
            "media_job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="SET NULL")
        ),
        sa.Column("batches", JSONB, nullable=False),
        sa.Column("completion", JSONB),
    )
    op.create_index("ix_upload_text_job", "upload_text", ["job_id"])
    op.create_index("ix_upload_text_media_job", "upload_text", ["media_job_id"])


def downgrade() -> None:
    op.drop_table("upload_text")
