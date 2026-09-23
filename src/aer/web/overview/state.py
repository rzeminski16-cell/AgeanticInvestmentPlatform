"""Band 3 of Today — *the state of things* — four quiet figures with no actions.

Page specification §1.3: book value and the day's move; companies watched and when the
next check runs; spent this month against the ceiling; reports held and how many are
current. Quiet on purpose — no colour beyond ink, no chart, and profit is never the
headline — and every figure read from the record on the way here. The book is walked
twice, at the last close and the close before it, because the day's move is a change
between two valuations and a change is a calculation (:func:`aer.calc.changes.relative_change`),
never a figure a template would subtract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.db.models import Report, User, WorkOrder
from aer.errors import AerError
from aer.services import calculations as calculation_service
from aer.services import company_record as record_service
from aer.services import daily_pass
from aer.services import portfolio as portfolio_service
from aer.services.overview import spend_since, start_of_month
from aer.web import figures
from aer.web.portfolio.dashboard import days_move
from aer.web.portfolio.pages import percent, pounds

__all__ = ["StateFigure", "state_for"]

_log = structlog.get_logger("aer.web.overview.state")

# What a figure says where there is none. The same dash the book uses.
NO_FIGURE = "—"


@dataclass(frozen=True, slots=True)
class StateFigure:
    """One of the four: a label, a value already rendered, one line beneath, and where
    the working is. ``href`` is where the line leads when it names something the operator
    can change — the platform, for a check nobody scheduled."""

    key: str
    label: str
    value: str
    note: str
    href: str = ""


async def state_for(
    session: AsyncSession, *, user: User, settings: Settings, now: datetime | None = None
) -> tuple[StateFigure, ...]:
    """The four figures, in the specification's order."""
    moment = now or datetime.now(UTC)
    return (
        await _book(session, user=user),
        await _watched(session, user=user, now=moment),
        await _spent(session, settings=settings, now=moment),
        await _reports(session, user=user),
    )


async def _book(session: AsyncSession, *, user: User) -> StateFigure:
    """Book value at the last close, and the day's move against the close before it."""
    book = await portfolio_service.default_book(session, user_id=user.id)
    if book is None:
        return StateFigure(
            key="book", label="Book value", value=NO_FIGURE, note="No book yet.", href="/portfolio"
        )
    try:
        as_of = await portfolio_service.latest_close(session, portfolio=book)
        context = calculation_service.new_context()
        today = await portfolio_service.book_as_at(session, context, portfolio=book, as_of=as_of)
    except AerError as problem:
        _log.info("state.book_unreadable", reason=str(problem))
        return StateFigure(
            key="book",
            label="Book value",
            value=NO_FIGURE,
            note="The book could not be valued.",
            href="/portfolio",
        )
    if not today.is_complete or today.net_assets is None:
        return StateFigure(
            key="book",
            label="Book value",
            value=NO_FIGURE,
            note=f"Withheld: a position could not be valued as at {as_of:%d %B %Y}.",
            href="/portfolio",
        )
    change = await days_move(session, book=book, as_of=as_of, latest=today.net_assets.value)
    move = percent(change) if change is not None else ""
    return StateFigure(
        key="book",
        label="Book value",
        value=pounds(today.net_assets.value, book.base_currency),
        note=(
            f"{move} on the day, at the close of {as_of:%d %B %Y}."
            if move
            else f"At the close of {as_of:%d %B %Y}; no prior close to move from."
        ),
        href="/portfolio",
    )


async def _watched(session: AsyncSession, *, user: User, now: datetime) -> StateFigure:
    """Companies in the record, and when the daily pass next reads them."""
    records = await record_service.records_for(session, user=user, now=now)
    last = await daily_pass.last_pass(session, user_id=user.id)
    finished = last.finished_at if last is not None else None
    state = daily_pass.pass_state(finished, now=now)
    count = len(records)
    label_count = f"{count} compan{'y' if count == 1 else 'ies'}"
    if finished is None:
        return StateFigure(
            key="watched",
            label="Companies watched",
            value=label_count,
            note="No check scheduled — the daily pass has never run.",
            href="/settings",
        )
    due = finished + daily_pass.CADENCE
    note = (
        f"Next check due {due:%d %B %Y at %H:%M} UTC."
        if not state.is_missed
        else f"Next check overdue: due {due:%d %B %Y at %H:%M} UTC and not yet run."
    )
    return StateFigure(
        key="watched", label="Companies watched", value=label_count, note=note, href="/companies"
    )


async def _spent(session: AsyncSession, *, settings: Settings, now: datetime) -> StateFigure:
    """This month's model spend against the monthly ceiling, said as money."""
    since = start_of_month(now)
    spent = await spend_since(session, since=since)
    ceiling = settings.monthly_budget_gbp
    return StateFigure(
        key="spent",
        label="Spent this month",
        value=figures.pounds(spent),
        note=f"Of a {figures.pounds(ceiling)} ceiling, since {since:%d %B}.",
        href="/costs",
    )


async def _reports(session: AsyncSession, *, user: User) -> StateFigure:
    """Approved reports the account holds, and how many are current (ADR 0116)."""
    held = list(
        await session.scalars(
            select(Report)
            .join(WorkOrder, WorkOrder.id == Report.request_id)
            .where(WorkOrder.user_id == user.id, Report.immutable.is_(True))
        )
    )
    current = sum(1 for report in held if report.is_current)
    count = len(held)
    return StateFigure(
        key="reports",
        label="Reports held",
        value=f"{count} report{'s' if count != 1 else ''}",
        note=(
            f"{current} current; the rest superseded or withdrawn."
            if count
            else "None approved yet."
        ),
        href="/reports",
    )
