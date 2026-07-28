"""The background worker: where a research run actually executes.

A run takes twenty to sixty minutes. Doing that inside an HTTP request would mean a
browser tab holding a connection open for an hour, a proxy timing it out somewhere in the
middle, and no way to close the laptop. So the web process enqueues and the worker runs.

**The worker is not a second implementation of anything.** It resolves a job, builds the
service bundle, and calls :func:`aer.services.runs.execute` — the same function the tests
call directly. What the worker adds is a process to run it in and a queue to reach it.

**One transaction per run attempt, committed at the end.** A run that dies mid-step leaves
the database as it was at the last commit, and the engine resumes from the last step that
succeeded. Committing per step would be finer-grained and would also mean a half-written
step's output could be read as complete.

Run it with::

    uv run arq aer.worker.WorkerSettings
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import structlog
from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings, get_settings
from aer.db.engine import create_engine
from aer.logging import configure_logging
from aer.runtime import build_services
from aer.services import runs as run_service

__all__ = ["RUN_RESEARCH_TASK", "WorkerSettings", "enqueue_run", "run_research"]

_log = structlog.get_logger("aer.worker")

RUN_RESEARCH_TASK = "run_research"

# How long a single run may take before arq abandons it. Generous: a real run is twenty to
# sixty minutes, and a worker that killed one at the median would fail exactly the runs
# that had the most work in them.
_JOB_TIMEOUT_SECONDS = 7200

# Runs are not retried automatically. A failed run has already spent money, and blindly
# repeating it would spend the same again on the same failure. Resuming is a deliberate
# act, and the engine makes it cheap by skipping the steps that succeeded.
_MAX_TRIES = 1


async def run_research(ctx: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Execute a research run to its next stopping point.

    Stops at an approval gate, at the budget cap, or at the end. Called again after an
    approval, it resumes from the gate rather than the beginning.
    """
    settings: Settings = ctx["settings"]
    session_factory: async_sessionmaker[Any] = ctx["session_factory"]
    redis: Redis = ctx["aer_redis"]

    parsed = uuid.UUID(job_id)
    services = build_services(settings, redis=redis)

    async with session_factory() as session:
        state = await run_service.run_state(session, job_id=parsed)

        outcome = await run_service.execute(
            session,
            job=state.job,
            settings=settings,
            provider=services.provider,
            store=services.store,
            sec_client=services.sec_client,
        )
        # One commit, at the end. A run that dies mid-step leaves the database as it was,
        # and the engine resumes from the last step that succeeded.
        await session.commit()

    _log.info(
        "worker.run_finished",
        job_id=job_id,
        status=outcome.status.value,
        spend_gbp=str(outcome.spend_gbp),
        waiting=outcome.is_waiting,
    )
    return {
        "job_id": job_id,
        "status": outcome.status.value,
        "spend_gbp": str(outcome.spend_gbp),
    }


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
        pool = await create_pool(_redis_settings_from(redis))
        task = await pool.enqueue_job(RUN_RESEARCH_TASK, str(job_id))
    except Exception as exc:
        _log.warning("worker.enqueue_failed", job_id=str(job_id), error=str(exc))
        return None
    finally:
        # Closed every time. `create_pool` opens its own connection pool, and a web
        # process that enqueues without closing leaks one per approval -- invisible until
        # the Redis connection limit is reached, at which point nothing can be queued at
        # all.
        if pool is not None:
            await pool.aclose()

    return task.job_id if task is not None else None


def _redis_settings_from(redis: Any) -> RedisSettings:
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


async def _startup(ctx: dict[str, Any]) -> None:
    """Build the engine, the session factory and a Redis client, once per worker."""
    configure_logging()
    settings = get_settings()

    engine = create_engine(settings)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["aer_redis"] = Redis.from_url(settings.redis_url, decode_responses=True)

    _log.info("worker.started", database=settings.database_url.split("@")[-1])


async def _shutdown(ctx: dict[str, Any]) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    redis = ctx.get("aer_redis")
    if redis is not None:
        await redis.aclose()
    _log.info("worker.stopped")


def _worker_redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    """arq's entry point. ``uv run arq aer.worker.WorkerSettings``."""

    # arq reads these as class attributes, so they are declared as ClassVar rather than
    # moved into an __init__ it never calls.
    functions: ClassVar[list[Any]] = [run_research]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = _worker_redis_settings
    job_timeout = _JOB_TIMEOUT_SECONDS
    max_tries = _MAX_TRIES

    # One run at a time. A second concurrent run would double the rate against every data
    # provider and make the shared token bucket the only thing standing between this
    # platform and an IP ban.
    max_jobs = 1
