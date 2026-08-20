"""Putting a run on the queue, from the web process.

Separate from :mod:`aer.worker` on purpose, and the separation is load-bearing rather than
tidy. arq reads its configuration out of ``WorkerSettings.__dict__``, so the Redis settings
have to be a real value in that class body — which means importing :mod:`aer.worker`
requires valid configuration. The web process only ever needs to *enqueue*, and making a
page fail to import because a worker setting could not be resolved would be an absurd
coupling.

So the queue's name and the enqueue call live here, the worker imports them, and nothing
imports the worker except the worker.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from arq.connections import RedisSettings

__all__ = ["RUN_RESEARCH_TASK", "enqueue_run", "redis_settings_from"]

_log = structlog.get_logger("aer.queue")

# The registered task name. Shared rather than repeated: a producer and a consumer that
# disagree about it produce a queue that accepts work nothing ever runs.
RUN_RESEARCH_TASK = "run_research"


async def enqueue_run(redis: Any, job_id: uuid.UUID) -> str | None:
    """Queue a run, from the web process.

    Returns the queued task's id, or ``None`` if the queue is unavailable — the caller
    decides what to do about that. A web request that failed because a background queue was
    down would be an unhelpful error for an operator who has just approved a plan; the run
    is recorded and can be started again.
    """
    from arq import create_pool  # noqa: PLC0415 -- only needed when actually enqueueing

    pool = None
    try:
        pool = await create_pool(redis_settings_from(redis))
        task = await pool.enqueue_job(RUN_RESEARCH_TASK, str(job_id))
    except Exception as exc:
        _log.warning("queue.enqueue_failed", job_id=str(job_id), error=str(exc))
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
