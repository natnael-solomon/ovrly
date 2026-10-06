"""Immutable report versions, saved reports, reanalysis requests and the voice audit (BE-10)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_reports"
down_revision = "0007_captures"
branch_labels = None
depends_on = None

_IMMUTABLE_FUNCTION = """
CREATE FUNCTION ovrly_report_versions_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'report versions are immutable';
END;
$$ LANGUAGE plpgsql
"""
_IMMUTABLE_TRIGGER = """
CREATE TRIGGER report_versions_immutable
BEFORE UPDATE ON report_versions
FOR EACH ROW EXECUTE FUNCTION ovrly_report_versions_immutable()
"""


def upgrade() -> None:
    op.create_table(
        "report_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "investigation_id",
            sa.Uuid(),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("fixture", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "investigation_id", "version", name="uq_report_versions_investigation_version"
        ),
    )
    op.create_index("ix_report_versions_owner_id", "report_versions", ["owner_id"])
    # A published version is never edited; corrections and expansions insert a new one.
    op.execute(_IMMUTABLE_FUNCTION)
    op.execute(_IMMUTABLE_TRIGGER)
    op.create_table(
        "saved_reports",
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("report_id", sa.Uuid(), primary_key=True),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_saved_reports_owner_saved", "saved_reports", ["owner_id", "saved_at"])
    op.create_table(
        "reanalysis_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            sa.Uuid(),
            sa.ForeignKey("investigations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_reanalysis_requests_owner_key"),
    )
    op.create_index(
        "ix_reanalysis_requests_investigation_id", "reanalysis_requests", ["investigation_id"]
    )
    op.create_table(
        "voice_actions",
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=True),
        sa.Column("target_id", sa.String(128), nullable=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("voice_actions")
    op.drop_index("ix_reanalysis_requests_investigation_id", table_name="reanalysis_requests")
    op.drop_table("reanalysis_requests")
    op.drop_index("ix_saved_reports_owner_saved", table_name="saved_reports")
    op.drop_table("saved_reports")
    op.execute("DROP TRIGGER report_versions_immutable ON report_versions")
    op.execute("DROP FUNCTION ovrly_report_versions_immutable()")
    op.drop_index("ix_report_versions_owner_id", table_name="report_versions")
    op.drop_table("report_versions")
