"""Run endpoints: start, watch, decide.

Thin. Starting a run creates a job and queues it; approving a gate records a decision and
queues a continuation. Everything that decides anything lives in
:mod:`aer.services.runs` and :mod:`aer.services.approvals`, so the HTML pages and the JSON
API cannot disagree about what a gate means.

**Approving does not run anything inline.** It records the decision, commits, and enqueues.
A gate approval that executed the next steps inside the request would hold a browser
connection open for the length of a research run — and would silently do nothing if the
operator's tab closed halfway through.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse
from starlette.status import HTTP_202_ACCEPTED, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, RedisClient, StateDep
from aer.api.sse import SSE_MEDIA_TYPE, event_stream
from aer.core.enums import Decision, GateKind
from aer.db.models import Job, JobStep, ResearchRequest, User, WorkOrder
from aer.errors import AerError
from aer.queue import enqueue_run
from aer.services import approvals as approval_service
from aer.services import cancellation as cancellation_service
from aer.services import provenance
from aer.services import runs as run_service
from aer.services.approvals import payload_hash_for
from aer.workflow.registry import WorkflowRegistryError, resolve_workflow
from aer.workflow.workflows.vertical_slice_v1 import (
    unmapped_gate_required,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunNotFoundError(AerError):
    """No such run, or it belongs to someone else."""

    code = "run_not_found"
    http_status = HTTP_404_NOT_FOUND


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID


class DecisionRequest(BaseModel):
    """A decision at a gate.

    ``payload_hash`` is required and is the hash of exactly what the operator was shown.
    Without it an approval says only that somebody clicked something — see
    :mod:`aer.services.approvals`.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    payload_hash: str = Field(min_length=64, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)


class RunRead(BaseModel):
    """A run's current state."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    request_id: uuid.UUID
    status: str
    workflow_version: str
    code_version: str
    spend_gbp: str

    # Every step the workflow declares, not only those that have started -- see
    # `RunState.timeline`. `steps_total` is therefore the length of `steps`, restated so a
    # caller reading progress does not have to count.
    steps: list[dict[str, Any]]
    steps_total: int
    steps_done: int
    current_step: str | None


class CancelRequest(BaseModel):
    """Why the run is being stopped. Optional, and worth asking for.

    "Cancelled at 14:02" answers nothing three months later; "wrong as-of date" answers
    everything.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=4000)


class DraftRead(BaseModel):
    """The drafted sections gate 2 decides on, and the hash an approval must echo."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    sections: list[dict[str, Any]]

    # Conflicts the deterministic ladder refused to settle. Part of the payload the hash is
    # taken over, so approving with them outstanding is on the record rather than a claim
    # about what a client happened to render.
    escalations: list[dict[str, Any]]

    payload_hash: str


class FinancialsRead(BaseModel):
    """The tags this run's extraction could not place, and the hash confirming them needs."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID

    # Whether the gate applies at all. A run whose every tag mapped answers `false` with an
    # empty list rather than 404 — "nothing to confirm" is a result, not a missing resource.
    required: bool

    unmapped_tags: list[str]
    facts_written: int
    payload_hash: str


class SourcesRead(BaseModel):
    """A run's acquired documents, and the counts a reader checks first."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    sources: list[dict[str, Any]]

    # Two counts, not one. `quarantined` is how many were refused; `inadmissible` is how
    # many are *still* refused after any overrides. A single number would hide the
    # difference between "nothing was doubtful" and "everything doubtful was waved through".
    quarantined: int
    inadmissible: int


class ClaimsRead(BaseModel):
    """A run's claims, in section order."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    claims: list[dict[str, Any]]
    unsupported: int


@router.post("", status_code=HTTP_202_ACCEPTED, response_model=RunRead, summary="Start a run")
async def start_run(
    payload: StartRunRequest,
    session: DbSession,
    redis: RedisClient,
    user: CurrentUser,
) -> RunRead:
    """Create the run for a request and queue it.

    Returns immediately. The run happens in the worker; watch it at
    ``GET /api/runs/{id}/events``.
    """
    request = await session.get(ResearchRequest, payload.request_id)
    if request is None or request.work_order.user_id != user.id:
        message = f"No research request {payload.request_id}."
        raise RunNotFoundError(message, context={"request_id": str(payload.request_id)})

    job = await run_service.start_run(session, request=request)
    await session.commit()

    await enqueue_run(redis, job.id)
    return await _read(session, job_id=job.id)


@router.get("/{job_id}", response_model=RunRead, summary="Retrieve a run")
async def read_run(job_id: uuid.UUID, session: DbSession, user: CurrentUser) -> RunRead:
    await _owned_job(session, job_id=job_id, user=user)
    return await _read(session, job_id=job_id)


@router.get(
    "/{job_id}/events",
    summary="Watch a run over server-sent events",
    response_class=StreamingResponse,
)
async def stream_run(
    job_id: uuid.UUID, state: StateDep, session: DbSession, user: CurrentUser
) -> StreamingResponse:
    """Stream a run's progress until it finishes.

    Ownership is checked once, before the stream opens. Re-checking on every tick would
    query the same row a thousand times to answer a question that cannot change.
    """
    await _owned_job(session, job_id=job_id, user=user)

    return StreamingResponse(
        event_stream(state.session_factory, job_id=job_id),
        media_type=SSE_MEDIA_TYPE,
        headers={
            # Buffering an event stream defeats it entirely: nginx will hold the whole
            # response until the run ends and then deliver an hour of progress at once.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{job_id}/draft", response_model=DraftRead, summary="The draft a run is waiting on")
async def read_draft(job_id: uuid.UUID, session: DbSession, user: CurrentUser) -> DraftRead:
    """What gate 2 shows: the drafted sections, and the hash approving them requires.

    The payload comes from the workflow this run recorded, through the registry rather than
    by importing its builder — the same structure the draft step hashed. Rebuilding the
    shape here would eventually rebuild it differently, and the symptom would be a gate that
    rejects every approval.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    payload = await _payload_for(session, job=job, gate=GateKind.FINAL)
    return DraftRead(
        job_id=job_id,
        sections=list(payload["sections"]),
        escalations=list(payload["escalations"]),
        payload_hash=payload_hash_for(payload),
    )


@router.get(
    "/{job_id}/financials",
    response_model=FinancialsRead,
    summary="The unmapped tags a run is waiting on",
)
async def read_financials(
    job_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> FinancialsRead:
    """What the conditional financials gate shows: tags the concept map could not place.

    ``required`` is false, and the list empty, for a run whose every tag mapped. That is not
    an error — it is the answer, and it is the state most runs are in.
    """
    job = await _owned_job(session, job_id=job_id, user=user)

    produced: dict[str, Any] | None = await session.scalar(
        select(JobStep.output_ref)
        .where(JobStep.job_id == job_id, JobStep.step_key == "extract")
        .order_by(JobStep.attempt.desc())
        .limit(1)
    )
    if produced is None:
        message = f"Run {job_id} has not extracted anything yet."
        raise RunNotFoundError(message, context={"job_id": str(job_id)})

    payload = await _payload_for(session, job=job, gate=GateKind.UNMAPPED_CONCEPTS)
    return FinancialsRead(
        job_id=job_id,
        required=unmapped_gate_required(produced),
        unmapped_tags=list(payload["unmapped_tags"]),
        facts_written=int(payload["facts_written"]),
        payload_hash=payload_hash_for(payload),
    )


@router.get(
    "/{job_id}/sources",
    response_model=SourcesRead,
    summary="What this run acquired, and whether it may be relied on",
)
async def read_sources(job_id: uuid.UUID, session: DbSession, user: CurrentUser) -> SourcesRead:
    """Every document this run fetched, with its provenance record in full.

    Tier, both publication dates, the artefact digest, the licence note, whether robots was
    consulted, whether it was quarantined and why, and whether a document tried to smuggle
    instructions. Nothing is filtered out — a quarantined source appears here quarantined,
    because "what did this run refuse to use?" is a question the record has to answer.
    """
    await _owned_job(session, job_id=job_id, user=user)
    sources = await provenance.sources_for_run(session, job_id)
    return SourcesRead(
        job_id=job_id,
        sources=[source.as_dict() for source in sources],
        quarantined=sum(1 for source in sources if source.quarantined),
        inadmissible=sum(1 for source in sources if not source.is_admissible),
    )


@router.get(
    "/{job_id}/claims",
    response_model=ClaimsRead,
    summary="What this run's report asserts",
)
async def read_claims(job_id: uuid.UUID, session: DbSession, user: CurrentUser) -> ClaimsRead:
    """The run's claims in section order, each with its citation count.

    The index a reader arrives at from a report; ``GET /api/claims/{id}`` is the
    drill-down behind each entry.
    """
    await _owned_job(session, job_id=job_id, user=user)
    claims = await provenance.claims_for_run(session, job_id)
    return ClaimsRead(
        job_id=job_id,
        claims=[claim.as_dict() for claim in claims],
        unsupported=sum(1 for claim in claims if not claim.is_supported),
    )


@router.post(
    "/{job_id}/cancel",
    status_code=HTTP_202_ACCEPTED,
    response_model=RunRead,
    summary="Ask a run to stop",
)
async def cancel_run(
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    payload: Annotated[CancelRequest | None, Body()] = None,
) -> RunRead:
    """Record a request to stop this run.

    202, not 200: the run has been *asked* to stop, and will do so at the next step
    boundary. A step already in flight — a model call, a filing being fetched — runs to
    completion, because abandoning it would throw away work already paid for while leaving
    the audit trail claiming the run stopped earlier than it did.
    """
    job = await _owned_job(session, job_id=job_id, user=user)

    await cancellation_service.request_cancellation(
        session, job=job, actor=user, reason=payload.reason if payload else None
    )
    await session.commit()

    return await _read(session, job_id=job_id)


@router.post(
    "/{job_id}/gates/{gate}/decide",
    status_code=HTTP_202_ACCEPTED,
    response_model=RunRead,
    summary="Approve or reject at a gate",
)
async def decide_gate(
    job_id: uuid.UUID,
    gate: GateKind,
    *,
    payload: Annotated[DecisionRequest, Body()],
    session: DbSession,
    redis: RedisClient,
    user: CurrentUser,
) -> RunRead:
    """Record a decision and, if it was an approval, queue the run to continue.

    Raises:
        ValidationError: If this gate was already decided, or an earlier one has not been
            approved. Both are refused in :mod:`aer.services.approvals`.
    """
    job = await _owned_job(session, job_id=job_id, user=user)

    await approval_service.record_decision(
        session,
        job=job,
        gate=gate,
        decision=payload.decision,
        actor=user,
        payload_hash=payload.payload_hash,
        notes=payload.notes,
    )
    await session.commit()

    if payload.decision is Decision.APPROVED:
        # Queued, not run inline. A gate approval that executed the next steps would hold
        # the browser's connection open for the length of a research run.
        await enqueue_run(redis, job.id)

    return await _read(session, job_id=job_id)


# -- Internals ---------------------------------------------------------------------------


async def _payload_for(session: AsyncSession, *, job: Job, gate: GateKind) -> dict[str, Any]:
    """Exactly what this run's workflow puts at that gate.

    Through the registry rather than by importing a builder, so a page rendering an
    approval does not have to know which workflow raised it. A run whose workflow this
    build no longer has renders an empty payload rather than raising: the page's job is to
    show what was approved, and "this build cannot read that workflow" is a better answer
    than a 500 (ADR 0071).
    """
    try:
        builder = resolve_workflow(job.workflow_version).gate_payload()
    except WorkflowRegistryError:
        return {}
    return dict(await builder(session, job=job, gate=gate.value))


async def _owned_job(session: AsyncSession, *, job_id: uuid.UUID, user: User) -> Job:
    """The run, if it belongs to this user.

    One error for "does not exist" and "is not yours". Distinguishing them would let a
    caller enumerate which ids exist by watching for a 403 among the 404s.
    """
    job: Job | None = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(Job.id == job_id, WorkOrder.user_id == user.id)
    )
    if job is None:
        message = f"No run {job_id}."
        raise RunNotFoundError(message, context={"job_id": str(job_id)})
    return job


async def _read(session: AsyncSession, *, job_id: uuid.UUID) -> RunRead:
    state = await run_service.run_state(session, job_id=job_id)
    payload = state.as_dict()
    return RunRead(
        job_id=state.job.id,
        # The run root's id. Identical to `request_id` for a research run — the detail
        # row shares the root's key — and NOT NULL, where the old column no longer is.
        request_id=state.job.work_order_id,
        status=payload["status"],
        workflow_version=state.job.workflow_version,
        code_version=state.job.code_version,
        spend_gbp=payload["spend_gbp"],
        steps=payload["steps"],
        steps_total=payload["steps_total"],
        steps_done=payload["steps_done"],
        current_step=payload["current_step"],
    )
