"""A company, as identified by the registries that issue identifiers for it.

The row that ties a research request's typed ticker to a real, resolvable entity. Until it
exists, everything downstream is working from a string the operator typed; once it exists,
every fact and every filing hangs off an identifier the publisher itself issued.

**Identifiers are optional individually and required collectively.** A US registrant has a
CIK. A UK company has a Companies House number. A company listed in both places has both,
and a company this platform supports has at least one — an entity with neither cannot be
looked up anywhere, so nothing can be verified about it.

**One listing per row, for now.** A company with a primary listing in London and an ADR in
New York is two securities and one company, and the plan splits those into ``companies``
and ``securities`` in Phase 2. The MVP researches one listing at a time, so the ticker and
exchange live here and the split arrives when there is something that needs it. Adding a
table nobody queries is not free: it has to be migrated, tested and kept correct.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidPk

if TYPE_CHECKING:
    from aer.db.models.financial_fact import FinancialFact

__all__ = ["Company"]


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[UuidPk]

    # -- Identity ------------------------------------------------------------------------

    name: Mapped[str] = mapped_column(Text, nullable=False)

    # EDGAR's Central Index Key, zero-padded to ten characters. Stored as text rather than
    # an integer precisely because of that padding: "0000789019" and 789019 are the same
    # number and only one of them is a working URL.
    cik: Mapped[str | None] = mapped_column(String(10), unique=True)

    # Companies House registration number: eight characters, and not always numeric --
    # Scottish companies are "SC######" and limited partnerships "LP######". Reserved for
    # the UK adapter; nothing populates it yet.
    company_number: Mapped[str | None] = mapped_column(String(16), unique=True)

    isin: Mapped[str | None] = mapped_column(String(12))

    # -- Listing -------------------------------------------------------------------------

    ticker: Mapped[str] = mapped_column(String(12), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)

    # -- Classification ------------------------------------------------------------------

    # SEC Standard Industrial Classification. Coarse and dated, and still the only sector
    # label that arrives free with the filing index rather than from a licensed taxonomy.
    sic: Mapped[str | None] = mapped_column(String(8))
    sic_description: Mapped[str | None] = mapped_column(Text)

    # "MMDD" as EDGAR reports it, e.g. "0630" for a June year end. Needed to tell a fiscal
    # year from a calendar one, which is the difference between comparing like with like
    # and comparing a retailer's Christmas with a bank's.
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4))

    created_at: Mapped[Timestamp] = created_at_column()

    facts: Mapped[list[FinancialFact]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ticker", "exchange", name="uq_companies_listing"),
        # At least one registry identifier. A company with neither cannot be resolved
        # against any authority, so nothing about it could be verified -- which makes it
        # exactly the kind of row that should not be creatable.
        CheckConstraint(
            "cik IS NOT NULL OR company_number IS NOT NULL",
            name="has_a_registry_identifier",
        ),
        # Spelled out rather than relying on `char_length(NULL) = 10` evaluating to NULL
        # and so passing. It does, but a reader should not have to know that to see that
        # the column is still nullable.
        CheckConstraint("cik IS NULL OR char_length(cik) = 10", name="cik_is_zero_padded"),
        CheckConstraint("isin IS NULL OR char_length(isin) = 12", name="isin_is_iso6166_length"),
        CheckConstraint(
            "fiscal_year_end IS NULL OR char_length(fiscal_year_end) = 4",
            name="fiscal_year_end_is_mmdd",
        ),
        Index("ix_companies_name", "name"),
    )

    def __repr__(self) -> str:
        return f"<Company {self.ticker}.{self.exchange} cik={self.cik} {self.name!r}>"
