"""Identity, upload intake, investigations and idempotency records (BE-05)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003_identity_intake"
down_revision: str | Sequence[str] | None = "0002_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "principals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "credentials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_credentials_token_hash"),
    )
    op.create_table(
        "uploads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("declared_sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("max_bytes", sa.BigInteger, nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_uploads_owner_id", "uploads", ["owner_id"])
    op.create_table(
        "investigations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("upload_id", UUID(as_uuid=True), sa.ForeignKey("uploads.id"), nullable=True),
        sa.Column("declared_duration_ms", sa.BigInteger, nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_investigations_owner_created", "investigations", ["owner_id", "created_at"])
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "investigation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer, nullable=False),
        sa.Column("response_body", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("ix_investigations_owner_created", table_name="investigations")
    op.drop_table("investigations")
    op.drop_index("ix_uploads_owner_id", table_name="uploads")
    op.drop_table("uploads")
    op.drop_table("credentials")
    op.drop_table("principals")
