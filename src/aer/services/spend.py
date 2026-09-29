"""What the platform spent, and how much of it the cache saved.

Gap A15: Phase 6's cost optimisation pass had no baseline to measure against. `costs` rows
have been written since Task 10 and nothing ever read them back in aggregate, so "is this
run expensive?" could only be answered by opening the database, and "did that change help?"
could not be answered at all.

**The cache-hit rate is the number A14 is judged by.** Asking for a cache is not the same as
getting one: a prefix under the model's minimum, a dictionary serialised in a different
order, a per-call string that crept ahead of the shared block — each produces a run that
pays full price and looks correctly configured. The only evidence either way is the ratio of
cache-read tokens to what a run would have read uncached, and that comes from the
`agent_runs` counters the provider already writes.

**Read-only, and deliberately arithmetic rather than clever.** Every figure here is a sum or
a ratio of stored values. Nothing re-prices anything: the `costs` rows carry the money as it
was metered at the time, which is the honest figure even after a price change.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import AgentRun, Cost, Job, JobStep, WorkOrder
from aer.providers.protocol import SCHEMA_REJECTED

__all__ = [
    "ActivitySpend",
    "RoleSpend",
    "SpendSummary",
    "discarded_since",
    "spend_by_activity_since",
    "spend_by_job",
    "spend_by_role",
    "spend_summary",
]


async def spend_by_job(
    session: AsyncSession, job_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """What each run has spent, summed from its cost rows, in one query.

    **The cost rows, never ``jobs.total_cost_gbp``.** A research run writes a cost row for
    every call it pays for and never writes the running total, so a surface reading the
    column showed £0.00 beside a run that had spent £7 (ROADMAP §3.19 item 87 fixed the
    requests list; this is the same answer for every other surface). A run with no cost
    rows is absent from the result, and a caller reads that as nothing spent.
    """
    if not job_ids:
        return {}
    totals = await session.execute(
        select(Cost.job_id, func.coalesce(func.sum(Cost.amount_gbp), 0))
        .where(Cost.job_id.in_(list(job_ids)))
        .group_by(Cost.job_id)
    )
    return {job_id: Decimal(str(total)) for job_id, total in totals.tuples() if job_id is not None}


@dataclass(frozen=True, slots=True)
class CacheUse:
    """Prompt tokens, split by how they were charged."""

    fresh_tokens: int = 0
    read_tokens: int = 0
    written_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        """Everything the prompt cost, however it was charged.

        ``input_tokens`` from the API is the *uncached remainder*, not the whole prompt — a
        detail that makes a heavily cached run look tiny if the other two are ignored.
        """
        return self.fresh_tokens + self.read_tokens + self.written_tokens

    @property
    def hit_rate(self) -> Decimal | None:
        """Share of prompt tokens served from cache, or ``None`` when nothing was sent.

        ``None`` rather than zero: a run that made no calls has no hit rate, and reporting
        it as 0% would put a run that never asked next to one that asked and missed.
        """
        total = self.prompt_tokens
        if total == 0:
            return None
        return (Decimal(self.read_tokens) / Decimal(total)).quantize(Decimal("0.0001"))


@dataclass(frozen=True, slots=True)
class RoleSpend:
    """One role's share of a run."""

    role: str
    model: str
    calls: int
    output_tokens: int
    cache: CacheUse


@dataclass(frozen=True, slots=True)
class SpendSummary:
    """What one run, or the whole platform, has cost."""

    total_gbp: Decimal
    calls: int
    output_tokens: int
    cache: CacheUse
    by_kind: tuple[tuple[str, Decimal], ...] = ()

    @property
    def hit_rate(self) -> Decimal | None:
        return self.cache.hit_rate


def _agent_runs_for(job_id: uuid.UUID | None) -> Select[tuple[AgentRun]]:
    statement = select(AgentRun)
    if job_id is not None:
        statement = statement.join(JobStep, JobStep.id == AgentRun.job_step_id).where(
            JobStep.job_id == job_id
        )
    return statement


async def spend_summary(session: AsyncSession, *, job_id: uuid.UUID | None = None) -> SpendSummary:
    """Totals for one run, or for every run when ``job_id`` is omitted."""
    money = select(func.coalesce(func.sum(Cost.amount_gbp), 0))
    kinds = select(Cost.category, func.coalesce(func.sum(Cost.amount_gbp), 0)).group_by(
        Cost.category
    )
    if job_id is not None:
        money = money.where(Cost.job_id == job_id)
        kinds = kinds.where(Cost.job_id == job_id)

    total = Decimal(str(await session.scalar(money) or 0))
    by_kind = tuple(
        (str(kind), Decimal(str(amount))) for kind, amount in (await session.execute(kinds)).all()
    )

    runs = list(await session.scalars(_agent_runs_for(job_id)))
    return SpendSummary(
        total_gbp=total,
        calls=len(runs),
        output_tokens=sum(r.output_tokens or 0 for r in runs),
        cache=_cache_use(runs),
        by_kind=tuple(sorted(by_kind)),
    )


async def spend_by_role(
    session: AsyncSession, *, job_id: uuid.UUID | None = None
) -> list[RoleSpend]:
    """Where a run's tokens went, heaviest first.

    Grouped by role *and* model, not by role alone. The router maps one to the other, but a
    routing change mid-project leaves the same role recorded against two models, and
    averaging across them would hide exactly the comparison worth making.
    """
    runs = list(await session.scalars(_agent_runs_for(job_id)))

    grouped: dict[tuple[str, str], list[AgentRun]] = {}
    for run in runs:
        grouped.setdefault((run.agent_role, run.model), []).append(run)

    rows = [
        RoleSpend(
            role=role,
            model=model,
            calls=len(members),
            output_tokens=sum(r.output_tokens or 0 for r in members),
            cache=_cache_use(members),
        )
        for (role, model), members in grouped.items()
    ]
    return sorted(rows, key=lambda r: (-r.cache.prompt_tokens, r.role))


def _cache_use(runs: list[AgentRun]) -> CacheUse:
    return CacheUse(
        fresh_tokens=sum(r.input_tokens or 0 for r in runs),
        read_tokens=sum(r.cache_read_tokens or 0 for r in runs),
        written_tokens=sum(r.cache_write_tokens or 0 for r in runs),
    )


async def recent_runs(session: AsyncSession, *, limit: int = 20) -> list[tuple[Job, Decimal]]:
    """The most recent runs with what each cost, newest first."""
    jobs = list(
        await session.scalars(
            select(Job).order_by(Job.started_at.desc().nullslast(), Job.id.desc()).limit(limit)
        )
    )
    if not jobs:
        return []

    rows = (
        await session.execute(
            select(Cost.job_id, func.coalesce(func.sum(Cost.amount_gbp), 0))
            .where(Cost.job_id.in_([job.id for job in jobs]))
            .group_by(Cost.job_id)
        )
    ).all()
    totals: dict[uuid.UUID, Decimal] = {
        job_id: Decimal(str(amount)) for job_id, amount in rows if job_id is not None
    }
    return [(job, totals.get(job.id, Decimal(0))) for job in jobs]


async def discarded_since(session: AsyncSession, *, since: datetime) -> Decimal:
    """What replies paid for and not usable have cost since ``since``.

    A reply the schema refused is metered like any other, and its run carries the schema's
    verdict as its stop reason (`agents.base`), which is what finds it here. Nothing else in the
    platform sums it, and it is the one line of spend that bought nothing at all. Platform-wide,
    for the reason :func:`aer.services.overview.spend_since` gives.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0))
        .join(AgentRun, AgentRun.id == Cost.agent_run_id)
        .where(AgentRun.stop_reason == SCHEMA_REJECTED, Cost.occurred_at >= since)
    )
    return Decimal(str(total or 0))


@dataclass(frozen=True, slots=True)
class ActivitySpend:
    """What one kind of work has cost since a moment, and how many runs of it there were.

    ``tool`` and ``refresh_kind`` are ``None`` for spend whose run has since been removed: a
    cost row outlives its run (the reference is set null), and the money was still spent.
    """

    tool: str | None
    refresh_kind: str | None
    runs: int
    amount_gbp: Decimal


async def spend_by_activity_since(session: AsyncSession, *, since: datetime) -> list[ActivitySpend]:
    """Spend since ``since`` by the tool that incurred it, a refresh apart from a full report.

    Every cost row lands in exactly one group, so the groups sum to what
    :func:`aer.services.overview.spend_since` reports for the same moment. Heaviest first.
    """
    rows = await session.execute(
        select(
            WorkOrder.tool,
            Job.refresh_kind,
            func.count(func.distinct(Job.id)),
            func.coalesce(func.sum(Cost.amount_gbp), 0),
        )
        .select_from(Cost)
        .outerjoin(Job, Job.id == Cost.job_id)
        .outerjoin(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(Cost.occurred_at >= since)
        .group_by(WorkOrder.tool, Job.refresh_kind)
    )
    found = [
        ActivitySpend(tool=tool, refresh_kind=kind, runs=int(runs), amount_gbp=Decimal(str(amount)))
        for tool, kind, runs, amount in rows.tuples()
    ]
    return sorted(found, key=lambda row: (-row.amount_gbp, row.tool or "", row.refresh_kind or ""))
