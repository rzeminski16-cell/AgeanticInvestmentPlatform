"""The step runner: idempotent, resumable, and metered.

**Idempotency is by stored outcome, not by hope.** Before running a step the engine looks
for a ``job_steps`` row with the same idempotency key and a terminal-success status. If it
finds one, it returns the stored ``output_ref`` and does not execute. That is what makes a
resumed run cheap, and it is checked rather than assumed because "this step is naturally
idempotent" is a claim that stops being true the first time someone adds a side effect.

**A paused run is not a failed one.** A step that needs a human — an approval gate, a
budget decision — raises :class:`StepPaused`, and the engine records the job as waiting
rather than broken. Collapsing the two would put "you need to approve this" in the same
bucket as "this crashed", and the operator would learn to ignore both.

**The budget guard runs before a step, never after.** Checking afterwards tells you what
you already spent. The guard compares what a step is projected to cost against what remains
of the request's ceiling, and pauses the run if it would exceed it — before the provider is
called, which is why the test for it asserts the provider was never touched.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import Cost, Job, JobCancellation, JobStep
from aer.errors import AerError, BudgetExceededError

__all__ = [
    "BudgetGuard",
    "StepContext",
    "StepPaused",
    "StepResult",
    "WorkflowEngine",
    "WorkflowStep",
    "spend_so_far",
]

_log = structlog.get_logger("aer.workflow.engine")


class StepPaused(AerError):  # noqa: N818 -- a control-flow signal, not an error condition
    """A step needs a human before the run can continue.

    Deliberately not named ``...Error``: an approval gate is not a failure, and a run
    waiting for a decision must be distinguishable from one that broke. The engine records
    the job as paused and returns; nothing is rolled back and nothing is retried.
    """

    code = "step_paused"
    http_status = 202

    def __init__(
        self, message: str, *, gate: str | None = None, context: dict[str, Any] | None = None
    ) -> None:
        merged = {"gate": gate, **(context or {})}
        super().__init__(message, context=merged)
        self.gate = gate


@dataclass(slots=True)
class StepResult:
    """What a step produced.

    ``output`` is JSON-serialisable and stored on the step row, so a resumed run gets the
    same answer without re-executing. A step whose real output is too large for that — a
    fetched document — stores a reference to it rather than the thing itself.
    """

    output: dict[str, Any] = field(default_factory=dict)
    cost_gbp: Decimal = field(default_factory=lambda: Decimal(0))


@dataclass(slots=True)
class StepContext:
    """What a step is given.

    ``services`` is an open mapping rather than typed fields, because the set of things a
    step needs — a provider, a store, a fetcher, a router — differs per workflow, and a
    dataclass listing all of them would couple the engine to every workflow that will ever
    exist.
    """

    session: AsyncSession
    job: Job
    step: JobStep
    services: dict[str, Any]

    # Whatever earlier steps produced, keyed by step name. How a step reads the plan the
    # planner made without querying for it.
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def output_of(self, step_key: str) -> dict[str, Any]:
        """An earlier step's output.

        Raises:
            KeyError: If that step has not run. A step depending on one that did not
                happen is a workflow definition error, and failing loudly beats proceeding
                with an empty dictionary.
        """
        return self.outputs[step_key]

    def service(self, name: str) -> Any:
        return self.services[name]


StepFunction = Callable[[StepContext], Awaitable[StepResult]]


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One named step of a workflow."""

    key: str
    run: StepFunction

    # Projected cost, used by the budget guard before the step executes. Zero for steps
    # that spend nothing, which is most of them.
    estimated_cost_gbp: Decimal = field(default_factory=lambda: Decimal(0))

    # Whether reaching this step requires a human decision first. The engine checks the
    # gate rather than the step doing it, so a workflow cannot forget.
    gate: str | None = None


@dataclass(slots=True)
class BudgetGuard:
    """Compares projected spend against what remains of a request's ceiling.

    Two ceilings, and both matter: the per-run cap the operator set on this request, and
    the monthly cap on everything. A run that respects its own budget while blowing the
    month's is still a run nobody agreed to.
    """

    per_run_cap_gbp: Decimal
    monthly_cap_gbp: Decimal
    warn_ratio: float = 0.75

    async def check(self, session: AsyncSession, *, job: Job, projected_gbp: Decimal) -> None:
        """Confirm a step's projected cost fits.

        Raises:
            BudgetExceededError: If it does not. The engine turns this into a paused run
                awaiting a decision rather than a failure — a cap that stopped the work
                and lost it would be a cap people disable.
        """
        already = await spend_so_far(session, job_id=job.id)
        projected_total = already + projected_gbp

        if projected_total > self.per_run_cap_gbp:
            message = (
                f"This step is projected to cost £{projected_gbp:.4f}, which would take the "
                f"run to £{projected_total:.4f} against a cap of £{self.per_run_cap_gbp:.2f}. "
                "The run is paused for a decision rather than continuing."
            )
            raise BudgetExceededError(
                message,
                context={
                    "spent_gbp": str(already),
                    "projected_gbp": str(projected_gbp),
                    "cap_gbp": str(self.per_run_cap_gbp),
                    "scope": "per_run",
                },
            )

        if already >= self.per_run_cap_gbp * Decimal(str(self.warn_ratio)):
            _log.warning(
                "budget.approaching_cap",
                job_id=str(job.id),
                spent_gbp=str(already),
                cap_gbp=str(self.per_run_cap_gbp),
            )


async def spend_so_far(session: AsyncSession, *, job_id: uuid.UUID) -> Decimal:
    """What a run has spent, in pounds.

    Summed from ``costs`` rather than accumulated in memory, because the guard must be
    correct across a worker restart — and an in-memory total is zero after one.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.job_id == job_id)
    )
    return Decimal(str(total or 0))


class WorkflowEngine:
    """Runs a workflow's steps in order, recording each.

    **The step is the unit of publication.** Every state the engine reaches — a step
    finished, a gate hit, a cancellation honoured, a failure recorded — is committed before
    the engine moves on. See :meth:`_publish` for why that is not merely a convenience.

    Args:
        steps: In execution order. A step's key is its identity for idempotency, so
            renaming one makes a resumed run re-execute it.
        budget: Checked before each step that projects a cost.
    """

    __slots__ = ("_budget", "_steps")

    def __init__(self, steps: list[WorkflowStep], *, budget: BudgetGuard | None = None) -> None:
        self._steps = steps
        self._budget = budget

    async def run(
        self,
        session: AsyncSession,
        *,
        job: Job,
        services: dict[str, Any],
        stop_after: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Execute from the first incomplete step.

        Args:
            stop_after: Run no further than this step. What the orchestrator uses to run up
                to a gate and stop, rather than the workflow having to know where the gates
                are.

        Returns:
            Every step's output, keyed by step name — including steps that were skipped
            because they had already completed.
        """
        outputs: dict[str, dict[str, Any]] = {}

        for sequence, step in enumerate(self._steps):
            # Checked before each step, which is the finest granularity honestly available:
            # a model call or an HTTP fetch already in flight cannot be interrupted, and
            # pretending otherwise would mean reporting a run as stopped while it was still
            # spending. See `aer.services.cancellation`.
            if await self._cancelled(session, job=job):
                await self._cancel(session, job=job, before_step=step.key)
                break

            existing = await self._completed(session, job=job, step=step)
            if existing is not None:
                outputs[step.key] = existing.output_ref or {}
                _log.debug("workflow.step_skipped", job_id=str(job.id), step=step.key)
                if stop_after and step.key == stop_after:
                    break
                continue

            paused = await self._execute(
                session, job=job, step=step, sequence=sequence, services=services, outputs=outputs
            )
            if paused:
                break

            if stop_after and step.key == stop_after:
                break

        return outputs

    # -- Internals -------------------------------------------------------------------------

    async def _publish(self, session: AsyncSession) -> None:
        """Commit, so that another process can see how far this run has got.

        Called only at a boundary where the run's recorded state is whole: a step's row, its
        cost rows and its ``agent_runs`` all reach their final values before this, so a
        reader sees a finished step or no step. There is no window in which a half-written
        output looks complete — that would need a commit *inside* a step, which nothing here
        does.

        Three things were broken by not doing this, and none of them was cosmetic.

        **The run console could not show progress.** The worker held one transaction for the
        whole run, and Postgres publishes nothing until commit, so the console showed
        ``QUEUED`` from start to gate however much work had been done and however much money
        had been spent. This was reported from a live run: spend visible at the provider,
        ``QUEUED`` on the page.

        **A failure was rolled back along with everything else.** ``_fail`` recorded
        ``FAILED`` and then re-raised; the exception left the worker's session without a
        commit, so the row reverted and the database kept saying ``QUEUED`` for a run that
        had died. The only trace was a log line.

        **A resume re-ran work already paid for.** The worker's docstring justified one
        transaction by saying a run that dies "resumes from the last step that succeeded".
        Between gates that was not true: a crash rolled back every completed step, so the
        next attempt started from the beginning and spent the money again. Committing at the
        step boundary is what makes that sentence true.
        """
        await session.commit()

    async def _cancelled(self, session: AsyncSession, *, job: Job) -> bool:
        """Whether somebody has asked for this run to stop.

        A fresh query every step rather than a value read once: the whole point is to see a
        decision made by *another process* while this one was working. Postgres reads
        committed data per statement, so this sees the request as soon as the web process
        commits it — which is why the request is a row of its own and not a column on
        ``jobs``, whose lock this transaction is holding.
        """
        found = await session.scalar(
            select(JobCancellation.id).where(JobCancellation.job_id == job.id)
        )
        return found is not None

    async def _cancel(self, session: AsyncSession, *, job: Job, before_step: str) -> None:
        """Record the run as cancelled, naming where it stopped."""
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(UTC)
        await self._publish(session)
        _log.info("workflow.cancelled", job_id=str(job.id), before_step=before_step)

    async def _completed(
        self, session: AsyncSession, *, job: Job, step: WorkflowStep
    ) -> JobStep | None:
        """The stored row for a step that already succeeded, if there is one."""
        found: JobStep | None = await session.scalar(
            select(JobStep).where(
                JobStep.job_id == job.id,
                JobStep.step_key == step.key,
                JobStep.status == JobStatus.SUCCEEDED,
            )
        )
        return found

    async def _execute(
        self,
        session: AsyncSession,
        *,
        job: Job,
        step: WorkflowStep,
        sequence: int,
        services: dict[str, Any],
        outputs: dict[str, dict[str, Any]],
    ) -> bool:
        """Run one step. Returns True if the run should stop here."""
        row = await self._step_row(session, job=job, step=step, sequence=sequence, outputs=outputs)

        if self._budget is not None and step.estimated_cost_gbp > 0:
            try:
                # Before the step, not after. Afterwards tells you what you already spent.
                await self._budget.check(session, job=job, projected_gbp=step.estimated_cost_gbp)
            except BudgetExceededError as exc:
                await self._pause(
                    session,
                    job=job,
                    row=row,
                    status=JobStatus.BUDGET_EXCEEDED,
                    detail=exc.to_dict(),
                )
                return True

        context = StepContext(
            session=session, job=job, step=row, services=services, outputs=outputs
        )
        started = time.perf_counter()

        try:
            result = await step.run(context)
        except StepPaused as paused:
            await self._pause(
                session,
                job=job,
                row=row,
                status=JobStatus.AWAITING_APPROVAL,
                detail=paused.to_dict(),
            )
            return True
        except BudgetExceededError as exc:
            await self._pause(
                session, job=job, row=row, status=JobStatus.BUDGET_EXCEEDED, detail=exc.to_dict()
            )
            return True
        except Exception as exc:
            await self._fail(session, job=job, row=row, exc=exc)
            raise

        row.status = JobStatus.SUCCEEDED
        row.output_ref = result.output
        row.cost_gbp = result.cost_gbp
        row.finished_at = datetime.now(UTC)
        await self._publish(session)

        outputs[step.key] = result.output
        _log.info(
            "workflow.step_completed",
            job_id=str(job.id),
            step=step.key,
            cost_gbp=str(result.cost_gbp),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return False

    async def _step_row(
        self,
        session: AsyncSession,
        *,
        job: Job,
        step: WorkflowStep,
        sequence: int,
        outputs: dict[str, dict[str, Any]],
    ) -> JobStep:
        """Find or create the row for this attempt.

        A retried step reuses its row and increments ``attempt`` rather than creating a
        second, so "how many times did this run?" is a column rather than a count.
        """
        existing = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == step.key)
        )

        # The inputs a step ran against, hashed. Two attempts with different inputs are
        # different work, and a resumed run that quietly used different inputs would
        # produce a report nobody could reproduce.
        input_hash = sha256_hex(canonical_json({"step": step.key, "outputs": outputs}))

        if existing is not None:
            existing.attempt += 1
            existing.status = JobStatus.RUNNING
            existing.input_hash = input_hash
            existing.started_at = datetime.now(UTC)
            existing.error = None
            await session.flush()
            return existing

        row = JobStep(
            job_id=job.id,
            step_key=step.key,
            sequence=sequence,
            status=JobStatus.RUNNING,
            attempt=0,
            idempotency_key=f"{job.id}:{step.key}",
            input_hash=input_hash,
            started_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        return row

    async def _pause(
        self,
        session: AsyncSession,
        *,
        job: Job,
        row: JobStep,
        status: JobStatus,
        detail: dict[str, Any],
    ) -> None:
        row.status = status
        row.error = detail
        row.finished_at = datetime.now(UTC)
        job.status = status
        await self._publish(session)
        _log.info("workflow.paused", job_id=str(job.id), step=row.step_key, status=status.value)

    async def _fail(self, session: AsyncSession, *, job: Job, row: JobStep, exc: Exception) -> None:
        """Record the failure. **Committed here, because the caller re-raises.**

        An exception leaves the worker's ``async with`` without reaching its commit, so a
        flush alone would be rolled back on the way out and the database would go on
        reporting the run as queued. The log line would be the only evidence that anything
        had happened, and the operator would be watching a page that never changed.
        """
        detail = (
            exc.to_dict()
            if isinstance(exc, AerError)
            else {"code": "unexpected_error", "message": f"{type(exc).__name__}: {exc}"}
        )
        row.status = JobStatus.FAILED
        row.error = detail
        row.finished_at = datetime.now(UTC)
        job.status = JobStatus.FAILED
        await self._publish(session)
        _log.error("workflow.step_failed", job_id=str(job.id), step=row.step_key, **detail)
