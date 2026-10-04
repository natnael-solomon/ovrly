"""Account link columns on principals (BC-D07, BE-05 part 2)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005_account_link"
down_revision: str | Sequence[str] | None = "0004_job_retries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("principals", sa.Column("google_sub", sa.String(255), nullable=True))
    op.add_column(
        "principals",
        sa.Column("merged_into", UUID(as_uuid=True), sa.ForeignKey("principals.id"), nullable=True),
    )
    op.add_column("principals", sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_principals_google_sub", "principals", ["google_sub"])


def downgrade() -> None:
    op.drop_constraint("uq_principals_google_sub", "principals", type_="unique")
    op.drop_column("principals", "merged_at")
    op.drop_column("principals", "merged_into")
    op.drop_column("principals", "google_sub")
