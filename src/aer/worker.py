"""The background worker: where a research run actually executes.

A run takes twenty to sixty minutes. Doing that inside an HTTP request would mean a
browser tab holding a connection open for an hour, a proxy timing it out somewhere in the
middle, and no way to close the laptop. So the web process enqueues and the worker runs.

**The worker is not a second implementation of anything.** It resolves a job, builds the
service bundle, and calls :func:`aer.services.runs.execute` — the same function the tests
call directly. What the worker adds is a process to run it in and a queue to reach it.

**One transaction per step, committed as each one finishes.** A run that dies mid-step
leaves the database as it was at the last commit, and the engine resumes from the last step
that succeeded.

That used to say "one transaction per run", with the same claim about resuming attached to
it — and the claim was false. Between gates, a crash rolled back *every* completed step, so
the next attempt began at step one and paid for the work again. It also meant the run console
could show nothing: Postgres publishes on commit, so a run that had spent real money still
read ``QUEUED`` from the web process, and a run that had *failed* reverted to ``QUEUED`` when
the exception unwound this function before its commit. The step boundary is where the
recorded state is whole, so it is where the commit belongs. See
``aer.workflow.engine.WorkflowEngine._publish``.

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
from aer.db.models import Thesis, User
from aer.errors import ValidationError
from aer.logging import configure_logging
from aer.runtime import build_services
from aer.services import runs as run_service
from aer.services import theses as thesis_service
from aer.services import thesis_monitor
from aer.services.configuration import effective_settings
from aer.tracing import configure_tracing
from aer.version import version

__all__ = ["WorkerSettings", "run_monitor", "run_research"]

_log = structlog.get_logger("aer.worker")

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

    **A job whose run no longer exists is discarded, not failed** (gap A57). The queue
    outlives the rows it points at — `aer reset-research` removes the runs and Redis
    keeps the entries — so a worker starting after a reset replayed every dead job as an
    error with a full traceback, and raising made arq retry each one. Twenty tracebacks
    at startup is a window in which a real failure is invisible, and there is nothing
    here to recover: the run the job names is gone, and its absence is the answer.
    """
    settings: Settings = ctx["settings"]
    session_factory: async_sessionmaker[Any] = ctx["session_factory"]
    redis: Redis = ctx["aer_redis"]

    parsed = uuid.UUID(job_id)

    async with session_factory() as session:
        # Before anything is built for it: a job naming a run that no longer exists is
        # answered by its absence, and resolving settings or opening a provider for it
        # would be work done on behalf of nothing.
        try:
            state = await run_service.run_state(session, job_id=parsed)
        except ValidationError:
            _log.warning("worker.run_vanished", job_id=job_id)
            return {"job_id": job_id, "status": "discarded", "spend_gbp": "0"}

        # Read once, here, rather than per step. A run whose routing or budget changed
        # halfway through would have a provenance record describing two platforms; this way
        # a change applies to runs that start after it, which is what an operator means by
        # "change the model". See ADR 0050.
        settings = await effective_settings(session, settings)
        services = build_services(settings, redis=redis)

        outcome = await run_service.execute(
            session,
            session_factory=session_factory,
            job=state.job,
            settings=settings,
            provider=services.provider,
            store=services.store,
            sec_client=services.sec_client,
            fetcher=services.fetcher,
        )
        # The engine and `execute` commit at every state they reach, so by here there is
        # normally nothing pending. Kept because this function owns the session: anything a
        # future step leaves uncommitted is published by whoever opened the transaction, not
        # left to be discovered when a row is missing.
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


async def run_monitor(ctx: dict[str, Any], thesis_id: str) -> dict[str, Any]:
    """One monitor pass over one thesis (roadmap §3.6).

    The pass reads every predicated premise against what has been filed since, writes
    findings, and stops with a finding rather than pausing if a call would breach a cap
    (ADR 0078). A thesis that has gone, or was retired since it was queued, is discarded
    the way a vanished run is: its absence is the answer.
    """
    settings: Settings = ctx["settings"]
    session_factory: async_sessionmaker[Any] = ctx["session_factory"]
    redis: Redis = ctx["aer_redis"]

    parsed = uuid.UUID(thesis_id)

    async with session_factory() as session:
        thesis = await session.get(Thesis, parsed)
        if thesis is None or thesis.is_retired:
            _log.warning("worker.thesis_vanished", thesis_id=thesis_id)
            return {"thesis_id": thesis_id, "status": "discarded", "spend_gbp": "0"}
        # Reloaded through the service so the premises and their judgements arrive by the
        # loaders the pass reads them through, rather than as first-touch lazy loads.
        loaded = await thesis_service.thesis_of(session, parsed, user_id=thesis.user_id)
        user = await session.get(User, thesis.user_id)
        if loaded is None or user is None:  # pragma: no cover -- the row was just read
            return {"thesis_id": thesis_id, "status": "discarded", "spend_gbp": "0"}

        settings = await effective_settings(session, settings)
        services = build_services(settings, redis=redis)
        outcome = await thesis_monitor.run_monitor(
            session,
            settings=settings,
            provider=services.provider,
            router=services.router,
            store=services.store,
            user=user,
            thesis=loaded,
        )
        await session.commit()

    _log.info(
        "worker.monitor_finished",
        thesis_id=thesis_id,
        job_id=str(outcome.job.id),
        findings=len(outcome.findings),
        stopped=outcome.stopped,
        spend_gbp=str(outcome.spend_gbp),
    )
    return {
        "thesis_id": thesis_id,
        "status": outcome.job.status.value,
        "findings": len(outcome.findings),
        "spend_gbp": str(outcome.spend_gbp),
    }


async def _startup(ctx: dict[str, Any]) -> None:
    """Build the engine, the session factory and a Redis client, once per worker."""
    configure_logging()
    # The worker is where a run actually happens, so it is the process whose spans matter.
    # Off unless AER_OTEL_ENDPOINT is set; see ADR 0049.
    configure_tracing(service_version=version())
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


class WorkerSettings:
    """arq's entry point. ``uv run arq aer.worker.WorkerSettings``.

    arq reads this class's ``__dict__`` directly — not through ``getattr`` — so every
    value here must be the finished article. A function assigned to ``redis_settings``,
    intending lazy resolution, is handed to arq *as a function*, and the worker dies on
    startup with ``'function' object has no attribute 'host'``. A property or a metaclass
    would not help either: neither appears in ``__dict__``.

    So the connection settings are resolved when this module is imported, which is why
    nothing but the worker imports it — see :mod:`aer.queue`.
    """

    # Declared ClassVar rather than moved into an __init__ arq never calls.
    functions: ClassVar[list[Any]] = [run_research, run_monitor]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = _JOB_TIMEOUT_SECONDS
    max_tries = _MAX_TRIES

    # One run at a time. A second concurrent run would double the rate against every data
    # provider and make the shared token bucket the only thing standing between this
    # platform and an IP ban.
    max_jobs = 1
