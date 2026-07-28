"""The proposed research plan, shown at the first approval gate.

Persisted before approval rather than after, and never mutated afterwards. The operator
approves a *specific* plan with a *specific* cost estimate, and the stored row is the
evidence of what was actually put in front of them. If planning ran again the result would
be a new row, not an edit — otherwise "what did I approve?" becomes unanswerable, which
defeats the point of having a gate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.job import Job
    from aer.db.models.request import ResearchRequest

__all__ = ["ResearchPlan"]


class ResearchPlan(Base):
    __tablename__ = "research_plans"

    id: Mapped[UuidPk]
    request_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("research_requests.id", ondelete="CASCADE"), nullable=False
    )

    # Which version of the workflow produced this plan, e.g. "equity-research@1.3.0".
    # Recorded so a run can be reproduced against the workflow definition that made it,
    # not whatever the definition happens to say today.
    workflow_version: Mapped[str] = mapped_column(Text, nullable=False)

    # The typed ResearchPlan: sections, tasks, agents. JSONB because its shape is owned by
    # a Pydantic model that will evolve, and migrating a normalised version of it on every
    # planner change would be a lot of work for no query benefit.
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # [{provider, url_pattern, tier, purpose}] — what the run intends to fetch, shown at
    # the gate so the operator can refuse a source before anything is spent.
    planned_sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)

    estimated_cost_gbp: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    estimated_runtime_seconds: Mapped[int] = mapped_column(nullable=False)
    known_risks: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    created_at: Mapped[Timestamp] = created_at_column()

    request: Mapped[ResearchRequest] = relationship(back_populates="plans")
    jobs: Mapped[list[Job]] = relationship(back_populates="plan")

    __table_args__ = (
        CheckConstraint("estimated_cost_gbp >= 0", name="estimated_cost_non_negative"),
        CheckConstraint("estimated_runtime_seconds >= 0", name="estimated_runtime_non_negative"),
        Index("ix_research_plans_request_id_created_at", "request_id", text("created_at DESC")),
    )
