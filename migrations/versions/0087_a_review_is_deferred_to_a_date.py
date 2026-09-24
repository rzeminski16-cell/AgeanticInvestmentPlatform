"""A review is deferred to a date (F14).

``review_deferrals``: a closed position the operator has chosen to review later — the
position as ``reviews`` names it (a security in a book, and the date its holding returned to
nil), the date a person will review it by, and why. Append-only: deferring again writes a
new row and the latest governs, and a deferral whose date passes with no review lapses on
its own, so nothing is updated or deleted to make it so. Not a judgement — it asserts
nothing about the company — which is why it is a table of its own rather than a fourth
``judgement_kind`` value with a detail table.

The downgrade drops the table.

Revision ID: 0087
Revises: 0086
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_deferrals",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "portfolio_id",
            sa.Uuid(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "security_id",
            sa.Uuid(),
            sa.ForeignKey("securities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("closed_on", sa.Date(), nullable=False),
        sa.Column("review_by", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        # The statement's clock, not the transaction's: two deferrals in one transaction
        # must still have an order, because the latest governs.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("review_by > closed_on", name="review_deferral_is_after_the_close"),
        sa.CheckConstraint("char_length(btrim(reason)) > 0", name="review_deferral_says_why"),
    )
    op.create_index(
        "ix_review_deferrals_position_created_at",
        "review_deferrals",
        ["portfolio_id", "security_id", "closed_on", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_review_deferrals_position_created_at", table_name="review_deferrals")
    op.drop_table("review_deferrals")
