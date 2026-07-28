"""One section of one report: what was actually produced.

A :class:`~aer.db.models.section_definition.SectionDefinition` says what a section *is*;
this row says what a particular run *made* of it. Separating the two is what lets a
definition be versioned and reused while each run keeps its own content, its own
confidence, and its own record of having been skipped.

``position`` is copied from the definition rather than joined at render time. That is
deliberate duplication: the order a report was rendered in is a property of the report, and
a definition whose position changes later must not silently reorder a report that has
already been approved.

**``status`` distinguishes three kinds of absence.** A section that failed, a section not
yet generated, and a section that did not apply are very different things, and a reader of
the report deserves to know which. Collapsing them into "no content" is how a report ends
up silently missing an analysis nobody noticed was meant to be there.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.section_definition import SectionDefinition

__all__ = ["ReportSection", "SectionStatus"]


class SectionStatus(StrEnum):
    """What happened to this section in this run."""

    PENDING = "pending"
    """Not generated yet."""

    GENERATED = "generated"
    """Content produced and validated against the contract."""

    FAILED = "failed"
    """Generation was attempted and did not succeed. Distinct from pending: a reader
    needs to know the difference between "not yet" and "tried and could not"."""

    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
    """The section's applicability predicate excluded it — a bank has no inventory
    turnover. Recorded rather than omitted, so the absence is explained."""


class ReportSection(Base):
    __tablename__ = "report_sections"

    id: Mapped[UuidPk]

    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    # RESTRICT: a definition a report was rendered against must not be deletable while the
    # report stands, or the report loses the record of what it was asked to produce.
    section_definition_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("section_definitions.id", ondelete="RESTRICT"), nullable=False
    )

    # Denormalised from the definition so a report can be listed and ordered without a
    # join, and -- more importantly -- so a later change to a definition cannot reorder or
    # rename a section in a report that has already been approved.
    section_key: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    status: Mapped[SectionStatus] = mapped_column(
        SaEnum(
            SectionStatus,
            name="section_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SectionStatus.PENDING,
        server_default=SectionStatus.PENDING.value,
    )

    # Structured, validated against the definition's output_contract. JSONB rather than
    # rendered text because the renderer works from the structure -- and because a
    # validator asking "does every numeric claim resolve to a calculation?" needs fields,
    # not prose.
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    confidence: Mapped[float | None] = mapped_column(Float)
    low_confidence_reason: Mapped[str | None] = mapped_column(Text)

    token_cost: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))

    created_at: Mapped[Timestamp] = created_at_column()

    definition: Mapped[SectionDefinition] = relationship()

    __table_args__ = (
        UniqueConstraint("job_id", "section_key", name="uq_report_sections_key_per_job"),
        # Generated means there is content. Without this, a section could report success
        # while rendering as nothing, and the report would be quietly short.
        CheckConstraint(
            "(status <> 'generated') OR (content IS NOT NULL)",
            name="generated_sections_have_content",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_is_a_probability",
        ),
        CheckConstraint("token_cost >= 0", name="token_cost_is_not_negative"),
        # The query the renderer makes, and the only order a report is ever assembled in.
        Index("ix_report_sections_job_id_position", "job_id", "position"),
    )

    def __repr__(self) -> str:
        return f"<ReportSection {self.section_key} pos={self.position} {self.status.value}>"
