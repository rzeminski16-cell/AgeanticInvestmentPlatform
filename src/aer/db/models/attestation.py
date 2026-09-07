"""What the operator's own book says, at two times and one grade of evidence.

The fourth record class (ADR 0073). A filing is something somebody published, an assumption
is something somebody chose and justified, a calculation is arithmetic over those — and a
fill price is none of the three. Nobody filed it: it exists on a contract note addressed to
one person. Nobody chose it: the market did, and the operator found out afterwards. No code
computed it. The same is true of a holding, a cash balance and a cost basis, which are the
four numbers every portfolio figure eventually rests on.

**Two times, and neither is derivable from the other.** ``effective_at`` is when the thing
was true of the book; ``recorded_at`` is when the platform was told. A trade dealt on Monday
and entered on Thursday is one row with two different dates, and a portfolio shown as at
Tuesday must include it while a record of *what was known* on Tuesday must not. ADR 0075
argues why the portfolio clock is not the research clock; this table only has to store both.

**The grade is a column, not a rendering.** ``documented`` means extracted from a hashed
``USER_SUPPLIED`` artefact — a contract note, a custodian statement — with the full
artefact → extraction → locator → citation chain applying unchanged, because everything
below the locator is subject-agnostic and does not care whether the bytes came from a 20-F
or a broker's PDF. ``attested`` means typed and self-certified. A check constraint ties the
two together: a documented row must name its document and an attested one must not, so the
grade cannot drift away from the evidence behind it.

What the grade *does* is :mod:`aer.calc.attestation`'s business, and it is worth stating
here too: it propagates up every lineage it enters, and a figure standing on an attested
node reaches a shareable surface as a type with no field for the figure. Not a flag — a
missing field, because nobody argues with a number that is not in the object they were
handed.

**A correction is a new row.** These are immutable. A mis-keyed quantity is superseded,
never updated: a net asset value was computed from it, a monitor may have read it, and an
``UPDATE`` would silently rewrite an input to arithmetic that has already happened. The
superseded row is also the interesting one — "I entered 1,000 and meant 100" is precisely
what a post-trade review exists to see (ADR 0081), and an update erases it.

**The check constraints below are documentation; the migration is the enforcement.** The
test schema is built by running the real Alembic path, and autogenerate does not compare
CHECK constraints at all — so a model whose checks disagreed with the migration's would
produce no drift and no failure. `migrations/versions/0053` decides what the database
refuses.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import AttestationKind, Grade, TransactionKind
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.judgement import Decision
    from aer.db.models.security import Security

__all__ = ["Attestation", "Transaction"]


def _enum(kind: type, name: str) -> SaEnum:
    return SaEnum(kind, name=name, values_callable=lambda e: [m.value for m in e])


class Attestation(Base):
    """One thing the operator asserts about their own affairs."""

    __tablename__ = "attestations"

    id: Mapped[UuidPk]

    # Which subtype this is, and therefore which detail table holds its numbers. One value
    # today; see `AttestationKind` on why adding one is a schema change rather than a
    # string.
    kind: Mapped[AttestationKind] = mapped_column(
        _enum(AttestationKind, "attestation_kind"), nullable=False
    )

    grade: Mapped[Grade] = mapped_column(_enum(Grade, "attestation_grade"), nullable=False)

    # When it was true of the book, and when the platform was told. See the module
    # docstring: two clocks, and a row that conflated them could not be read as at a date.
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[Timestamp] = created_at_column()

    # The hashed artefact this was extracted from, for a documented row, and NULL for a
    # typed one. `RESTRICT` because there is no valid state in which a documented
    # attestation outlives the document that made it documented — unlike a rate, which is
    # platform-wide and outlives the request that fetched it (ADR 0084), this document is
    # the operator's own and lives under the same book.
    source_document_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT")
    )

    # Who is asserting this. Their name on the assertion, which is the difference between an
    # attestation and an anonymous number — and, as on `assumption_proposals.proposed_by`,
    # it records origin rather than granting authority.
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)

    # Why, where there is a why. Optional on purpose, and this is the line ADR 0073 draws
    # against `assumptions`: that table's `justification` is NOT NULL because an assumption
    # without a stated reason is a guess wearing a label. A fill price has no justification,
    # because nobody reasoned their way to it.
    note: Mapped[str | None] = mapped_column(Text)

    # Which row this one corrects. Unique, so a row is superseded at most once: two
    # corrections of one entry would fork the history and "the current value" would depend
    # on which branch a reader followed.
    supersedes_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("attestations.id", ondelete="RESTRICT")
    )

    transaction: Mapped[Transaction | None] = relationship(
        back_populates="attestation", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        # The grade and the evidence cannot disagree. A documented row with no document is
        # a typed figure wearing a filing's guarantee, and an attested row *with* one is a
        # document nobody bothered to read — both are worth refusing rather than storing.
        CheckConstraint(
            "(grade = 'documented') = (source_document_id IS NOT NULL)",
            name="attestation_grade_matches_its_evidence",
        ),
        CheckConstraint("id <> supersedes_id", name="attestation_does_not_supersede_itself"),
        UniqueConstraint("supersedes_id", name="uq_attestations_supersedes_once"),
        CheckConstraint("char_length(btrim(recorded_by)) > 0", name="attestor_is_not_blank"),
        Index("ix_attestations_effective_at", "effective_at"),
        Index("ix_attestations_source_document_id", "source_document_id"),
    )

    @property
    def is_documented(self) -> bool:
        return self.grade is Grade.DOCUMENTED

    def __repr__(self) -> str:
        return f"<Attestation {self.kind.value} {self.grade.value} at {self.effective_at}>"


class Transaction(Base):
    """One thing that happened to the book, as the operator reports it.

    **The quantity is signed, and the sign is checked against the kind.** A sell entered as
    a positive number would *add* shares to a holding, and the resulting position would look
    entirely ordinary — so the database refuses it rather than leaving the negation to
    whichever form happened to be used.

    **What the quantity counts depends on the kind, and there are three answers.** A buy of
    100 shares is 100 units of that security at a price per unit. A dividend of £50 is 50
    units of GBP with no price at all, because cash has no price in its own currency. And a
    split's quantity is the *ratio* the share count is multiplied by (ADR 0094) — derived
    from the corporate action it points at, never typed, and read by the book's walk as a
    multiplication rather than an addition. The price is present exactly when the
    transaction is a buy or a sell.

    Nothing here is a position. A holding, a cost basis, a cash balance and a net asset
    value are all recorded calculations over these rows as at a date, and there is no
    ``positions`` table for them to be written into (ADR 0083).
    """

    __tablename__ = "transactions"

    # Shared primary key: a transaction *is* an attestation, seen from its subtype. A
    # separate id with a unique foreign key would allow a transaction with no attestation
    # above it, which is a trade with no assertor, no grade and no two times.
    #
    # `UuidFk` rather than `UuidPk`, even though this is the primary key. The primary-key
    # alias carries `server_default=gen_random_uuid()`, and on a foreign key that means an
    # INSERT omitting the parent silently invents one instead of failing — the exact defect
    # `aer/db/types.py` keeps the two aliases apart for. The drift check caught it here.
    attestation_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("attestations.id", ondelete="CASCADE"), primary_key=True
    )

    portfolio_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[TransactionKind] = mapped_column(
        _enum(TransactionKind, "transaction_kind"), nullable=False
    )

    # The listing dealt in, where there is one. `RESTRICT`, because deleting a security out
    # from under a trade would leave a holding of something nameless.
    security_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("securities.id", ondelete="RESTRICT")
    )

    # The corporate action a derived row derives from — carried by splits, and only by
    # splits (ADR 0094). "Never as a quantity that changed with nothing behind it", made
    # structural: the provenance chain runs row → action → the action's hashed vendor
    # artefact. `RESTRICT`, because a derived row's source is not deletable out from
    # under it.
    corporate_action_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("corporate_actions.id", ondelete="RESTRICT")
    )

    # When it was dealt, and when it settled. The first is what a position as at a date is
    # computed to; the second is what a cash balance an operator can actually spend is.
    # Settlement is nullable because a dividend advice often does not state one.
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    settlement_date: Mapped[date | None] = mapped_column(Date)

    # Units of the security, or units of `currency` where there is none. Signed: positive is
    # into the book, negative is out of it.
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)

    # Per unit, in `currency`, and only for a buy or a sell.
    price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))

    # Commission, stamp duty, custody — whatever was charged as part of this event, as a
    # positive number. A fee charged on its own is a `FEE` transaction; this is the one
    # bundled into a deal.
    #
    # **It reaches two figures and not one.** Money spent to acquire shares is money the
    # shares cost, so a purchase's fee joins the cost pool — and it is cash out either way,
    # so it reduces the balance whichever side of the deal it was charged on. A *disposal's*
    # fee reduces cash and leaves the pool alone (ADR 0085): folding it in would inflate the
    # cost basis of what is still held by the cost of selling something else.
    fees: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, server_default=text("0"))

    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # The decision this trade carried out, where the operator said so (ADR 0104). On the
    # trade and pointing at the judgement, never the other way round: a judgement may not
    # enter a lineage (ADR 0074), and what a position's arithmetic reads off this row is
    # the quantity, the price and the fees — never this column. SET NULL, because a trade
    # is a fact about the book whatever became of the reasoning behind it.
    decision_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("decisions.judgement_id", ondelete="SET NULL")
    )

    attestation: Mapped[Attestation] = relationship(back_populates="transaction")
    security: Mapped[Security | None] = relationship()
    decision: Mapped[Decision | None] = relationship(back_populates="transactions")

    __table_args__ = (
        # Into the book or out of it, and never neither. A transaction that moves nothing is
        # a mis-entry, and summing it changes no answer while looking like a record.
        CheckConstraint("quantity <> 0", name="transaction_moves_something"),
        # The sign follows from the kind, so a sell cannot be entered as an addition. A
        # split is positive whichever way it points: 0.1 is a consolidation, not a
        # negative quantity (ADR 0094).
        CheckConstraint(
            "(kind IN ('buy', 'dividend', 'deposit', 'split')) = (quantity > 0)",
            name="transaction_sign_matches_its_kind",
        ),
        # A ratio of one moves nothing, and `transaction_moves_something` cannot see that:
        # a ratio of one is not a quantity of zero.
        CheckConstraint(
            "kind <> 'split' OR quantity <> 1",
            name="transaction_split_multiplies",
        ),
        # A multiplication needs a subject. Nothing else forces a non-priced row to name a
        # security.
        CheckConstraint(
            "kind <> 'split' OR security_id IS NOT NULL",
            name="transaction_split_names_its_security",
        ),
        # Every split points at the action behind it, and nothing else may. A split the
        # vendor has not recorded is not enterable by hand (ADR 0094).
        CheckConstraint(
            "(kind = 'split') = (corporate_action_id IS NOT NULL)",
            name="transaction_split_derives_from_an_action",
        ),
        # Buys and sells are priced; nothing else is. Cash has no price in its own currency,
        # and a dividend with a "price" would be a number nothing could interpret.
        CheckConstraint(
            "(kind IN ('buy', 'sell')) = (price IS NOT NULL)",
            name="transaction_price_is_for_dealing_only",
        ),
        CheckConstraint("price IS NULL OR price > 0", name="transaction_price_is_positive"),
        # A price needs something priced. Together with the rule above this makes a buy or a
        # sell name its security, while leaving a dividend free to name the holding it came
        # from without pretending to a price.
        CheckConstraint(
            "price IS NULL OR security_id IS NOT NULL", name="transaction_price_needs_a_security"
        ),
        CheckConstraint("fees >= 0", name="transaction_fees_are_not_negative"),
        CheckConstraint(
            "currency = upper(currency) AND char_length(currency) = 3",
            name="transaction_currency_is_an_iso_code",
        ),
        CheckConstraint(
            "settlement_date IS NULL OR settlement_date >= trade_date",
            name="transaction_settles_no_earlier_than_it_deals",
        ),
        # Every read is "this book, everything up to a cutoff", so the index is on the pair
        # in that order.
        Index("ix_transactions_portfolio_trade_date", "portfolio_id", "trade_date"),
        Index("ix_transactions_security_id", "security_id"),
        # One derived row per book per action, held by the database for the reason
        # `uq_source_document_per_artefact` gives: the database is the only participant
        # that sees both writers. Partial, in migration 0018's idiom: ordinary trades
        # carry no action and stay out of the index entirely.
        Index(
            "uq_transactions_split_per_action",
            "portfolio_id",
            "corporate_action_id",
            unique=True,
            postgresql_where=text("corporate_action_id IS NOT NULL"),
        ),
        Index("ix_transactions_decision_id", "decision_id"),
    )

    def __repr__(self) -> str:
        return f"<Transaction {self.kind.value} {self.quantity} on {self.trade_date}>"
