"""What keeps needing revising, counted — never taught (ADR 0091).

The read side of the revision memory. `revision_notes` records what the critique loop did,
one row per decision; this module groups those rows by challenge class across runs and
says which classes recur. That is the whole of the automation: a recurring class becomes
standing guidance for future runs only when the operator authors a methodology skill
through the §3.11 boundary — versioned, pinned at gate 1, additive-only, containment
proved by the ADR 0040 corpus. A lesson the platform wrote into its own prompts would be a
critic entrenching its own mistake, unreviewed, which is what invariant 7 exists to stop.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import RevisionNote

__all__ = ["RECURRENCE_BAR", "LessonCandidate", "recurring_lessons"]

# A class met once is a finding; a class met across runs is a candidate lesson. Two is
# the smallest number that distinguishes them, and the CLI's default filter.
RECURRENCE_BAR = 2


@dataclass(frozen=True, slots=True)
class LessonCandidate:
    """One class of challenge and how often it has provoked the loop."""

    scope: str
    dimension: str
    jobs: int
    notes: int
    latest_statements: tuple[str, ...]

    @property
    def recurring(self) -> bool:
        """Whether more than one run has met this class — the bar for calling it a pattern."""
        return self.jobs >= RECURRENCE_BAR


async def recurring_lessons(
    session: AsyncSession, *, minimum_jobs: int = RECURRENCE_BAR, samples: int = 3
) -> list[LessonCandidate]:
    """Every challenge class seen in at least ``minimum_jobs`` distinct runs, worst first.

    ``minimum_jobs=1`` lists everything the loop has ever recorded, which is what an
    operator reviewing a single run wants; the default is the recurrence bar, because a
    class met once is a finding and a class met across runs is a candidate lesson.
    """
    grouped = await session.execute(
        select(
            RevisionNote.scope,
            RevisionNote.dimension,
            func.count(distinct(RevisionNote.job_id)).label("jobs"),
            func.count().label("notes"),
        )
        .group_by(RevisionNote.scope, RevisionNote.dimension)
        .having(func.count(distinct(RevisionNote.job_id)) >= minimum_jobs)
        .order_by(
            func.count(distinct(RevisionNote.job_id)).desc(),
            func.count().desc(),
            RevisionNote.scope,
            RevisionNote.dimension,
        )
    )

    candidates: list[LessonCandidate] = []
    for scope, dimension, jobs, notes in grouped:
        statements = await session.scalars(
            select(RevisionNote.statement)
            .where(RevisionNote.scope == scope, RevisionNote.dimension == dimension)
            .order_by(RevisionNote.created_at.desc(), RevisionNote.id.desc())
            .limit(samples)
        )
        candidates.append(
            LessonCandidate(
                scope=scope,
                dimension=dimension,
                jobs=int(jobs),
                notes=int(notes),
                latest_statements=tuple(dict.fromkeys(statements)),
            )
        )
    return candidates
