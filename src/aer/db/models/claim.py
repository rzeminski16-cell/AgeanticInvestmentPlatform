"""One assertion a report makes, and what it rests on.

A section's prose is not the unit of evidence — a paragraph can contain a figure that traces
to a filing and a sentence beside it that traces to nothing. The claim is the unit, so that
"is this report supported?" is a query rather than a reading exercise.

**A numeric claim names the figure it asserts.** Exactly one of ``financial_fact_id``,
``calculation_id`` and ``attestation_id``, enforced by a check constraint, because invariant
3 says no figure reaches a report unless it is a stored fact, a recorded calculation or an
attestation. Making that a column rather than a convention means a claim asserting a number
nobody computed cannot be written down in the first place.

**The third arm arrived with ADR 0069 and admits no unevidenced figure.** An attestation is
what the operator's own book says — a holding, a fill, a cash balance — and without an arm
of its own a report could never make a numeric claim about a position at all. What it does
*not* do is loosen invariant 1: a ``documented`` attestation traces to a hashed artefact by
the same chain a filing does, and an ``attested`` one reaches no shareable surface, because
the type it propagates into (:class:`aer.calc.attestation.Attested`) has no field for the
figure. Three kinds of figure, not three kinds of evidence.

**Macro is still a seam and this is not it.** A ``macro_observations`` row — a gilt yield, an
index level — is neither a financial fact nor an attestation, so a published statistic still
has no arm here and reaches a report only wrapped in a :class:`~aer.db.models.calculation`.
That wrapper is not a fabrication, but under it the platform cannot state a published
statistic as a numeric claim without inventing arithmetic to hold it. ADR 0069 named the
question and declined to answer it; widening this constraint has not answered it either.

**The citation requirement is not enforced here**, and could not be: "at least one verified
citation" is a fact about another table, which no check constraint can see. It is enforced at
gate 2, in code, before a human is asked to approve anything — see
:mod:`aer.services.citations`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import ClaimKind
from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.citation import Citation
    from aer.db.models.report_section import ReportSection

__all__ = ["Claim"]


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[UuidPk]

    # CASCADE: a claim exists only as part of a section's content. A re-drafted section
    # replaces its claims, and orphaned ones would be counted by every coverage metric.
    report_section_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("report_sections.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[ClaimKind] = mapped_column(
        SaEnum(ClaimKind, name="claim_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    # The sentence as it appears in the report. Stored rather than derived from the section's
    # content, because what was approved at gate 2 must stay legible after the content model
    # for that section changes.
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # RESTRICT on both: the figure a claim asserts cannot be deleted out from under it. That
    # would leave a published number with nothing behind it, which is the state the whole
    # provenance chain exists to make impossible.
    financial_fact_id: Mapped[UuidFk | None] = mapped_column(
        ForeignKey("financial_facts.id", ondelete="RESTRICT"), nullable=True
    )
    calculation_id: Mapped[UuidFk | None] = mapped_column(
        ForeignKey("calculations.id", ondelete="RESTRICT"), nullable=True
    )
    # RESTRICT for the same reason as the other two, and it bites harder here: an
    # attestation is corrected by a *superseding row* rather than an update, so the row a
    # published claim named stays exactly as it was written and cannot be deleted out from
    # under it.
    attestation_id: Mapped[UuidFk | None] = mapped_column(
        ForeignKey("attestations.id", ondelete="RESTRICT"), nullable=True
    )

    created_at: Mapped[Timestamp] = created_at_column()

    section: Mapped[ReportSection] = relationship(back_populates="claims")
    citations: Mapped[list[Citation]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("char_length(text) > 0", name="ck_claims_text_is_present"),
        # A numeric claim asserts exactly one figure; a non-numeric claim asserts none. Both
        # halves matter: without the first, a number could appear with no lineage; without the
        # second, an opinion could carry a fact id that nothing checks and readers would
        # reasonably assume was verified.
        #
        # Three arms since ADR 0069, and still *exactly* one. A claim naming both a fact and
        # an attestation would be a sentence asserting two numbers, and which one a reader
        # was being shown would depend on which column the renderer happened to read.
        CheckConstraint(
            "(kind = 'numeric') = ("
            "  (financial_fact_id IS NOT NULL)::int + (calculation_id IS NOT NULL)::int"
            "  + (attestation_id IS NOT NULL)::int = 1"
            ")",
            name="ck_claims_numeric_claims_name_one_figure",
        ),
        Index("ix_claims_report_section_id", "report_section_id"),
        Index("ix_claims_kind", "kind"),
    )

    def __repr__(self) -> str:
        return f"Claim(id={self.id!r}, kind={self.kind!r}, text={self.text[:40]!r})"
