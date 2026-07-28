"""A reported number, and everything needed to defend it.

This is where the platform's central claim becomes a table. Every figure that reaches a
report is either a row here or a calculation whose inputs are rows here, and each row
carries the four things that make a number defensible: what it measures, in what unit,
over what period, and **on what date it was said**.

**``filed_date`` is not metadata.** It is part of the identity of the fact. Two rows can
report different values for the same company's same period in the same unit and both be
correct, because they were filed two years apart and the later one is a restatement. The
uniqueness constraint therefore includes it: collapsing them would silently destroy the
point-in-time record, which is the one thing this schema exists to preserve.

**``source_document_id`` is not nullable.** A fact with no provenance is a number somebody
typed. The chain is fact → source document → artefact → SHA-256, and it is unbroken by
construction rather than by convention.

**On the extraction layer.** ``docs/PLAN.md`` places an ``extractions`` table between the
source document and the fact, recording which extractor produced it, at which version, and
the verbatim excerpt the citation verifier re-reads. That table belongs to the extraction
task and is not built here; this model links straight to the source document, and carries
``accession`` and ``raw_concept`` so the locator is reconstructible in the meantime. See
``docs/adr/0010-facts-cite-source-documents-until-extractions-exist.md``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import FactBasis
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.company import Company
    from aer.db.models.source_document import SourceDocument

__all__ = ["FinancialFact"]


def _enum(python_enum: type, name: str) -> SaEnum:
    return SaEnum(python_enum, name=name, values_callable=lambda e: [m.value for m in e])


class FinancialFact(Base):
    __tablename__ = "financial_facts"

    id: Mapped[UuidPk]

    company_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    # RESTRICT, not CASCADE. Deleting the document a fact came from would leave the fact
    # standing with nothing behind it, which is precisely the state this schema is built
    # to make impossible. Removing evidence has to mean removing what rests on it.
    source_document_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )

    # -- What it measures ----------------------------------------------------------------

    # The canonical concept where the filer's tag mapped onto one, otherwise the tag
    # itself. Comparisons across companies use this; comparisons need the raw tag too,
    # which is why both are kept.
    concept: Mapped[str] = mapped_column(Text, nullable=False)
    raw_concept: Mapped[str | None] = mapped_column(Text)
    taxonomy: Mapped[str | None] = mapped_column(String(32))

    value: Mapped[Decimal] = mapped_column(Numeric(38, 6), nullable=False)

    # "USD", "shares", "USD/shares", "pure". Never dropped and never assumed: the same
    # numeral is eleven orders of magnitude apart depending on which of these it is.
    unit: Mapped[str] = mapped_column(String(32), nullable=False)

    # Power of ten already applied to `value`. Zero throughout for EDGAR, which reports
    # absolute figures; a source reporting "in millions" would set 6 rather than quietly
    # multiplying, so the original presentation stays recoverable.
    scale: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))

    # -- When it applies -----------------------------------------------------------------

    # NULL for an instant measure. A balance sheet line is a fact about a moment, and
    # giving it a start date would make a stock look like a flow.
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    fiscal_year: Mapped[int | None] = mapped_column()
    fiscal_period: Mapped[str | None] = mapped_column(String(8))

    # -- When it was said ----------------------------------------------------------------

    # The point-in-time key. Everything in this schema that prevents look-ahead bias comes
    # back to comparing this against a request's as-of date.
    filed_date: Mapped[date] = mapped_column(Date, nullable=False)

    form: Mapped[str | None] = mapped_column(String(16))
    accession: Mapped[str | None] = mapped_column(String(20))

    basis: Mapped[FactBasis] = mapped_column(
        _enum(FactBasis, "fact_basis"),
        nullable=False,
        default=FactBasis.AS_REPORTED,
        server_default=FactBasis.AS_REPORTED.value,
    )

    created_at: Mapped[Timestamp] = created_at_column()

    company: Mapped[Company] = relationship(back_populates="facts")
    source_document: Mapped[SourceDocument] = relationship()

    __table_args__ = (
        # NULLS NOT DISTINCT is load-bearing. `fiscal_period` is nullable, and under the
        # SQL default two NULLs are never equal -- so without this, a fact with no fiscal
        # period could be inserted any number of times and the constraint would permit
        # every copy. Postgres 15 added the modifier; before it, the workaround was a
        # sentinel value, which is a lie stored in a column.
        Index(
            "uq_financial_facts_observation",
            "company_id",
            "concept",
            "unit",
            "period_end",
            "fiscal_period",
            "basis",
            "filed_date",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "period_start IS NULL OR period_start <= period_end",
            name="period_runs_forwards",
        ),
        CheckConstraint("char_length(unit) > 0", name="unit_is_present"),
        CheckConstraint("char_length(concept) > 0", name="concept_is_present"),
        # The working range of a power-of-ten scale. Outside it the figure is not a
        # rescaled number, it is a parsing accident.
        CheckConstraint("scale BETWEEN -12 AND 12", name="scale_is_a_sane_power_of_ten"),
        # The query every analysis makes: this company's history of one concept, most
        # recent period first, most recently filed first within a period.
        Index(
            "ix_financial_facts_company_concept_period",
            "company_id",
            "concept",
            text("period_end DESC"),
            text("filed_date DESC"),
        ),
        Index("ix_financial_facts_source_document_id", "source_document_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinancialFact {self.concept}={self.value} {self.unit} "
            f"{self.period_end} filed={self.filed_date}>"
        )
