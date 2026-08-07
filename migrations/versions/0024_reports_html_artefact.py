"""Add reports.html_artefact_id: the stored preview HTML the PDF derives from.

Task 48. Since task 46 the Gate 2 preview and the PDF share one HTML serialisation of
one assembled document; this column is where the render step archives that HTML, so
"what was approved" is a content-addressed file rather than something re-renderable from
rows that may since have changed. RESTRICT like its two siblings: deleting an artefact a
frozen report points at would un-write the record.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("html_artefact_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_reports_html_artefact_id_artefacts"),
        "reports",
        "artefacts",
        ["html_artefact_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_reports_html_artefact_id_artefacts"), "reports", type_="foreignkey")
    op.drop_column("reports", "html_artefact_id")
