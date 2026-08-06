"""One run's score against one §2.10 metric, as the dashboard and the triggers read it.

The CI gate proves the machinery against fixture corpora; these rows are the same
arithmetic applied to a live run's own tables (task 39). One row per metric per run,
because the question the dashboard and the task 41 escalation engine both ask is "how did
*this* run do on *this* metric?", and a row is an answer that survives the run's context.

**``passed`` is nullable, and NULL means the metric was not exercised.** A run with no
post-dated source has nothing for look-ahead recall to measure; recording that as a pass
would claim a check that never ran, and omitting the row would make "every completed run
carries all eight" unverifiable. NULL says, precisely, "there was nothing to check" — and
the check constraint ties it to a NULL value, so a row cannot claim a score without a
verdict or a verdict without a score.

**Rows are replaceable, not append-only.** An evaluation is derived data — re-runnable
from the run's own rows at any time — unlike the audit chain it sits beside. A re-draft
re-validates and the rows follow; the unique constraint per (job, metric) is what makes
"the run's citation score" one answer rather than a history to disambiguate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

__all__ = ["Evaluation"]


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[UuidPk]

    # CASCADE: an evaluation describes a run and is recomputable from it; it has no life
    # of its own once the run is gone.
    job_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    # The §2.10 metric name, from `aer.eval.metrics.Metric`. A string column rather than
    # an enum type because task 42 grows the set, and an ALTER TYPE per metric would make
    # the schema the registry of what can be measured.
    metric: Mapped[str] = mapped_column(String(64), nullable=False)

    # NULL together with `passed`: the not-exercised state. Eight places matches the
    # deltas the numerical metric reports.
    value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    threshold: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    passed: Mapped[bool | None] = mapped_column()

    # Population, named failures, and any LLM advisories — which are recorded here and
    # nowhere else, because advice that could reach a verdict column would stop being
    # advice.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[Timestamp] = created_at_column()

    __table_args__ = (
        UniqueConstraint("job_id", "metric", name="uq_evaluations_one_row_per_metric"),
        # A score and a verdict arrive together or not at all. NULL/NULL is "not
        # exercised"; anything mixed is a writer that lost track halfway.
        CheckConstraint(
            "(value IS NULL) = (passed IS NULL)",
            name="ck_evaluations_score_and_verdict_travel_together",
        ),
        Index("ix_evaluations_job_id", "job_id"),
    )

    def __repr__(self) -> str:
        verdict = "not exercised" if self.passed is None else ("pass" if self.passed else "fail")
        return f"Evaluation({self.metric}={self.value} [{verdict}])"
