"""Liveness and readiness probes.

Two endpoints, deliberately different in kind. Collapsing them into one "health" endpoint
is the usual mistake: a restart loop follows, because the process gets killed for a
dependency outage it would have survived.

* ``/healthz`` — **liveness**. Is this process running and able to answer? It touches
  nothing external, so it cannot fail for a reason a restart would not fix.
* ``/readyz`` — **readiness**. Can this process do useful work *right now*? It checks
  every dependency and reports each one separately, because "not ready" without saying
  what is missing sends you reading logs to learn something the probe already knew.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Final

from fastapi import APIRouter, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from aer.api.deps import DbSession, RedisClient
from aer.logging import redact_value
from aer.version import git_sha, version

__all__ = ["router"]

router = APIRouter(tags=["health"])

# A probe that hangs is worse than one that fails: an orchestrator waiting on a response
# learns nothing, while a fast failure is actionable. Fixed rather than configurable --
# there is no deployment in which a local dependency taking two seconds to answer a
# `SELECT 1` should still count as ready.
_PROBE_TIMEOUT_SECONDS: Final = 2.0

_DETAIL_MAX_CHARS: Final = 300


def _failure(exc: BaseException) -> dict[str, Any]:
    """Describe a failed check without repeating whatever the driver put in the message.

    Driver exceptions quote hosts, ports, users and SQL. None of that belongs in an
    unauthenticated endpoint, so the text is truncated and passed through the same
    redaction the logs use.
    """
    if isinstance(exc, TimeoutError):
        detail = f"timed out after {_PROBE_TIMEOUT_SECONDS}s"
    else:
        detail = str(exc)[:_DETAIL_MAX_CHARS]
    return {
        "status": "error",
        "error": type(exc).__name__,
        "detail": redact_value(detail),
    }


async def _timed(check: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            await check
    except Exception as exc:
        result = _failure(exc)
    else:
        result = {"status": "ok"}
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


async def _check_database(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1"))


async def _check_redis(client: Redis) -> None:
    await client.ping()


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str | None]:
    """Always 200 while the process can answer. Touches no dependency."""
    return {"status": "ok", "version": version(), "git_sha": git_sha()}


@router.get("/readyz", summary="Readiness probe")
async def readyz(response: Response, session: DbSession, redis: RedisClient) -> dict[str, Any]:
    """200 when every dependency answers, 503 with a per-dependency breakdown otherwise."""
    database, cache = await asyncio.gather(
        _timed(_check_database(session)),
        _timed(_check_redis(redis)),
    )
    checks = {"database": database, "redis": cache}

    failing = sorted(name for name, result in checks.items() if result["status"] != "ok")
    if failing:
        response.status_code = HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if not failing else "unavailable",
        "version": version(),
        "failing": failing,
        "checks": checks,
    }
