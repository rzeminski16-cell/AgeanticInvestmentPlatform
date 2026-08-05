"""Listed instruments, their end-of-day bars, and the actions that restate them.

Three tables and one idea: **the raw series is what was published, and the adjusted series is
a calculation over it.** A closing price is a fact that never changes; the *adjusted* close
changes every time the company splits its stock or pays a dividend, retroactively, for the
whole history. A schema that stored only adjusted prices would be storing a figure that
silently rewrites itself, and nothing in it could say why.

So ``price_bars`` holds what the exchange printed and ``corporate_actions`` holds the events,
each with the ex-date that decides which bars it touches. Task 29's adjustment is a recorded
calculation over the two, and the point-in-time clamp falls out of the same structure: a
valuation dated to June applies only the actions whose ex-date had arrived by June, because a
split announced in September did not exist yet.

**A security is not a company.** One company can have several listings — a dual listing, an
ADR, two share classes with different votes — and they trade at different prices in different
currencies. Prices belong to the listing.

**LSE quotes in pence, and this is the per-cent trap wearing a hat.** A Barclays price of
250 means £2.50, not £250, and the number carries no marker saying which. It is dimensionless
in exactly the way a percentage was in ADR 0027, so the convention is recorded on the security
(``quote_currency`` is ``GBX`` rather than ``GBP``) and the conversion to major units is a
single traced calculation rather than a division somebody remembers.

**Licensed, and therefore erasable.** Everything here is acquired from a paid feed whose
agreement requires deletion after the subscription ends. The bars and actions are ordinary
rows; the *payloads* they were parsed from are purgeable under ADR 0031, and
``source_document_id`` is what still answers "where did this come from?" after the bytes have
gone.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
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

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.company import Company

__all__ = ["CorporateAction", "CorporateActionKind", "PriceBar", "Security"]


class CorporateActionKind(StrEnum):
    """What kind of event restated the price series.

    Two, and the list is deliberately short. A rights issue, a spin-off and a return of
    capital all adjust a price series too, and none of them is modelled — because each needs
    its own arithmetic and a wrong one is worse than an absent one. A run whose company had
    one of those is a run whose adjusted series is incomplete, and task 29 reports that rather
    than guessing.
    """

    SPLIT = "split"
    """A change in the number of shares. Restates every bar before the ex-date."""

    DIVIDEND = "dividend"
    """Cash paid out. Restates the total-return series, not the price series."""


class Security(Base):
    """One listing of one instrument on one exchange."""

    __tablename__ = "securities"

    id: Mapped[UuidPk]

    # Nullable, and that is not laziness. Task 30's peer set contains companies this platform
    # has never researched and may never resolve against a registry; a peer's price series is
    # still worth having, and requiring a `companies` row first would mean either resolving
    # every peer against EDGAR or not having comparables at all.
    company_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )

    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)

    # The vendor's own key — "MSFT.US", "BARC.LSE". Recorded rather than derived from the
    # ticker and exchange, because that mapping is a vendor convention: it is theirs to
    # change, and a rule for reconstructing it would be wrong the first time they did.
    provider_symbol: Mapped[str] = mapped_column(Text, nullable=False)

    name: Mapped[str | None] = mapped_column(Text)

    # What the *prices* are denominated in, which is not always what the company reports in.
    # `GBX` for a London listing quoted in pence — see the module docstring. Stored as given
    # rather than normalised, so a bar always means what the exchange printed.
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)

    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    created_at: Mapped[Timestamp] = created_at_column()

    company: Mapped[Company | None] = relationship(back_populates="securities")
    bars: Mapped[list[PriceBar]] = relationship(back_populates="security")
    actions: Mapped[list[CorporateAction]] = relationship(back_populates="security")

    __table_args__ = (
        # One row per listing. The provider symbol is unique on its own, but the pair is what
        # a caller looks up by and a second row for one listing would make "the price" depend
        # on which row was read.
        UniqueConstraint("provider_symbol", name="uq_securities_provider_symbol"),
        UniqueConstraint("ticker", "exchange", name="uq_securities_listing"),
        CheckConstraint("char_length(ticker) > 0", name="has_a_ticker"),
        CheckConstraint("quote_currency = upper(quote_currency)", name="quote_currency_is_upper"),
        Index("ix_securities_company_id", "company_id"),
    )

    def __repr__(self) -> str:
        return f"<Security {self.provider_symbol}>"


class PriceBar(Base):
    """One trading day, as the exchange printed it.

    **Raw, never adjusted.** The adjusted series is derived from these and the corporate
    actions, as a recorded calculation; storing an adjusted close here would be storing a
    figure that changes retroactively with no record of what changed it.

    ``adjusted_close`` is the *vendor's* own adjusted figure, kept as a cross-check rather
    than as the answer. Where this platform's own adjustment and the vendor's disagree, that
    is a disagreement worth surfacing — and one that cannot be surfaced at all if only one of
    them is stored.
    """

    __tablename__ = "price_bars"

    id: Mapped[UuidPk]

    security_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )

    bar_date: Mapped[date] = mapped_column(Date, nullable=False)

    # NUMERIC rather than float, for the reason everything else here is: a price that rounds
    # is a return that drifts, and a return that drifts is a beta nobody can reproduce.
    open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))

    volume: Mapped[int | None] = mapped_column(BigInteger)

    # The archived response this bar was parsed from. Survives the payload's purge under
    # ADR 0031, so "where did this come from?" is answerable after the bytes are gone even
    # though "show me those bytes" is not.
    source_document_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    security: Mapped[Security] = relationship(back_populates="bars")

    __table_args__ = (
        # One bar per day per listing. A vendor correcting a historical bar therefore
        # *collides* rather than silently overwriting, which routes it into the disagreement
        # ladder instead of changing a number nobody was told about.
        UniqueConstraint("security_id", "bar_date", name="uq_price_bars_day"),
        CheckConstraint("high >= low", name="high_is_not_below_low"),
        CheckConstraint("high >= open AND high >= close", name="high_is_the_highest"),
        CheckConstraint("low <= open AND low <= close", name="low_is_the_lowest"),
        # All four, not just the traded ends. `open > 0` does not imply `low > 0`, and a
        # vendor row with a nil low would otherwise pass every check here and then produce an
        # infinite return the day it was divided into.
        CheckConstraint(
            "open > 0 AND high > 0 AND low > 0 AND close > 0", name="prices_are_positive"
        ),
        CheckConstraint("volume IS NULL OR volume >= 0", name="volume_is_not_negative"),
        # Every read is "this security, every bar up to a cutoff", so the index is on the
        # pair in that order.
        Index("ix_price_bars_security_date", "security_id", "bar_date"),
    )

    def __repr__(self) -> str:
        return f"<PriceBar {self.security_id} {self.bar_date} close={self.close}>"


class CorporateAction(Base):
    """A split or a dividend, dated by the day the price started reflecting it.

    **The ex-date is the one that matters.** Declaration, record and payment dates are
    administrative; the ex-date is when the market price steps, and therefore when an
    adjustment applies. It is also what makes the point-in-time clamp work: an action whose
    ex-date is after the as-of date had not happened yet and must not restate anything.
    """

    __tablename__ = "corporate_actions"

    id: Mapped[UuidPk]

    security_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[CorporateActionKind] = mapped_column(
        SaEnum(
            CorporateActionKind,
            name="corporate_action_kind",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    record_date: Mapped[date | None] = mapped_column(Date)
    pay_date: Mapped[date | None] = mapped_column(Date)

    # The factor the share count is multiplied by: 2 for a two-for-one, 0.1 for a
    # one-for-ten consolidation. Set for a split and null for a dividend, and the check
    # constraint below enforces the pairing — a row that is neither one thing nor the other
    # would adjust a price series by an amount nobody can name.
    split_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))

    dividend_amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))

    # A dividend can be declared in a currency the share is not quoted in — a London listing
    # quoted in pence paying a dollar dividend is ordinary. Stored rather than inferred.
    dividend_currency: Mapped[str | None] = mapped_column(String(3))

    source_document_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    security: Mapped[Security] = relationship(back_populates="actions")

    __table_args__ = (
        # Unprefixed names throughout: the metadata naming convention already prepends
        # `ck_corporate_actions_`, and repeating the table word here pushed one of these past
        # PostgreSQL's 63-character identifier limit, where SQLAlchemy silently truncated it
        # and appended a hash. A constraint nobody can name is a constraint nobody can drop.
        CheckConstraint(
            "(kind = 'split' AND split_ratio IS NOT NULL AND dividend_amount IS NULL)"
            " OR (kind = 'dividend' AND dividend_amount IS NOT NULL AND split_ratio IS NULL)",
            name="matches_its_kind",
        ),
        CheckConstraint("split_ratio IS NULL OR split_ratio > 0", name="split_is_positive"),
        CheckConstraint(
            "dividend_amount IS NULL OR dividend_amount > 0",
            name="dividend_is_positive",
        ),
        CheckConstraint(
            "dividend_amount IS NULL OR dividend_currency IS NOT NULL",
            name="dividend_states_its_currency",
        ),
        Index("ix_corporate_actions_security_ex_date", "security_id", "ex_date"),
        # **Two partial uniques rather than one, because the two kinds differ.** A security
        # cannot split twice on one ex-date — that is arithmetically one split — so the pair
        # is unique for splits. Dividends are not: an ordinary and a special dividend
        # sharing an ex-date is ordinary, so the amount is part of their identity. One
        # constraint over (security, kind, ex_date) would have rejected a real pair of
        # dividends; one over the amount alone would have let a duplicated split through.
        Index(
            "uq_corporate_actions_split",
            "security_id",
            "ex_date",
            unique=True,
            postgresql_where=text("kind = 'split'"),
        ),
        Index(
            "uq_corporate_actions_dividend",
            "security_id",
            "ex_date",
            "dividend_amount",
            unique=True,
            postgresql_where=text("kind = 'dividend'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<CorporateAction {self.kind.value} {self.security_id} ex={self.ex_date}>"
