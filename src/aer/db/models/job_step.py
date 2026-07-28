"""A single step of a job: the unit of resumability and of audit.

Two columns carry the weight here.

``idempotency_key`` is what makes a crashed run recoverable. A step that already completed
returns its stored ``output_ref`` instead of re-executing, so resuming after a worker dies
does not re-fetch every filing, re-run every model call, or re-spend the budget.

``input_hash`` is what makes resumption *correct*. If a step's inputs changed, its stored
output is stale and reusing it would silently mix results from two different runs. The
hash is how that is detected rather than assumed.

``output_ref`` holds pointers — artefact ids, row ids — never bulk payloads. Large blobs
belong in the content-addressed artefact store; putting them here would bloat every query
that reads the step list to render a progress view.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SaEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.core.enums import JobStatus
from aer.db.base import Base
from aer.db.types import Sha256, TimestampOptional, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job import Job

__all__ = ["JobStep"]


class JobStep(Base):
    __tablename__ = "job_steps"

    id: Mapped[UuidPk]
    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    # Stable DAG node identifier, e.g. "acquire.sec.10k". Stable across runs so the same
    # logical step can be compared between them.
    step_key: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        SaEnum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )

    # Retries are recorded, not overwritten: a step that succeeded on its third attempt is
    # a different audit story from one that succeeded immediately, and the difference
    # matters when a provider is flaky.
    attempt: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))

    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[Sha256] = mapped_column(nullable=False)
    output_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    cost_gbp: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("0")
    )

    started_at: Mapped[TimestampOptional]
    finished_at: Mapped[TimestampOptional]
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    job: Mapped[Job] = relationship(back_populates="steps")

    __table_args__ = (
        # The resumability contract: one row per (job, step, attempt). A retry must
        # increment `attempt` rather than overwrite history.
        UniqueConstraint("job_id", "step_key", "attempt", name="uq_job_steps_job_step_attempt"),
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        CheckConstraint("cost_gbp >= 0", name="cost_non_negative"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name="finished_implies_started",
        ),
        Index("ix_job_steps_job_id_sequence", "job_id", "sequence"),
        Index("ix_job_steps_idempotency_key", "idempotency_key"),
    )
