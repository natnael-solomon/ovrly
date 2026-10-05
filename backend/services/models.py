"""Core table definitions shared by the API, migrations and tests."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
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
