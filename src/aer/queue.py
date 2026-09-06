"""Putting a run on the queue, from the web process.

Separate from :mod:`aer.worker` on purpose, and the separation is load-bearing rather than
tidy. arq reads its configuration out of ``WorkerSettings.__dict__``, so the Redis settings
have to be a real value in that class body — which means importing :mod:`aer.worker`
requires valid configuration. The web process only ever needs to *enqueue*, and making a
page fail to import because a worker setting could not be resolved would be an absurd
coupling.

So the queue's name and the enqueue call live here, the worker imports them, and nothing
imports the worker except the worker.

**Whether a worker is alive is read from here too.** The confirmation run of 2026-09-05
sat queued overnight because no worker was running, and nothing on the console or in
``aer diagnose`` said so: the web process only enqueues, and a queue with nothing reading
it looks exactly like a queue about to be read. arq's worker writes a health record to
Redis every ``health_check_interval`` seconds with a lifetime one second longer, so the
record's presence *is* the liveness signal — no process registry, no heartbeat of our own.
The interval is set short here and honoured by the worker, and :func:`worker_health` reads
the record back for every surface that can say "queued" — so that none of them says it
without being able to add "and nobody is listening".
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Final

import structlog
from arq.connections import RedisSettings
from arq.constants import default_queue_name, health_check_key_suffix

__all__ = [
    "HEALTH_CHECK_INTERVAL_SECONDS",
    "HEALTH_CHECK_KEY",
    "RUN_MONITOR_TASK",
    "RUN_RESEARCH_TASK",
    "WorkerHealth",
    "enqueue_monitor",
    "enqueue_run",
    "redis_settings_from",
    "worker_health",
]

_log = structlog.get_logger("aer.queue")

# The registered task name. Shared rather than repeated: a producer and a consumer that
# disagree about it produce a queue that accepts work nothing ever runs.
RUN_RESEARCH_TASK = "run_research"
RUN_MONITOR_TASK = "run_monitor"

# How often the worker records that it is alive, and the key arq records it under. Thirty
# seconds rather than arq's hour-long default, because the record's lifetime is the
# interval plus one second and its absence is what every "no worker is listening" message
# rests on: an hour-old record would have said a dead worker was alive for an hour. Short
# enough that a run cannot sit queued for long before the console says why; long enough
# that the write is nothing against a run's own traffic.
HEALTH_CHECK_INTERVAL_SECONDS: Final = 30
HEALTH_CHECK_KEY: Final = f"{default_queue_name}{health_check_key_suffix}"

# What arq writes: "Sep-06 10:41:03 j_complete=1 j_failed=0 j_retried=0 j_ongoing=1
# queued=0". Only the counters are read; the timestamp is the worker's local clock with
# no year, and the record's remaining lifetime says how old it is more reliably.
_HEALTH_COUNTER: Final[re.Pattern[str]] = re.compile(r"\b(?P<name>[a-z_]+)=(?P<value>\d+)")


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    """What a worker last recorded about itself, and how long ago.

    ``ongoing`` is what it is doing now — with one job at a time, a queued run behind an
    ongoing one is waiting its turn, not stuck — and ``queued`` is how many the queue held
    when it looked. ``reported_seconds_ago`` is read from the record's remaining lifetime,
    so it is a floor of zero rather than a negative when a worker on a longer interval
    than this build's wrote it.
    """

    reported_seconds_ago: int
    ongoing: int
    queued: int
    completed: int
    failed: int


async def worker_health(redis: Any) -> WorkerHealth | None:
    """The health record a live worker keeps in Redis, or ``None`` when no worker has.

    ``None`` means no worker has recorded itself within the interval — a run put on the
    queue now will not start. Redis being unreachable is a different fact and is left to
    raise: the caller decides whether that is "unknown" on a readout or a page that must
    still render.
    """
    raw = await redis.get(HEALTH_CHECK_KEY)
    if not raw:
        return None
    remaining_ms = await redis.pttl(HEALTH_CHECK_KEY)
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    counters = {m.group("name"): int(m.group("value")) for m in _HEALTH_COUNTER.finditer(text)}
    lifetime_ms = (HEALTH_CHECK_INTERVAL_SECONDS + 1) * 1000
    age_ms = lifetime_ms - remaining_ms if remaining_ms and remaining_ms > 0 else 0
    return WorkerHealth(
        reported_seconds_ago=max(0, age_ms // 1000),
        ongoing=counters.get("j_ongoing", 0),
        queued=counters.get("queued", 0),
        completed=counters.get("j_complete", 0),
        failed=counters.get("j_failed", 0),
    )


async def enqueue_run(redis: Any, job_id: uuid.UUID) -> str | None:
    """Queue a run, from the web process.

    Returns the queued task's id, or ``None`` if the queue is unavailable — the caller
    decides what to do about that. A web request that failed because a background queue was
    down would be an unhelpful error for an operator who has just approved a plan; the run
    is recorded and can be started again.
    """
    return await _enqueue(redis, RUN_RESEARCH_TASK, job_id)


async def enqueue_monitor(redis: Any, thesis_id: uuid.UUID) -> str | None:
    """Queue one monitor pass over one thesis, from the web process (roadmap §3.6).

    The same shape as a run, keyed on the thesis rather than a job: the pass makes its own
    work order and job when it starts, so there is nothing to name before then.
    """
    return await _enqueue(redis, RUN_MONITOR_TASK, thesis_id)


async def _enqueue(redis: Any, task_name: str, identifier: uuid.UUID) -> str | None:
    from arq import create_pool  # noqa: PLC0415 -- only needed when actually enqueueing

    pool = None
    try:
        pool = await create_pool(redis_settings_from(redis))
        task = await pool.enqueue_job(task_name, str(identifier))
    except Exception as exc:
        _log.warning("queue.enqueue_failed", task=task_name, id=str(identifier), error=str(exc))
        return None
    finally:
        # Closed every time. `create_pool` opens its own connection pool, and a web
        # process that enqueues without closing leaks one per approval -- invisible until
        # the Redis connection limit is reached, at which point nothing can be queued at
        # all.
        if pool is not None:
            await pool.aclose()

    return task.job_id if task is not None else None


async def discard_queued_runs(redis: Any) -> int:
    """Drop every queued run, returning how many were dropped.

    For `reset-research` (gap A57). The queue outlives the rows it points at: deleting
    the runs leaves Redis holding entries naming jobs that no longer exist, and a worker
    started afterwards replays each one. The worker now discards them quietly, but a
    queue emptied at the same moment as the table it refers to is the honest state —
    nothing left pointing at nothing.

    Failure to reach Redis is reported, never raised: the rows are already gone by the
    time this runs, and a reset that succeeded must not report failure because the
    cleanup of a cache could not be done.
    """
    from arq import create_pool  # noqa: PLC0415 -- only needed when actually draining
    from arq.constants import default_queue_name, job_key_prefix  # noqa: PLC0415

    pool = None
    queued: list[Any] = []
    try:
        pool = await create_pool(redis_settings_from(redis))
        queued = list(await pool.queued_jobs())
        for job in queued:
            await pool.delete(f"{job_key_prefix}{job.job_id}")
        await pool.delete(default_queue_name)
    except Exception as exc:
        _log.warning("queue.drain_failed", error=str(exc))
        return 0
    finally:
        if pool is not None:
            await pool.aclose()
    return len(queued)


def redis_settings_from(redis: Any) -> RedisSettings:
    """Derive arq's connection settings from an existing client.

    Reuses whatever the application is already configured with rather than reading the
    environment a second time, so the worker and the web process cannot end up pointed at
    different Redis instances.
    """
    pool = getattr(redis, "connection_pool", None)
    kwargs = getattr(pool, "connection_kwargs", {}) if pool is not None else {}
    return RedisSettings(
        host=str(kwargs.get("host", "127.0.0.1")),
        port=int(kwargs.get("port", 6379)),
        database=int(kwargs.get("db", 0)),
        password=kwargs.get("password"),
    )
