"""Starting and resuming a run.

The join between a request, a workflow and the services a workflow's steps need. Thin on
purpose: everything interesting is in the engine, the steps, or the gates, and a thick
orchestrator would be a fourth place to look for behaviour.

**Resuming is the same call as starting.** :func:`execute` runs from the first incomplete
step, so "start" and "continue after an approval" and "recover after the worker died" are
one operation. Three entry points would be three chances for them to diverge.

**The service bundle is assembled here.** A provider, a router, a store, a fetcher, a SEC
client. Assembled at the edge and passed down, so the workflow's steps take what they need
rather than constructing it — which is what lets the whole workflow run against a fake
provider with no network and no spend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import TERMINAL_JOB_STATUSES, JobStatus
from aer.db.models import Job, JobStep, Report, ResearchRequest, WorkOrder
from aer.errors import ValidationError
from aer.providers.protocol import LLMProvider
from aer.providers.router import Router
from aer.services.mandate import mandate_of
from aer.storage.protocol import ArtefactStore
from aer.version import git_sha
from aer.workflow.engine import BudgetGuard, WorkflowEngine, spend_so_far
from aer.workflow.registry import DEFAULT_WORKFLOW_VERSION, WorkflowRegistryError, resolve_workflow

__all__ = [
    "RunOutcome",
    "RunState",
    "TimelineEntry",
    "awaiting_approval_count",
    "declared_steps",
    "execute",
    "latest_run",
    "run_state",
    "start_run",
]

_log = structlog.get_logger("aer.services.runs")


@dataclass(slots=True)
class RunOutcome:
    """Where a run got to."""

    job: Job
    outputs: dict[str, dict[str, Any]]
    status: JobStatus
    spend_gbp: Decimal

    @property
    def is_waiting(self) -> bool:
        """Whether the run stopped for a human rather than finishing or failing."""
        return self.status in {
            JobStatus.AWAITING_APPROVAL,
            JobStatus.BUDGET_EXCEEDED,
            # Step mode's deliberate stop (ADR 0090): waiting for whoever is at the
            # terminal to confirm the step just executed, not broken.
            JobStatus.PAUSED,
        }


async def start_run(session: AsyncSession, *, request: ResearchRequest) -> Job:
    """Create the job for a request, return the one that exists, or replace a dead one.

    **One report per request, not one job.** That was the Phase 1 rule and it still holds:
    a second run of a request that already produced a report needs a story about which
    report is current, and there is not one yet. But a run that was cancelled or failed
    produced no report, so nothing has to be chosen between — and refusing to start again
    would leave the operator with a request they can neither run, edit nor delete. That is
    what cancelling a run used to do, which is how this was found.

    Superseding creates a **new** job rather than resurrecting the old one. Reusing it
    would contradict the audit record — the row says it finished, with a time — and a
    cancelled job still carries its cancellation, so the engine would stop it again on its
    first step.
    """
    existing = await latest_run(session, request_id=request.id)
    if existing is not None and not await _may_be_superseded(session, job=existing):
        return existing

    job = Job(
        work_order_id=request.id,
        request_id=request.id,
        workflow_version=DEFAULT_WORKFLOW_VERSION,
        code_version=git_sha() or "unknown",
        status=JobStatus.QUEUED,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()

    _log.info(
        "run.started",
        job_id=str(job.id),
        request_id=str(request.id),
        workflow=DEFAULT_WORKFLOW_VERSION,
        # Which of the two things just happened. "A run started" is ambiguous once a
        # request can have more than one, and the second is the interesting case.
        supersedes=str(existing.id) if existing is not None else None,
    )
    return job


async def latest_run(session: AsyncSession, *, request_id: uuid.UUID) -> Job | None:
    """The most recent job for a request, if any.

    Newest first, with a never-started job last. ``jobs`` has no ``created_at`` — a job's
    life begins when it starts — so ``started_at`` is the ordering column, and nulls-last
    keeps a queued-but-unstarted job from shadowing a real one.
    """
    found: Job | None = await session.scalar(
        select(Job)
        .where(Job.work_order_id == request_id)
        .order_by(Job.started_at.desc().nullslast())
    )
    return found


async def current_run(session: AsyncSession, *, user_id: uuid.UUID) -> Job | None:
    """The run the operator is watching, across every request they own (ADR 0089).

    **Defined once, here, and never guessed by a template.** The navigation item and the
    `/runs/active` redirect both call this, so the link and the page it lands on cannot
    disagree about which run is current — which they would, the first time two definitions of
    "latest" were written by two people a fortnight apart.

    The most recently started run that has not reached a terminal state; failing that, the most
    recently touched one. A finished run is still somewhere to go when nothing is in flight —
    the alternative is a navigation item that vanishes the moment a run ends, which is exactly
    when an operator goes looking for it.

    Nulls last, for the reason `latest_run` gives: a queued-but-unstarted job has no
    `started_at` and must not shadow one that is actually running.
    """
    live = (
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(WorkOrder.user_id == user_id)
    )
    found: Job | None = await session.scalar(
        live.where(Job.status.not_in(TERMINAL_JOB_STATUSES)).order_by(
            Job.started_at.desc().nullslast()
        )
    )
    if found is not None:
        return found
    touched: Job | None = await session.scalar(live.order_by(Job.started_at.desc().nullslast()))
    return touched


async def _may_be_superseded(session: AsyncSession, *, job: Job) -> bool:
    """Whether starting again should replace this run rather than return it.

    Two conditions, and both are needed. Terminal, because a run that is queued, running or
    waiting at a gate is the run — starting "again" means watching that one. And no report,
    because a report is the thing there can only be one current version of.
    """
    if not job.status.is_terminal:
        return False
    report = await session.scalar(select(Report.id).where(Report.job_id == job.id).limit(1))
    return report is None


async def execute(
    session: AsyncSession,
    *,
    job: Job,
    settings: Settings,
    provider: LLMProvider,
    store: ArtefactStore,
    sec_client: Any,
    fetcher: Any = None,
    stop_after: str | None = None,
    session_factory: Any = None,
) -> RunOutcome:
    """Run the workflow from the first incomplete step.

    The same call whether this is the first attempt, a continuation after an approval, or a
    recovery after the worker died. A step that already succeeded returns its stored output
    and does not execute.
    """
    request = await mandate_of(session, job)
    if request is None:
        message = f"Job {job.id} has no research request."
        raise ValidationError(message, context={"job_id": str(job.id)})

    job.status = JobStatus.RUNNING
    # Committed, not just flushed. The console polls from another process, and until this
    # lands it reads QUEUED — for the whole first step, which is a model call lasting a
    # minute. An operator watching a page that says QUEUED while money is being spent has no
    # way to tell a working run from a dead worker.
    await session.commit()

    engine = WorkflowEngine(
        resolve_workflow(job.workflow_version).build_steps(),
        budget=BudgetGuard(
            # No per-run cap passed, and that is the point: the guard reads this request's
            # own ceiling from the work order at every check, so a cap the operator raises
            # mid-run takes effect at the next step rather than at the next execution. An
            # operator who set £0.50 on this request still means £0.50 until they say
            # otherwise; the global default is never substituted for it.
            monthly_cap_gbp=settings.monthly_budget_gbp,
            warn_ratio=settings.budget_warn_ratio,
        ),
    )

    outputs = await engine.run(
        session,
        job=job,
        services={
            "settings": settings,
            "provider": provider,
            "router": Router(settings),
            "store": store,
            "sec_client": sec_client,
            # Optional in the same way `session_factory` is: supplied by the worker, absent
            # in tests that want no network. A research worker offered no fetcher simply
            # does not see `fetch_known_url` on its menu.
            "fetcher": fetcher,
            # Present only where the caller runs with real sessions (the ARQ worker).
            # Without it the engine runs its waves one node at a time on this session,
            # which is what the savepoint-fixtured tests need.
            "session_factory": session_factory,
        },
        stop_after=stop_after,
    )

    spend = await spend_so_far(session, job_id=job.id)

    # A run that reached the end without pausing or failing is done. The render step sets
    # this too; setting it here as well covers a workflow that ends on a step which does
    # not, so "did it finish?" never depends on which step happened to be last.
    if job.status is JobStatus.RUNNING:
        job.status = JobStatus.SUCCEEDED
        job.finished_at = datetime.now(UTC)
        await session.commit()

    _log.info(
        "run.executed",
        job_id=str(job.id),
        status=job.status.value,
        steps=len(outputs),
        spend_gbp=str(spend),
    )
    return RunOutcome(job=job, outputs=outputs, status=job.status, spend_gbp=spend)


# What a step that has not started has spent, at the scale `job_steps.cost_gbp` stores.
# `Decimal(0)` would render "£0" beside a finished step's "£0.0000" and read as a different
# kind of zero.
_NOT_YET_SPENT: Final = Decimal("0.0000")


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One step as the console shows it, whether or not it has started.

    A step the run has not reached yet has no ``job_steps`` row — the engine creates one
    when it begins. Rendering only the rows that exist means an operator watching the first
    minute of a run sees a single line and no idea how many follow it, so the declared
    workflow supplies the rest at ``QUEUED``.
    """

    key: str
    status: JobStatus
    cost_gbp: Decimal
    attempt: int = 0
    error: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status.value,
            "attempt": self.attempt,
            "cost_gbp": str(self.cost_gbp),
            "error": self.error,
            # The server's clock, so the console can tick an elapsed time without asking
            # the browser to guess when the step began.
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }


@dataclass(slots=True)
class RunState:
    """What a run looks like right now, for the console and the API."""

    job: Job
    steps: list[JobStep]
    spend_gbp: Decimal

    # Every step this run's workflow declares, in order. Empty when the job was produced by
    # a workflow version this build no longer has, in which case the timeline falls back to
    # the rows that exist rather than inventing a shape from the wrong definition.
    declared_steps: tuple[str, ...] = ()

    @property
    def current_step(self) -> JobStep | None:
        running = [s for s in self.steps if s.status is JobStatus.RUNNING]
        return running[-1] if running else None

    @property
    def completed_keys(self) -> list[str]:
        return [s.step_key for s in self.steps if s.status is JobStatus.SUCCEEDED]

    @property
    def budget_scope(self) -> str | None:
        """Which ceiling stopped the run — ``per_run``, ``monthly``, or ``None``.

        Read back off the step that recorded the refusal rather than recomputed, so the
        console reports the cap that actually fired. The distinction is not cosmetic: raising
        the request's own cap releases a per-run stop and does nothing at all to a monthly
        one.
        """
        for step in reversed(self.steps):
            error = step.error or {}
            if error.get("code") == "budget_exceeded":
                scope = error.get("context", {}).get("scope")
                return str(scope) if scope else None
        return None

    @property
    def is_terminal(self) -> bool:
        return self.job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }

    @property
    def timeline(self) -> list[TimelineEntry]:
        """The declared steps, each carrying whatever the run has recorded against it.

        Declared order rather than ``sequence``, because the point is to show what is still
        to come. A recorded step whose key is not declared is appended rather than dropped:
        a run started under a different workflow version is exactly when an operator most
        needs to see everything that happened.
        """
        recorded = {step.step_key: step for step in self.steps}
        keys = [*self.declared_steps]
        keys += [key for key in recorded if key not in self.declared_steps]

        entries = []
        for key in keys:
            step = recorded.get(key)
            if step is None:
                entries.append(
                    TimelineEntry(key=key, status=JobStatus.QUEUED, cost_gbp=_NOT_YET_SPENT)
                )
                continue
            entries.append(
                TimelineEntry(
                    key=key,
                    status=step.status,
                    cost_gbp=step.cost_gbp,
                    attempt=step.attempt,
                    error=step.error,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                )
            )
        return entries

    def as_dict(self) -> dict[str, Any]:
        """The state frame, as the event stream and the console both read it.

        **Nothing derived from the current time belongs in here.** The stream hashes this
        to decide whether anything has changed, so a wall-clock field would make every poll
        look like news and turn a one-second poll into a one-second event. Liveness is a
        heartbeat event of its own; see :mod:`aer.api.sse`.
        """
        timeline = self.timeline
        return {
            "job_id": str(self.job.id),
            "status": self.job.status.value,
            "spend_gbp": str(self.spend_gbp),
            "steps_total": len(timeline),
            "steps_done": sum(1 for entry in timeline if entry.status is JobStatus.SUCCEEDED),
            "current_step": self.current_step.step_key if self.current_step else None,
            "steps": [entry.as_dict() for entry in timeline],
        }


async def run_state(session: AsyncSession, *, job_id: uuid.UUID) -> RunState:
    """The current state of a run.

    Raises:
        ValidationError: If there is no such job.
    """
    job = await session.get(Job, job_id)
    if job is None:
        message = f"No run {job_id}."
        raise ValidationError(message, context={"job_id": str(job_id)})

    steps = list(
        await session.scalars(
            select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.sequence)
        )
    )
    return RunState(
        job=job,
        steps=steps,
        spend_gbp=await spend_so_far(session, job_id=job_id),
        declared_steps=declared_steps(job.workflow_version),
    )


def declared_steps(workflow_version: str) -> tuple[str, ...]:
    """The step keys a workflow version declares, in order.

    Read from the registry rather than stored on the job, so the console cannot show a plan
    the engine would not follow. A version this build genuinely does not have still returns
    nothing — see :attr:`RunState.declared_steps` — but it is now the registry that decides
    that, rather than an equality test against whichever workflow happened to be imported
    here. Every registered workflow answers; only an unregistered one is blank, and the log
    line says which it was.
    """
    try:
        return tuple(step.key for step in resolve_workflow(workflow_version).build_steps())
    except WorkflowRegistryError:
        _log.warning("runs.workflow_unregistered", workflow_version=workflow_version)
        return ()


async def awaiting_approval_count(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """How many of this operator's research runs are stopped at a gate.

    The shell's `approvals` badge (`web/shell/badges.py`). Scoped through the mandate
    rather than through the work order, because the provider that registers this declares
    itself the research tool's: a future monitor run stopped at its own gate is another
    tool's number to show, under its own label.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(Job)
        .join(ResearchRequest, ResearchRequest.id == Job.work_order_id)
        .where(Job.status == JobStatus.AWAITING_APPROVAL, ResearchRequest.user_id == user_id)
    )
    return int(total or 0)
