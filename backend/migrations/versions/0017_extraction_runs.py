"""Durable incremental extraction input and request ledger."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017_extraction_runs"
down_revision = "0016_stage_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("investigation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("data", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("extraction_runs")
