"""Which skill versions a plan runs with — pinned, composed, and shown to the approver.

The row a report points at when it names the exact version of every skill that shaped it.
Three properties carry that weight:

**The pin is to a version, not a key.** ``skill_version_id`` references the immutable
``skill_versions`` row, so editing a skill after approval changes nothing about any run
that pinned it — the whole point of versions being append-only.

**The composed policy is a snapshot of what was approved.** The composer runs at plan
time against the floor and the ceiling of that day, and the result — with every clamp —
is stored here, because gate 1 displays it and an approval is an approval of what was
displayed. Recomposing at execution against a *changed* floor would run something nobody
signed off.

**A skipped skill is a row, not an absence.** ``skipped_not_applicable`` with its reason,
so "why is my skill not in this plan?" has an answer on the page rather than a shrug in a
log.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aer.db.base import Base, created_at_column
from aer.db.types import Timestamp, UuidFk, UuidPk

if TYPE_CHECKING:
    from aer.db.models.skill import Skill, SkillVersion
    from aer.db.models.work_order import WorkOrder

__all__ = ["PLANNED", "SKIPPED_NOT_APPLICABLE", "PlanSkillPin"]

PLANNED = "planned"
SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"


class PlanSkillPin(Base):
    __tablename__ = "plan_skill_pins"

    id: Mapped[UuidPk]

    # The run root, not the plan. Hanging off `research_plans` meant the platform's one
    # governed instruction mechanism was available to exactly one tool: a thesis monitor,
    # having no research plan, could not pin a skill at all (ADR 0068).
    #
    # The table keeps its name for now, which is a misnomer this revision accepts rather
    # than pays a rename for. The cost that is not cosmetic: a request may hold several
    # plans, so pins are one set per work order, and a re-planned work order can no longer
    # say which of two sets a given job ran under. If that becomes a live need the answer
    # is the `supersedes_id` idiom, not a second foreign key — two columns claiming to own
    # one pin is how a provenance answer becomes ambiguous.
    work_order_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # RESTRICT on both: a skill whose versions are pinned by a plan is part of that
    # plan's provenance, and deleting it out from under an approved run would leave the
    # report unable to say what shaped it.
    skill_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False
    )
    skill_version_id: Mapped[UuidFk] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # -- The composed policy the approver saw (custom sections only) ---------------------

    min_sources: Mapped[int | None] = mapped_column(Integer)
    requires_primary: Mapped[bool | None] = mapped_column(Boolean)
    max_tier: Mapped[int | None] = mapped_column(Integer)
    allow_forward_looking: Mapped[bool | None] = mapped_column(Boolean)
    token_budget: Mapped[int | None] = mapped_column(Integer)
    granted_tools: Mapped[list[str] | None] = mapped_column(JSONB)

    # [{field, requested, effective, reason}] — the receipts. Empty list means the
    # request composed unchanged.
    clamps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    estimated_cost_gbp: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
    )

    created_at: Mapped[Timestamp] = created_at_column()

    work_order: Mapped[WorkOrder] = relationship()
    skill: Mapped[Skill] = relationship()
    skill_version: Mapped[SkillVersion] = relationship()

    __table_args__ = (
        UniqueConstraint("work_order_id", "skill_id", name="uq_plan_skill_pins_one_pin_per_skill"),
        CheckConstraint("status IN ('planned', 'skipped_not_applicable')", name="status_is_known"),
        # A skipped skill must say why; a planned one carries its policy instead.
        CheckConstraint(
            "status != 'skipped_not_applicable' OR reason != ''", name="skips_carry_reasons"
        ),
    )
