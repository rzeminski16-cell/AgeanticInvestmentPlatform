"""One execution of an approved plan.

``code_version`` records the git SHA that ran. Combined with the pinned artefacts and the
recorded calculations, it is what makes "reproduce this report" a real operation rather
than an aspiration: you can check out the exact commit that produced a number.

A job's ``status`` distinguishes waiting from failing. ``PAUSED``, ``AWAITING_APPROVAL``
and ``BUDGET_EXCEEDED`` are all resumable states that need a human, not errors — see
:class:`aer.core.enums.JobStatus`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import JobStatus
from aer.db.base import Base
from aer.db.types import TimestampOptional, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job_step import JobStep
    from aer.db.models.plan import ResearchPlan
    from aer.db.models.request import ResearchRequest

__all__ = ["Job"]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UuidPk]
    request_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False
    )

    # Nullable, and RESTRICT rather than CASCADE: a plan that a job ran against must not
    # be deletable while the job survives, or the run loses the record of what it executed.
    plan_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("research_plans.id", ondelete="RESTRICT")
    )

    workflow_version: Mapped[str] = mapped_column(Text, nullable=False)
    code_version: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        SaEnum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )

    started_at: Mapped[TimestampOptional]
    finished_at: Mapped[TimestampOptional]

    total_cost_gbp: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("0")
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    request: Mapped[ResearchRequest] = relationship(back_populates="jobs")
    plan: Mapped[ResearchPlan | None] = relationship(back_populates="jobs")
    steps: Mapped[list[JobStep]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobStep.sequence",
    )

    __table_args__ = (
        CheckConstraint("total_cost_gbp >= 0", name="total_cost_non_negative"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name="finished_implies_started",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        Index("ix_jobs_request_id", "request_id"),
        Index("ix_jobs_status", "status"),
    )
