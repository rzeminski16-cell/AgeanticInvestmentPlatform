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

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import JobStatus
from aer.db.base import Base
from aer.db.types import TimestampOptional, UuidFk, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job_step import JobStep
    from aer.db.models.plan import ResearchPlan
    from aer.db.models.work_order import WorkOrder

__all__ = ["Job"]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UuidPk]

    # The run root. NOT NULL, because this is what the spend guard walks to for a per-run
    # cap and invariant 6 does not admit a run without one (ADR 0072).
    work_order_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False
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

    # Developer step-through (ADR 0090). On the row rather than in the invocation, so the
    # pause after each executed step holds wherever the run executes — the CLI stepping it
    # and the worker continuing it after a gate approval read the same flag.
    step_mode: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    started_at: Mapped[TimestampOptional]
    finished_at: Mapped[TimestampOptional]

    total_cost_gbp: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("0")
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # What kind of run this is (F4, ADR 0131 §1): a full run, or a refresh of the report
    # `refreshes_report_id` names. A refresh is the one job that starts on a request whose
    # report is current, and the render step reads this to say `refreshed` when it
    # supersedes that report. SET NULL on the report: withdrawing it keeps the run.
    refresh_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'full'")
    )
    # ``use_alter``: reports point at jobs and this points back, and the metadata's table
    # sort refuses a cycle unless one side is added after both tables exist.
    refreshes_report_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey(
            "reports.id", ondelete="SET NULL", use_alter=True, name="fk_jobs_refreshes_report_id"
        )
    )
    # When the operator read a refresh's change summary; the work list's row reads it.
    changes_read_at: Mapped[TimestampOptional] = mapped_column(DateTime(timezone=True))

    work_order: Mapped[WorkOrder] = relationship(back_populates="jobs")
    plan: Mapped[ResearchPlan | None] = relationship(back_populates="jobs")
    steps: Mapped[list[JobStep]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobStep.sequence",
    )

    __table_args__ = (
        CheckConstraint("total_cost_gbp >= 0", name="total_cost_non_negative"),
        CheckConstraint(
            "refresh_kind IN ('full', 'refresh')", name="job_refresh_kind_is_one_of_two"
        ),
        # A full run names no report. A refresh names the one it refreshes, and keeps its
        # kind when that report is deleted (the reference is set null): the run happened,
        # and a record that forgot it was a refresh would misreport its own workflow.
        CheckConstraint(
            "refresh_kind = 'refresh' OR refreshes_report_id IS NULL",
            name="job_full_run_names_no_report",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name="finished_implies_started",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        Index("ix_jobs_work_order_id", "work_order_id"),
        Index("ix_jobs_status", "status"),
    )
