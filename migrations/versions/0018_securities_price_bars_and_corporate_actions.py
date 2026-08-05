"""Listings, raw end-of-day bars, and the actions that restate them.

**The raw series is stored and the adjusted series is computed.** A closing price is a fact
that never changes; an *adjusted* close changes retroactively every time the company splits
or pays a dividend, for the whole history at once. A table of adjusted prices is a table
whose contents rewrite themselves with nothing in it able to say why — so ``price_bars``
holds what the exchange printed, ``corporate_actions`` holds the events with the ex-date that
decides which bars each one touches, and the adjustment is a recorded calculation over the
two.

The point-in-time clamp falls out of the same shape rather than needing its own machinery: a
valuation dated to June applies only actions whose ex-date had arrived by June, because a
split announced in September had not happened. `docs/PLAN.md` §"Prices use bars with
``date <= as_of_date`` only" is then one predicate rather than a convention.

``securities`` is separate from ``companies`` because one company can have several listings —
a dual listing, an ADR, two share classes — trading at different prices in different
currencies. ``company_id`` is nullable so a peer's price series can exist before, or without,
that peer being resolved against a registry.

``quote_currency`` is what the *prices* are in, which is not always what the company reports
in: a London listing quotes in pence, so Barclays at 250 means £2.50. That convention is
dimensionless in exactly the way a percentage was in ADR 0027, and recording it here is what
lets the conversion be a single traced calculation rather than a division somebody remembers.

The uniqueness on ``(security_id, bar_date)`` means a vendor correcting a historical bar
*collides* rather than silently overwriting, which routes the correction into the
disagreement ladder instead of changing a number nobody was told about.

Everything here is acquired from a licensed feed. The rows are ordinary; the payloads they
were parsed from are purgeable under ADR 0031, and ``source_document_id`` is what still
answers "where did this come from?" once the bytes are gone.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    corporate_action_kind = postgresql.ENUM(
        "split", "dividend", name="corporate_action_kind", create_type=False
    )
    corporate_action_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "securities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("provider_symbol", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # SET NULL rather than CASCADE: deleting a company must not take the price history
        # with it, because a report already written cited those prices.
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="SET NULL",
            name="fk_securities_company",
        ),
        sa.UniqueConstraint("provider_symbol", name="uq_securities_provider_symbol"),
        sa.UniqueConstraint("ticker", "exchange", name="uq_securities_listing"),
        # Unprefixed: the naming convention prepends `ck_securities_`, and repeating the table
        # word costs characters against PostgreSQL's 63-character identifier limit.
        sa.CheckConstraint("char_length(ticker) > 0", name="has_a_ticker"),
        sa.CheckConstraint(
            "quote_currency = upper(quote_currency)", name="quote_currency_is_upper"
        ),
    )
    op.create_index("ix_securities_company_id", "securities", ["company_id"])

    op.create_table(
        "price_bars",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(20, 6), nullable=False),
        sa.Column("high", sa.Numeric(20, 6), nullable=False),
        sa.Column("low", sa.Numeric(20, 6), nullable=False),
        sa.Column("close", sa.Numeric(20, 6), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(20, 6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            ondelete="CASCADE",
            name="fk_price_bars_security",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            ondelete="SET NULL",
            name="fk_price_bars_source_document",
        ),
        sa.UniqueConstraint("security_id", "bar_date", name="uq_price_bars_day"),
        # A bar that fails these is not a bar. They are cheap, they are checked by the
        # database rather than by whichever parser wrote the row, and each of them has been a
        # real vendor bug somewhere.
        sa.CheckConstraint("high >= low", name="high_is_not_below_low"),
        sa.CheckConstraint("high >= open AND high >= close", name="high_is_the_highest"),
        sa.CheckConstraint("low <= open AND low <= close", name="low_is_the_lowest"),
        # All four, not just the traded ends: `open > 0` does not imply `low > 0`, and a nil
        # low is an infinite return the first time something divides into it.
        sa.CheckConstraint(
            "open > 0 AND high > 0 AND low > 0 AND close > 0", name="prices_are_positive"
        ),
        sa.CheckConstraint("volume IS NULL OR volume >= 0", name="volume_is_not_negative"),
    )
    # Every read is "this security, every bar up to a cutoff", so the index is on the pair in
    # that order. An index on the date alone would scan every listing ever stored.
    op.create_index("ix_price_bars_security_date", "price_bars", ["security_id", "bar_date"])

    op.create_table(
        "corporate_actions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", corporate_action_kind, nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("pay_date", sa.Date(), nullable=True),
        sa.Column("split_ratio", sa.Numeric(20, 10), nullable=True),
        sa.Column("dividend_amount", sa.Numeric(38, 12), nullable=True),
        sa.Column("dividend_currency", sa.String(length=3), nullable=True),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            ondelete="CASCADE",
            name="fk_corporate_actions_security",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            ondelete="SET NULL",
            name="fk_corporate_actions_source_document",
        ),
        # A row that is neither cleanly a split nor cleanly a dividend would adjust a price
        # series by an amount nobody can name.
        sa.CheckConstraint(
            "(kind = 'split' AND split_ratio IS NOT NULL AND dividend_amount IS NULL)"
            " OR (kind = 'dividend' AND dividend_amount IS NOT NULL AND split_ratio IS NULL)",
            name="matches_its_kind",
        ),
        sa.CheckConstraint("split_ratio IS NULL OR split_ratio > 0", name="split_is_positive"),
        sa.CheckConstraint(
            "dividend_amount IS NULL OR dividend_amount > 0",
            name="dividend_is_positive",
        ),
        sa.CheckConstraint(
            "dividend_amount IS NULL OR dividend_currency IS NOT NULL",
            name="dividend_states_its_currency",
        ),
    )
    op.create_index(
        "ix_corporate_actions_security_ex_date", "corporate_actions", ["security_id", "ex_date"]
    )

    # **Two partial uniques rather than one whole one, because the two kinds differ.** A
    # security cannot split twice on one ex-date -- that is arithmetically one split -- so the
    # pair is unique for splits. Dividends are not: an ordinary and a special dividend sharing
    # an ex-date is ordinary, so the amount is part of their identity. A single constraint
    # over (security, kind, ex_date) would have rejected a real pair of dividends, and one
    # over the amount alone would have let a duplicated split through.
    op.execute(
        "CREATE UNIQUE INDEX uq_corporate_actions_split "
        "ON corporate_actions (security_id, ex_date) WHERE kind = 'split'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_corporate_actions_dividend "
        "ON corporate_actions (security_id, ex_date, dividend_amount) WHERE kind = 'dividend'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_corporate_actions_dividend")
    op.execute("DROP INDEX IF EXISTS uq_corporate_actions_split")
    op.drop_index("ix_corporate_actions_security_ex_date", table_name="corporate_actions")
    op.drop_table("corporate_actions")

    op.drop_index("ix_price_bars_security_date", table_name="price_bars")
    op.drop_table("price_bars")

    op.drop_index("ix_securities_company_id", table_name="securities")
    op.drop_table("securities")

    postgresql.ENUM(name="corporate_action_kind").drop(op.get_bind(), checkfirst=True)
