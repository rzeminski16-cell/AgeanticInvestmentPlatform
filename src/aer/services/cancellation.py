"""Asking a run to stop.

**Cancelling is a request, not an act.** The worker is inside a step — an HTTP fetch, a
model call — and neither can be interrupted from another process without abandoning work
that has already been paid for. So this records the request and the engine acts on it at
the next step boundary, which is the finest granularity that can be reported honestly.

That is why the two timestamps differ, and why both are kept: ``requested_at`` on the
cancellation, ``finished_at`` on the job. A console that claimed a run had stopped the
instant the button was pressed would be lying for as long as the current step took.

**A run that has already finished cannot be cancelled.** Not because the write would fail,
but because it would be a false record: the run reached its end, and saying otherwise in
the audit trail would misdescribe what happened.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus
from aer.db.models import AuditEvent, Job, JobCancellation, User
from aer.errors import ConflictError

__all__ = ["TERMINAL_STATUSES", "cancellation_for", "request_cancellation"]

_log = structlog.get_logger("aer.services.cancellation")

# A run in one of these has stopped for good. Nothing further executes, so there is nothing
# to cancel.
TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})


async def request_cancellation(
    session: AsyncSession,
    *,
    job: Job,
    actor: User,
    reason: str | None = None,
) -> JobCancellation:
    """Record that this run should stop.

    Idempotent: asking twice returns the standing request rather than creating a second.
    The operator wants the run stopped and has said so; a second row would be a second
    thing to interpret, and there is only one decision here.

    Raises:
        ConflictError: If the run has already reached a terminal state. Recording a
            cancellation against a finished run would put something in the audit trail
            that did not happen.
    """
    if job.status in TERMINAL_STATUSES:
        message = (
            f"This run has already {job.status.value.lower()}. There is nothing left to "
            "cancel, and recording one would describe something that did not happen."
        )
        raise ConflictError(message, context={"job_id": str(job.id), "status": job.status.value})

    existing = await cancellation_for(session, job_id=job.id)
    if existing is not None:
        return existing

    cancellation = JobCancellation(job_id=job.id, requested_by=actor.id, reason=reason)
    session.add(cancellation)
    await session.flush()

    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor.email,
            event_type="run.cancellation_requested",
            payload={
                "job_id": str(job.id),
                "reason": reason,
                # What the run was doing when the operator asked. Reconstructing it later
                # from timestamps alone is guesswork.
                "status_when_requested": job.status.value,
            },
            previous=previous,
            request_id=job.request_id,
            job_id=job.id,
        )
    )
    await session.flush()

    _log.info(
        "run.cancellation_requested",
        job_id=str(job.id),
        actor=actor.email,
        status=job.status.value,
    )
    return cancellation


async def cancellation_for(session: AsyncSession, *, job_id: uuid.UUID) -> JobCancellation | None:
    """The standing cancellation request for a run, if there is one."""
    found: JobCancellation | None = await session.scalar(
        select(JobCancellation).where(JobCancellation.job_id == job_id)
    )
    return found
