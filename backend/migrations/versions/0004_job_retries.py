"""Add retry class, per-class retry counters and the provider request id to jobs (BE-04)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_job_retries"
down_revision = "0003_identity_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("retry_class", sa.Text()))
    op.add_column("jobs", sa.Column("retry_counts", JSONB(), nullable=False, server_default="{}"))
    op.add_column("jobs", sa.Column("provider_request_id", sa.Text()))
    op.create_index("ix_jobs_provider_request_id", "jobs", ["provider_request_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_provider_request_id", table_name="jobs")
    op.drop_column("jobs", "provider_request_id")
    op.drop_column("jobs", "retry_counts")
    op.drop_column("jobs", "retry_class")
