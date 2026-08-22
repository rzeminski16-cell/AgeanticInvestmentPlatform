"""The step runner: a dependency graph, idempotent, resumable, and metered.

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
called, which is why the test for it asserts the provider was never touched. Where nodes
run concurrently, a node's projection also counts the estimates of everything already in
flight, because two siblings each individually under the cap can jointly be over it.

**The steps form a graph, and the graph is code** (`docs/PLAN.md` §2.5). A step that
declares no dependencies chains after the one declared before it — which is every workflow
the first three phases wrote, unchanged. A step that declares ``needs`` is placed in the
graph explicitly, and independent nodes may run concurrently, bounded by
:data:`MAX_PARALLEL_NODES`. Parallelism is for breadth of source coverage, not a belief
that more agents are better, and the bound is deliberately not configuration.

**Concurrency needs its own sessions.** One ``AsyncSession`` must never be used from two
tasks, so parallel nodes each open a session from ``services["session_factory"]`` and
commit their own step boundary; the coordinator alone touches the job row. Without a
factory in services the engine is exactly the serial engine it always was — which is also
the path every single-file test exercises, on the savepoint-joined session the fixtures
provide.

**Stopping is drain, never abandon.** A pause, a budget refusal, a failure or a
cancellation stops *scheduling*; nodes already in flight are awaited to their own recorded
outcome first. Work in flight is work being paid for, and a run whose record says "paused"
while a node was still writing would be a record that lies. A failed node abandons its
dependants — and only its dependants: an independent branch completes and keeps its work
before the failure is re-raised.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import JobStatus
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import Cost, Job, JobCancellation, JobStep
from aer.errors import AerError, BudgetExceededError
from aer.tracing import span

__all__ = [
    "MAX_PARALLEL_NODES",
    "BudgetGuard",
    "StepContext",
    "StepPaused",
    "StepResult",
    "WorkflowDefinitionError",
    "WorkflowEngine",
    "WorkflowStep",
    "spend_so_far",
    "spend_this_month",
]

_log = structlog.get_logger("aer.workflow.engine")

# The hard bound on concurrent nodes, from `docs/PLAN.md` §2.5: "max 7 workers". A module
# constant rather than a setting, because a bound that can be raised in configuration is a
# bound that will be — and the number is part of the cost model the budget was set against.
MAX_PARALLEL_NODES: Final = 7


class WorkflowDefinitionError(AerError):
    """A workflow's graph is malformed.

    Always a code defect caught at construction: a duplicate key, a dependency on a step
    that is not declared earlier, an unknown ``stop_after``. Each would otherwise surface
    mid-run as a hang or a silently wrong order, with money already spent.
    """

    code = "workflow_definition"


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

    def optional_service(self, name: str) -> Any:
        """A service that may legitimately not be there, or ``None``.

        For capabilities rather than dependencies. A licensed market-data client is
        configured on some machines and not others, and a step that handles its absence
        should not also have to distinguish "no subscription" from "this workflow harness
        does not supply that key" — both mean the same thing to the step, and the
        distinction has no action attached to it.

        Not a general escape from :meth:`service`. Anything a step cannot proceed without
        goes through that one, so a missing dependency still fails loudly.
        """
        return self.services.get(name)


StepFunction = Callable[[StepContext], Awaitable[StepResult]]


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One named node of a workflow's graph."""

    key: str
    run: StepFunction

    # Projected cost, used by the budget guard before the step executes. Zero for steps
    # that spend nothing, which is most of them.
    estimated_cost_gbp: Decimal = field(default_factory=lambda: Decimal(0))

    # Whether reaching this step requires a human decision first. The engine checks the
    # gate rather than the step doing it, so a workflow cannot forget.
    gate: str | None = None

    # Where this node sits in the graph. ``None`` — the default — chains it after the step
    # declared immediately before it, which is the linear workflow every phase so far
    # wrote and means an existing declaration keeps its exact order with no edits. An
    # explicit set names the dependencies, all of which must be declared earlier in the
    # list; an empty set is a node with no dependencies at all, free to start immediately.
    needs: frozenset[str] | None = None


@dataclass(slots=True)
class BudgetGuard:
    """Compares projected spend against what remains of a request's ceiling.

    Two ceilings, and both matter: the per-run cap the operator set on this request, and
    the monthly cap on everything. A run that respects its own budget while blowing the
    month's is still a run nobody agreed to.

    **The refusal names which ceiling it hit**, because the remedies are different and only
    one of them is on the request: raising a request's own cap does nothing whatever to a
    monthly stop.
    """

    per_run_cap_gbp: Decimal
    monthly_cap_gbp: Decimal
    warn_ratio: float = 0.75

    async def check(
        self,
        session: AsyncSession,
        *,
        job: Job,
        projected_gbp: Decimal,
        now: datetime | None = None,
    ) -> None:
        """Confirm a step's projected cost fits under **both** ceilings.

        Args:
            now: The moment that decides which calendar month is being counted. Injected
                so the boundary can be tested without waiting for one.

        Raises:
            BudgetExceededError: If it does not. The engine turns this into a paused run
                awaiting a decision rather than a failure — a cap that stopped the work
                and lost it would be a cap people disable.
        """
        already = await spend_so_far(session, job_id=job.id)
        self._refuse_if_over(
            scope="per_run",
            noun="run",
            spent=already,
            projected_gbp=projected_gbp,
            cap=self.per_run_cap_gbp,
            remedy="Raise the cap on this request to continue.",
        )

        # This run's own rows are inside the window too, so the month's total is
        # `this_month + projected` — the run's spend is not added a second time.
        this_month = await spend_this_month(session, now=now or datetime.now(UTC))
        self._refuse_if_over(
            scope="monthly",
            noun="month",
            spent=this_month,
            projected_gbp=projected_gbp,
            cap=self.monthly_cap_gbp,
            remedy=(
                "This is the ceiling across every run this month, so raising this "
                "request's own cap will not release it."
            ),
        )

        self._warn_if_near(job, scope="per_run", spent=already, cap=self.per_run_cap_gbp)
        self._warn_if_near(job, scope="monthly", spent=this_month, cap=self.monthly_cap_gbp)

    def _refuse_if_over(
        self,
        *,
        scope: str,
        noun: str,
        spent: Decimal,
        projected_gbp: Decimal,
        cap: Decimal,
        remedy: str,
    ) -> None:
        total = spent + projected_gbp
        if total <= cap:
            return
        message = (
            f"This step is projected to cost £{projected_gbp:.4f}, which would take the "
            f"{noun} to £{total:.4f} against a cap of £{cap:.2f}. The run is paused for a "
            f"decision rather than continuing. {remedy}"
        )
        raise BudgetExceededError(
            message,
            context={
                "spent_gbp": str(spent),
                "projected_gbp": str(projected_gbp),
                "cap_gbp": str(cap),
                "scope": scope,
            },
        )

    def _warn_if_near(self, job: Job, *, scope: str, spent: Decimal, cap: Decimal) -> None:
        if spent >= cap * Decimal(str(self.warn_ratio)):
            _log.warning(
                "budget.approaching_cap",
                scope=scope,
                job_id=str(job.id),
                spent_gbp=str(spent),
                cap_gbp=str(cap),
            )


def _detail_of(exc: Exception) -> dict[str, Any]:
    """The error as it is stored on the step row.

    ``AerError`` already knows how to describe itself with a stable code; anything else is
    a defect, and its type name is the most useful thing there is to say about it.
    """
    if isinstance(exc, AerError):
        return exc.to_dict()
    return {"code": "unexpected_error", "message": f"{type(exc).__name__}: {exc}"}


async def spend_this_month(session: AsyncSession, *, now: datetime) -> Decimal:
    """Everything every run has spent in ``now``'s calendar month, in pounds.

    The month is UTC's, because ``occurred_at`` is stored in UTC. A boundary that moved with
    the reader's timezone would reset the cap at a different instant depending on where the
    operator happened to be standing.

    **No join.** ``costs.job_id`` is ``SET NULL`` rather than ``CASCADE`` precisely so that
    deleting a request cannot erase what it cost (migration 0009); reaching the month's total
    through the job would hand that escape straight back.
    """
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.occurred_at >= start)
    )
    return Decimal(str(total))


async def spend_so_far(session: AsyncSession, *, job_id: uuid.UUID) -> Decimal:
    """What a run has spent, in pounds.

    Summed from ``costs`` rather than accumulated in memory, because the guard must be
    correct across a worker restart — and an in-memory total is zero after one.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.job_id == job_id)
    )
    return Decimal(str(total or 0))


class _Outcome(StrEnum):
    """What one node's execution came to, for the coordinator to act on."""

    SUCCEEDED = "succeeded"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass(slots=True)
class _NodeReport:
    """A parallel node's result, carried back across the task boundary.

    The node has already recorded its own ``job_steps`` row and committed it; what remains
    — the job-level status transition, the shared outputs, re-raising a failure — belongs
    to the coordinator, which is the only thing allowed to touch the control session.
    """

    step: WorkflowStep
    outcome: _Outcome
    output: dict[str, Any] = field(default_factory=dict)
    pause_status: JobStatus | None = None
    exception: Exception | None = None


class WorkflowEngine:
    """Runs a workflow's graph, recording each node.

    **The step is the unit of publication.** Every state the engine reaches — a step
    finished, a gate hit, a cancellation honoured, a failure recorded — is committed before
    the engine moves on. See :meth:`_publish` for why that is not merely a convenience.

    Args:
        steps: The graph, in declaration order. A step's key is its identity for
            idempotency, so renaming one makes a resumed run re-execute it. Dependencies
            (:attr:`WorkflowStep.needs`) must name steps declared earlier — which is what
            makes a cycle unrepresentable rather than merely checked for.
        budget: Checked before each node that projects a cost.
        max_parallel: The fan-out bound. Overridable *downwards* for tests; the ceiling is
            :data:`MAX_PARALLEL_NODES` and asking for more is a definition error.
    """

    __slots__ = ("_budget", "_max_parallel", "_needs", "_sequence", "_steps")

    def __init__(
        self,
        # A sequence rather than a list: the registry hands out a workflow's steps, and a
        # caller that could append to them could change what a version means mid-run.
        steps: Sequence[WorkflowStep],
        *,
        budget: BudgetGuard | None = None,
        max_parallel: int = MAX_PARALLEL_NODES,
    ) -> None:
        if not 1 <= max_parallel <= MAX_PARALLEL_NODES:
            message = (
                f"max_parallel is {max_parallel}; the bound is 1 to {MAX_PARALLEL_NODES}. "
                "The ceiling is part of the cost model and is not raised per workflow."
            )
            raise WorkflowDefinitionError(message, context={"max_parallel": max_parallel})

        self._steps = steps
        self._budget = budget
        self._max_parallel = max_parallel
        self._needs: dict[str, frozenset[str]] = {}
        self._sequence: dict[str, int] = {}

        declared: list[str] = []
        for index, step in enumerate(steps):
            if step.key in self._needs:
                message = f"Two steps claim the key {step.key!r}."
                raise WorkflowDefinitionError(message, context={"step": step.key})
            if step.needs is None:
                # The chain default: after the previously declared step. This is every
                # linear workflow, unchanged.
                resolved = frozenset({declared[-1]}) if declared else frozenset()
            else:
                unknown = step.needs - set(declared)
                if unknown:
                    message = (
                        f"The step {step.key!r} needs {sorted(unknown)}, which are not "
                        "declared earlier in the workflow. Dependencies point backwards "
                        "in the declaration; that is what makes a cycle unrepresentable."
                    )
                    raise WorkflowDefinitionError(
                        message, context={"step": step.key, "unknown": sorted(unknown)}
                    )
                resolved = step.needs
            self._needs[step.key] = resolved
            self._sequence[step.key] = index
            declared.append(step.key)

    async def run(
        self,
        session: AsyncSession,
        *,
        job: Job,
        services: dict[str, Any],
        stop_after: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Execute every incomplete node the target set reaches.

        Args:
            stop_after: Run no further than this step — its ancestors and itself, nothing
                else. What the orchestrator uses to run up to a gate and stop, rather than
                the workflow having to know where the gates are. An unknown key is refused:
                a typo here would silently run the whole workflow, which is the
                spending-past-the-gate direction of wrong.

        Returns:
            Every completed step's output, keyed by step name — including steps that were
            skipped because they had already completed.
        """
        outputs: dict[str, dict[str, Any]] = {}
        factory: async_sessionmaker[AsyncSession] | None = services.get("session_factory")

        pending = [step for step in self._steps if step.key in self._targets(stop_after)]
        abandoned: set[str] = set()
        first_failure: Exception | None = None
        stop_scheduling = False

        while pending and not stop_scheduling:
            # Checked before each scheduling round, which is the finest granularity
            # honestly available: a model call or an HTTP fetch already in flight cannot
            # be interrupted, and pretending otherwise would mean reporting a run as
            # stopped while it was still spending. See `aer.services.cancellation`.
            if await self._cancelled(session, job=job):
                await self._cancel(session, job=job, before_step=pending[0].key)
                return outputs

            # A node whose dependency was abandoned is unsatisfiable: abandon it too, and
            # only it — an independent branch stays pending.
            blocked = [step for step in pending if self._needs[step.key] & abandoned]
            for step in blocked:
                pending.remove(step)
                abandoned.add(step.key)
                _log.info("workflow.step_abandoned", job_id=str(job.id), step=step.key)

            ready = [step for step in pending if self._needs[step.key] <= outputs.keys()]
            if not ready:
                break

            # Already-completed nodes cost nothing and need no scheduling decisions.
            skipped = False
            for step in list(ready):
                existing = await self._completed(session, job=job, step=step)
                if existing is not None:
                    outputs[step.key] = existing.output_ref or {}
                    pending.remove(step)
                    ready.remove(step)
                    skipped = True
                    _log.debug("workflow.step_skipped", job_id=str(job.id), step=step.key)
            if skipped and not ready:
                continue

            if len(ready) == 1 or factory is None or self._max_parallel == 1:
                # The serial path: today's engine, on the caller's session. Every linear
                # workflow — a chain has one ready node at a time — stays on this path
                # whatever services carry.
                step = ready[0]
                pending.remove(step)
                paused = await self._execute(
                    session,
                    job=job,
                    step=step,
                    sequence=self._sequence[step.key],
                    services=services,
                    outputs=outputs,
                )
                if paused:
                    return outputs
                continue

            reports = await self._run_wave(
                session,
                job=job,
                wave=ready[: self._max_parallel],
                factory=factory,
                services=services,
                outputs=outputs,
            )
            stop_scheduling, failure = await self._apply_reports(
                session,
                job=job,
                reports=reports,
                pending=pending,
                outputs=outputs,
                abandoned=abandoned,
            )
            if first_failure is None:
                first_failure = failure

        if first_failure is not None:
            # Independent branches have finished and kept their work; now the run is what
            # it is. The job-level status and the re-raise preserve the serial contract.
            job.status = JobStatus.FAILED
            await self._publish(session)
            raise first_failure

        return outputs

    async def _apply_reports(
        self,
        session: AsyncSession,
        *,
        job: Job,
        reports: list[_NodeReport],
        pending: list[WorkflowStep],
        outputs: dict[str, dict[str, Any]],
        abandoned: set[str],
    ) -> tuple[bool, Exception | None]:
        """Fold a drained wave back into the coordinator's state.

        Returns whether scheduling must stop, and the first failure if there was one. The
        job-level pause transition happens here, on the control session, after the wave has
        drained — the one place it is safe.
        """
        stop_scheduling = False
        failure: Exception | None = None
        for report in reports:
            pending.remove(report.step)
            if report.outcome is _Outcome.SUCCEEDED:
                outputs[report.step.key] = report.output
            elif report.outcome is _Outcome.PAUSED:
                stop_scheduling = True
                job.status = report.pause_status or JobStatus.AWAITING_APPROVAL
                await self._publish(session)
            else:
                abandoned.add(report.step.key)
                if failure is None:
                    failure = report.exception
        return stop_scheduling, failure

    def _targets(self, stop_after: str | None) -> set[str]:
        """The nodes a run bounded by ``stop_after`` may execute."""
        if stop_after is None:
            return set(self._needs)
        if stop_after not in self._needs:
            message = (
                f"stop_after names {stop_after!r}, which is not a step of this workflow. "
                "Refused rather than ignored: ignoring it would run the whole workflow, "
                "past whatever gate the caller meant to stop at."
            )
            raise WorkflowDefinitionError(message, context={"stop_after": stop_after})

        closure: set[str] = set()
        frontier = [stop_after]
        while frontier:
            key = frontier.pop()
            if key in closure:
                continue
            closure.add(key)
            frontier.extend(self._needs[key])
        return closure

    async def _run_wave(
        self,
        session: AsyncSession,
        *,
        job: Job,
        wave: list[WorkflowStep],
        factory: async_sessionmaker[AsyncSession],
        services: dict[str, Any],
        outputs: dict[str, dict[str, Any]],
    ) -> list[_NodeReport]:
        """Run one wave of independent nodes concurrently, each on its own session.

        The wave is drained whole: whatever any node comes to, every node that started is
        awaited to its own recorded outcome before this returns. That is the "drain, never
        abandon" rule — and it is also what makes the coordinator's job-status transition
        safe, because nothing is still writing when it happens.
        """
        in_flight = Decimal(0)
        started: list[asyncio.Task[_NodeReport]] = []
        reports: list[_NodeReport] = []

        for step in wave:
            if self._budget is not None and step.estimated_cost_gbp > 0:
                try:
                    # The node's own estimate plus everything already in flight: two
                    # siblings each individually under the cap can jointly be over it,
                    # and the guard only sees committed spend.
                    await self._budget.check(
                        session, job=job, projected_gbp=in_flight + step.estimated_cost_gbp
                    )
                except BudgetExceededError as exc:
                    row = await self._step_row(
                        session,
                        job=job,
                        step=step,
                        sequence=self._sequence[step.key],
                        outputs=outputs,
                    )
                    row.status = JobStatus.BUDGET_EXCEEDED
                    row.error = exc.to_dict()
                    row.finished_at = datetime.now(UTC)
                    await self._publish(session)
                    reports.append(
                        _NodeReport(
                            step=step,
                            outcome=_Outcome.PAUSED,
                            pause_status=JobStatus.BUDGET_EXCEEDED,
                        )
                    )
                    # The run is pausing; starting more siblings now would be spending
                    # into a decision that has already been taken. What has started,
                    # drains.
                    break

            in_flight += step.estimated_cost_gbp
            started.append(
                asyncio.create_task(
                    self._run_node(
                        factory,
                        job_id=job.id,
                        step=step,
                        services=services,
                        outputs=dict(outputs),
                    )
                )
            )

        if started:
            reports.extend(await asyncio.gather(*started))
        return reports

    async def _run_node(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        job_id: uuid.UUID,
        step: WorkflowStep,
        services: dict[str, Any],
        outputs: dict[str, dict[str, Any]],
    ) -> _NodeReport:
        """One parallel node: own session, own step row, own commit; never the job row.

        The job-level transition is the coordinator's, applied after the wave drains — two
        nodes writing ``job.status`` concurrently would be a last-writer-wins race over the
        one row that says what this run is.
        """
        async with factory() as node_session:
            job = await node_session.get(Job, job_id)
            if job is None:  # pragma: no cover -- the coordinator holds a live job row
                message = f"Job {job_id} vanished mid-run."
                raise AerError(message, context={"job_id": str(job_id)})

            row = await self._step_row(
                node_session,
                job=job,
                step=step,
                sequence=self._sequence[step.key],
                outputs=outputs,
            )
            # Read while the session is certainly healthy. A step that breaks its session
            # takes attribute loads down with it, so the failure path must already hold
            # everything it needs to identify the row — see `_record_node_failure`.
            row_id = row.id
            context = StepContext(
                session=node_session, job=job, step=row, services=services, outputs=outputs
            )
            started = time.perf_counter()

            try:
                with span(
                    f"step.{step.key}", **{"aer.job_id": str(job.id), "aer.step_key": step.key}
                ):
                    result = await step.run(context)
            except StepPaused as paused:
                await self._record_node_stop(
                    node_session,
                    row=row,
                    status=JobStatus.AWAITING_APPROVAL,
                    detail=paused.to_dict(),
                )
                return _NodeReport(
                    step=step,
                    outcome=_Outcome.PAUSED,
                    pause_status=JobStatus.AWAITING_APPROVAL,
                )
            except BudgetExceededError as exc:
                await self._record_node_stop(
                    node_session,
                    row=row,
                    status=JobStatus.BUDGET_EXCEEDED,
                    detail=exc.to_dict(),
                )
                return _NodeReport(
                    step=step,
                    outcome=_Outcome.PAUSED,
                    pause_status=JobStatus.BUDGET_EXCEEDED,
                )
            except Exception as exc:
                detail = _detail_of(exc)
                await self._record_node_failure(
                    node_session,
                    job_id=job_id,
                    row_id=row_id,
                    step_key=step.key,
                    detail=detail,
                )
                _log.error("workflow.step_failed", job_id=str(job_id), step=step.key, **detail)
                return _NodeReport(step=step, outcome=_Outcome.FAILED, exception=exc)

            row.status = JobStatus.SUCCEEDED
            row.output_ref = result.output
            row.cost_gbp = result.cost_gbp
            row.finished_at = datetime.now(UTC)
            await node_session.commit()

            _log.info(
                "workflow.step_completed",
                job_id=str(job_id),
                step=step.key,
                cost_gbp=str(result.cost_gbp),
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            return _NodeReport(step=step, outcome=_Outcome.SUCCEEDED, output=result.output)

    async def _record_node_failure(
        self,
        node_session: AsyncSession,
        *,
        job_id: uuid.UUID,
        row_id: uuid.UUID,
        step_key: str,
        detail: dict[str, Any],
    ) -> None:
        """Write the failure, even when the session that failed cannot write anything.

        **This is the difference between a run recorded as FAILED and a run that says
        RUNNING for ever.** A step that dies inside a flush — a unique violation, say —
        leaves its session in a pending-rollback state, so the commit that records the
        failure raises ``PendingRollbackError`` of its own. That exception escaped the
        node, escaped ``gather``, escaped the engine and killed the queue job, which meant
        nobody ever set the step to FAILED and nobody ever set the job to FAILED. The
        console went on showing a run in progress that had been dead for an hour, and the
        only trace was a traceback in the worker's terminal.

        So: try the ordinary way, and if the session refuses at any point, roll it back and
        write the failure on a clean transaction. The rollback discards whatever the step
        had written before it broke, which is the right trade — that work was already
        unreachable, and a recorded failure is worth more than an unrecorded fragment.

        **The row is addressed by id, not by instance.** A poisoned session raises on the
        *attribute access itself*, not merely on the commit: touching an expired column
        loads it, loading needs the transaction, and the transaction is gone. That is where
        the live failure actually landed — on ``job.status = FAILED``, three lines before
        any commit — so holding plain ids from before the step ran is what stops this
        method's own bookkeeping being the thing that fails.
        """
        try:
            row = await node_session.get(JobStep, row_id)
            if row is not None:
                row.status = JobStatus.FAILED
                row.error = detail
                row.finished_at = datetime.now(UTC)
            await node_session.commit()
            return
        except SQLAlchemyError as broken:
            _log.warning(
                "workflow.failure_commit_refused",
                job_id=str(job_id),
                step=step_key,
                error=str(broken),
            )

        await node_session.rollback()
        # The row is there to be found because a step publishes its row before running.
        fresh = await node_session.get(JobStep, row_id)
        if fresh is None:  # pragma: no cover -- the row is committed before the step runs
            _log.error("workflow.failure_unrecorded", job_id=str(job_id), step=step_key)
            return
        fresh.status = JobStatus.FAILED
        fresh.error = detail
        fresh.finished_at = datetime.now(UTC)
        await node_session.commit()

    async def _record_node_stop(
        self,
        node_session: AsyncSession,
        *,
        row: JobStep,
        status: JobStatus,
        detail: dict[str, Any],
    ) -> None:
        """A parallel node's pause, recorded on its row and committed by its session."""
        row.status = status
        row.error = detail
        row.finished_at = datetime.now(UTC)
        await node_session.commit()

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

        # Read while the session is certainly healthy. A step that breaks its session takes
        # attribute loads down with it, so the failure path must already hold everything it
        # needs to identify the row -- see `_record_node_failure`.
        identity = (job.id, row.id, row.step_key)
        context = StepContext(
            session=session, job=job, step=row, services=services, outputs=outputs
        )
        started = time.perf_counter()

        try:
            with span(f"step.{step.key}", **{"aer.job_id": str(job.id), "aer.step_key": step.key}):
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
            await self._fail(session, job=job, identity=identity, exc=exc)
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
        """Find or create the row for this attempt, and **publish it before running**.

        A retried step reuses its row and increments ``attempt`` rather than creating a
        second, so "how many times did this run?" is a column rather than a count.

        **The commit here is what makes a running step visible at all.** Flushing alone
        keeps the row inside this transaction, so for the whole of a step — minutes, for a
        model call — no other process could see that it had started. The console showed the
        job as RUNNING in its header while every step below read QUEUED, and the elapsed
        clock that exists to prove a long step is alive never appeared, because from a
        reader's side nothing was ever running.

        This does not weaken :meth:`_publish`'s rule. A row saying "this step began at T,
        attempt N" is a whole state: the step has not run, so there is no half-written
        output to expose. What a reader cannot see is the *outcome*, and that is exactly
        what ``RUNNING`` says.

        A worker that dies now leaves a ``RUNNING`` row rather than no row. That is the
        better of the two: the resume path already reuses it and increments ``attempt``,
        and "this step was attempted and never finished" is worth more than silence.
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
            await self._publish(session)
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
        await self._publish(session)
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

    async def _fail(
        self,
        session: AsyncSession,
        *,
        job: Job,
        identity: tuple[uuid.UUID, uuid.UUID, str],
        exc: Exception,
    ) -> None:
        """Record the failure. **Committed here, because the caller re-raises.**

        An exception leaves the worker's ``async with`` without reaching its commit, so a
        flush alone would be rolled back on the way out and the database would go on
        reporting the run as queued. The log line would be the only evidence that anything
        had happened, and the operator would be watching a page that never changed.
        """
        detail = _detail_of(exc)
        # Handed in, not read here. A poisoned session refuses attribute loads, so by this
        # point even `job.id` raises — which is precisely where the live failure landed,
        # three lines before any commit.
        job_id, row_id, step_key = identity

        # The same hazard the parallel path carries, and the same remedy: a step that died
        # inside a flush leaves a session that can neither read nor commit, so recording
        # the failure would fail too and the run would stay RUNNING for ever.
        await self._record_node_failure(
            session, job_id=job_id, row_id=row_id, step_key=step_key, detail=detail
        )

        # Safe now: whichever branch ran, the session is usable on the way out of it.
        job.status = JobStatus.FAILED
        await self._publish(session)
        _log.error("workflow.step_failed", job_id=str(job_id), step=step_key, **detail)
