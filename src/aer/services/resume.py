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

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus
from aer.db.models import AuditEvent, Job, JobStep, User
from aer.errors import ConflictError
from aer.queue import HEALTH_CHECK_INTERVAL_SECONDS, WorkerHealth

__all__ = ["Stranding", "resume_run", "set_step_mode", "stranding_of"]

_log = structlog.get_logger("aer.services.resume")

# The states a resume is refused from, each for its own reason: SUCCEEDED because running
# again is superseding's job, CANCELLED because a standing cancellation is an operator's
# recorded decision the engine would honour at the first boundary anyway, RUNNING because a
# worker may be mid-step and a second execution would race the first over the rows that
# make resumption safe. Everything else — FAILED, PAUSED, AWAITING_APPROVAL,
# BUDGET_EXCEEDED, and a QUEUED job whose enqueue was lost — continues.
#
# RUNNING has one exception, and it is the caller's to establish: a run whose worker died
# under it. The row says RUNNING because nothing was left to say otherwise, no worker will
# ever pick it up again (arq does not retry, by design), and until the readiness audit of
# 2026-09 the only way on was to re-enqueue the job id by hand. :func:`stranding_of` is
# the check; `resume_run(..., stranded=True)` is the attestation that it was made.
UNRESUMABLE_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.CANCELLED, JobStatus.RUNNING})

# How long a step must have been RUNNING before a worker reporting nothing in flight
# counts as evidence: the health record is written every interval, so a job picked up a
# moment ago can still read as "nothing ongoing" until the next one.
STRANDED_AFTER_SECONDS = HEALTH_CHECK_INTERVAL_SECONDS + 1


@dataclass(frozen=True, slots=True)
class Stranding:
    """Whether a RUNNING run has a worker executing it, and how that was decided.

    ``stranded`` is only ever true for a RUNNING job. ``reason`` is written for the person
    who has to decide whether to press *Continue*: what was observed, not a verdict.
    """

    stranded: bool
    reason: str


async def stranding_of(
    session: AsyncSession,
    *,
    job: Job,
    health: WorkerHealth | None,
    now: datetime | None = None,
) -> Stranding:
    """Decide whether a RUNNING job is stranded — recorded as running, with nobody running it.

    Two observations make the case. No worker has reported within the health interval:
    nothing is executing anything. Or a worker has reported and has nothing in flight,
    while this run's step began longer ago than the interval: had that worker taken the
    step, its record would say so by now. A worker with a job in flight is not evidence
    either way — with one worker it is probably this run — so the refusal stands.

    `health` is the caller's to fetch (``aer.queue.worker_health``): this module reads the
    database and decides; it does not open Redis.
    """
    if job.status is not JobStatus.RUNNING:
        return Stranding(False, f"This run is {job.status.value}, not running.")

    moment = now or datetime.now(UTC)
    began = await session.scalar(
        select(JobStep.started_at)
        .where(JobStep.job_id == job.id, JobStep.status == JobStatus.RUNNING)
        .order_by(JobStep.attempt.desc(), JobStep.sequence.desc())
        .limit(1)
    )
    running_for = int((moment - (began or job.started_at or moment)).total_seconds())

    if health is None:
        return Stranding(
            True,
            f"No worker has reported in the last {STRANDED_AFTER_SECONDS} seconds, so "
            "nothing is executing this run. Continuing re-runs the step it stopped in; "
            "the completed steps are kept.",
        )
    if health.ongoing:
        return Stranding(
            False,
            f"A worker reported {health.reported_seconds_ago} seconds ago with "
            f"{health.ongoing} job(s) in flight, and this run may be one of them.",
        )
    if running_for < STRANDED_AFTER_SECONDS:
        return Stranding(
            False,
            f"The current step began {running_for} seconds ago; the worker may not have "
            f"reported it yet. Look again after {STRANDED_AFTER_SECONDS} seconds.",
        )
    return Stranding(
        True,
        f"A worker reported {health.reported_seconds_ago} seconds ago with nothing in "
        f"flight, and this run's step has been recorded as running for {running_for} "
        "seconds. Nothing is executing it. Continuing re-runs that step; the completed "
        "steps are kept.",
    )


async def resume_run(
    session: AsyncSession,
    *,
    job: Job,
    actor: User,
    reason: str | None = None,
    stranded: bool = False,
) -> Job:
    """Record the decision to continue this run, and return it ready to re-enqueue.

    The caller enqueues (or executes inline, for a stepped run); this function owns the
    record. Deliberately not idempotent in the way cancellation is — each resume is its
    own decision, and a run resumed twice was decided about twice.

    ``stranded`` is the caller's attestation, from :func:`stranding_of`, that a RUNNING
    job has no worker executing it. With it a RUNNING job continues like a FAILED one and
    the audit event says so; without it RUNNING is refused as it always was.

    Raises:
        ConflictError: If the run's state does not admit continuing. The message names
            the state and the remedy, because "cannot resume" without either is the kind
            of refusal an operator works around rather than understands.
    """
    if job.status in UNRESUMABLE_STATUSES and not (stranded and job.status is JobStatus.RUNNING):
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
            # Only ever true when resumed from RUNNING: the record of a run that was
            # continued over a worker's corpse should say that is what happened.
            "stranded": stranded and resumed_from is JobStatus.RUNNING,
        },
        job=job,
    )

    _log.info(
        "run.resumed",
        job_id=str(job.id),
        actor=actor.email,
        resumed_from=resumed_from.value,
        stranded=stranded and resumed_from is JobStatus.RUNNING,
    )
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
            request_id=job.work_order_id,
            job_id=job.id,
        )
    )
    await session.flush()
