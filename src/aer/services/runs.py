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
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import JobStatus
from aer.db.models import Job, JobStep, ResearchRequest
from aer.errors import ValidationError
from aer.providers.protocol import LLMProvider
from aer.providers.router import Router
from aer.storage.protocol import ArtefactStore
from aer.version import git_sha
from aer.workflow.engine import BudgetGuard, WorkflowEngine, spend_so_far
from aer.workflow.workflows.vertical_slice_v1 import WORKFLOW_VERSION, build_steps

__all__ = ["RunOutcome", "execute", "run_state", "start_run"]

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
        return self.status in {JobStatus.AWAITING_APPROVAL, JobStatus.BUDGET_EXCEEDED}


async def start_run(session: AsyncSession, *, request: ResearchRequest) -> Job:
    """Create the job for a request, or return the one that exists.

    One job per request in Phase 1. A second run of the same request is a Phase 2 concern
    and needs a story about which report is current; returning the existing job is the
    honest behaviour until there is one.
    """
    existing = await session.scalar(
        # Newest first, with a never-started job last. `jobs` has no created_at -- a job's
        # life begins when it starts -- so `started_at` is the ordering column, and
        # nulls-last keeps a queued-but-unstarted job from shadowing a real one.
        select(Job).where(Job.request_id == request.id).order_by(Job.started_at.desc().nullslast())
    )
    if existing is not None:
        return existing

    job = Job(
        request_id=request.id,
        workflow_version=WORKFLOW_VERSION,
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
        workflow=WORKFLOW_VERSION,
    )
    return job


async def execute(
    session: AsyncSession,
    *,
    job: Job,
    settings: Settings,
    provider: LLMProvider,
    store: ArtefactStore,
    sec_client: Any,
    stop_after: str | None = None,
) -> RunOutcome:
    """Run the workflow from the first incomplete step.

    The same call whether this is the first attempt, a continuation after an approval, or a
    recovery after the worker died. A step that already succeeded returns its stored output
    and does not execute.
    """
    request = await session.get(ResearchRequest, job.request_id)
    if request is None:
        message = f"Job {job.id} has no research request."
        raise ValidationError(message, context={"job_id": str(job.id)})

    job.status = JobStatus.RUNNING
    await session.flush()

    engine = WorkflowEngine(
        build_steps(),
        budget=BudgetGuard(
            # The request's own ceiling, not the global default: an operator who set £0.50
            # on this request meant £0.50 on this request.
            per_run_cap_gbp=request.max_cost_gbp,
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
        await session.flush()

    _log.info(
        "run.executed",
        job_id=str(job.id),
        status=job.status.value,
        steps=len(outputs),
        spend_gbp=str(spend),
    )
    return RunOutcome(job=job, outputs=outputs, status=job.status, spend_gbp=spend)


@dataclass(slots=True)
class RunState:
    """What a run looks like right now, for the console and the API."""

    job: Job
    steps: list[JobStep]
    spend_gbp: Decimal

    @property
    def current_step(self) -> JobStep | None:
        running = [s for s in self.steps if s.status is JobStatus.RUNNING]
        return running[-1] if running else None

    @property
    def completed_keys(self) -> list[str]:
        return [s.step_key for s in self.steps if s.status is JobStatus.SUCCEEDED]

    @property
    def is_terminal(self) -> bool:
        return self.job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": str(self.job.id),
            "status": self.job.status.value,
            "spend_gbp": str(self.spend_gbp),
            "steps": [
                {
                    "key": step.step_key,
                    "status": step.status.value,
                    "attempt": step.attempt,
                    "cost_gbp": str(step.cost_gbp),
                    "error": step.error,
                }
                for step in self.steps
            ],
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
    return RunState(job=job, steps=steps, spend_gbp=await spend_so_far(session, job_id=job_id))
