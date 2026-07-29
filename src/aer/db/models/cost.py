"""What a run spent, line by line.

**This table is the budget cap, not a report about it.** The guard before each model step
sums these rows and compares the total against the request's ceiling. A cap that reads from
somewhere else, or from an estimate, is a cap that lets a run through at whatever the
estimate was wrong by.

**The exchange rate is on the row.** Prices are published in USD; the budget is in GBP.
Storing only the converted amount would make last month's costs unreconcilable the moment
the configured rate changed. Storing the rate alongside makes every row self-describing —
you can always recover what was actually charged and what it was converted at.

**Three nullable parents, deliberately.** A cost belongs to a job, and usually to a step,
and sometimes to a specific model call. A data-provider fee belongs to the job with no
step; a token charge belongs to all three. Requiring the narrowest would mean inventing a
step for a cost that did not have one.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFkOptional, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job import Job

__all__ = ["Cost"]


class Cost(Base):
    __tablename__ = "costs"

    id: Mapped[UuidPk]

    # SET NULL, not CASCADE. All three of these chain back to `research_requests`, so
    # cascading meant deleting a request erased every record of what it had cost -- by
    # three separate paths. A monthly cap you can get under by deleting the thing you
    # spent it on is not a cap. The row survives with its amount, its date, its provider
    # and its model; what it was spent on is preserved in the `request.deleted` audit
    # entry, which by construction outlives the request. See migration 0009.
    job_id: Mapped[UuidFkOptional] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    job_step_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("job_steps.id", ondelete="SET NULL")
    )
    agent_run_id: Mapped[UuidFkOptional] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )

    # llm_input | llm_output | cache_write | cache_read | web_search | data_api.
    # Text rather than an enum: the set of things that cost money will grow faster than a
    # migration cadence, and an unrecognised category should record rather than refuse.
    category: Mapped[str] = mapped_column(Text, nullable=False)

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)

    units: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_type: Mapped[str] = mapped_column(Text, nullable=False)

    # Six decimal places: enough that one cheap call is not rounded to nothing, and a
    # thousand of them still sum correctly.
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    amount_gbp: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)

    occurred_at: Mapped[Timestamp] = created_at_column()

    job: Mapped[Job] = relationship()

    __table_args__ = (
        CheckConstraint("units >= 0", name="units_are_not_negative"),
        CheckConstraint("amount_usd >= 0", name="usd_is_not_negative"),
        CheckConstraint("amount_gbp >= 0", name="gbp_is_not_negative"),
        CheckConstraint("fx_rate > 0", name="fx_rate_is_positive"),
        CheckConstraint("char_length(btrim(category)) > 0", name="category_is_recorded"),
        # The query the budget guard makes on every model step: what has this run spent?
        Index("ix_costs_job_id_occurred_at", "job_id", "occurred_at"),
        Index("ix_costs_agent_run_id", "agent_run_id"),
        # The monthly-budget query: everything spent in a window, across all runs.
        Index("ix_costs_occurred_at", "occurred_at"),
    )

    def __repr__(self) -> str:
        return f"<Cost {self.category} {self.amount_gbp} GBP ({self.units} {self.unit_type})>"
