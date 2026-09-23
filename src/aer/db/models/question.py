"""A question asked over a company's record, and what became of it (F6, ADR 0130).

One row per question. The tier it resolved to and the sentence that says why are written
before anything runs, because the tier is what the operator was shown before the answer
(page specification §13). What follows depends on the tier: a tier-1 answer is figures
struck on the question's own job; a tier-2 answer is prose over what the record already
held, with the pass's cost read from the cost rows afterwards; a tier-3 question carries
the price it was shown, the approval that carried that price, and the documents it added.

``company_id`` is not null, against the specification's draft: a question is answered from
a company's record, and a question over nothing has no record to be answered from.
``content`` is the structured answer — the figures with their calculation ids, the
paragraphs with the citations they rest on — from which the page and the drawer are
composed; ``answer`` is the prose alone, so a reader of the table sees what was said
without parsing anything.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base
from aer.db.types import Timestamp, TimestampOptional, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.company import Company
    from aer.db.models.job import Job
    from aer.db.models.user import User

__all__ = ["Question"]


class Question(Base):
    """One question, the tier it resolved to, and its answer or its price."""

    __tablename__ = "questions"

    id: Mapped[UuidPk]

    user_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # The report whose run a tier-1 answer re-struck, when the company has one. SET NULL,
    # like a cost row's job: withdrawing the report keeps the question and its answer.
    report_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"))
    # The question's own run root (ADR 0072): the job its calculations, its model call and
    # its cost rows are written under, and by which the calculation walk checks ownership.
    job_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))

    # The statement clock, as the watchlist's `followed_at`: two questions asked in one
    # transaction must not share an instant, because the page lists them in order asked.
    asked_at: Mapped[Timestamp] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)

    # 1 recompute, 2 re-read, 3 research — resolved before anything runs, and recorded
    # with the sentence that says why, so a wrong resolution is a row somebody can read.
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tier_rationale: Mapped[str] = mapped_column(Text, nullable=False)

    # Tier 3 only. Null means the operator never said go ahead, and it never ran.
    approved_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))
    estimated_cost_gbp: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    actual_cost_gbp: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))

    answer: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Tier 3: the source document ids the question contributed. The point of the feature
    # (data model Gap 5) — the evidence that a question left the record larger.
    documents_added: Mapped[list[Any] | None] = mapped_column(JSONB)

    user: Mapped[User] = relationship()
    company: Mapped[Company] = relationship()
    job: Mapped[Job | None] = relationship()

    __table_args__ = (
        CheckConstraint("tier IN (1, 2, 3)", name="question_tier_is_one_of_three"),
        CheckConstraint("char_length(btrim(question)) > 0", name="question_is_not_blank"),
        CheckConstraint(
            "char_length(btrim(tier_rationale)) > 0", name="question_tier_has_a_rationale"
        ),
        CheckConstraint(
            "estimated_cost_gbp IS NULL OR estimated_cost_gbp >= 0",
            name="question_estimate_is_not_negative",
        ),
        CheckConstraint(
            "actual_cost_gbp IS NULL OR actual_cost_gbp >= 0",
            name="question_cost_is_not_negative",
        ),
        # An answer is dated and a date has an answer: the page reads either as "answered".
        CheckConstraint(
            "(answer IS NULL) = (answered_at IS NULL)", name="question_answer_is_dated"
        ),
        Index("ix_questions_user_id_asked_at", "user_id", "asked_at"),
        Index("ix_questions_company_id_asked_at", "company_id", "asked_at"),
    )

    @property
    def is_answered(self) -> bool:
        return self.answered_at is not None

    @property
    def is_approved(self) -> bool:
        return self.approved_at is not None
