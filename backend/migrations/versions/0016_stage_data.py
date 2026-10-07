"""Persist fenced stage checkpoints and bounded provider attempt state."""

from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_stage_data"
down_revision = "0015_speech_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", Column("stage_data", JSONB, nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("jobs", "stage_data")
