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
from aer.db.models import Job, ResearchRequest, User
from aer.errors import AerError
from aer.queue import enqueue_run
from aer.services import approvals as approval_service
from aer.services import cancellation as cancellation_service
from aer.services import runs as run_service
from aer.services.approvals import payload_hash_for
from aer.workflow.workflows.vertical_slice_v1 import final_gate_payload

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
    steps: list[dict[str, Any]]


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
    payload_hash: str


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
    if request is None or request.user_id != user.id:
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

    The payload comes from the workflow's own :func:`final_gate_payload`, the same call the
    draft step hashed. Rebuilding the shape here would eventually rebuild it differently,
    and the symptom would be a gate that rejects every approval.
    """
    await _owned_job(session, job_id=job_id, user=user)
    payload = await final_gate_payload(session, job_id=job_id)
    return DraftRead(
        job_id=job_id,
        sections=list(payload["sections"]),
        payload_hash=payload_hash_for(payload),
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


async def _owned_job(session: AsyncSession, *, job_id: uuid.UUID, user: User) -> Job:
    """The run, if it belongs to this user.

    One error for "does not exist" and "is not yours". Distinguishing them would let a
    caller enumerate which ids exist by watching for a 403 among the 404s.
    """
    job: Job | None = await session.scalar(
        select(Job)
        .join(ResearchRequest, ResearchRequest.id == Job.request_id)
        .where(Job.id == job_id, ResearchRequest.user_id == user.id)
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
        request_id=state.job.request_id,
        status=payload["status"],
        workflow_version=state.job.workflow_version,
        code_version=state.job.code_version,
        spend_gbp=payload["spend_gbp"],
        steps=payload["steps"],
    )
