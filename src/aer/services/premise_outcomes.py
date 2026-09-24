"""What became of the premises held against a report (ADR 0122 §2, its consequences).

``prior_research_comparison`` compares a prior report's view with this run's. The judgement
layer lets it compare the premises the operator held *against* that report with what
happened to them — the monitor's latest reading, a withdrawal and its reason, the verdicts
a post-trade review filed — which is the look-back the whole loop exists to produce. Every
row here is read from the record and quoted: the reading is the monitor's, the verdict is
the review's, and nothing is measured again.

Nothing here is evidence. A premise is the operator's own view (ADR 0074), so the rows
reach the operator's copy of the document and are withheld from the copy that leaves
(ADR 0129's shape, applied by :func:`aer.services.history.comparison_for_audience`), and
no claim can cite one (ADR 0122 §3).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from aer.db.models import Finding, Premise, Report, Review, ReviewVerdict, Thesis
from aer.services.theses import latest_reading
from aer.services.thesis_monitor import predicate_sentence

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["PremiseOutcome", "lookback_rows", "premise_outcomes_for"]


@dataclass(frozen=True, slots=True)
class PremiseOutcome:
    """One premise held against a prior report, and what the record says became of it."""

    thesis: Thesis
    premise: Premise
    reading: Finding | None
    verdicts: tuple[ReviewVerdict, ...]

    @property
    def held(self) -> str:
        """When it was held and what would defeat it, as the holder wrote it."""
        when = f"Held from {self.premise.judgement.held_at:%d %B %Y}"
        if self.premise.has_predicate:
            return f"{when}: {predicate_sentence(self.premise)}."
        if self.premise.review_by is not None:
            looked_at = f"{self.premise.review_by:%d %B %Y}"
            return f"{when}; no predicate, to be looked at again by {looked_at}."
        return f"{when}."

    @property
    def state(self) -> str:
        """What became of it, from the rows alone: the thesis's retirement, the withdrawal,
        the monitor's latest reading and every review's verdict, in that order."""
        parts: list[str] = []
        if self.thesis.retired_at is not None:
            retired = f"{self.thesis.retired_at:%d %B %Y}"
            parts.append(f"The thesis was retired on {retired}: {self.thesis.retirement_reason}")
        judgement = self.premise.judgement
        if judgement.withdrawn_at is not None:
            withdrawn = f"{judgement.withdrawn_at:%d %B %Y}"
            parts.append(f"Withdrawn on {withdrawn}: {judgement.withdrawn_reason}")
        if self.reading is not None and self.reading.status is not None:
            read_on = f"{self.reading.created_at:%d %B %Y}"
            parts.append(f"Last read by the monitor on {read_on} as {self.reading.status.value}")
        for verdict in self.verdicts:
            found = verdict.verdict.value.replace("_", " ")
            note = f" — {verdict.note}" if verdict.note else ""
            parts.append(f"A post-trade review found it {found}{note}")
        if not parts:
            return "Not yet read by the monitor, and not yet reviewed."
        return ". ".join(part.rstrip(".") for part in parts) + "."


async def premise_outcomes_for(
    session: AsyncSession, *, report: Report, user_id: uuid.UUID
) -> list[PremiseOutcome]:
    """Every premise of this person's theses written against ``report``, oldest thesis
    first, each with the monitor's latest reading and every verdict a review filed on it.

    Retired theses and withdrawn premises are here too: what became of them is the point,
    and a look-back that showed only the views still held would be the one that flatters.
    """
    theses = await session.scalars(
        select(Thesis)
        .options(selectinload(Thesis.premises))
        .where(Thesis.report_id == report.id, Thesis.user_id == user_id)
        .order_by(func.coalesce(Thesis.written_at, Thesis.created_at), Thesis.id)
    )
    outcomes: list[PremiseOutcome] = []
    for thesis in theses:
        for premise in sorted(thesis.premises, key=lambda row: row.position):
            verdicts = tuple(
                await session.scalars(
                    select(ReviewVerdict)
                    .join(Review, Review.judgement_id == ReviewVerdict.review_id)
                    .where(ReviewVerdict.premise_id == premise.judgement_id)
                    .order_by(Review.closed_on, ReviewVerdict.review_id)
                )
            )
            outcomes.append(
                PremiseOutcome(
                    thesis=thesis,
                    premise=premise,
                    reading=await latest_reading(session, premise),
                    verdicts=verdicts,
                )
            )
    return outcomes


def lookback_rows(outcomes: Sequence[PremiseOutcome]) -> list[dict[str, str]]:
    """The outcomes in the comparison section's own row shape, so the operator's copy shows
    them in the one table and the export names the report each was held against."""
    return [
        {
            "aspect": f"Premise — {outcome.premise.statement}",
            "prior": outcome.held,
            "current": outcome.state,
            "prior_report_id": str(outcome.thesis.report_id or ""),
        }
        for outcome in outcomes
    ]
