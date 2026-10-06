"""Core table definitions shared by the API, migrations and tests."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from services.database import metadata

principals = Table(
    "principals",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # Stable Google subject once linked (BC-D07); one account per subject.
    Column("google_sub", String(255), nullable=True),
    # Set on a guest whose device continued as an existing account; its credentials are revoked.
    Column("merged_into", UUID(as_uuid=True), ForeignKey("principals.id"), nullable=True),
    Column("merged_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("google_sub", name="uq_principals_google_sub"),
)

credentials = Table(
    "credentials",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "principal_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("token_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("token_hash", name="uq_credentials_token_hash"),
)

quota_usage = Table(
    "quota_usage",
    metadata,
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("day", Date, nullable=False),
    Column("checks", Integer, nullable=False),
    Column("upload_bytes", BigInteger, nullable=False),
)

provider_slots = Table(
    "provider_slots",
    metadata,
    Column("provider", String(64), primary_key=True),
    Column("slot", Integer, primary_key=True),
    Column("lease_id", UUID(as_uuid=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

uploads = Table(
    "uploads",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("state", String(16), nullable=False),
    Column("declared_size_bytes", BigInteger, nullable=False),
    Column("declared_sha256", String(64), nullable=False),
    Column("content_type", String(128), nullable=False),
    Column("max_bytes", BigInteger, nullable=False),
    Column("storage_key", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Index("ix_uploads_owner_id", "owner_id"),
)

investigations = Table(
    "investigations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_kind", String(16), nullable=False),
    Column("source_url", Text, nullable=True),
    Column("upload_id", UUID(as_uuid=True), ForeignKey("uploads.id"), nullable=True),
    Column("declared_duration_ms", BigInteger, nullable=True),
    Column("state", String(32), nullable=False),
    Column("stage", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    Column("error_code", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_investigations_owner_created", "owner_id", "created_at"),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("key", String(200), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column(
        "investigation_id",
        UUID(as_uuid=True),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("response_status", Integer, nullable=False),
    Column("response_body", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

capture_sessions = Table(
    "capture_sessions",
    metadata,
    Column(
        "id",
        UUID(as_uuid=True),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("owner_id", UUID(as_uuid=True), ForeignKey("principals.id"), nullable=False),
    Column("request_key", String(200), nullable=False),
    Column("chunk_duration_ms", Integer, nullable=False),
    Column("state", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("continue_research", Boolean, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    UniqueConstraint("owner_id", "request_key", name="uq_capture_owner_request"),
)

capture_chunks = Table(
    "capture_chunks",
    metadata,
    Column(
        "session_id",
        UUID(as_uuid=True),
        ForeignKey("capture_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("seq", Integer, primary_key=True),
    Column("end_ms", Integer, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("content_type", String(128), nullable=False),
    Column("modality", String(16), nullable=False),
    Column("storage_key", String(32), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=True),
    Column(
        "job_id",
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
)

# BE-10 (#33). A report version is immutable (a database trigger rejects UPDATE); corrections
# and expansions insert a new version. ``owner_id`` repeats the investigation's owner so the
# owner-scoped loader applies directly.
report_versions = Table(
    "report_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "investigation_id",
        UUID(as_uuid=True),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False),
    Column("change_summary", Text, nullable=False),
    # True only for development stub reports built from the contract fixtures.
    Column("fixture", Boolean, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "investigation_id", "version", name="uq_report_versions_investigation_version"
    ),
    Index("ix_report_versions_owner_id", "owner_id"),
)

# An explicit save keeps a snapshot of the version, with no foreign key to it: saves moved to
# an account by a second-device link (BC-D07) must survive the guest's workspace expiry.
saved_reports = Table(
    "saved_reports",
    metadata,
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("report_id", UUID(as_uuid=True), primary_key=True),
    Column("investigation_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    Column("report", JSONB, nullable=False),
    Column("saved_at", DateTime(timezone=True), nullable=False),
    Index("ix_saved_reports_owner_saved", "owner_id", "saved_at"),
)

# One row per accepted reanalysis request (BE-10): the idempotency record, the version a
# correction published at once and the version the reanalysis job published later.
reanalysis_requests = Table(
    "reanalysis_requests",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "investigation_id",
        UUID(as_uuid=True),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("reason", String(16), nullable=False),
    Column("base_version", Integer, nullable=False),
    Column("published_version", Integer, nullable=True),
    Column("result_version", Integer, nullable=True),
    Column("job_id", UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
    Column("response", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # The confirmed full-video investigation of an expansion (same owner); null otherwise.
    Column(
        "source_investigation_id",
        UUID(as_uuid=True),
        ForeignKey(
            "investigations.id",
            ondelete="SET NULL",
            name="fk_reanalysis_requests_source_investigation_id",
        ),
        nullable=True,
    ),
    UniqueConstraint("owner_id", "idempotency_key", name="uq_reanalysis_requests_owner_key"),
)

# Audit and replay record of each voice action (BC-D04). No transcript or audio is received
# or stored; only the structured request and the response sent back.
voice_actions = Table(
    "voice_actions",
    metadata,
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("request_id", String(128), primary_key=True),
    Column("action", String(64), nullable=False),
    Column("target_kind", String(16), nullable=True),
    Column("target_id", String(128), nullable=True),
    Column("result", String(16), nullable=False),
    Column("error_code", String(64), nullable=True),
    Column("response", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# A token bucket per provider account (BE-09, #27), shared by every worker process: the
# Scholarxiv Papers and Router APIs share one hourly request limit per account.
provider_buckets = Table(
    "provider_buckets",
    metadata,
    Column("name", String(64), primary_key=True),
    Column("tokens", Float, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
