"""Everything the runbook checks by hand before a paid run, in one readout.

`docs/users/the-confirmation-run.md` stage 1 walks the operator through seven checks —
the code they are running, the services, the schema, an account, the configuration, the
wire contract, the worker — and the confirmation run of 2026-09-05 still lost a night to
the one that is easiest to skip, because each check lived in its own command and nothing
said which had not been done. This module asks every question the platform can answer for
itself and prints one line per answer, so the run that follows is started on a known
footing rather than an assumed one.

**Three verdicts, and what each means for the run.** A ``FAIL`` is something the run
cannot survive: no database, a schema behind the models, no worker listening, no model
key, no user. A ``WARN`` is something the run survives and the operator should know: no
price feed (the comparables table stays empty, by design), a cap within a retry's worth of
the last run's cost, a month with less room than a run. A ``SKIP`` is a check that could
not be made because an earlier one failed, or one only a paid call can make — the wire
contract, which `just test-live` proves for a fraction of a penny and this command names
rather than performs. **Nothing here calls a model or fetches anything**: preflight costs
nothing and changes nothing.

**What this cannot see.** The worker reads ``.env`` when *it* starts, so a key added since
is in this process and not in the worker's; the readout says so beside the price feed
rather than pretending to know. The worker's own startup line is the authority there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.api.deps import current_user_or_none
from aer.config import Settings
from aer.db.schema_check import schema_drift
from aer.logging import redact_value
from aer.queue import HEALTH_CHECK_INTERVAL_SECONDS, worker_health
from aer.services.configuration import effective_settings
from aer.services.spend import recent_runs
from aer.workflow.engine import spend_this_month

__all__ = ["Check", "CheckStatus", "Preflight", "run_preflight"]

_log = structlog.get_logger("aer.services.preflight")

# A probe that hangs tells the operator nothing; a fast failure tells them what to start.
_PROBE_TIMEOUT_SECONDS: Final = 5.0

# The headroom a cap should carry over the last run's cost before a run with the same
# shape is likely to pause at it: one retried section on the dearest route, roughly.
_CAP_HEADROOM: Final = Decimal("1.25")


class CheckStatus(StrEnum):
    PASS = "pass"  # noqa: S105 -- a verdict, not a secret
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class Check:
    """One question answered: its name, its verdict, and the sentence that explains it."""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class Preflight:
    """The whole readout, in the order the runbook asks the questions."""

    at: datetime
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        """Nothing the run cannot survive. Warnings are the operator's to weigh."""
        return not any(check.status is CheckStatus.FAIL for check in self.checks)

    def counted(self, status: CheckStatus) -> int:
        return sum(1 for check in self.checks if check.status is status)


async def run_preflight(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Any,
    now: datetime | None = None,
) -> Preflight:
    """Every check, each reported on its own, none of them able to stop the others.

    The order is the runbook's, and the dependencies are honoured rather than hidden: a
    schema cannot be checked on a database that does not answer, and a worker cannot be
    looked for in a Redis that does not, so those rows say ``SKIP`` and name the row they
    are waiting on instead of reporting a second failure for the first cause.
    """
    moment = now or datetime.now(UTC)
    checks: list[Check] = [_provider_key(settings)]

    database = await _probe("database", _database_answers(session_factory))
    checks.append(database)
    if database.status is CheckStatus.PASS:
        async with session_factory() as session:
            checks.append(await _probe("schema", _schema_at_head(session)))
            checks.append(await _probe("user", _a_user_exists(session)))
            resolved = await effective_settings(session, settings)
            checks.append(await _probe("run_cap", _cap_against_last_run(session, resolved)))
            checks.append(await _probe("monthly_room", _months_room(session, resolved, moment)))
    else:
        checks.extend(
            _skipped(name, "not checked: the database did not answer")
            for name in ("schema", "user", "run_cap", "monthly_room")
        )

    cache = await _probe("redis", _redis_answers(redis))
    checks.append(cache)
    if cache.status is CheckStatus.PASS:
        checks.append(await _probe("worker", _a_worker_is_listening(redis)))
    else:
        checks.append(_skipped("worker", "not checked: Redis did not answer"))

    checks.append(_price_feed(settings))
    checks.append(
        Check(
            "wire_contract",
            CheckStatus.SKIP,
            "not checked here: `just test-live` proves the API still accepts this build's "
            "payloads, for a fraction of a penny. Run it before a paid run.",
        )
    )

    readout = Preflight(at=moment, checks=tuple(checks))
    _log.info(
        "preflight.completed",
        ok=readout.ok,
        failed=[check.name for check in checks if check.status is CheckStatus.FAIL],
        warned=[check.name for check in checks if check.status is CheckStatus.WARN],
    )
    return readout


# -- The checks --------------------------------------------------------------------------------


def _provider_key(settings: Settings) -> Check:
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        return Check(
            "provider_key",
            CheckStatus.FAIL,
            "AER_ANTHROPIC_API_KEY is not set. The run would stop at its first model call.",
        )
    return Check(
        "provider_key", CheckStatus.PASS, "AER_ANTHROPIC_API_KEY is set (value not shown)."
    )


def _price_feed(settings: Settings) -> Check:
    if settings.price_feed_configured:
        return Check(
            "price_feed",
            CheckStatus.PASS,
            "AER_EODHD_API_KEY is set in this process. The worker reads .env when it starts: "
            "its startup line must say price_feed=configured too, or restart it.",
        )
    return Check(
        "price_feed",
        CheckStatus.WARN,
        "AER_EODHD_API_KEY is not set. No peers will be proposed and the comparables table "
        "stays empty; the valuation page says so. Set it and restart the worker if you want "
        "peers.",
    )


async def _database_answers(session_factory: async_sessionmaker[AsyncSession]) -> Check:
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))
    return Check("database", CheckStatus.PASS, "PostgreSQL answers.")


async def _schema_at_head(session: AsyncSession) -> Check:
    drift = await schema_drift(session)
    if drift.is_clean:
        return Check("schema", CheckStatus.PASS, "The database schema matches the models.")
    return Check("schema", CheckStatus.FAIL, drift.as_message())


async def _a_user_exists(session: AsyncSession) -> Check:
    user = await current_user_or_none(session)
    if user is None:
        return Check(
            "user",
            CheckStatus.FAIL,
            "No user exists, and a run needs one to approve its gates. Create it: "
            "uv run aer seed-user --email you@example.com",
        )
    return Check("user", CheckStatus.PASS, f"{user.email} can approve the gates.")


async def _redis_answers(redis: Any) -> Check:
    await redis.ping()
    return Check("redis", CheckStatus.PASS, "Redis answers.")


async def _a_worker_is_listening(redis: Any) -> Check:
    health = await worker_health(redis)
    window = HEALTH_CHECK_INTERVAL_SECONDS + 1
    if health is None:
        return Check(
            "worker",
            CheckStatus.FAIL,
            f"No worker has reported in the last {window} s. A run started now sits queued "
            "until one is started: just worker",
        )
    ago = f"reported {health.reported_seconds_ago} s ago"
    if health.ongoing:
        return Check(
            "worker",
            CheckStatus.WARN,
            f"A worker is alive ({ago}) but busy with {health.ongoing} job(s); it takes one at "
            "a time, so a run started now waits its turn.",
        )
    return Check("worker", CheckStatus.PASS, f"A worker is alive and idle ({ago}).")


async def _cap_against_last_run(session: AsyncSession, settings: Settings) -> Check:
    """The platform ceiling against what the last run that spent actually cost."""
    cap = settings.per_run_budget_gbp
    spent = next(
        ((job, cost) for job, cost in await recent_runs(session, limit=20) if cost > 0), None
    )
    if spent is None:
        return Check(
            "run_cap",
            CheckStatus.PASS,
            f"The per-run ceiling is £{cap:.2f} (AER_PER_RUN_BUDGET_GBP). No earlier run has "
            "spent anything to compare it with.",
        )
    job, cost = spent
    stated = (
        f"The per-run ceiling is £{cap:.2f}; the last run that spent ({job.id}) cost £{cost:.2f}"
    )
    if cap < cost:
        return Check(
            "run_cap",
            CheckStatus.WARN,
            f"{stated}, more than the ceiling. A run of the same shape pauses at the cap "
            "mid-way; raise AER_PER_RUN_BUDGET_GBP, and set the request's cap to match.",
        )
    if cap < cost * _CAP_HEADROOM:
        return Check(
            "run_cap",
            CheckStatus.WARN,
            f"{stated}, within a retry's worth of the ceiling. Every refused section costs "
            "another attempt; a run with a few more pauses at the cap for a decision. Raise "
            "AER_PER_RUN_BUDGET_GBP if you would rather not be asked mid-draft.",
        )
    return Check("run_cap", CheckStatus.PASS, f"{stated}; the ceiling has room for retries.")


async def _months_room(session: AsyncSession, settings: Settings, now: datetime) -> Check:
    spent = await spend_this_month(session, now=now)
    cap = settings.monthly_budget_gbp
    room = cap - spent
    stated = f"£{spent:.2f} of the month's £{cap:.2f} is spent; £{room:.2f} remains"
    if room <= 0:
        return Check(
            "monthly_room",
            CheckStatus.FAIL,
            f"{stated}. Every model call is refused until the month turns or "
            "AER_MONTHLY_BUDGET_GBP is raised.",
        )
    if room < settings.per_run_budget_gbp:
        return Check(
            "monthly_room",
            CheckStatus.WARN,
            f"{stated}, less than one run's ceiling of £{settings.per_run_budget_gbp:.2f}. A "
            "full run may stop at the month's cap rather than its own.",
        )
    return Check("monthly_room", CheckStatus.PASS, f"{stated}.")


# -- Running a check without letting it take the others down ---------------------------------


async def _probe(name: str, check: Coroutine[Any, Any, Check]) -> Check:
    """One check, bounded in time, its failure a row rather than a traceback.

    The driver's message is truncated and redacted before it is shown: it quotes hosts,
    ports and users, and this readout is the kind of thing that gets pasted into a bug
    report.
    """
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            return await check
    except TimeoutError:
        return Check(name, CheckStatus.FAIL, f"did not answer within {_PROBE_TIMEOUT_SECONDS:g} s.")
    # Every failure is a row, whatever raised it: the readout is the diagnostic.
    except Exception as failure:
        detail = redact_value(str(failure)[:300]) or type(failure).__name__
        return Check(name, CheckStatus.FAIL, f"{type(failure).__name__}: {detail}")


def _skipped(name: str, why: str) -> Check:
    return Check(name, CheckStatus.SKIP, why)
