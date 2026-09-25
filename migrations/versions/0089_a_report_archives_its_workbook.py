"""Add reports.workbook_artefact_id: the model workbook archived beside the PDF.

ADR 0134, F5. The workbook carries the report's discounted cash flow as live formulas, and it
is archived when the report is approved so the download is a file rather than a regeneration
from rows that may since have changed — the rule the report's other notations already follow.
RESTRICT like its three siblings: deleting an artefact a frozen report points at would
un-write the record. Nullable, because a report with no discounted cash flow has no workbook,
and a report approved before this migration has none either.

Revision ID: 0089
Revises: 0088
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("workbook_artefact_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_reports_workbook_artefact_id_artefacts"),
        "reports",
        "artefacts",
        ["workbook_artefact_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_reports_workbook_artefact_id_artefacts"), "reports", type_="foreignkey"
    )
    op.drop_column("reports", "workbook_artefact_id")
