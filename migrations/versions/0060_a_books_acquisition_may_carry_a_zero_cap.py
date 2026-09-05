"""A book's acquisition may carry a zero cap.

ADR 0093. A portfolio data acquisition is a work order whose subject is the book, budgeted
at zero model spend by design — no step under it may call a model, and a zero cap is the
enforcement rather than the declaration: the budget guard walks to the root, finds 0, and
refuses any call some future change wires in by mistake. The positive check was right when
every work order was a model-calling run; it becomes ``>= 0`` so the one kind of order
that must never spend structurally cannot.

Revision ID: 0060
Revises: 0059
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_work_orders_cost_is_positive", "work_orders", type_="check")
    op.create_check_constraint(
        "ck_work_orders_cost_is_positive", "work_orders", sa.text("max_cost_gbp >= 0")
    )


def downgrade() -> None:
    # Exact only while no zero-cap order exists; Postgres validates on creation, so a
    # database that has recorded a book acquisition refuses the downgrade loudly rather
    # than silently outlawing rows it already holds.
    op.drop_constraint("ck_work_orders_cost_is_positive", "work_orders", type_="check")
    op.create_check_constraint(
        "ck_work_orders_cost_is_positive", "work_orders", sa.text("max_cost_gbp > 0")
    )
