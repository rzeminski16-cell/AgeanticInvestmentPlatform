"""A limit the operator stated on their book (ADR 0136).

**The operator's policy, written down, and never the platform's.** Three kinds, each a
fraction of the book: one position, the five largest together, and one sector's share. Nothing
proposes a limit — no default, no suggestion, no example value — and no agent's output schema
has these columns: until the operator states one, there is none.

**A limit blocks nothing.** It changes what the operator's own pages say about the book, *over
the 10% ceiling you set*, and never a trade, a decision or a run. The comparison is made in
code over the figures the risk page already strikes, so nothing here is a figure either.

**Superseded, never edited.** Changing a limit writes a new row that supersedes the old one,
and the supersession is unique so a limit's history cannot fork. Withdrawing one records when
and why, both or neither. What the operator allowed themselves when they made a decision is
part of the record a later review of that decision reads.

The CHECK constraints are documentation; the migration is the enforcement, because
autogenerate does not compare them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import LimitKind
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, TimestampOptional, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.portfolio import Portfolio

__all__ = ["BookLimit"]


class BookLimit(Base):
    """One limit on one book, as the operator stated it."""

    __tablename__ = "book_limits"

    id: Mapped[UuidPk]

    portfolio_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )

    # Whose statement this is (ADR 0120 §1): every new table carries its owner, so no
    # service function has to reach a person through the book.
    user_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[LimitKind] = mapped_column(
        SaEnum(LimitKind, name="limit_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    # The sector a sector limit caps, by the exposure band's own label — so the limit is
    # compared with exactly the share the risk page prints for it. Blank for the other kinds.
    sector: Mapped[str | None] = mapped_column(Text)

    # A share of the book: 0.10 is a ten per cent ceiling. Six places, because a limit is a
    # round number somebody typed and the column should not invent precision beyond it.
    fraction: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)

    stated_by: Mapped[str] = mapped_column(Text, nullable=False)
    stated_at: Mapped[Timestamp] = created_at_column()

    # Which limit this one replaces. Unique, so a limit is superseded at most once.
    supersedes_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("book_limits.id", ondelete="RESTRICT")
    )

    withdrawn_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))
    withdrawn_reason: Mapped[str | None] = mapped_column(Text)

    portfolio: Mapped[Portfolio] = relationship()

    __table_args__ = (
        CheckConstraint("fraction > 0 AND fraction <= 1", name="book_limit_is_a_share"),
        CheckConstraint(
            "(kind = 'sector') = (sector IS NOT NULL)", name="book_limit_sector_iff_sector_kind"
        ),
        CheckConstraint(
            "sector IS NULL OR char_length(btrim(sector)) > 0",
            name="book_limit_sector_is_not_blank",
        ),
        CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_reason IS NULL)",
            name="book_limit_withdrawal_has_a_reason",
        ),
        CheckConstraint("id <> supersedes_id", name="book_limit_does_not_supersede_itself"),
        UniqueConstraint("supersedes_id", name="uq_book_limits_supersedes_once"),
        Index("ix_book_limits_portfolio_id", "portfolio_id"),
        Index("ix_book_limits_user_id", "user_id"),
    )

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    def __repr__(self) -> str:
        where = f" {self.sector}" if self.sector else ""
        return f"<BookLimit {self.kind.value}{where} {self.fraction}>"
