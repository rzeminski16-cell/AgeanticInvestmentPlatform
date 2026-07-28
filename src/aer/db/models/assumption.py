"""A number chosen rather than observed.

Every valuation rests on assumptions — a discount rate, a terminal growth rate, an FX
rate, a margin that persists. They are not facts and must never be presented as facts, but
they are also not arbitrary: each has a reason, and a reviewer's most useful question about
a valuation is almost always "why that number?".

**A justification is mandatory.** ``NOT NULL`` with a non-empty ``CHECK``. An assumption
without a stated reason is a guess wearing a label, and the moment one is allowed the table
fills with them. Making the field required at the schema level means the reason is written
while the person still knows it, rather than reconstructed months later.

**Assumptions are proposed, then approved.** This is exactly the division of labour the
platform is built around: the language model proposes a value *with* a justification and a
confidence, and a human accepts, amends or rejects it. ``approved`` records which happened.
Nothing enforces approval yet — the gate arrives with the orchestrator — but the column
exists now so that no assumption is ever ambiguous about whether anybody agreed with it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, TimestampOptional, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.request import ResearchRequest

__all__ = ["Assumption"]


class Assumption(Base):
    __tablename__ = "assumptions"

    id: Mapped[UuidPk]

    request_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False
    )

    # Nullable: an assumption can be made while planning, before any job exists. SET NULL
    # rather than CASCADE so the assumption survives the job that used it -- otherwise
    # deleting a failed run would erase the reasoning behind its numbers.
    job_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))

    # -- What is assumed -----------------------------------------------------------------

    name: Mapped[str] = mapped_column(Text, nullable=False)

    value: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)

    # Carried for the same reason it is carried on a fact: a discount rate of 0.09 and a
    # discount rate of 9 differ by two orders of magnitude, and only the unit says which.
    unit: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pure")

    # -- Why ------------------------------------------------------------------------------

    # NOT NULL and non-empty. The whole point of the table.
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    # 0..1. A model proposing a terminal growth rate at 0.4 confidence and one proposing it
    # at 0.9 are making very different claims, and the report should be able to say so.
    confidence: Mapped[float | None] = mapped_column(Float)

    # Which agent role or which human proposed this. Free text rather than an enum: the set
    # of roles will change, and an assumption proposed by a role that no longer exists
    # should still say so rather than failing to load.
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="system")

    # -- Approval --------------------------------------------------------------------------

    approved: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    approved_at: Mapped[TimestampOptional]
    approved_by: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[Timestamp] = created_at_column()

    request: Mapped[ResearchRequest] = relationship()

    __table_args__ = (
        # One value per named assumption per request. Two different discount rates in one
        # valuation is not a disagreement to be averaged, it is a bug.
        UniqueConstraint("request_id", "name", name="uq_assumptions_name_per_request"),
        CheckConstraint(
            "char_length(btrim(justification)) > 0",
            name="justification_is_not_blank",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_is_a_probability",
        ),
        CheckConstraint("char_length(name) > 0", name="name_is_not_blank"),
        # An approval with no approver, or an approver with no approval, is a record
        # nobody can act on. Both or neither.
        CheckConstraint(
            "(approved AND approved_at IS NOT NULL) OR (NOT approved AND approved_at IS NULL)",
            name="approval_has_a_timestamp",
        ),
        Index("ix_assumptions_request_id", "request_id"),
        Index("ix_assumptions_job_id", "job_id"),
    )

    def __repr__(self) -> str:
        return f"<Assumption {self.name}={self.value} {self.unit} approved={self.approved}>"
