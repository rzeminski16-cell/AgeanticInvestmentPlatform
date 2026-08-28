"""Asking a run to continue — as itself (ADR 0090).

The mirror of :mod:`aer.services.cancellation`, and held to the same standard of honesty.
A resume never rewrites what the run said about itself: the failed attempts keep their
rows, their errors and their costs, and what is added is an appended, hash-linked audit
event recording that a person chose to continue and from what state. ``jobs.status`` is
where the run is *now*; the history was always elsewhere.

**Resuming re-enqueues the same job.** That is the whole point — §2.3's finding was that a
failure one step from the end cost the entire run again, because the only supported path
was superseding into a fresh job. The engine has skipped completed steps since Phase 1;
this module is the deliberate way in.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus
from aer.db.models import AuditEvent, Job, User
from aer.errors import ConflictError

__all__ = ["resume_run", "set_step_mode"]

_log = structlog.get_logger("aer.services.resume")

# The states a resume is refused from, each for its own reason: SUCCEEDED because running
# again is superseding's job, CANCELLED because a standing cancellation is an operator's
# recorded decision the engine would honour at the first boundary anyway, RUNNING because a
# worker may be mid-step and a second execution would race the first over the rows that
# make resumption safe. Everything else — FAILED, PAUSED, AWAITING_APPROVAL,
# BUDGET_EXCEEDED, and a QUEUED job whose enqueue was lost — continues.
UNRESUMABLE_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.CANCELLED, JobStatus.RUNNING})


async def resume_run(
    session: AsyncSession,
    *,
    job: Job,
    actor: User,
    reason: str | None = None,
) -> Job:
    """Record the decision to continue this run, and return it ready to re-enqueue.

    The caller enqueues (or executes inline, for a stepped run); this function owns the
    record. Deliberately not idempotent in the way cancellation is — each resume is its
    own decision, and a run resumed twice was decided about twice.

    Raises:
        ConflictError: If the run's state does not admit continuing. The message names
            the state and the remedy, because "cannot resume" without either is the kind
            of refusal an operator works around rather than understands.
    """
    if job.status in UNRESUMABLE_STATUSES:
        remedy = {
            JobStatus.SUCCEEDED: "It finished; to run again, start the request afresh.",
            JobStatus.CANCELLED: (
                "It was cancelled, and that decision stands; to run again, start the "
                "request afresh."
            ),
            JobStatus.RUNNING: "It is running now; there is nothing to continue.",
        }[job.status]
        message = f"This run is {job.status.value}. {remedy}"
        raise ConflictError(message, context={"job_id": str(job.id), "status": job.status.value})

    resumed_from = job.status
    job.status = JobStatus.QUEUED
    await _append_event(
        session,
        actor=actor,
        event_type="run.resumed",
        payload={
            "job_id": str(job.id),
            "resumed_from": resumed_from.value,
            "reason": reason,
        },
        job=job,
    )

    _log.info("run.resumed", job_id=str(job.id), actor=actor.email, resumed_from=resumed_from.value)
    return job


async def set_step_mode(
    session: AsyncSession,
    *,
    job: Job,
    actor: User,
    enabled: bool,
) -> Job:
    """Turn the deliberate step-through on or off for this run (ADR 0090).

    Recorded in the audit chain because it changes how the run executes — a run that
    paused eleven times and one that ran straight through should be distinguishable later.
    Idempotent in effect but not in record: asking for the state the run is already in
    writes nothing.

    Raises:
        ConflictError: If the run has already stopped for good. Stepping a finished run
            means nothing, and recording the request would describe a run that no longer
            executes.
    """
    if job.status.is_terminal:
        message = (
            f"This run has already {job.status.value.lower()}; there are no further steps "
            "to pause between."
        )
        raise ConflictError(message, context={"job_id": str(job.id), "status": job.status.value})

    if bool(job.step_mode) == enabled:
        return job

    job.step_mode = enabled
    await _append_event(
        session,
        actor=actor,
        event_type="run.step_mode_changed",
        payload={"job_id": str(job.id), "enabled": enabled},
        job=job,
    )

    _log.info("run.step_mode_changed", job_id=str(job.id), actor=actor.email, enabled=enabled)
    return job


async def _append_event(
    session: AsyncSession,
    *,
    actor: User,
    event_type: str,
    payload: dict[str, str | bool | None],
    job: Job,
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor.email,
            event_type=event_type,
            payload=dict(payload),
            previous=previous,
            request_id=job.request_id,
            job_id=job.id,
        )
    )
    await session.flush()
