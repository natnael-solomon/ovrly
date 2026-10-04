"""Add principal ownership and durable cancellation receipts for job actions."""

import sqlalchemy as sa
from alembic import op

revision = "0005_job_ownership"
down_revision = "0004_job_retries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy queue rows have no authenticated owner; do not guess one from their payload.
    op.add_column("jobs", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_jobs_owner_id_principals", "jobs", "principals", ["owner_id"], ["id"])
    op.add_column("jobs", sa.Column("cancel_outcome", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "cancel_outcome")
    op.drop_constraint("fk_jobs_owner_id_principals", "jobs", type_="foreignkey")
    op.drop_column("jobs", "owner_id")
