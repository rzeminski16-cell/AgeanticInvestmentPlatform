"""What the operator follows, the queue of what to research next, and the standing budget
the queue spends.

Roadmap §3.10, under ADR 0107. Three things.

**An entry is a standing intention.** A listing checked against the same universe rule a
research request is, a sentence saying what would make it worth researching, and when the
platform came to know the operator follows it. Nothing on it says "researched".

**A commission is a dated run.** The queue turns an entry into an ordinary research request
— the same validation, the same gates, the same report — as at a date, with the per-run
cap, and keeps the link. An entry's state is read from its latest commission's run.

**The standing budget bounds what the queue may start.** A pound figure for the calendar
month, with the caps of runs still alive reserved against it, so a queue cannot commission
the month's budget in an afternoon and find out in a week. A run the queue starts keeps
its own cap and the month's cap applies on top.

Nothing here approves anything: every run the queue starts stops at gate one for the
operator, as every research run does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.config import Settings
from aer.core.enums import AnalysisMode, JobStatus
from aer.core.schemas.request import ResearchRequestCreate
from aer.core.universe import check_universe
from aer.db.models import (
    AuditEvent,
    Job,
    Report,
    ResearchRequest,
    User,
    WatchlistCommission,
    WatchlistEntry,
)
from aer.errors import BudgetExceededError, ConflictError, ValidationError
from aer.services import requests as request_service
from aer.services import runs as run_service
from aer.workflow.engine import spend_so_far

__all__ = [
    "DEFAULT_HORIZON_MONTHS",
    "DEFAULT_MODE",
    "LIVE_STATUSES",
    "Drain",
    "EntryState",
    "StandingBudget",
    "commission",
    "commission_next",
    "entries_for",
    "entry_of",
    "follow",
    "queue_for",
    "standing_budget",
    "state_of",
    "states_for",
    "stop_following",
]

_log = structlog.get_logger("aer.services.watchlist")

TOOL: Final = "watchlist"

# What a commission asks for, beyond what the entry carries. The standard depth and a year:
# the request form's own defaults, so a run the queue starts is the run the operator would
# have commissioned by hand with nothing changed.
DEFAULT_HORIZON_MONTHS: Final = 12
DEFAULT_MODE: Final = AnalysisMode.STANDARD
_HORIZON_LABEL: Final = "Commissioned from the watchlist"

# A run in one of these may still spend up to its cap, so the standing budget reserves the
# rest of that cap against it (ADR 0107 §2). A finished, failed or cancelled run has spent
# what it spent.
LIVE_STATUSES: Final[frozenset[JobStatus]] = frozenset(
    {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.PAUSED,
        JobStatus.AWAITING_APPROVAL,
        JobStatus.BUDGET_EXCEEDED,
    }
)


# -- The standing budget ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StandingBudget:
    """What the queue may still start this month, and why."""

    budget_gbp: Decimal
    spent_gbp: Decimal
    reserved_gbp: Decimal
    cap_gbp: Decimal
    """The per-run cap a commission would carry: what one more run costs the room."""
    month_start: date

    @property
    def room_gbp(self) -> Decimal:
        return max(Decimal(0), self.budget_gbp - self.spent_gbp - self.reserved_gbp)

    def affords(self, cap: Decimal) -> bool:
        return cap <= self.room_gbp

    @property
    def fits(self) -> int:
        """How many more runs at the cap the room holds."""
        if self.cap_gbp <= 0:
            return 0
        return int(self.room_gbp // self.cap_gbp)


async def standing_budget(
    session: AsyncSession, *, settings: Settings, user_id: uuid.UUID, now: datetime | None = None
) -> StandingBudget:
    """The queue's monthly ceiling less what its runs spent and what the live ones still may.

    The month is UTC's, the same month :func:`aer.workflow.engine.spend_this_month` uses, so
    the two ceilings reset at one instant.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    commissions = list(
        await session.scalars(
            select(WatchlistCommission)
            .join(WatchlistEntry, WatchlistEntry.id == WatchlistCommission.entry_id)
            .where(
                WatchlistEntry.user_id == user_id,
                WatchlistCommission.commissioned_at >= start,
                WatchlistCommission.request_id.is_not(None),
            )
        )
    )
    spent = Decimal(0)
    reserved = Decimal(0)
    for row in commissions:
        assert row.request_id is not None
        job = await run_service.latest_run(session, request_id=row.request_id)
        if job is None:
            continue
        on_this = await spend_so_far(session, job_id=job.id)
        spent += on_this
        if job.status in LIVE_STATUSES:
            reserved += max(Decimal(0), Decimal(str(row.cap_gbp)) - on_this)
    return StandingBudget(
        budget_gbp=settings.watchlist_budget_gbp,
        spent_gbp=spent,
        reserved_gbp=reserved,
        cap_gbp=settings.per_run_budget_gbp,
        month_start=start.date(),
    )


# -- Following ------------------------------------------------------------------------------


async def follow(
    session: AsyncSession,
    *,
    user: User,
    company_name: str,
    ticker: str,
    exchange: str,
    why: str = "",
) -> WatchlistEntry:
    """Start following a listing, checked against the universe a research request is.

    Raises:
        ValidationError: If the name is blank, or the listing is outside the universe —
            every reason together, as the request form reports them.
        ConflictError: If the listing is already followed and not withdrawn.
    """
    name = company_name.strip()
    symbol = ticker.strip().upper()
    venue = exchange.strip().upper()
    if not name or not symbol or not venue:
        message = "Following a company needs its name, its ticker and its exchange."
        raise ValidationError(message, context={"field": "company_name"})
    exclusions = check_universe(ticker=symbol, exchange=venue, company_name=name)
    if exclusions:
        message = " ".join(exclusion.message for exclusion in exclusions)
        raise ValidationError(
            message,
            context={"field": "ticker", "rules": [row.rule.value for row in exclusions]},
        )
    existing = await session.scalar(
        select(WatchlistEntry).where(
            WatchlistEntry.user_id == user.id,
            WatchlistEntry.ticker == symbol,
            WatchlistEntry.exchange == venue,
            WatchlistEntry.withdrawn_at.is_(None),
        )
    )
    if existing is not None:
        message = f"{symbol}.{venue} is already followed."
        raise ConflictError(message, context={"entry_id": str(existing.id)})

    entry = WatchlistEntry(
        user_id=user.id, company_name=name, ticker=symbol, exchange=venue, why=why.strip()
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry, attribute_names=["followed_at", "commissions"])
    await _record(
        session,
        actor=user.email,
        event_type="watchlist.followed",
        entry_id=entry.id,
        payload={
            "entry_id": str(entry.id),
            "company_name": name,
            "ticker": symbol,
            "exchange": venue,
            "why": entry.why,
        },
    )
    _log.info("watchlist.followed", entry_id=str(entry.id), listing=entry.listing)
    return entry


async def stop_following(
    session: AsyncSession, *, user: User, entry: WatchlistEntry, reason: str
) -> WatchlistEntry:
    """Withdraw an entry with the reason. The row stays; what was followed is still a record.

    Raises:
        ValidationError: If the reason is blank.
        ConflictError: If the entry is not this person's, or is already withdrawn.
    """
    if entry.user_id != user.id:
        message = "A watchlist entry is withdrawn by the person following it."
        raise ConflictError(message, context={"entry_id": str(entry.id)})
    if entry.is_withdrawn:
        message = f"{entry.listing} is no longer followed."
        raise ConflictError(message, context={"entry_id": str(entry.id)})
    if not reason.strip():
        message = "Stopping following needs a reason, so the record says why."
        raise ValidationError(message, context={"field": "reason"})
    entry.withdrawn_at = datetime.now(UTC)
    entry.withdrawn_reason = reason.strip()
    await session.flush()
    await _record(
        session,
        actor=user.email,
        event_type="watchlist.withdrawn",
        entry_id=entry.id,
        payload={
            "entry_id": str(entry.id),
            "listing": entry.listing,
            "reason": entry.withdrawn_reason,
        },
    )
    return entry


async def entries_for(
    session: AsyncSession, *, user_id: uuid.UUID, include_withdrawn: bool = False
) -> list[WatchlistEntry]:
    statement = (
        select(WatchlistEntry)
        .options(selectinload(WatchlistEntry.commissions))
        .where(WatchlistEntry.user_id == user_id)
        .order_by(WatchlistEntry.followed_at, WatchlistEntry.id)
    )
    if not include_withdrawn:
        statement = statement.where(WatchlistEntry.withdrawn_at.is_(None))
    return list(await session.scalars(statement))


async def entry_of(
    session: AsyncSession, entry_id: uuid.UUID, *, user_id: uuid.UUID
) -> WatchlistEntry | None:
    found: WatchlistEntry | None = await session.scalar(
        select(WatchlistEntry)
        .options(selectinload(WatchlistEntry.commissions))
        .where(WatchlistEntry.id == entry_id, WatchlistEntry.user_id == user_id)
    )
    return found


# -- States ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntryState:
    """An entry and what its latest commission's run came to."""

    entry: WatchlistEntry
    commission: WatchlistCommission | None
    request: ResearchRequest | None
    job: Job | None
    report: Report | None

    @property
    def state(self) -> str:
        """``queued``, ``commissioned``, ``researched`` or ``stopped``.

        Read from the run rather than stored on the entry (ADR 0107 §1): a report makes
        it researched, a live run makes it commissioned, a dead run or a purged request
        puts it back in the queue with the reason shown.
        """
        if self.entry.is_withdrawn:
            return "withdrawn"
        if self.commission is None:
            return "queued"
        if self.report is not None:
            return "researched"
        if self.job is not None and self.job.status in LIVE_STATUSES:
            return "commissioned"
        return "stopped" if self.request is not None else "queued"

    @property
    def is_queued(self) -> bool:
        return self.state in {"queued", "stopped"}


async def state_of(session: AsyncSession, entry: WatchlistEntry) -> EntryState:
    latest = max(entry.commissions, key=lambda row: row.commissioned_at, default=None)
    request = job = report = None
    if latest is not None and latest.request_id is not None:
        request = await session.get(ResearchRequest, latest.request_id)
        if request is not None:
            job = await run_service.latest_run(session, request_id=request.id)
            report = await session.scalar(
                select(Report)
                .where(Report.request_id == request.id)
                .order_by(Report.created_at.desc())
                .limit(1)
            )
    return EntryState(entry=entry, commission=latest, request=request, job=job, report=report)


async def states_for(session: AsyncSession, *, user_id: uuid.UUID) -> list[EntryState]:
    return [await state_of(session, entry) for entry in await entries_for(session, user_id=user_id)]


async def queue_for(session: AsyncSession, *, user_id: uuid.UUID) -> list[EntryState]:
    """The entries with no live run and no report, in the order they were followed."""
    return [state for state in await states_for(session, user_id=user_id) if state.is_queued]


# -- Commissioning ---------------------------------------------------------------------------


async def commission(
    session: AsyncSession,
    *,
    settings: Settings,
    user: User,
    entry: WatchlistEntry,
    as_of: date | None = None,
    budget: StandingBudget | None = None,
) -> tuple[WatchlistCommission, Job]:
    """Turn an entry into a research run as at a date, inside the standing budget.

    The request is an ordinary research request with the form's own defaults and the
    per-run cap; the run it starts stops at gate one for the operator. The caller enqueues
    the job — the queue is the web process's or the terminal's business.

    Raises:
        ConflictError: If the entry is not this person's, is withdrawn, or already has a
            run alive.
        BudgetExceededError: If the run's cap would not fit in the standing budget's room
            this month. Named after the scope, like the two guards a run carries.
        ValidationError: If the request the entry describes is refused by the rules a
            hand-written one would be.
    """
    if entry.user_id != user.id:
        message = "A watchlist entry is commissioned by the person following it."
        raise ConflictError(message, context={"entry_id": str(entry.id)})
    if entry.is_withdrawn:
        message = f"{entry.listing} is no longer followed, so nothing is commissioned."
        raise ConflictError(message, context={"entry_id": str(entry.id)})
    state = await state_of(session, entry)
    if state.state == "commissioned":
        message = f"{entry.listing} already has a run alive; wait for it or cancel it first."
        raise ConflictError(message, context={"entry_id": str(entry.id)})

    room = (
        budget
        if budget is not None
        else await standing_budget(session, settings=settings, user_id=user.id)
    )
    cap = settings.per_run_budget_gbp
    if not room.affords(cap):
        message = (
            f"The standing budget has £{room.room_gbp:.2f} of room this month and a run may "
            f"spend up to £{cap:.2f}. Commission by hand from the requests page, raise "
            "the watchlist budget, or wait for next month."
        )
        raise BudgetExceededError(
            message,
            context={
                "scope": "watchlist",
                "room_gbp": str(room.room_gbp),
                "cap_gbp": str(cap),
                "budget_gbp": str(room.budget_gbp),
            },
        )

    dated = as_of or datetime.now(UTC).date()
    payload = ResearchRequestCreate(
        company_name=entry.company_name,
        ticker=entry.ticker,
        exchange=entry.exchange,
        as_of_date=dated,
        investment_horizon_months=DEFAULT_HORIZON_MONTHS,
        horizon_label=_HORIZON_LABEL,
        analysis_mode=DEFAULT_MODE,
        point_in_time=True,
        max_cost_gbp=cap,
    )
    request = await request_service.create_request(
        session, user=user, payload=payload, limits=request_service.limits_from(settings)
    )
    job = await run_service.start_run(session, request=request)
    # The relationship rather than the key, so a caller may read `row.entry` afterwards
    # without a lazy load, which an async session refuses.
    row = WatchlistCommission(
        entry=entry,
        request_id=request.id,
        as_of_date=dated,
        cap_gbp=cap,
        commissioned_by=user.email,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, attribute_names=["commissioned_at"])
    await session.refresh(entry, attribute_names=["commissions"])
    await _record(
        session,
        actor=user.email,
        event_type="watchlist.commissioned",
        entry_id=entry.id,
        payload={
            "entry_id": str(entry.id),
            "listing": entry.listing,
            "request_id": str(request.id),
            "job_id": str(job.id),
            "as_of_date": dated.isoformat(),
            "cap_gbp": str(cap),
            "room_gbp_before": str(room.room_gbp),
        },
    )
    _log.info(
        "watchlist.commissioned",
        entry_id=str(entry.id),
        listing=entry.listing,
        request_id=str(request.id),
        job_id=str(job.id),
        as_of=dated.isoformat(),
    )
    return row, job


@dataclass(frozen=True, slots=True)
class Drain:
    """What commissioning the next entries came to."""

    commissioned: tuple[tuple[WatchlistCommission, Job], ...]
    left: int
    """Entries still queued when the walk stopped."""
    stopped: str
    """Why the walk stopped short of the queue's end, or empty if it did not."""


async def commission_next(
    session: AsyncSession,
    *,
    settings: Settings,
    user: User,
    limit: int | None = None,
) -> Drain:
    """Walk the queue in order, starting runs while the standing budget affords them.

    Stops at the first entry the budget cannot afford, or the first the request rules
    refuse, and says which. A run started here is a run the operator has to approve at
    gate one like any other; nothing is approved by this walk.
    """
    queued = await queue_for(session, user_id=user.id)
    budget = await standing_budget(session, settings=settings, user_id=user.id)
    started: list[tuple[WatchlistCommission, Job]] = []
    stopped = ""
    for index, state in enumerate(queued):
        if limit is not None and len(started) >= limit:
            break
        try:
            row, job = await commission(
                session, settings=settings, user=user, entry=state.entry, budget=budget
            )
        except (BudgetExceededError, ValidationError, ConflictError) as refused:
            stopped = f"{state.entry.listing}: {refused}"
            return Drain(commissioned=tuple(started), left=len(queued) - index, stopped=stopped)
        started.append((row, job))
        # The room shrinks by the cap just reserved; re-read rather than subtract, so the
        # figure the next refusal quotes is the figure the ledger holds.
        budget = await standing_budget(session, settings=settings, user_id=user.id)
    return Drain(commissioned=tuple(started), left=len(queued) - len(started), stopped=stopped)


# -- The chain ------------------------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    entry_id: uuid.UUID,
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            job_id=None,
            subject_kind="watchlist_entry",
            subject_id=entry_id,
        )
    )
    await session.flush()
