"""What the operator follows, and each time the queue turned following into research.

ADR 0107. Two clocks, on two tables. An entry is a standing intention: a listing, why it is
worth watching, and when the platform came to know the operator follows it — `followed_at`
on the database clock, because nothing outside can be trusted to say so (ADR 0075). A
commission is a dated run: the research request the queue created, the as-of date it is
dated, the cap it was given, and when. Nothing on the entry says "researched"; that word
belongs to a commission's run and its report.

The request a commission points at is an ordinary research request. Deleting it keeps the
commission with its date and loses the link, the way a cost row outlives its job.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, TimestampOptional, UuidFk, UuidFkOptional, UuidPk

__all__ = ["WatchlistCommission", "WatchlistEntry"]


class WatchlistEntry(Base):
    """One listing the operator follows, with the sentence that says why."""

    __tablename__ = "watchlist_entries"

    id: Mapped[UuidPk]

    user_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str] = mapped_column(String(12), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)

    # What would make it worth researching. A sentence, and the reason the queue exists
    # rather than a list of tickers.
    why: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # The statement clock, not the transaction's: the queue runs in the order followed, and
    # `now()` would give two entries followed in one transaction the same instant.
    followed_at: Mapped[Timestamp] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    withdrawn_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))
    withdrawn_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    commissions: Mapped[list[WatchlistCommission]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="WatchlistCommission.commissioned_at",
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(company_name)) > 0", name="watchlist_entry_names_its_company"
        ),
        CheckConstraint("ticker = upper(ticker)", name="watchlist_entry_ticker_is_upper"),
        Index("ix_watchlist_entries_user_id_followed_at", "user_id", "followed_at"),
    )

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    @property
    def listing(self) -> str:
        return f"{self.ticker}.{self.exchange}"

    def __repr__(self) -> str:
        return f"<WatchlistEntry {self.listing}>"


class WatchlistCommission(Base):
    """One time the queue turned an entry into a dated research run."""

    __tablename__ = "watchlist_commissions"

    id: Mapped[UuidPk]

    entry_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("watchlist_entries.id", ondelete="CASCADE"), nullable=False
    )

    # The research request the commission created, and ADR 0072's run root through it.
    # Nullable and set null on delete: the commission is what the queue did, and it stays
    # said after the research it started is gone.
    request_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("research_requests.id", ondelete="SET NULL")
    )

    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    # The per-run cap the request was given, kept here because the standing budget reserves
    # it while the run is alive and the request's own cap may be raised later by hand.
    cap_gbp: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    commissioned_by: Mapped[str] = mapped_column(Text, nullable=False)
    commissioned_at: Mapped[Timestamp] = created_at_column()

    entry: Mapped[WatchlistEntry] = relationship(back_populates="commissions")

    __table_args__ = (
        CheckConstraint("cap_gbp > 0", name="watchlist_commission_cap_is_positive"),
        Index("ix_watchlist_commissions_entry_id", "entry_id"),
        Index("ix_watchlist_commissions_request_id", "request_id"),
    )

    def __repr__(self) -> str:
        return f"<WatchlistCommission {self.entry_id} as at {self.as_of_date}>"
