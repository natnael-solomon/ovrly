"""Provider holds that pause a bucket without discarding its balance (#22)."""

import sqlalchemy as sa
from alembic import op

revision = "0018_provider_bucket_holds"
down_revision = "0017_extraction_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_buckets", sa.Column("held_until", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("provider_buckets", "held_until")
