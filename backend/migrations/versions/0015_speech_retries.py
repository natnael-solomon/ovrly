"""Owner-scoped explicit speech-retry receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0015_speech_retries"
down_revision = "0014_media_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speech_retries",
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("request_key", sa.String(200), primary_key=True),
        sa.Column(
            "investigation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("response", JSONB, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("speech_retries")
