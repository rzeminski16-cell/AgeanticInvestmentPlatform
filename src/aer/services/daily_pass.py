"""The daily pass: one scheduled job after the close, and one place to look when it did not run.

F15, under ADRs 0083 and 0120. The platform has a *queue*, which is a different thing from a
*schedule*: a queue runs what somebody asked for, and nothing in it ever happens because a day
went by. This is the schedule.

**What the pass does today is prices, and only prices.** For every listing this person holds or
watches, it reads the closes the platform does not already have and stores them. That is what
"the book is valued at yesterday's close" means here, because there is no ``positions`` table
to write a valuation into (ADR 0083): the book is computed on the way to the screen, from bars,
so having yesterday's bars *is* having the book valued. Nothing is cached and nothing is
pre-computed.

**It does not run premise checks, and that is a correction to F15 rather than an omission.**
The feature specification says the pass should *"run any premise check whose cadence is due"*
because *"premise checks are arithmetic and cost nothing"*. Measured against
:mod:`aer.services.thesis_monitor`, that is not what a premise check is: code measures the
metric and decides the predicate, and **then the model is asked what the new facts do to the
premise**. The arithmetic is free; the reading is a model call. An unattended job that makes
model calls spends money with nobody watching, under a specification line that says no standing
budget is needed — so the premise half waits for F11 and for the operator's decision about
standing spend. Recorded in roadmap §3.19.

**A pass is a run like any other**: a work order, a job, a status, a recorded spend of zero. It
is not a log line, because *"when did this last work?"* has to be answerable from the record
rather than from whatever the worker's terminal still has in its scrollback.

**Scoped to a person, always** (ADR 0120 §1). Which listings to read is a fact about somebody's
book and their watchlist; the bars that come back are the world's, and are shared.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import JobStatus, RequestStatus
from aer.db.models import Job, Portfolio, Security, Transaction, User, WatchlistEntry, WorkOrder
from aer.errors import AerError
from aer.services import price_alerts
from aer.services.price_acquisition import PriceClient
from aer.services.prices import record_bars
from aer.version import git_sha

__all__ = [
    "CADENCE",
    "GRACE",
    "NEVER_RUN",
    "SUBJECT_BOOK",
    "TOOL",
    "WORKFLOW_VERSION",
    "DailyOutcome",
    "PassState",
    "last_pass",
    "listings_to_read",
    "pass_state",
    "run_daily_pass",
]

_log = structlog.get_logger("aer.services.daily_pass")

TOOL: Final = "daily"
SUBJECT_BOOK: Final = "portfolio"
WORKFLOW_VERSION: Final = "daily_pass_v1"

# How often the pass is meant to run, and how late it may be before it is *missed* rather
# than merely due. Six hours because a schedule that ran at 22:00 and is read at 09:00 the
# next morning has not failed — it is not due again yet — and calling that a miss is how a
# health indicator teaches its reader to ignore it.
CADENCE: Final = timedelta(days=1)
GRACE: Final = timedelta(hours=6)

NEVER_RUN: Final = (
    "No daily pass has run yet. The schedule starts with the worker; until one has run "
    "there is nothing to be late."
)

# A pass reads prices and nothing else, so it cannot spend. Recorded rather than left at the
# column default, because "this run cost nothing" and "nobody set a budget" are different
# claims and the ledger has to be able to tell them apart.
NO_SPEND: Final = Decimal("0.00")


@dataclass(slots=True)
class DailyOutcome:
    """What one pass over one person's listings did."""

    job: Job
    as_of: date
    read: int = 0
    """Listings the pass asked the vendor about."""

    stored: int = 0
    """Bars the platform did not already hold, now stored."""

    problems: list[str] = field(default_factory=list)
    """One sentence per listing the pass could not read. Never raised: one delisted ticker
    does not get to stop the other forty."""

    note: str = ""

    alerts: int = 0
    """Price moves past a threshold, each a finding beside the thesis state (F11)."""


@dataclass(frozen=True, slots=True)
class PassState:
    """Whether the schedule is working, for the surface that says so."""

    last_finished: datetime | None
    is_missed: bool
    sentence: str


async def listings_to_read(session: AsyncSession, *, user_id: uuid.UUID) -> list[Security]:
    """Every listing this person holds or watches, once each.

    Held first, watched second, because a position is money and a watchlist entry is
    curiosity — and if a vendor ceiling ever bites, it should bite the curiosity.

    A watched company with no listing on record is not here and is not a problem: the
    watchlist stores a ticker and an exchange, and the platform creates the listing when a
    run prices it or a trade names it (ADR 0093).
    """
    held = (
        select(Security)
        .join(Transaction, Transaction.security_id == Security.id)
        .join(Portfolio, Portfolio.id == Transaction.portfolio_id)
        .where(Portfolio.user_id == user_id, Portfolio.archived_at.is_(None))
    )
    watched = (
        select(Security)
        .join(
            WatchlistEntry,
            (WatchlistEntry.ticker == Security.ticker)
            & (WatchlistEntry.exchange == Security.exchange),
        )
        .where(WatchlistEntry.user_id == user_id, WatchlistEntry.withdrawn_at.is_(None))
    )

    found: dict[uuid.UUID, Security] = {}
    for statement in (held, watched):
        for row in await session.scalars(statement):
            found.setdefault(row.id, row)
    return sorted(found.values(), key=lambda row: (row.ticker, row.exchange))


async def run_daily_pass(
    session: AsyncSession,
    client: PriceClient | None,
    *,
    user: User,
    settings: Settings,
    as_of: date,
    now: datetime | None = None,
) -> DailyOutcome:
    """Read yesterday's closes for everything this person holds or watches.

    Args:
        client: ``None`` when no market-data subscription is configured, which is an
            ordinary state and not an error. The pass still runs, still records that it
            ran, and says in its note that it had nothing to read with — because a
            schedule that silently does nothing is the failure this feature exists to make
            visible.
        as_of: The close to read up to. The caller's, not the clock's: a pass is about a
            *market* day, and the machine's idea of today is not one.
    """
    started = now or datetime.now(UTC)
    order = WorkOrder(
        user_id=user.id,
        tool=TOOL,
        subject_kind=SUBJECT_BOOK,
        subject_id=None,
        as_of_date=as_of,
        max_cost_gbp=NO_SPEND,
        status=RequestStatus.RUNNING,
    )
    session.add(order)
    await session.flush()
    job = Job(
        work_order_id=order.id,
        workflow_version=WORKFLOW_VERSION,
        code_version=git_sha() or "unknown",
        status=JobStatus.RUNNING,
        started_at=started,
    )
    session.add(job)
    await session.flush()
    outcome = DailyOutcome(job=job, as_of=as_of)

    listings = await listings_to_read(session, user_id=user.id)
    if client is None:
        outcome.note = (
            f"No market-data subscription is configured, so no close could be read for any "
            f"of the {len(listings)} listings on record. The book will value at whatever "
            "date the platform last has a price for."
        )
    elif not listings:
        outcome.note = (
            "Nothing is held and nothing is watched, so there was no close to read. The pass ran."
        )
    else:
        for security in listings:
            outcome.read += 1
            try:
                response = await client.fetch_bars(security.provider_symbol, as_of=as_of)
            except AerError as refused:
                # One bad symbol is one bad symbol. The alternative — raising — makes a
                # delisting stop the other listings from being read at all, every night,
                # until somebody notices.
                outcome.problems.append(f"{security.ticker}: {refused.message}")
                continue
            stored = await record_bars(session, security=security, response=response, job_id=job.id)
            outcome.stored += stored.inserted

    # The second kind of alert, once the closes are in (F11). Arithmetic on stored bars and
    # nothing else: no provider is in reach of this function, so it cannot spend.
    moved = await price_alerts.check_watched_listings(
        session, user=user, settings=settings, as_of=as_of, job_id=job.id, now=now
    )
    outcome.alerts = len(moved)

    finished = datetime.now(UTC) if now is None else now
    job.status = JobStatus.SUCCEEDED
    job.finished_at = finished
    job.total_cost_gbp = NO_SPEND
    order.status = RequestStatus.COMPLETED
    _log.info(
        "daily_pass.finished",
        user_id=str(user.id),
        job_id=str(job.id),
        as_of=as_of.isoformat(),
        read=outcome.read,
        stored=outcome.stored,
        alerts=outcome.alerts,
        problems=len(outcome.problems),
    )
    return outcome


async def last_pass(session: AsyncSession, *, user_id: uuid.UUID) -> Job | None:
    """The most recent finished pass for this person, or ``None``."""
    found: Job | None = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(
            WorkOrder.user_id == user_id,
            WorkOrder.tool == TOOL,
            Job.finished_at.is_not(None),
        )
        .order_by(Job.finished_at.desc())
        .limit(1)
    )
    return found


def pass_state(last_finished: datetime | None, *, now: datetime) -> PassState:
    """Whether the schedule is working, said in words.

    Pure, and takes ``now`` rather than reading a clock, so a test can hold the clock still
    and assert the sentence at the hour it wants rather than at the hour it happens to be —
    which is the only way *"a missed run is visible"* can be tested at all without waiting
    a day for it.
    """
    if last_finished is None:
        return PassState(last_finished=None, is_missed=False, sentence=NEVER_RUN)

    since = now - last_finished
    when = f"{last_finished:%d %B %Y at %H:%M} UTC"
    if since <= CADENCE + GRACE:
        return PassState(
            last_finished=last_finished,
            is_missed=False,
            sentence=f"The daily pass last ran on {when}.",
        )
    days = since // timedelta(days=1)
    missed = f"{days} day{'s' if days != 1 else ''}"
    return PassState(
        last_finished=last_finished,
        is_missed=True,
        sentence=(
            f"The daily pass has not run for {missed} — it last ran on {when}. Prices are "
            "as stale as that, and so is every figure computed from them."
        ),
    )
