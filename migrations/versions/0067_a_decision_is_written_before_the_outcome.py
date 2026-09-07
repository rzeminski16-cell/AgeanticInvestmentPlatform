"""A decision is written before the outcome, and the trade points back at it.

Roadmap §3.7, under ADRs 0074, 0081 and 0104. One value on an existing type, one new type,
one table, and one column.

``judgement_kind`` gains ``decision``: the second judgement subtype, in the shape 0102 left
for it. Added in an autocommit block, as revisions 0062 and 0066 did for their enum values.

``decisions`` is keyed on the judgement's own id — a decision *is* a judgement seen from
its consequence — and carries the thesis it acts on, the security and book where named, an
``action`` from the closed ``decision_action`` type, the statement, and the four things a
post-trade review holds the operator to: a size *as a sentence*, a horizon in months, an
exit plan, a review date. There is no numeric size column, on purpose (ADR 0074).

``transactions.decision_id`` is the trade saying which decision it carried out. On the
trade and pointing at the judgement, never the reverse, so a judgement still enters no
lineage; ``SET NULL`` because a trade is a fact about the book whatever became of the
reasoning behind it.

The downgrade drops the column, the table and the action type. The ``judgement_kind``
value stays, as every added enum value does: Postgres cannot remove one without rebuilding
the type, and a value no row carries is inert.

Revision ID: 0067
Revises: 0066
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

_ACTIONS = ("buy", "add", "trim", "sell", "hold", "pass")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE judgement_kind ADD VALUE IF NOT EXISTS 'decision'")

    sa.Enum(*_ACTIONS, name="decision_action").create(op.get_bind(), checkfirst=True)
    action = postgresql.ENUM(*_ACTIONS, name="decision_action", create_type=False)

    op.create_table(
        "decisions",
        sa.Column(
            "judgement_id",
            sa.Uuid(),
            sa.ForeignKey("judgements.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "thesis_id",
            sa.Uuid(),
            sa.ForeignKey("theses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "portfolio_id",
            sa.Uuid(),
            sa.ForeignKey("portfolios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "security_id",
            sa.Uuid(),
            sa.ForeignKey("securities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("action", action, nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("size_statement", sa.Text(), nullable=True),
        sa.Column("horizon_months", sa.Integer(), nullable=True),
        sa.Column("exit_plan", sa.Text(), nullable=True),
        sa.Column("review_by", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "char_length(btrim(statement)) > 0", name="decision_statement_is_not_blank"
        ),
        sa.CheckConstraint(
            "horizon_months IS NULL OR horizon_months > 0", name="decision_horizon_is_positive"
        ),
    )
    op.create_index("ix_decisions_thesis_id", "decisions", ["thesis_id"])
    op.create_index("ix_decisions_security_id", "decisions", ["security_id"])
    op.create_index("ix_decisions_review_by", "decisions", ["review_by"])

    op.add_column(
        "transactions",
        sa.Column(
            "decision_id",
            sa.Uuid(),
            sa.ForeignKey(
                "decisions.judgement_id",
                ondelete="SET NULL",
                name="fk_transactions_decision_id",
            ),
            nullable=True,
        ),
    )
    op.create_index("ix_transactions_decision_id", "transactions", ["decision_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_decision_id", table_name="transactions")
    op.drop_column("transactions", "decision_id")
    op.drop_table("decisions")
    sa.Enum(name="decision_action").drop(op.get_bind(), checkfirst=True)
    # `judgement_kind` keeps 'decision'. See the module docstring.
