"""A report is superseded, never replaced, and exactly one is current (ADR 0116).

Three columns and one partial unique index, and the index is the decision. ``superseded_by``
names the report that replaced this one, ``superseded_at`` says when that was decided and
``supersession_reason`` says why, in the operator's words or the platform's; a withdrawal is
a supersession with no successor. **Current** means approved, not superseded by anything and
not withdrawn, and the index makes two current reports for one company unrepresentable
rather than something a service remembers to check.

Nothing is backfilled: every approved report is current, because it is. The one thing that
can fail is the index, on a company that already has two approved reports; the migration
checks for that first and refuses with the company named, because a migration that aborts
on the index halfway through is worse than one that refuses to start.

Revision ID: 0075
Revises: 0074
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

INDEX = "reports_one_current_per_company"
CURRENT = "immutable AND superseded_by IS NULL AND superseded_at IS NULL"


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "superseded_by",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("reports", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reports", sa.Column("supersession_reason", sa.Text(), nullable=True))
    op.create_check_constraint("report_does_not_supersede_itself", "reports", "id <> superseded_by")
    op.create_check_constraint(
        "report_supersession_is_dated_and_explained",
        "reports",
        "(superseded_at IS NULL) = (supersession_reason IS NULL)",
    )
    op.create_check_constraint(
        "report_successor_implies_supersession",
        "reports",
        "superseded_by IS NULL OR superseded_at IS NOT NULL",
    )

    crowded = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT company_id, count(*) FROM reports "
                "WHERE immutable AND company_id IS NOT NULL "
                "GROUP BY company_id HAVING count(*) > 1"
            )
        )
        .fetchall()
    )
    if crowded:
        named = ", ".join(f"{company} ({count} approved)" for company, count in crowded)
        message = (
            "Two or more approved reports are current for the same company, which ADR 0116 "
            f"makes unrepresentable: {named}. Supersede all but one of each — with a reason — "
            "before migrating."
        )
        raise RuntimeError(message)

    op.create_index(
        INDEX, "reports", ["company_id"], unique=True, postgresql_where=sa.text(CURRENT)
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="reports")
    op.drop_constraint("report_successor_implies_supersession", "reports", type_="check")
    op.drop_constraint("report_supersession_is_dated_and_explained", "reports", type_="check")
    op.drop_constraint("report_does_not_supersede_itself", "reports", type_="check")
    op.drop_column("reports", "supersession_reason")
    op.drop_column("reports", "superseded_at")
    op.drop_column("reports", "superseded_by")
