"""The main menu: the front door, and the one page that renders when nothing else can.

It does two jobs, in that order. It is the **launcher** — every tool this platform has or
is going to have, from `web/tools/registry.py`, so the shape of the product is visible on
arrival rather than discovered. And it is the **work list** — counts from the registered
badges and a feed from the registered attention providers, so there is a reason to come
back rather than only a reason to arrive.

**The launcher needs no database and the work list does.** That split is the whole design
of this handler. The front page of a local tool is the page you open when something is not
working, and the most likely reason you are looking at it is that Postgres is not running:
a blank 500 there tells you nothing. So the tools always render, the counts are attempted,
and a failure becomes a notice saying which failure it was.

Every other page needs the database and fails loudly without it. Degrading a page that
shows data would mean showing an empty list as though it were the truth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_308_PERMANENT_REDIRECT

from aer.api.deps import DbSession, RedisClient, get_current_user
from aer.core.dates import format_date
from aer.db.schema_check import schema_drift
from aer.errors import AerError
from aer.services.overview import spend_since, start_of_month
from aer.version import build_identity
from aer.web import figures
from aer.web.overview.attention import Attention, Severity, items_for
from aer.web.shell.badges import Badge, cached_counts_for
from aer.web.templating import render
from aer.web.tools.registry import installed_tools

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.overview")

_NOT_REACHABLE: Final = (
    "The database is not reachable. Start it with `just up`, then reload this page. "
    "/readyz reports which dependencies are answering."
)

# The order the feed is grouped into on the page. Held here rather than derived from the
# enum's declaration order, because a reordering of the vocabulary should not silently
# reorder a screen.
SEVERITY_ORDER: Final[tuple[Severity, ...]] = (
    Severity.BLOCKED,
    Severity.BROKEN,
    Severity.IDLE,
)


@router.get("/", response_class=HTMLResponse, summary="Main menu")
async def main_menu(request: Request, session: DbSession, redis: RedisClient) -> Response:
    """Every tool, then everything waiting for you in whichever of them it is waiting.

    The clock is read here rather than in the service, so `services/overview.py` stays free
    of clock reads and a test can ask for a specific month without moving the machine's.
    """
    now = datetime.now(UTC)
    since = start_of_month(now)

    problem: str | None = None
    badges: tuple[Badge, ...] = ()
    attention: tuple[Attention, ...] = ()
    spend: str | None = None

    try:
        # Before the queries, not after. A schema two migrations behind can leave the
        # launcher rendering perfectly while every tool behind it returns an opaque 500;
        # checking eagerly is what makes this the page that tells you.
        drift = await schema_drift(session)
        if not drift.is_clean:
            problem = drift.as_message()

        user = await get_current_user(session)
        badges = await cached_counts_for(redis, session, user_id=user.id)
        attention = await items_for(session, user_id=user.id)
        spend = _pounds(await spend_since(session, since=since))
    except AerError as exc:
        # A configuration problem the operator can act on, such as no user having been
        # seeded. Its message says how to fix it, so show it.
        problem = exc.message
    except (SQLAlchemyError, OSError):
        # `OSError` as well: a refused connection surfaces as a bare
        # `ConnectionRefusedError`, because asyncpg raises it while *creating* the
        # connection, before there is a DBAPI error for SQLAlchemy to wrap. Catching only
        # `SQLAlchemyError` would miss the single most common failure there is.
        problem = await _database_problem(session)

    page: Response = render(
        request,
        "index.html",
        {
            "tools": installed_tools(),
            "build": build_identity(),
            "problem": problem,
            "badges": badges,
            "attention": attention,
            "spend": spend,
            # Rendered here rather than in the template: `%-d` does not exist outside
            # glibc, and `format_date` is the one place that knows it.
            "since": f"Since {format_date(since, '%-d %B')}",
            "severity_order": SEVERITY_ORDER,
            "blocked": Severity.BLOCKED,
            "broken": Severity.BROKEN,
            "idle": Severity.IDLE,
        },
    )
    return page


@router.get("/overview", summary="The main menu, at its former address")
async def overview_moved() -> Response:
    """Permanent, because the page did not change — only where it lives.

    A redirect rather than a deletion: this URL was in the navigation, in a screenshot and
    in whatever the operator bookmarked, and 404ing it would be a lie about a page that is
    right there. 308 rather than 302 so a browser stops asking.
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

    Kept as a name because this module and its tests both read it; **the implementation moved
    to `web/figures.py`** so the console and the seven gates render the same number the same
    way. It was private here, and a private renderer is how a second one gets written.

    Not through `render/display.money`: that door exists for a company's figures in a report's
    own currency, resolved against a `HouseStyle` (ADR 0056), and this screen has no report and
    no house style.
    """
    return figures.pounds(amount)
