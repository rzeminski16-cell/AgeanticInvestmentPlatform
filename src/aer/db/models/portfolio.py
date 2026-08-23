"""A book of holdings. One row today, and a table from the first day for a reason.

Separating an ISA from a SIPP, or a real book from a paper one, is the first thing anybody
asks for after the second week — and the difference between that being a setting and a
migration is whether this table exists before there is data in it. It costs one join now.

**A portfolio is not a research request and does not inherit its clock.** ADR 0071: a
research run is a point and a portfolio is continuous. What a portfolio has instead of an
as-of date is a reader who chooses one, which is why nothing here carries a date at all.

``base_currency`` is what the book is *reported* in, not what it holds. A sterling investor
holding dollars has a GBP base currency and USD positions, and the conversion between them
is a recorded calculation over a dated rate (ADR 0078), never a property of this row.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, TimestampOptional, UuidFk, UuidPk

__all__ = ["Portfolio"]


class Portfolio(Base):
    """One book, belonging to one person."""

    __tablename__ = "portfolios"

    id: Mapped[UuidPk]

    user_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)

    # ISO 4217, and never `GBX`. A London listing quoting in pence is a quote convention on
    # the security (ADR 0032); a book is reported in pounds.
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)

    created_at: Mapped[Timestamp] = created_at_column()

    # Put away rather than destroyed, as a research request is. A closed account's history
    # is exactly what the post-trade reviewer of ADR 0077 exists to read.
    archived_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_portfolios_name_per_user"),
        CheckConstraint("char_length(btrim(name)) > 0", name="portfolio_name_is_not_blank"),
        CheckConstraint(
            "base_currency = upper(base_currency) AND char_length(base_currency) = 3",
            name="portfolio_base_currency_is_an_iso_code",
        ),
        Index("ix_portfolios_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<Portfolio {self.name} ({self.base_currency})>"
