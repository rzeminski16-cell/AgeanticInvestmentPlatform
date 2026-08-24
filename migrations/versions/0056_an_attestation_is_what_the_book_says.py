"""An attestation is what the book says, at two times and one grade of evidence.

ADR 0073, and the first tables in this platform that hold data about the operator rather
than about a public company.

Three tables and three enum types.

``portfolios`` exists from the first day with one row in it. Separating an ISA from a SIPP
is the first thing anybody asks for, and whether that is a setting or a migration is decided
by whether this table is here before there is data in it.

``attestations`` is the supertype: two times, a grade, an assertor, and a supersession link.
A check ties the grade to the evidence — a ``documented`` row must name its source document
and an ``attested`` one must not — so the two cannot drift apart. ``supersedes_id`` is
UNIQUE, so a row is corrected at most once and the history cannot fork.

``transactions`` is the one subtype, keyed on the attestation's own id rather than carrying
its own: a transaction *is* an attestation seen from below, and a separate key would allow
a trade with no assertor, no grade and no two times. Its checks are where the arithmetic is
protected — a signed quantity whose sign follows from the kind, so a sell entered as a
positive number is refused rather than quietly adding shares.

**No ``positions`` table, and there will not be one** (ADR 0083). A holding, a cost basis, a
cash balance and a net asset value are recorded calculations over these rows as at a date.

The downgrade drops the three tables and the three types. Nothing existing references them,
so it is a plain drop — unlike ``provider`` in 0052, these types were created here and can
be removed here.

Revision ID: 0056
Revises: 0055
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

_GRADES = ("documented", "attested")
_ATTESTATION_KINDS = ("transaction",)
_TRANSACTION_KINDS = ("buy", "sell", "dividend", "fee", "deposit", "withdrawal")


def upgrade() -> None:
    sa.Enum(*_GRADES, name="attestation_grade").create(op.get_bind(), checkfirst=True)
    sa.Enum(*_ATTESTATION_KINDS, name="attestation_kind").create(op.get_bind(), checkfirst=True)
    sa.Enum(*_TRANSACTION_KINDS, name="transaction_kind").create(op.get_bind(), checkfirst=True)

    # `create_type=False`: the types were created above, and a column definition inside
    # CREATE TABLE would otherwise try to create each a second time.
    grade = postgresql.ENUM(*_GRADES, name="attestation_grade", create_type=False)
    attestation_kind = postgresql.ENUM(
        *_ATTESTATION_KINDS, name="attestation_kind", create_type=False
    )
    transaction_kind = postgresql.ENUM(
        *_TRANSACTION_KINDS, name="transaction_kind", create_type=False
    )

    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "name", name="uq_portfolios_name_per_user"),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="portfolio_name_is_not_blank"),
        sa.CheckConstraint(
            "base_currency = upper(base_currency) AND char_length(base_currency) = 3",
            name="portfolio_base_currency_is_an_iso_code",
        ),
    )
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])

    op.create_table(
        "attestations",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("kind", attestation_kind, nullable=False),
        sa.Column("grade", grade, nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "source_document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("recorded_by", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("attestations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(grade = 'documented') = (source_document_id IS NOT NULL)",
            name="attestation_grade_matches_its_evidence",
        ),
        sa.CheckConstraint("id <> supersedes_id", name="attestation_does_not_supersede_itself"),
        sa.UniqueConstraint("supersedes_id", name="uq_attestations_supersedes_once"),
        sa.CheckConstraint("char_length(btrim(recorded_by)) > 0", name="attestor_is_not_blank"),
    )
    op.create_index("ix_attestations_effective_at", "attestations", ["effective_at"])
    op.create_index("ix_attestations_source_document_id", "attestations", ["source_document_id"])

    op.create_table(
        "transactions",
        sa.Column(
            "attestation_id",
            sa.Uuid(),
            sa.ForeignKey("attestations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "portfolio_id",
            sa.Uuid(),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", transaction_kind, nullable=False),
        sa.Column(
            "security_id",
            sa.Uuid(),
            sa.ForeignKey("securities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("price", sa.Numeric(38, 12), nullable=True),
        sa.Column("fees", sa.Numeric(38, 12), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.CheckConstraint("quantity <> 0", name="transaction_moves_something"),
        sa.CheckConstraint(
            "(kind IN ('buy', 'dividend', 'deposit')) = (quantity > 0)",
            name="transaction_sign_matches_its_kind",
        ),
        sa.CheckConstraint(
            "(kind IN ('buy', 'sell')) = (price IS NOT NULL)",
            name="transaction_price_is_for_dealing_only",
        ),
        sa.CheckConstraint("price IS NULL OR price > 0", name="transaction_price_is_positive"),
        sa.CheckConstraint(
            "price IS NULL OR security_id IS NOT NULL", name="transaction_price_needs_a_security"
        ),
        sa.CheckConstraint("fees >= 0", name="transaction_fees_are_not_negative"),
        sa.CheckConstraint(
            "currency = upper(currency) AND char_length(currency) = 3",
            name="transaction_currency_is_an_iso_code",
        ),
        sa.CheckConstraint(
            "settlement_date IS NULL OR settlement_date >= trade_date",
            name="transaction_settles_no_earlier_than_it_deals",
        ),
    )
    op.create_index(
        "ix_transactions_portfolio_trade_date", "transactions", ["portfolio_id", "trade_date"]
    )
    op.create_index("ix_transactions_security_id", "transactions", ["security_id"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("attestations")
    op.drop_table("portfolios")
    sa.Enum(name="transaction_kind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="attestation_kind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="attestation_grade").drop(op.get_bind(), checkfirst=True)
