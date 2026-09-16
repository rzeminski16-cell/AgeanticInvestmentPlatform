"""A decision at a gate is superseded, never re-asserted (ADR 0123).

``approvals.supersedes_id`` names the decision a later one replaces: a nullable self-reference
with the three constraints the other supersede-shaped tables carry — a row does not supersede
itself, a decision is superseded at most once, and the superseded row is never deleted from
under the one that points at it. Rows are still never updated; a change of mind at a gate
whose page has moved is a new row that says which decision it replaces, and the workflow
reads the newest row nothing supersedes.

No existing row changes: every decision recorded before this migration supersedes nothing.

Revision ID: 0074
Revises: 0073
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("approvals.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "approval_does_not_supersede_itself", "approvals", "id <> supersedes_id"
    )
    op.create_unique_constraint("uq_approvals_supersedes_once", "approvals", ["supersedes_id"])


def downgrade() -> None:
    op.drop_constraint("uq_approvals_supersedes_once", "approvals", type_="unique")
    op.drop_constraint("approval_does_not_supersede_itself", "approvals", type_="check")
    op.drop_column("approvals", "supersedes_id")
