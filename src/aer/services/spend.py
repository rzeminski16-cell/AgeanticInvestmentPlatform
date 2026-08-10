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
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import AgentRun, Cost, Job, JobStep

__all__ = ["RoleSpend", "SpendSummary", "spend_by_role", "spend_summary"]


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
