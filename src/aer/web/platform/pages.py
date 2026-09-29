"""Platform, Book: the limits the operator states on their book (§19, ADR 0136).

Each limit is blank until the operator states it, and the page offers no default, no
suggestion and no example value — a placeholder that read *10* would be the platform stating a
policy by other means. Beside each limit in force is the figure it caps as the book stands,
compared in code (`services.limits.standings`), so the operator sets a limit knowing what it
means today. A changed limit supersedes the old one and a withdrawn one keeps its reason; the
history of both sits at the foot of the page, because what the operator allowed themselves
when they made a decision is what a later review of it reads.

The stated shocks are the risk page's; this page lists them and links there rather than
keeping a second form for the same statement.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.core.dates import format_date
from aer.core.enums import LimitKind
from aer.db.models import BookLimit, Portfolio, User
from aer.errors import AerError
from aer.render import display
from aer.services import limits as limit_service
from aer.services import portfolio as portfolio_service
from aer.services import risk as risk_service
from aer.services.calculations import new_context
from aer.services.performance import exposure_as_at
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, form_token, set_csrf_cookie
from aer.web.pages import problem_page
from aer.web.templating import render

__all__ = ["LimitRow", "limit_rows", "router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.platform")

_DAY = "%-d %B %Y"
_HERE = "/platform/book"


@dataclass(frozen=True, slots=True)
class LimitRow:
    """One kind of limit as the page shows it: in force or not stated, and what it caps."""

    kind: str
    label: str
    value: str
    today: str
    tone: str
    limit_id: uuid.UUID | None
    sector: str = ""


def _share(value: Decimal | None) -> str:
    return display.percentage(value, in_table=True) if value is not None else ""


def _today(standing: limit_service.Standing) -> tuple[str, str]:
    """What the limit means as the book stands, and the tone to say it in."""
    limit = standing.limit
    if limit.kind is LimitKind.SINGLE_POSITION:
        if standing.over:
            names = ", ".join(standing.over)
            count = len(standing.over)
            noun = "position is" if count == 1 else "positions are"
            return f"{count} {noun} over it today: {names}", "warning"
        if standing.share is None:
            return "nothing priced is held today", "muted"
        return f"the largest position is {_share(standing.share)} today", (
            "warning" if standing.is_near else "muted"
        )
    if standing.share is None:
        return "no figure for it today", "muted"
    if limit.kind is LimitKind.SECTOR and standing.share == 0:
        return "nothing held in it today", "muted"
    tone = "warning" if standing.is_over or standing.is_near else "muted"
    words = f"{_share(standing.share)} today"
    if standing.is_over:
        words += ", over it"
    elif standing.is_near:
        words += ", within two points of it"
    return words, tone


def _rows(
    in_force: limit_service.Limits, standings: tuple[limit_service.Standing, ...]
) -> list[LimitRow]:
    """The two whole-book kinds always, stated or not, then each sector limit in force."""
    by_id = {row.limit.id: row for row in standings}
    rows: list[LimitRow] = []
    for kind, limit in (
        (LimitKind.SINGLE_POSITION, in_force.single_position),
        (LimitKind.FIVE_LARGEST, in_force.five_largest),
    ):
        if limit is None:
            rows.append(
                LimitRow(
                    kind=kind.value,
                    label=limit_service.KIND_WORDS[kind],
                    value="not set",
                    today="You have stated none, so none is drawn on any page.",
                    tone="muted",
                    limit_id=None,
                )
            )
            continue
        today, tone = _today(by_id[limit.id])
        rows.append(
            LimitRow(
                kind=kind.value,
                label=limit_service.KIND_WORDS[kind],
                value=limit_service.limit_percent(limit),
                today=today,
                tone=tone,
                limit_id=limit.id,
            )
        )
    for name in sorted(in_force.sectors):
        limit = in_force.sectors[name]
        today, tone = _today(by_id[limit.id])
        rows.append(
            LimitRow(
                kind=LimitKind.SECTOR.value,
                label=f"{limit_service.KIND_WORDS[LimitKind.SECTOR]} — {name}",
                value=limit_service.limit_percent(limit),
                today=today,
                tone=tone,
                limit_id=limit.id,
                sector=name,
            )
        )
    return rows


def _history_row(row: BookLimit, *, successor: BookLimit | None) -> dict[str, str]:
    what = limit_service.KIND_WORDS[row.kind] + (f" — {row.sector}" if row.sector else "")
    if row.withdrawn_at is not None:
        ended = f"withdrawn on {format_date(row.withdrawn_at, _DAY)}: {row.withdrawn_reason}"
    elif successor is not None:
        ended = (
            f"replaced on {format_date(successor.stated_at, _DAY)} by "
            f"{limit_service.limit_percent(successor)}"
        )
    else:
        ended = "in force"
    return {
        "what": what,
        "value": limit_service.limit_percent(row),
        "stated": format_date(row.stated_at, _DAY),
        "ended": ended,
    }


@dataclass(frozen=True, slots=True)
class _Standing:
    in_force: limit_service.Limits
    as_of: Any
    exposure: Any
    standings: tuple[limit_service.Standing, ...]


async def _standing(session: Any, *, book: Portfolio) -> _Standing:
    """The limits in force and what each caps as the book stood at its latest close — the
    figures the risk page strikes, compared in code, in a ledger nothing saves."""
    in_force = await limit_service.limits_of(session, portfolio=book)
    as_of = await portfolio_service.latest_close(session, portfolio=book)
    ledger = new_context()
    view = await portfolio_service.book_as_at(session, ledger, portfolio=book, as_of=as_of)
    exposure = await exposure_as_at(session, ledger, portfolio=book, as_of=as_of, view=view)
    return _Standing(
        in_force=in_force,
        as_of=as_of,
        exposure=exposure,
        standings=limit_service.standings(in_force, book=view, exposure=exposure),
    )


async def limit_rows(session: Any, *, book: Portfolio) -> list[LimitRow]:
    """The limits as this page lists them, for a page that shows them without the forms."""
    read = await _standing(session, book=book)
    return _rows(read.in_force, read.standings)


async def _context(
    session: Any, *, book: Portfolio, problem: str = "", typed: dict[str, str]
) -> dict[str, Any]:
    read = await _standing(session, book=book)
    sector_band = next((band for band in read.exposure.bands if band.kind == "sector"), None)
    history = await limit_service.history_of(session, portfolio=book)
    successors = {row.supersedes_id: row for row in history if row.supersedes_id is not None}
    in_force_ids = {row.id for row in read.in_force.all()}
    return {
        "book": book,
        "as_of": format_date(read.as_of, _DAY),
        "rows": _rows(read.in_force, read.standings),
        "sectors": [
            {"value": row.label, "label": f"{row.label} ({_share(row.share.value)} today)"}
            for row in (sector_band.slices if sector_band is not None else ())
            if row.known
        ],
        "history": [
            _history_row(row, successor=successors.get(row.id))
            for row in history
            if row.id not in in_force_ids
        ],
        "scenarios": [
            {"name": scenario.name, "stated": format_date(scenario.created_at, _DAY)}
            for scenario in await risk_service.scenarios_for(session, portfolio=book)
        ],
        "problem": problem,
        "typed": typed,
    }


@router.get(_HERE, response_class=HTMLResponse, summary="Your book's limits")
async def book_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    book = await portfolio_service.default_book(session, user_id=user.id)
    token = form_token(request, settings)
    context: dict[str, Any] = (
        await _context(session, book=book, typed={}) if book is not None else {"book": None}
    )
    response: Response = render(
        request,
        "platform/book.html",
        {**context, "csrf_field": CSRF_FIELD_NAME, "csrf_token": token},
    )
    set_csrf_cookie(response, token)
    return response


@router.post(f"{_HERE}/limits", summary="State a limit on the book")
async def state_limit(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """A limit the operator states, superseding the one of its kind in force."""
    book = await portfolio_service.default_book(session, user_id=user.id)
    if book is None:
        return problem_page(request, "There is no book to limit yet.", status=HTTP_404_NOT_FOUND)
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "No limit was stated.")
    try:
        kind = LimitKind(submitted.get("kind", ""))
        await limit_service.state_limit(
            session,
            portfolio=book,
            actor=user,
            kind=kind,
            fraction=limit_service.parse_percent(submitted.get("percent", "")),
            sector=submitted.get("sector") if kind is LimitKind.SECTOR else None,
        )
        await session.commit()
    except ValueError:
        await session.rollback()
        return problem_page(request, "That is not a kind of limit.", status=400, back=_HERE)
    except AerError as refused:
        await session.rollback()
        return await _refusal(
            request, session, settings, user, problem=str(refused), submitted=submitted
        )
    return RedirectResponse(_HERE, status_code=HTTP_303_SEE_OTHER)


@router.post(f"{_HERE}/limits/{{limit_id}}/withdraw", summary="Withdraw a limit")
async def withdraw_limit(
    limit_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The operator stops holding themselves to a limit, and says why. The row stays."""
    book = await portfolio_service.default_book(session, user_id=user.id)
    limit = await session.get(BookLimit, limit_id)
    if book is None or limit is None or limit.user_id != user.id:
        return problem_page(request, "No such limit.", status=HTTP_404_NOT_FOUND, back=_HERE)
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "No limit was withdrawn.")
    try:
        await limit_service.withdraw_limit(
            session,
            portfolio=book,
            limit=limit,
            actor=user,
            reason=submitted.get("reason", ""),
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return await _refusal(
            request, session, settings, user, problem=str(refused), submitted=submitted
        )
    return RedirectResponse(_HERE, status_code=HTTP_303_SEE_OTHER)


async def _refusal(
    request: Request,
    session: Any,
    settings: Any,
    user: User,
    *,
    problem: str,
    submitted: dict[str, str],
) -> Response:
    """The page again, with the refusal above the forms and what was typed still in them."""
    # A rollback expires every row the session holds, the user among them.
    await session.refresh(user)
    book = await portfolio_service.default_book(session, user_id=user.id)
    assert book is not None
    token = form_token(request, settings)
    context = await _context(session, book=book, problem=problem, typed=submitted)
    response: Response = render(
        request,
        "platform/book.html",
        {**context, "csrf_field": CSRF_FIELD_NAME, "csrf_token": token},
        status_code=422,
    )
    set_csrf_cookie(response, token)
    return response


async def _submitted(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}


def _refused(request: Request, consequence: str) -> Response:
    return problem_page(
        request,
        f"This form's security token was missing or had expired. {consequence}",
        status=HTTP_403_FORBIDDEN,
        back=_HERE,
    )
