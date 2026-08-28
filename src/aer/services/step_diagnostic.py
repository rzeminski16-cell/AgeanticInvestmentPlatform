"""The per-step readout of a run, assembled from what the run already recorded.

Roadmap §3.15's diagnostic. Everything here is a read: the step rows carry status,
attempt, timing, cost and the recorded error; the step's stored output *is* the parsed
product; and each model call's `agent_runs` row carries the tokens, the stop reason and
the artefact hashes of the archived request and response — which is where "raw versus
parsed" lives, because the raw exchange was archived when it happened (ADR 0016's family
of guarantees) and only needed surfacing.

**No model call, ever.** An LLM judging a step would add a paid call to steps that cost
nothing, work against the speed a step-through exists to buy, and duplicate the critique
loop, whose subject is content rather than execution. A diagnostic that costs money is a
diagnostic nobody runs twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from aer.core.enums import JobStatus
from aer.db.models import AgentRun, Artefact, Job, JobStep
from aer.errors import ValidationError
from aer.services.runs import declared_steps
from aer.workflow.engine import spend_so_far

__all__ = [
    "ModelExchange",
    "RunDiagnostic",
    "StepDiagnostic",
    "run_diagnostic",
]


@dataclass(frozen=True, slots=True)
class ModelExchange:
    """One model call a step made, with both halves of the exchange addressable."""

    agent_role: str
    model: str
    effort: str | None
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None
    latency_ms: int | None

    # The archived payloads, by content hash. `aer.storage` reads them back with nothing
    # but this string, so the readout hands the operator the address rather than the
    # bytes: a raw response is often megabytes, and the point of printing the hash is
    # that "why did it say that?" has somewhere to go.
    request_sha256: str | None
    response_sha256: str | None


@dataclass(frozen=True, slots=True)
class StepDiagnostic:
    """One step as the terminal shows it: what ran, what it made, what it cost."""

    key: str
    sequence: int
    status: JobStatus
    attempt: int
    cost_gbp: Decimal
    started_at: datetime | None
    finished_at: datetime | None
    error: dict[str, Any] | None
    output: dict[str, Any]
    exchanges: tuple[ModelExchange, ...] = ()

    @property
    def elapsed_seconds(self) -> float | None:
        """Wall-clock length of the recorded attempt, where both ends were recorded."""
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def attempts(self) -> int:
        """How many times this step has run. ``attempt`` is zero-based on the row."""
        return self.attempt + 1


@dataclass(frozen=True, slots=True)
class RunDiagnostic:
    """The whole run, step by step, in the order the workflow declares."""

    job_id: uuid.UUID
    status: JobStatus
    workflow_version: str
    code_version: str
    step_mode: bool
    spend_gbp: Decimal
    steps: tuple[StepDiagnostic, ...] = ()

    # Every declared step the run has not reached, in declared order — so the readout can
    # say what comes next rather than leaving the operator to deduce it from absence.
    not_reached: tuple[str, ...] = ()

    @property
    def next_step(self) -> str | None:
        """The first declared step with no successful attempt, or ``None`` at the end.

        A recorded-but-unsuccessful step comes before the unreached ones: it is what the
        engine will execute next, and it is usually the row the operator is here about.
        """
        for step in self.steps:
            if step.status is not JobStatus.SUCCEEDED:
                return step.key
        return self.not_reached[0] if self.not_reached else None

    def step(self, key: str) -> StepDiagnostic | None:
        """One recorded step's readout, or ``None`` where nothing has been recorded."""
        return next((step for step in self.steps if step.key == key), None)


async def run_diagnostic(session: AsyncSession, *, job_id: uuid.UUID) -> RunDiagnostic:
    """Assemble the readout for one run.

    Raises:
        ValidationError: If there is no such run.
    """
    job = await session.get(Job, job_id)
    if job is None:
        message = f"No run {job_id}."
        raise ValidationError(message, context={"job_id": str(job_id)})

    rows = list(
        await session.scalars(
            select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.sequence)
        )
    )
    exchanges = await _exchanges_by_step(session, job_id=job_id)

    steps = tuple(
        StepDiagnostic(
            key=row.step_key,
            sequence=row.sequence,
            status=row.status,
            attempt=row.attempt,
            cost_gbp=row.cost_gbp,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error=row.error,
            output=row.output_ref or {},
            exchanges=exchanges.get(row.id, ()),
        )
        for row in rows
    )

    recorded = {row.step_key for row in rows}
    return RunDiagnostic(
        job_id=job_id,
        status=job.status,
        workflow_version=job.workflow_version,
        code_version=job.code_version,
        step_mode=bool(job.step_mode),
        spend_gbp=await spend_so_far(session, job_id=job_id),
        steps=steps,
        not_reached=tuple(
            key for key in declared_steps(job.workflow_version) if key not in recorded
        ),
    )


async def _exchanges_by_step(
    session: AsyncSession, *, job_id: uuid.UUID
) -> dict[uuid.UUID, tuple[ModelExchange, ...]]:
    """Every model call the run made, grouped by the step that made it.

    One query for the run rather than one per step: a readout that issued a query per row
    would make the diagnostic of a long run slower than the steps it describes.
    """
    request_artefact = aliased(Artefact)
    response_artefact = aliased(Artefact)
    rows = await session.execute(
        select(AgentRun, request_artefact.sha256, response_artefact.sha256)
        .join(JobStep, JobStep.id == AgentRun.job_step_id)
        .outerjoin(request_artefact, request_artefact.id == AgentRun.request_payload_ref)
        .outerjoin(response_artefact, response_artefact.id == AgentRun.response_payload_ref)
        .where(JobStep.job_id == job_id)
        .order_by(AgentRun.created_at)
    )

    grouped: dict[uuid.UUID, list[ModelExchange]] = {}
    for run, request_sha, response_sha in rows:
        grouped.setdefault(run.job_step_id, []).append(
            ModelExchange(
                agent_role=run.agent_role,
                model=run.model,
                effort=run.effort,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                stop_reason=run.stop_reason,
                latency_ms=run.latency_ms,
                request_sha256=request_sha,
                response_sha256=response_sha,
            )
        )
    return {step_id: tuple(items) for step_id, items in grouped.items()}
