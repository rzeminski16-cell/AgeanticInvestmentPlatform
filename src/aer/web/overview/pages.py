"""Today: the hub every visit starts on (page specification §1, corrected 28 September 2026).

**A hub leads with state; an inbox leads with events** (the page guide's *The hub*). So the
page opens on the verdict — the book in one sentence, its figures and the conviction strip —
then what the platform did while the operator was away beside the small numbered queue of what
needs them, a way to start something, and the suggestions the record has earned, last. The
launcher that used to lead this page is gone: the menu is the launcher (ADR 0112, amended).

**The one page that must render when nothing else can.** The front page of a local tool is the
page you open when something is not working, and the most likely reason is that Postgres is
not running: a blank 500 there tells you nothing. So everything is attempted, and a failure
becomes a notice saying which failure it was — not reachable, or reachable and behind on its
migrations.

**Opening the page is recorded** (revision 0092), because *Since {day}* starts from the
operator's last look. It is the one write this page makes, and it is committed only once the
page has rendered: a render that failed must not move the window the next one briefs from.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_308_PERMANENT_REDIRECT

from aer.api.deps import DbSession, SettingsDep, get_current_user
from aer.core.dates import format_date
from aer.core.visits import Visit
from aer.db.schema_check import schema_drift
from aer.errors import AerError
from aer.services import visits as visit_service
from aer.services.overview import has_ever_commissioned
from aer.version import build_identity
from aer.web import figures
from aer.web.overview.attention import Attention, items_for
from aer.web.overview.briefing import Briefing, briefing_for
from aer.web.overview.hub import BookVerdict, book_verdict
from aer.web.overview.suggestions import SHOWN, Suggestion, suggestions_for
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.overview")

_NOT_REACHABLE: Final = (
    "The database is not reachable. Start it with `just up`, then reload this page. "
    "/readyz reports which dependencies are answering."
)

# The first-run card's words, and the ones a returning operator sees instead (§1's states).
_FIRST_RUN: Final = {
    "heading": "Research your first company",
    "text": (
        "Start with two things: connect a model provider, then commission your first "
        "report. Writing a request costs nothing and starts nothing — the run stops for your "
        "approval before it spends anything."
    ),
    "research": "Commission research",
    "ask": False,
    "settings": True,
}
_RETURNING: Final = {
    "heading": "Start something",
    "text": "Research a company, or ask a question about one you already hold.",
    "research": "Research a company",
    "ask": True,
    "settings": False,
}


@router.get("/", response_class=HTMLResponse, summary="Today")
async def today(request: Request, session: DbSession, settings: SettingsDep) -> Response:
    """The hub: the verdict, what happened since the last look, what needs you, what next.

    The clock is read here rather than in the services, so each of them stays free of clock
    reads and a test can hold the page at the hour it wants.
    """
    now = datetime.now(UTC)
    problem: str | None = None
    name = ""
    visit: Visit | None = None
    verdict: BookVerdict | None = None
    briefing = Briefing(lines=(), more=0)
    attention: tuple[Attention, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()
    # Whether this operator has ever written a request. `True` until proven otherwise, so a
    # database that could not be read shows the ordinary page rather than greeting a
    # long-standing operator as a newcomer — the one way this flag can be actively insulting.
    commissioned = True

    try:
        # Before the queries, not after. A schema two migrations behind can leave half the
        # page rendering while the rest returns an opaque 500; checking eagerly is what makes
        # this the page that tells you.
        drift = await schema_drift(session)
        if not drift.is_clean:
            problem = drift.as_message()
        user = await get_current_user(session)
        name = user.display_name
        visit = await visit_service.open_today(session, user=user, now=now)
        attention = await items_for(session, user_id=user.id)
        commissioned = await has_ever_commissioned(session, user_id=user.id)
        verdict = await book_verdict(session, user=user, settings=settings, now=now)
        briefing = await briefing_for(session, user=user, since=visit.since, now=now)
        suggestions = await suggestions_for(session, user=user, now=now)
    except AerError as exc:
        # A configuration problem the operator can act on, such as no user having been
        # seeded. Its message says how to fix it, so show it.
        problem = exc.message
    except (SQLAlchemyError, OSError):
        # `OSError` as well: a refused connection surfaces as a bare `ConnectionRefusedError`,
        # because asyncpg raises it while *creating* the connection, before there is a DBAPI
        # error for SQLAlchemy to wrap.
        problem = await _database_problem(session)

    first_run = verdict is not None and not commissioned and not attention
    if first_run and verdict is not None:
        # §1's state *nothing at all*: the page says so, and the start box offers the first
        # request. Figures that are dashes stay, because what the platform will read is part
        # of what it says.
        verdict = replace(
            verdict,
            headline="Nothing is recorded yet.",
            detail=(
                "Commission research on a company and its report, figures and sources start the "
                "record. Record what you hold, and this page says whether your reasons still "
                "stand."
            ),
        )
    page: Response = render(
        request,
        "index.html",
        {
            "build": build_identity(),
            "problem": problem,
            "greeting": _greeting(name, visit),
            "date_line": _date_line(now, visit),
            "since_heading": _since_heading(now, visit),
            "since_words": _since_words(now, visit),
            "verdict": verdict,
            "briefing": briefing,
            "attention": attention,
            "first_run": first_run,
            "start": _FIRST_RUN if first_run else _RETURNING,
            # §1.2: the four with the oldest condition, and how many more qualify. Absent on
            # first run, where the one card is the start box's.
            "suggestions": () if first_run else suggestions[:SHOWN],
            "more_suggestions": 0 if first_run else max(len(suggestions) - SHOWN, 0),
        },
    )
    if problem is None:
        await session.commit()
    return page


def _greeting(name: str, visit: Visit | None) -> str:
    """A greeting with no time of day in it: the platform does not know the operator's."""
    if not name:
        return "Today"
    if visit is not None and visit.is_first:
        return f"Welcome, {name}"
    return f"Welcome back, {name}"


def _date_line(now: datetime, visit: Visit | None) -> str:
    today = format_date(now.date(), "%A %-d %B %Y")
    if visit is None:
        return today
    if visit.last_looked is None:
        return f"{today} · your first look, so the briefing covers the last seven days"
    return f"{today} · you last looked {_when(visit.last_looked, now)}"


def _since_heading(now: datetime, visit: Visit | None) -> str:
    if visit is None or visit.last_looked is None:
        return "The last seven days"
    return f"Since {_day(visit.last_looked, now)}"


def _since_words(now: datetime, visit: Visit | None) -> str:
    """How the empty briefing ends its sentence: *Nothing happened since Thursday.*"""
    if visit is None or visit.last_looked is None:
        return "in the last seven days"
    return f"since {_day(visit.last_looked, now)}"


def _day(moment: datetime, now: datetime) -> str:
    """A look named as a day: *this morning*, *yesterday*, *Thursday*, *3 September*."""
    days = (now.date() - moment.date()).days
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:  # noqa: PLR2004 -- a week is where a weekday stops naming one day
        return format_date(moment.date(), "%A")
    return format_date(moment.date(), "%-d %B")


def _when(moment: datetime, now: datetime) -> str:
    day = _day(moment, now)
    return day if day in {"earlier today", "yesterday"} else f"on {day}"


@router.get("/overview", summary="Today, at its former address")
async def overview_moved() -> Response:
    """Permanent, because the page did not change — only where it lives.

    A redirect rather than a deletion: this URL was in the navigation, in a screenshot and in
    whatever the operator bookmarked, and 404ing it would be a lie about a page that is right
    there. 308 rather than 302 so a browser stops asking.
    """
    return RedirectResponse("/", status_code=HTTP_308_PERMANENT_REDIRECT)


async def _database_problem(session: DbSession) -> str:
    """Say *which* database problem this is, now that there are two worth telling apart.

    "Not reachable" and "reachable but two migrations behind" have completely different
    fixes, and reporting the second as the first sends the operator to restart a container
    that was working perfectly. The failed statement has poisoned the transaction, so the
    rollback is not optional — without it the drift query fails too and every problem looks
    like an outage again.
    """
    try:
        await session.rollback()
        drift = await schema_drift(session)
    except (SQLAlchemyError, OSError):
        return _NOT_REACHABLE
    return _NOT_REACHABLE if drift.is_clean else drift.as_message()


def _pounds(amount: Decimal) -> str:
    """Operator spend, in pounds.

    Kept as a name because this module's tests read it; **the implementation lives in
    `web/figures.py`** so every page renders the same number the same way.
    """
    return figures.pounds(amount)
