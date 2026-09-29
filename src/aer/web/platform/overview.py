"""Platform: settings and state on one page (page specification §19, drawn as *Settings and
state*).

Six sheets, each answering one question about the tool rather than about the money: what it
has spent this month, what the operator allows the book, how the watching is set, where the
evidence comes from, whether a copy of it all exists, and whether the machinery is running.
Every line is read from the record or from the settings in force, and a line the record cannot
support says so. The drawing's two warnings — spend that bought nothing, and a backup nobody
has proved — are only worth drawing if they are true, so the first is summed from the runs
the schema refused and the second says plainly that nothing records a backup yet.

**Never the only route to anything** (§19's must-not). The editors keep their own pages — the
settings, the book's limits, the costs call by call — and each sheet links to its own. Nothing
here is a form, and nothing here shows a credential: a key is *there* or *not there*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from fastapi import APIRouter, Request
from redis.asyncio import Redis
from sqlalchemy import func, select
from starlette.responses import HTMLResponse, Response

from aer.api.deps import CurrentUser, DbSession, RedisClient, SettingsDep
from aer.config import Settings
from aer.core.dates import format_date
from aer.core.enums import WatchCadence
from aer.db.models import Artefact, Portfolio, User
from aer.services import configuration, price_alerts
from aer.services import portfolio as portfolio_service
from aer.services import risk as risk_service
from aer.services import spend as spend_service
from aer.services.daily_pass import DAILY_PASS_HOUR_UTC
from aer.services.overview import spend_since, start_of_month
from aer.services.watchlist import DEFAULT_PRICE_WINDOW_DAYS, window_words
from aer.web import figures
from aer.web.platform import health
from aer.web.platform.pages import limit_rows
from aer.web.templating import render

__all__ = ["Line", "router"]

router = APIRouter(include_in_schema=False)

HERE: Final = "/platform"


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a sheet: what it is, the sentence under it, and its value on the right."""

    key: str
    label: str
    detail: str
    value: str
    tone: str = ""
    """Empty for plain ink; ``warning`` for the lines the drawing puts in amber."""
    href: str = ""


# What each kind of work is called on the costs sheet, and its noun for one and for several.
# Keyed by the tool that opened the work order (`work_orders.tool`); a research run that
# refreshed a report is its own line, because a refresh is priced and chosen differently.
_ACTIVITY: Final[dict[str, tuple[str, str, str]]] = {
    "research": ("Full reports", "run", "runs"),
    "refresh": ("Refreshes", "refresh", "refreshes"),
    "ask": ("Questions", "question", "questions"),
    "review": ("Post-trade reviews", "review", "reviews"),
    "risk": ("Readings of the book", "reading", "readings"),
    "monitor": ("Thesis checks", "check", "checks"),
    "daily": ("The daily pass", "pass", "passes"),
}
_REMOVED: Final = ("Runs since deleted", "run", "runs")
_OTHER: Final = ("Other work", "run", "runs")

# What each credential opens, in the operator's words; the settings' own field names never
# reach the page (a name like that is an identifier, and §19 forbids anything copyable).
_SOURCES: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "companies_house_api_key",
        "Filings from the United Kingdom",
        "Companies House. Without a key, a London listing cannot be researched.",
        "companies-house",
    ),
    (
        "eodhd_api_key",
        "Prices",
        "End of day, from EODHD. Without a key, a valuation states no market price and "
        "no price alert is raised.",
        "prices",
    ),
    (
        "fred_api_key",
        "Rates and economic series",
        "FRED, for the risk-free rate a valuation is proposed.",
        "rates",
    ),
    (
        "anthropic_api_key",
        "The model",
        "Anthropic. Without a key, nothing can be researched, asked or reviewed.",
        "model",
    ),
)


@router.get(HERE, response_class=HTMLResponse, summary="Settings and state")
async def platform_page(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    redis: RedisClient,
    user: CurrentUser,
) -> Response:
    """The six sheets, each read from the record, each linking to where it is changed."""
    now = datetime.now(UTC)
    effective = await configuration.effective_settings(session, settings)
    book = await portfolio_service.default_book(session, user_id=user.id)
    page: Response = render(
        request,
        "platform/index.html",
        {
            "month": format_date(now, "%B %Y"),
            "costs": await _costs(session, ceiling=effective.monthly_budget_gbp, now=now),
            "limits": await _limits(session, book=book),
            "monitoring": await _monitoring(
                session, user=user, threshold=effective.price_move_threshold_pct, now=now
            ),
            "data": await _data(session, settings),
            "backups": _backups(),
            "health": await _health(session, redis, user=user),
        },
    )
    return page


# -- Costs ------------------------------------------------------------------------------------


async def _costs(session: Any, *, ceiling: Decimal, now: datetime) -> dict[str, Any]:
    """This month's spend against the ceiling, by the kind of work that spent it, and the
    replies that were paid for and could not be used."""
    since = start_of_month(now)
    spent = await spend_since(session, since=since)
    lines = [
        _activity_line(row)
        for row in await spend_service.spend_by_activity_since(session, since=since)
        if row.amount_gbp > 0
    ]
    discarded = await spend_service.discarded_since(session, since=since)
    return {
        "spent": Line(
            key="spent",
            label="Spent this month",
            detail=f"Against a {figures.pounds(ceiling)} ceiling, enforced in code.",
            value=figures.pounds(spent),
            tone="warning" if spent >= ceiling else "",
        ),
        "lines": lines,
        "discarded": Line(
            key="discarded",
            label="Discarded",
            detail=(
                "Replies paid for and not usable: the model answered in a shape the schema "
                "refused. Shown because nothing else sums it."
            ),
            value=figures.pounds(discarded),
            tone="warning" if discarded > 0 else "",
        ),
    }


def _activity_line(row: spend_service.ActivitySpend) -> Line:
    if row.tool is None:
        label, one, many = _REMOVED
    elif row.tool == "research" and row.refresh_kind == "refresh":
        label, one, many = _ACTIVITY["refresh"]
    else:
        label, one, many = _ACTIVITY.get(row.tool, _OTHER)
    return Line(
        key=row.tool or "removed",
        label=label,
        detail=f"{row.runs} {one if row.runs == 1 else many}" if row.runs else "",
        value=figures.pounds(row.amount_gbp),
    )


# -- The book's limits ------------------------------------------------------------------------


async def _limits(session: Any, *, book: Portfolio | None) -> list[Line]:
    """Each limit as the Book page states it, and the latest shock the risk page holds."""
    if book is None:
        return []
    lines = [
        Line(
            key=row.kind + (f":{row.sector}" if row.sector else ""),
            label=row.label,
            detail=row.today[0].upper() + row.today[1:],
            value=row.value,
            tone="warning" if row.tone == "warning" else "",
        )
        for row in await limit_rows(session, book=book)
    ]
    scenarios = await risk_service.scenarios_for(session, portfolio=book)
    lines.append(
        Line(
            key="shock",
            label="Your stated shock",
            detail=(
                f"The latest of {len(scenarios)} stated on the risk page, which reads what "
                "it would do to the book."
                if len(scenarios) > 1
                else "Stated on the risk page, which reads what it would do to the book."
                if scenarios
                else "None stated. The risk page states one in a press."
            ),
            value=scenarios[-1].name if scenarios else "none",
            href="/risk",
        )
    )
    return lines


# -- Monitoring -------------------------------------------------------------------------------


async def _monitoring(session: Any, *, user: User, threshold: Decimal, now: datetime) -> list[Line]:
    raised, dismissed = await price_alerts.moves_in_last_six_months(
        session, user_id=user.id, now=now
    )
    window = window_words(DEFAULT_PRICE_WINDOW_DAYS)
    daily = await health.daily_pass_words(session, user_id=user.id)
    return [
        Line(
            key="threshold",
            label="Default price threshold",
            detail="Each company you follow uses it unless you gave it one of its own.",
            value=f"±{threshold.normalize():f}% {window}",
            href="/settings#form-price_move_threshold_pct",
        ),
        Line(
            key="cadence",
            label="Default cadence",
            detail=(
                "How often a followed company's premises are due to be read; each can have its own."
            ),
            value=WatchCadence.MONTHLY.value.capitalize(),
        ),
        Line(
            key="checks",
            label="Checks run",
            detail=daily.detail,
            value=f"{DAILY_PASS_HOUR_UTC:02d}:00 UTC, after the New York close",
            tone="warning" if daily.label == "Overdue" else "",
        ),
        Line(
            key="dismissed",
            label="Alerts dismissed",
            detail=(
                "Too many dismissals mean the threshold is wrong, not the market."
                if dismissed
                else "A price alert you dismiss is counted here."
            ),
            value=(
                f"{dismissed} of {raised} in six months" if raised else "none raised in six months"
            ),
        ),
    ]


# -- Data and evidence ------------------------------------------------------------------------


async def _data(session: Any, settings: Settings) -> list[Line]:
    present = configuration.secret_presence(settings)
    lines = [
        Line(
            key="edgar",
            label="Filings from the United States",
            detail="SEC EDGAR, asked in the name you gave, as its fair-access policy requires.",
            value="Ready",
        )
    ]
    lines.extend(
        Line(
            key=key,
            label=label,
            detail=detail,
            value="Ready" if present.get(name) else "No key",
            tone="" if present.get(name) else "warning",
        )
        for name, label, detail, key in _SOURCES
    )
    held = int(await session.scalar(select(func.count()).select_from(Artefact)) or 0)
    lines.append(
        Line(
            key="documents",
            label="Documents held",
            detail="Each stored under the hash of its own bytes, so a citation can be re-read.",
            value=f"{held:,} document{'' if held == 1 else 's'}",
        )
    )
    return lines


# -- Backups ----------------------------------------------------------------------------------


def _backups() -> list[Line]:
    """What the platform knows about its own copies, which today is nothing.

    A backup is taken and restored from the terminal (`aer.services.backup`) into a directory
    the operator chooses, and neither act writes anything this page can read. So the page
    says that, in amber, rather than a reassuring line it cannot support.
    """
    return [
        Line(
            key="last-backup",
            label="Last backup",
            detail=(
                "Backups are taken from the terminal, and none is recorded where this page "
                "can see it."
            ),
            value="Not recorded",
            tone="warning",
        ),
        Line(
            key="last-proved",
            label="Last proved restorable",
            detail="A backup nobody has restored is a hope, not a backup.",
            value="Never recorded",
            tone="warning",
        ),
    ]


# -- Health -----------------------------------------------------------------------------------


async def _health(
    session: Any, redis: Redis, *, user: User
) -> list[tuple[str, str, health.Reading]]:
    """Each part of the machinery with its name, in the order a stalled run is diagnosed."""
    return [
        ("worker", "The worker", await health.worker_words(redis)),
        ("daily-pass", "The daily pass", await health.daily_pass_words(session, user_id=user.id)),
        ("queue", "The queue", await health.queue_words(session, user_id=user.id)),
        ("last-run", "The last run", await health.last_run_words(session, user_id=user.id)),
        ("database", "The database", await health.database_words(session)),
    ]
