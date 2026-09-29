"""A limit on the book is the operator's, stated and never proposed (ADR 0136).

One table, ``book_limits``: a limit the operator stated on a book — one position, the five
largest together, or one sector's share — as a fraction of the book, with who stated it and
when. Nothing proposes one and nothing is blocked by one; the operator's own pages compare the
figures the risk page already strikes with these rows, in code, and say *over the 10% ceiling
you set*.

A limit is superseded, never edited: a changed limit is a new row whose ``supersedes_id`` is
unique, and a withdrawn one keeps its reason beside the moment, both or neither. The table
carries ``user_id`` as ADR 0120's constraint requires of every new table.

Revision ID: 0093
Revises: 0092
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None

_KINDS = ("single_position", "five_largest", "sector")


def upgrade() -> None:
    sa.Enum(*_KINDS, name="limit_kind").create(op.get_bind(), checkfirst=True)
    kind = postgresql.ENUM(*_KINDS, name="limit_kind", create_type=False)

    op.create_table(
        "book_limits",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "portfolio_id",
            sa.Uuid(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", kind, nullable=False),
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column("fraction", sa.Numeric(7, 6), nullable=False),
        sa.Column("stated_by", sa.Text(), nullable=False),
        sa.Column(
            "stated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("book_limits.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("fraction > 0 AND fraction <= 1", name="book_limit_is_a_share"),
        sa.CheckConstraint(
            "(kind = 'sector') = (sector IS NOT NULL)", name="book_limit_sector_iff_sector_kind"
        ),
        sa.CheckConstraint(
            "sector IS NULL OR char_length(btrim(sector)) > 0",
            name="book_limit_sector_is_not_blank",
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_reason IS NULL)",
            name="book_limit_withdrawal_has_a_reason",
        ),
        sa.CheckConstraint("id <> supersedes_id", name="book_limit_does_not_supersede_itself"),
        sa.UniqueConstraint("supersedes_id", name="uq_book_limits_supersedes_once"),
    )
    op.create_index("ix_book_limits_portfolio_id", "book_limits", ["portfolio_id"])
    op.create_index("ix_book_limits_user_id", "book_limits", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_book_limits_user_id", table_name="book_limits")
    op.drop_index("ix_book_limits_portfolio_id", table_name="book_limits")
    op.drop_table("book_limits")
    sa.Enum(name="limit_kind").drop(op.get_bind(), checkfirst=True)
