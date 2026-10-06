"""Record the confirmed full-video investigation of an expansion reanalysis (BE-10, #34)."""

import sqlalchemy as sa
from alembic import op

revision = "0009_reanalysis_source"
down_revision = "0008_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reanalysis_requests",
        sa.Column("source_investigation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_reanalysis_requests_source_investigation_id",
        "reanalysis_requests",
        "investigations",
        ["source_investigation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_reanalysis_requests_source_investigation_id",
        "reanalysis_requests",
        type_="foreignkey",
    )
    op.drop_column("reanalysis_requests", "source_investigation_id")
