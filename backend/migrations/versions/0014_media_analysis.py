"""Durable per-source device-text grace periods, separate from research."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0014_media_analysis"
down_revision = "0013_upload_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_analysis",
        sa.Column(
            "media_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "investigation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("grace_seconds", sa.Integer, nullable=False),
        sa.Column("text_deadline", sa.DateTime(timezone=True)),
        sa.Column("text_expired", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_media_analysis_investigation", "media_analysis", ["investigation_id"])
    op.create_index("ix_media_analysis_deadline", "media_analysis", ["text_deadline"])


def downgrade() -> None:
    op.drop_table("media_analysis")
