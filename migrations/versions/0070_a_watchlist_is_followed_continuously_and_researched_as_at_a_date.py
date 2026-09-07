"""A watchlist is followed continuously and researched as at a date.

Roadmap §3.10, under ADR 0107. Two tables and no new type.

``watchlist_entries`` is what the operator follows: a listing, why, when the platform came
to know it (``followed_at``, the database's clock), and a withdrawal with a reason.
``watchlist_commissions`` is each time the queue turned an entry into a research run: the
request it created, the as-of date the run is dated, the cap it was given, who and when.

The downgrade drops both tables.

Revision ID: 0070
Revises: 0069
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("ticker", sa.String(12), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        # The statement clock: the queue runs in the order followed, and two entries
        # followed in one transaction must not share an instant.
        sa.Column(
            "followed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_reason", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint(
            "char_length(btrim(company_name)) > 0", name="watchlist_entry_names_its_company"
        ),
        sa.CheckConstraint("ticker = upper(ticker)", name="watchlist_entry_ticker_is_upper"),
    )
    op.create_index(
        "ix_watchlist_entries_user_id_followed_at",
        "watchlist_entries",
        ["user_id", "followed_at"],
    )

    op.create_table(
        "watchlist_commissions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "entry_id",
            sa.Uuid(),
            sa.ForeignKey("watchlist_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("research_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("cap_gbp", sa.Numeric(12, 2), nullable=False),
        sa.Column("commissioned_by", sa.Text(), nullable=False),
        sa.Column(
            "commissioned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("cap_gbp > 0", name="watchlist_commission_cap_is_positive"),
    )
    op.create_index("ix_watchlist_commissions_entry_id", "watchlist_commissions", ["entry_id"])
    op.create_index("ix_watchlist_commissions_request_id", "watchlist_commissions", ["request_id"])


def downgrade() -> None:
    op.drop_table("watchlist_commissions")
    op.drop_table("watchlist_entries")
