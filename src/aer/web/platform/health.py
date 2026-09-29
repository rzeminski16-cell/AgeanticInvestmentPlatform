"""Whether the machinery is running, in words: the worker, the daily pass, the queue, the last
run and the database (page specification §19, *Health*).

One module because they answer one question between them — *is anything happening, and is it
what should be?* — and two pages read them: the settings page, where the console sends an
operator whose run is queued with nothing to run it, and the Platform page's health sheet.
Every answer is read from a record — the worker's health key, the jobs table, the schema — and
where the record cannot be read the answer says so rather than guessing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.dates import format_date
from aer.core.enums import JobStatus
from aer.db.models import Job, ResearchRequest, WorkOrder
from aer.db.schema_check import schema_drift
from aer.queue import HEALTH_CHECK_INTERVAL_SECONDS, worker_health
from aer.services import daily_pass
from aer.web.vocabulary import Tone, job_state

__all__ = [
    "Reading",
    "daily_pass_words",
    "database_words",
    "last_run_words",
    "queue_words",
    "worker_words",
]


@dataclass(frozen=True, slots=True)
class Reading:
    """One part of the machinery as the operator reads it: a state, and the sentence behind it."""

    tone: str
    label: str
    detail: str
    href: str = ""


async def worker_words(redis: Redis) -> Reading:
    """The worker's status in words, from the record it keeps in Redis (`aer.queue`)."""
    try:
        health = await worker_health(redis)
    except (RedisError, OSError):
        return Reading(
            Tone.MUTED,
            "Unknown",
            "The worker's health record could not be read, so nothing can be said.",
        )
    window = HEALTH_CHECK_INTERVAL_SECONDS + 1
    if health is None:
        return Reading(
            Tone.FAILURE,
            "Not running",
            f"No worker has reported in the last {window} seconds. Nothing runs until one is "
            "started from the terminal where the platform runs; a queued run begins within a "
            "few seconds once it reports.",
        )
    return Reading(
        Tone.SUCCESS,
        "Running",
        f"Reported {health.reported_seconds_ago} seconds ago with {health.ongoing} run(s) in "
        "flight. It takes one at a time.",
    )


async def daily_pass_words(session: AsyncSession, *, user_id: uuid.UUID) -> Reading:
    """When the daily pass last ran, and whether it is late (F15).

    **A missed pass reads as a failure, not as a note.** F15's done-when is that a missed
    run is *visible rather than silent*, and a grey line saying "last ran on the 14th" is
    silent in every way that matters: it puts the arithmetic on the reader.
    """
    last = await daily_pass.last_pass(session, user_id=user_id)
    state = daily_pass.pass_state(
        last.finished_at if last is not None else None, now=datetime.now(UTC)
    )
    if state.last_finished is None:
        return Reading(Tone.MUTED, "Not yet", state.sentence)
    if state.is_missed:
        return Reading(Tone.FAILURE, "Overdue", state.sentence)
    return Reading(Tone.SUCCESS, "Up to date", state.sentence)


async def queue_words(session: AsyncSession, *, user_id: uuid.UUID) -> Reading:
    """How many of this operator's runs are waiting for the worker, and how many are on it."""
    counted = {
        status: int(count)
        for status, count in (
            await session.execute(
                select(Job.status, func.count())
                .join(WorkOrder, WorkOrder.id == Job.work_order_id)
                .where(
                    WorkOrder.user_id == user_id,
                    Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
                )
                .group_by(Job.status)
            )
        ).tuples()
    }
    waiting = counted.get(JobStatus.QUEUED, 0)
    running = counted.get(JobStatus.RUNNING, 0)
    if not waiting and not running:
        return Reading(Tone.SUCCESS, "Empty", "Nothing is waiting to run.")
    parts = []
    if running:
        parts.append(f"{running} run{'' if running == 1 else 's'} on the worker")
    if waiting:
        parts.append(f"{waiting} waiting {'its' if waiting == 1 else 'their'} turn")
    sentence = " and ".join(parts)
    return Reading(
        Tone.INFO,
        f"{waiting} waiting" if waiting else "Working",
        sentence[0].upper() + sentence[1:] + ".",
    )


async def last_run_words(session: AsyncSession, *, user_id: uuid.UUID) -> Reading:
    """The newest research run this operator started: whose it was, where it stands, and when."""
    newest = (
        await session.execute(
            select(Job, ResearchRequest)
            .join(WorkOrder, WorkOrder.id == Job.work_order_id)
            .join(ResearchRequest, ResearchRequest.id == Job.work_order_id)
            .where(WorkOrder.user_id == user_id)
            .order_by(Job.started_at.desc().nullslast(), Job.id.desc())
            .limit(1)
        )
    ).first()
    if newest is None:
        return Reading(Tone.MUTED, "None yet", "No research run has started.")
    job, request = newest
    state = job_state(job.status)
    when = job.finished_at or job.started_at
    dated = f", {format_date(when, '%-d %B %Y')}" if when is not None else ""
    return Reading(
        state.tone,
        state.label,
        f"{request.company_name} ({request.ticker}){dated}.",
        href=f"/runs/{job.id}",
    )


async def database_words(session: AsyncSession) -> Reading:
    """Whether the database is at the schema the code expects — a skipped migration is the
    one fault that breaks every tool at once, so it is said here as well as on Today."""
    drift = await schema_drift(session)
    if drift.is_clean:
        return Reading(Tone.SUCCESS, "Up to date", "At the schema this code expects.")
    return Reading(Tone.FAILURE, "Behind the code", drift.as_message())
