"""What the research tool has waiting for the operator.

Four questions, asked of rows that already exist: which runs are stopped at a gate, which
stopped at their cost cap, which failed, and which requests were written and never run.
Nothing here is new state — a run's status has always said all of this, and the only thing
missing was somewhere to read it that was not one run's console.

The screen that composes these lives in `web/overview/`, and the view models it builds are
its own. This module returns rows, because a service that returned a rendered sentence
would be a second place presentation is decided.

**Every listing is bounded and says so.** An operator with forty stopped runs has a
different problem from one with three, and a feed that silently showed the first eight
would describe the smaller problem. The caller is told what was left out.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import Select, func, select

from aer.core.enums import AnalysisMode, JobStatus, RequestStatus
from aer.db.models import Cost, Job, ResearchRequest, WorkOrder

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "DEFAULT_LIMIT",
    "Bounded",
    "capped_runs",
    "failed_runs",
    "spend_since",
    "start_of_month",
    "stopped_runs",
    "unstarted_requests",
]

# Enough to see a pattern, few enough to read. Past this the feed says how many more.
DEFAULT_LIMIT: Final = 8


@dataclass(frozen=True, slots=True)
class Bounded[T]:
    """A page of rows and the number this listing did not return.

    ``remaining`` is why this is a type rather than a list. A bare list cannot distinguish
    "these are all of them" from "these are the first eight", and the difference is the
    whole of what an attention feed is for.
    """

    rows: tuple[T, ...]
    remaining: int

    @property
    def is_empty(self) -> bool:
        return not self.rows


def start_of_month(now: datetime) -> datetime:
    """Midnight UTC on the first of ``now``'s month.

    Taken as an argument rather than read here: `core` and the services stay free of clock
    reads, so a test can ask for a month boundary without moving the machine's clock.
    """
    return datetime(now.year, now.month, 1, tzinfo=UTC)


async def spend_since(session: AsyncSession, *, since: datetime) -> Decimal:
    """Everything the platform has spent since ``since``.

    Not scoped to an operator, and the reason is `web/pages.py`'s `costs_page`: this is a
    single-user deployment (A5), and `costs.job_id` is nullable with ``ON DELETE SET NULL``
    so that spend outlives the run that incurred it. A total joined through `jobs` would
    quietly shrink when a request was purged — the one number on this page that must not
    move for a reason nobody can see.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.occurred_at >= since)
    )
    return Decimal(str(total or 0))


async def stopped_runs(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = DEFAULT_LIMIT
) -> Bounded[tuple[Job, ResearchRequest]]:
    """Runs paused at a gate: nothing moves until a person decides something."""
    return await _runs_with_status(session, JobStatus.AWAITING_APPROVAL, user_id, limit)


async def failed_runs(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = DEFAULT_LIMIT
) -> Bounded[tuple[Job, ResearchRequest]]:
    """Runs that stopped because something went wrong, newest first.

    ``BUDGET_EXCEEDED`` is deliberately not here. `JobStatus` says why: it is not an error
    and it is resumable after a decision, so a feed that filed it under failure would send
    an operator to debug a run that is simply waiting to be told to carry on.
    """
    return await _runs_with_status(session, JobStatus.FAILED, user_id, limit)


async def capped_runs(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = DEFAULT_LIMIT
) -> Bounded[tuple[Job, ResearchRequest]]:
    """Runs stopped at their cost ceiling, which is a decision rather than a fault."""
    return await _runs_with_status(session, JobStatus.BUDGET_EXCEEDED, user_id, limit)


async def unstarted_requests(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = DEFAULT_LIMIT
) -> Bounded[ResearchRequest]:
    """Drafts with no run against them — written, and then nothing.

    ``NOT EXISTS`` rather than an outer join: a request with several jobs would otherwise
    arrive several times and the bound would count duplicates as different work.
    """
    has_a_run = select(Job.id).where(Job.work_order_id == ResearchRequest.id).exists()
    base = (
        select(ResearchRequest)
        # Owner, status and archived are the run root's since ADR 0072; only the ticker
        # and the horizon are the mandate's.
        .join(WorkOrder, WorkOrder.id == ResearchRequest.id)
        .where(
            WorkOrder.user_id == user_id,
            WorkOrder.status == RequestStatus.DRAFT,
            WorkOrder.archived_at.is_(None),
            ~has_a_run,
        )
    )
    total = await _count(session, base)
    rows = list(
        await session.scalars(base.order_by(ResearchRequest.created_at.desc()).limit(limit))
    )
    return Bounded(rows=tuple(rows), remaining=max(0, total - len(rows)))


async def has_ever_commissioned(session: AsyncSession, *, user_id: uuid.UUID) -> bool:
    """Whether this operator has ever written a research request.

    The one question that separates "nothing is waiting for you" from "you have not started
    yet", and they are opposite pages. An empty work list means *caught up* to somebody who
    has been using the platform and *nothing here works* to somebody who has just installed
    it — and the second reader is the one who needs an instruction rather than congratulation.

    A request rather than a run, deliberately: writing one is the first thing an operator does
    and it costs nothing, so it is the earliest honest signal that they are under way. Somebody
    who has saved a draft and not started it is not a new operator; they are one row of work.
    """
    written = await session.scalar(
        select(ResearchRequest.id)
        .join(WorkOrder, WorkOrder.id == ResearchRequest.id)
        .where(WorkOrder.user_id == user_id)
        .limit(1)
    )
    return written is not None


async def _runs_with_status(
    session: AsyncSession, status: JobStatus, user_id: uuid.UUID, limit: int
) -> Bounded[tuple[Job, ResearchRequest]]:
    base = (
        select(Job, ResearchRequest)
        .join(ResearchRequest, ResearchRequest.id == Job.work_order_id)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(Job.status == status, WorkOrder.user_id == user_id)
    )
    total = await _count(session, base)
    fetched: Sequence[tuple[Job, ResearchRequest]] = (
        await session.execute(
            base.order_by(Job.started_at.desc().nullslast(), Job.id.desc()).limit(limit)
        )
    ).all()  # type: ignore[assignment]
    return Bounded(rows=tuple(fetched), remaining=max(0, total - len(fetched)))


async def _count(session: AsyncSession, base: Select[Any]) -> int:
    """How many rows the unbounded query would return.

    ``Select[Any]`` because the two callers select different shapes and `Select` is
    invariant in its row type; narrowing it would buy a cast at each call site rather than
    a check. What is asserted here is the count, and the tests assert it against rows.

    A second statement rather than a window function over the page: the ordering columns
    are nullable and a `COUNT(*) OVER ()` beside them made the plan a sort of the whole
    table for a page of eight.
    """
    counted = await session.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
    return int(counted or 0)


@dataclass(frozen=True, slots=True)
class TypicalCost:
    """What runs at this depth have actually cost, when there are enough of them to say.

    **The unavailable case is the important one.** A fresh install has no history, and the
    tempting answer — average the zero runs, render "£0.00" — is a figure with the confidence
    of a measurement and nothing behind it. Every field here is empty when `sample` is too
    small, so a template cannot print a range that was never computed.
    """

    low: Decimal | None
    high: Decimal | None
    sample: int

    @property
    def is_known(self) -> bool:
        return self.low is not None and self.high is not None


# Below this, the spread is one or two runs' luck rather than a typical cost. Three is not a
# statistical claim; it is the point at which quoting a range stops being a single anecdote
# wearing a plural.
MINIMUM_SAMPLE: Final = 3


async def typical_cost(
    session: AsyncSession, *, user_id: uuid.UUID, mode: AnalysisMode
) -> TypicalCost:
    """The cheapest and dearest finished run at this depth, for this operator.

    Their own runs rather than a global figure: cost depends on the provider, the model and
    the company, and a number from somebody else's setup is guidance about somebody else.

    Extremes rather than a mean. An operator setting a ceiling wants to know what it might
    cost, and a mean hides the run that went to eight pounds behind four that went to two.
    """
    finished = (
        select(Job.total_cost_gbp)
        .join(ResearchRequest, ResearchRequest.id == Job.work_order_id)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(
            WorkOrder.user_id == user_id,
            ResearchRequest.analysis_mode == mode,
            Job.status == JobStatus.SUCCEEDED,
            Job.total_cost_gbp > 0,
        )
    )
    costs = sorted((await session.scalars(finished)).all())
    if len(costs) < MINIMUM_SAMPLE:
        return TypicalCost(low=None, high=None, sample=len(costs))
    return TypicalCost(low=costs[0], high=costs[-1], sample=len(costs))
