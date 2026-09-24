"""Which method produced which outcome (ADR 0122 §2, the methodology library).

``calc/outcomes.py`` measures a confirmed assumption against the first fiscal year it
forecast, and the review layer grades a decision's process and its premises. With the
judgement layer in the map the same measurements run over *methods*: for each skill an
operator's runs pinned, the approved reports it shaped, how far their confirmed drivers
landed from what was later filed, and what became of the theses written against those
reports — the decisions, the reviews' process grades, the verdicts on the premises. A
library of methods that knows which ones worked, read from rows alone.

Nothing here reaches a report. The deltas go through a context that is thrown away, as the
company note's do (:func:`aer.services.history.driver_accuracy_for`), a count of verdicts
is a count, and a method's record is the operator's own reading of their own record.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from aer.calc.engine import CalculationContext
from aer.core.enums import PremiseVerdict, ProcessQuality
from aer.core.figures import plain_decimal
from aer.db.models import (
    Decision,
    PlanSkillPin,
    Report,
    Review,
    ReviewVerdict,
    Skill,
    Thesis,
    WorkOrder,
)
from aer.db.models.plan_skill_pin import PLANNED
from aer.services.history import MEASURED, assumption_outcomes_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["MethodRecord", "method_records"]


@dataclass(frozen=True, slots=True)
class MethodRecord:
    """One skill's record across every approved report a person's runs pinned it in."""

    key: str
    reports: int
    drivers_measured: int
    mean_absolute_delta: str | None
    theses: int
    decisions: int
    reviews: int
    process: tuple[tuple[str, int], ...]
    """Process grades over the reviews, in the enum's order, grades nobody gave left out."""
    verdicts: tuple[tuple[str, int], ...]
    """Verdicts over the reviewed premises, in the enum's order, verdicts nobody reached left
    out."""

    @property
    def sentence(self) -> str:
        """The record in one sentence, for the library's row."""
        parts = [f"Ran in {_count(self.reports, 'approved report')}"]
        if self.drivers_measured:
            parts.append(
                f"{_count(self.drivers_measured, 'confirmed driver')} measured against the "
                f"year forecast, mean absolute delta {self.mean_absolute_delta}"
            )
        else:
            parts.append("no confirmed driver measured yet")
        if not self.theses:
            parts.append("no thesis written against those reports yet")
            return "; ".join(parts) + "."
        parts.append(
            f"{_count(self.theses, 'thesis', 'theses')} written against those reports, "
            f"{_count(self.decisions, 'decision')}"
        )
        if not self.reviews:
            parts.append("no review yet")
            return "; ".join(parts) + "."
        graded = ", ".join(f"{word} {count}" for word, count in self.process)
        parts.append(f"{_count(self.reviews, 'review')} ({graded})")
        if self.verdicts:
            found = ", ".join(f"{word} {count}" for word, count in self.verdicts)
            parts.append(f"premises {found}")
        return "; ".join(parts) + "."


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number} {singular if number == 1 else (plural or singular + 's')}"


async def method_records(session: AsyncSession, *, user_id: uuid.UUID) -> dict[str, MethodRecord]:
    """Every skill this person's runs pinned as planned, with its record, by key.

    A skill pinned only on runs that never reached an approved report has no record yet and
    is not here: a method is judged by what it shaped, and nothing was shaped.
    """
    pinned = await session.execute(
        select(Skill.key, PlanSkillPin.work_order_id)
        .join(PlanSkillPin, PlanSkillPin.skill_id == Skill.id)
        .join(WorkOrder, WorkOrder.id == PlanSkillPin.work_order_id)
        .where(WorkOrder.user_id == user_id, PlanSkillPin.status == PLANNED)
    )
    orders_by_key: dict[str, set[uuid.UUID]] = {}
    for key, order_id in pinned.tuples().all():
        orders_by_key.setdefault(str(key), set()).add(order_id)

    # Thrown away on purpose: the realised drivers are recorded against the runs whose
    # comparison sections measured them, and a projection must not write a second copy.
    context = CalculationContext(code_version="projection")
    records: dict[str, MethodRecord] = {}
    for key in sorted(orders_by_key):
        # A research request is its work order (joined inheritance), so a report's request
        # id is the id a pin names.
        reports = list(
            await session.scalars(
                select(Report)
                .where(Report.request_id.in_(list(orders_by_key[key])), Report.immutable.is_(True))
                .order_by(Report.as_of_date, Report.created_at)
            )
        )
        if not reports:
            continue
        records[key] = await _record(session, context, key=key, reports=reports, user_id=user_id)
    return records


async def _record(
    session: AsyncSession,
    context: CalculationContext,
    *,
    key: str,
    reports: Sequence[Report],
    user_id: uuid.UUID,
) -> MethodRecord:
    deltas: list[Decimal] = []
    for report in reports:
        for outcome in await assumption_outcomes_for(session, context, prior=report):
            if outcome.status == MEASURED and outcome.delta is not None:
                deltas.append(abs(Decimal(outcome.delta)))

    thesis_ids = list(
        await session.scalars(
            select(Thesis.id).where(
                Thesis.report_id.in_([report.id for report in reports]), Thesis.user_id == user_id
            )
        )
    )
    decisions = 0
    reviews: list[Review] = []
    verdicts: list[ReviewVerdict] = []
    if thesis_ids:
        decisions = int(
            await session.scalar(
                select(func.count()).select_from(Decision).where(Decision.thesis_id.in_(thesis_ids))
            )
            or 0
        )
        reviews = list(
            await session.scalars(select(Review).where(Review.thesis_id.in_(thesis_ids)))
        )
    if reviews:
        verdicts = list(
            await session.scalars(
                select(ReviewVerdict).where(
                    ReviewVerdict.review_id.in_([review.judgement_id for review in reviews])
                )
            )
        )
    graded = Counter(review.process_quality for review in reviews)
    found = Counter(verdict.verdict for verdict in verdicts)
    mean = (
        (sum(deltas, Decimal(0)) / Decimal(len(deltas))).quantize(Decimal("0.000001"))
        if deltas
        else None
    )
    return MethodRecord(
        key=key,
        reports=len(reports),
        drivers_measured=len(deltas),
        mean_absolute_delta=plain_decimal(mean) if mean is not None else None,
        theses=len(thesis_ids),
        decisions=decisions,
        reviews=len(reviews),
        process=tuple((grade.value, graded[grade]) for grade in ProcessQuality if graded[grade]),
        verdicts=tuple(
            (verdict.value.replace("_", " "), found[verdict])
            for verdict in PremiseVerdict
            if found[verdict]
        ),
    )
