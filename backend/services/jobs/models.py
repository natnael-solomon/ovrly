"""Job queue tables registered on the shared Alembic metadata."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from services.database import metadata

# Owner-scoped cancellation/deletion fences: queued for their stage key but never claimed
# by a handler, so they are not running work (for example for active-check quotas).
DEVICE_TEXT_FENCE_STAGE = "upload_device_text"
FENCE_STAGES = (DEVICE_TEXT_FENCE_STAGE,)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("owner_id", Uuid, ForeignKey("principals.id")),
    Column("cancel_outcome", Text),
    Column("version", Integer, nullable=False),
    Column("stage", Text, nullable=False),
    Column("input_hash", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("cancel_requested", Boolean, nullable=False, server_default="false"),
    # Bumped on every claim; publication is compare-and-set against it.
    Column("fencing_token", BigInteger, nullable=False, server_default="0"),
    # Bumped on effective cancellation and deletion; a late publish can never match.
    Column("generation", Integer, nullable=False, server_default="0"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("lease_owner", Text),
    Column("lease_expires_at", TIMESTAMP(timezone=True)),
    Column("available_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("payload", JSONB, nullable=False),
    Column("failure", Text),
    # Class of the last scheduled retry or of the terminal failure.
    Column("retry_class", Text),
    # Scheduled retries per retry class; independent of ``attempts`` (claims).
    Column("retry_counts", JSONB, nullable=False, server_default="{}"),
    # Recorded by the handler before a provider call so an unknown outcome reconciles by id.
    Column("provider_request_id", Text),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("version", "stage", "input_hash", name="uq_jobs_stage_key"),
    Index("ix_jobs_claimable", "stage", "state", "available_at"),
    Index("ix_jobs_lease_expiry", "lease_expires_at"),
    Index("ix_jobs_provider_request_id", "provider_request_id"),
)

job_results = Table(
    "job_results",
    metadata,
    Column("job_id", Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("stage", Text, nullable=False),
    Column("input_hash", Text, nullable=False),
    Column("fencing_token", BigInteger, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("result", JSONB, nullable=False),
    Column("published_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("version", "stage", "input_hash", name="uq_job_results_stage_key"),
)
