"""What the book is exposed to and what a stated scenario would do to it, commented on
rather than scored.

One screen and three forms. The screen is the book's risk as at a date: what it would have
done with today's weights held fixed — volatility, drawdown, the tail — beside its
exposure and concentration, each measured holding's share of the risk, every stated
scenario's profit and loss, and the analyst's reading of all of it. The forms state a
scenario, withdraw one, and run the reading.

**Every figure on this page is a recorded calculation** (ADRs 0080, 0106), rendered here
from the same strings the analyst read, so the numeral a commentary is allowed to name is
the numeral the page shows. **Every figure is ex-ante**, and the page says so in words
beside the numbers rather than in a footnote.

**Nothing here sizes, limits, ranks or scores.** There is no control for any of it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import func, select
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, ProviderDep, RouterDep, SettingsDep, StoreDep
from aer.core.enums import ShockKind
from aer.db.models import Portfolio, PriceBar, RiskScenario, Transaction
from aer.errors import AerError
from aer.services import risk as risk_service
from aer.services.calculations import new_context
from aer.services.portfolio import Figure
from aer.web import figures, vocabulary
from aer.web import verdict as verdicts
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.risk")

# How many shocks the form offers at once. A scenario with more is stated as two.
SHOCK_ROWS: Final = 3

_KIND_CHOICES: Final = [
    {"value": member.value, "label": vocabulary.SHOCK_KINDS[member].label} for member in ShockKind
]


@dataclass(frozen=True, slots=True)
class FigureRow:
    label: str
    value: str
    note: str = ""
    href: str = ""


def _figure(label: str, figure: Figure | None, *, rendered: str, note: str = "") -> FigureRow:
    return FigureRow(
        label=label,
        value=rendered,
        note=note,
        href=f"/calculations/{figure.record.id}" if figure is not None else "",
    )


# -- The screen -----------------------------------------------------------------------------


@router.get("/risk", response_class=HTMLResponse, summary="Risk")
async def risk_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    book = await _book_of(session, user_id=user.id)
    token = new_csrf_token(settings)
    if book is None:
        empty: Response = render(
            request,
            "risk/index.html",
            {"no_book": True, "csrf_field": CSRF_FIELD_NAME, "csrf_token": token},
        )
        set_csrf_cookie(empty, token)
        return empty

    as_of = _requested_date(request) or await _latest_close(session, portfolio=book)
    context = new_context()
    try:
        view = await risk_service.risk_as_at(session, context, portfolio=book, as_of=as_of)
    except AerError as problem:
        _log.warning("risk.failed", portfolio=str(book.id), error=str(problem))
        return _problem(request, str(problem), status=problem.http_status)

    reading = await risk_service.latest_reading(session, portfolio=book)
    changed = await risk_service.last_trade_recorded_at(session, portfolio=book)
    stale = reading is not None and (
        changed is not None
        and reading.job.started_at is not None
        and changed > reading.job.started_at
    )
    block = risk_service.block_of(view)
    currency = book.base_currency

    response: Response = render(
        request,
        "risk/index.html",
        {
            "no_book": False,
            "book": book,
            "as_of": as_of,
            "as_of_iso": as_of.isoformat(),
            "window_from": f"{view.window_from:%d %B %Y}",
            "verdict": _risk_verdict(view),
            "reading_state": _reading_state(reading, stale=stale),
            "figures": _book_figures(view, currency),
            "problem": view.problem,
            "coverage_note": block.coverage,
            "concentration": next(
                (row for row in block.exposure if row.label == "Largest five holdings"), None
            ),
            "exposure": _exposure_rows(view),
            "exposure_problem": view.exposure.problem,
            "holdings": _holding_rows(view),
            "scenarios": _scenario_rows(view, currency),
            "kinds": _KIND_CHOICES,
            "shock_rows": range(1, SHOCK_ROWS + 1),
            "reading": _reading_context(reading),
            "stale": stale,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


def _risk_verdict(view: risk_service.RiskView) -> verdicts.Verdict:
    measured = len(view.measured)
    unmeasured = len(view.holdings) - measured
    clauses: list[verdicts.Count | str] = [
        verdicts.Count(measured, "holding is measured", "holdings are measured"),
        verdicts.Count(
            unmeasured, "holding could not be measured", "holdings could not be measured"
        ),
        verdicts.Count(len(view.scenarios), "scenario is stated", "scenarios are stated"),
    ]
    when_none = (
        f"Nothing to measure: {view.problem}"
        if view.problem
        else "Nothing priced to measure. Record what you hold and acquire its price history."
    )
    tone = vocabulary.Tone.INFO if view.is_measured else vocabulary.Tone.MUTED
    return verdicts.sentence(clauses, when_none=when_none, tone=tone)


def _reading_state(reading: risk_service.Reading | None, *, stale: bool) -> str:
    if reading is None:
        return (
            "The analyst has not read this book. The figures are computed; the reading is a "
            "pass you run."
        )
    if reading.failed:
        return "The last reading stopped at its cost ceiling. Run it again."
    when = f"{reading.job.started_at:%d %B %Y}" if reading.job.started_at else "an unknown date"
    if stale:
        return f"The book has traded since the analyst last read it on {when}."
    return f"Read by the analyst on {when}."


def _book_figures(view: risk_service.RiskView, currency: str) -> list[FigureRow]:
    del currency
    rows: list[FigureRow] = []
    if view.volatility is not None:
        rows.append(
            _figure(
                "Annualised volatility",
                view.volatility,
                rendered=risk_service.percent(view.volatility.value).lstrip("+"),
                note=f"Over {view.observations} daily returns, in each listing's own currency.",
            )
        )
    if view.drawdown is not None:
        rows.append(
            _figure(
                "Maximum drawdown",
                view.drawdown,
                rendered=risk_service.percent(view.drawdown.value),
                note="The worst peak-to-trough fall of the book as it stands.",
            )
        )
    if view.expected_shortfall is not None:
        rows.append(
            _figure(
                "Expected shortfall",
                view.expected_shortfall,
                rendered=risk_service.percent(view.expected_shortfall.value),
                note="The average of the worst five per cent of days.",
            )
        )
    if view.coverage is not None:
        rows.append(
            _figure(
                "Coverage",
                view.coverage,
                rendered=risk_service.percent(view.coverage.value).lstrip("+"),
                note="Of net assets in measured holdings. The rest is cash or unmeasured.",
            )
        )
    return rows


def _exposure_rows(view: risk_service.RiskView) -> list[dict[str, Any]]:
    bands = []
    for band in view.exposure.bands:
        bands.append(
            {
                "kind": band.kind,
                "title": band.title,
                "slices": [
                    {
                        "label": row.label,
                        "share": risk_service.percent(row.share.value).lstrip("+"),
                        "width": int(max(Decimal(0), min(Decimal(1), row.share.value)) * 100),
                        "members": ", ".join(row.members),
                    }
                    for row in band.slices[:5]
                ],
                "unknown": (
                    {
                        "label": band.unknown.label,
                        "share": risk_service.percent(band.unknown.share.value).lstrip("+"),
                        "members": ", ".join(band.unknown.members),
                    }
                    if band.unknown is not None
                    else None
                ),
            }
        )
    return bands


def _holding_rows(view: risk_service.RiskView) -> list[dict[str, Any]]:
    return [
        {
            "ticker": row.security.ticker,
            "exchange": row.security.exchange,
            "weight": risk_service.percent(row.weight.value).lstrip("+") if row.weight else "",
            "volatility": (
                risk_service.percent(row.volatility.value).lstrip("+") if row.volatility else ""
            ),
            "beta": (
                f"{row.beta_to_book.value.quantize(Decimal('0.01'))}" if row.beta_to_book else ""
            ),
            "contribution": (
                risk_service.percent(row.contribution.value).lstrip("+") if row.contribution else ""
            ),
            "observations": row.observations,
            "problem": row.problem,
            "is_measured": row.is_measured,
            "href": f"/calculations/{row.volatility.record.id}" if row.volatility else "",
        }
        for row in view.holdings
    ]


def _scenario_rows(view: risk_service.RiskView, currency: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row.scenario.id,
            "name": row.scenario.name,
            "shocks": [
                {
                    "kind": vocabulary.SHOCK_KINDS[shock.kind].label,
                    "target": shock.target,
                    "shock": risk_service.percent(shock.shock),
                }
                for shock in row.scenario.shocks
            ],
            "reached": ", ".join(row.reached),
            "count": len(row.reached),
            "pnl": risk_service.money(row.pnl.value, currency) if row.pnl else "",
            "impact": risk_service.percent(row.impact.value) if row.impact else "",
            "is_loss": bool(row.pnl and row.pnl.value < 0),
            "problem": row.problem,
            "href": f"/calculations/{row.pnl.record.id}" if row.pnl else "",
        }
        for row in view.scenarios
    ]


def _reading_context(reading: risk_service.Reading | None) -> dict[str, Any] | None:
    if reading is None:
        return None
    commentary = reading.commentary
    return {
        "id": reading.job.id,
        "as_of": reading.as_of.isoformat() if reading.as_of else "",
        "started": f"{reading.job.started_at:%d %B %Y}" if reading.job.started_at else "",
        "cost": figures.pounds(reading.job.total_cost_gbp),
        "failed": reading.failed,
        "reason": reading.reason,
        "nothing_to_read": reading.nothing_to_read,
        "commentary": commentary.model_dump(mode="json") if commentary is not None else None,
        "refusals": reading.refusals,
        "attempts": int(reading.output.get("attempts") or 0),
    }


# -- The forms ------------------------------------------------------------------------------


@router.post("/risk/read", summary="Run the analyst over the book")
async def read_book(  # noqa: PLR0917 -- the service bundle, spelt out
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    provider: ProviderDep,
    model_router: RouterDep,
    store: StoreDep,
) -> Response:
    """One analyst pass, in this process, and back to the page it read."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was read.")
    book = await _book_of(session, user_id=user.id)
    if book is None:
        return _problem(request, "No book to read.")
    try:
        as_of = date.fromisoformat(submitted.get("as_of", ""))
    except ValueError:
        as_of = await _latest_close(session, portfolio=book)

    try:
        job = await risk_service.run_reading(
            session,
            settings=settings,
            provider=provider,
            router=model_router,
            store=store,
            user=user,
            portfolio=book,
            as_of=as_of,
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    _log.info("risk.read_from_page", job_id=str(job.id), status=job.status.value)
    return RedirectResponse(f"/risk?as_of={as_of.isoformat()}", status_code=HTTP_303_SEE_OTHER)


@router.post("/risk/scenarios", summary="State a scenario")
async def state_scenario(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was stated.")
    book = await _book_of(session, user_id=user.id)
    if book is None:
        return _problem(request, "No book to state a scenario about.")

    try:
        shocks = _shocks_from(submitted)
    except ValueError as wrong:
        return _problem(request, str(wrong), status=400)

    try:
        await risk_service.state_scenario(
            session, actor=user, portfolio=book, name=submitted.get("name", ""), shocks=shocks
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    return RedirectResponse("/risk", status_code=HTTP_303_SEE_OTHER)


def _shocks_from(submitted: dict[str, str]) -> list[risk_service.Shock]:
    """The form's rows as shocks: a kind, a target, and a per cent that becomes a fraction.

    Raises:
        ValueError: If a row's kind is not one of the five, or its shock is not a number.
    """
    shocks: list[risk_service.Shock] = []
    for index in range(1, SHOCK_ROWS + 1):
        raw = submitted.get(f"shock_{index}", "").strip().rstrip("%").strip()
        if not raw:
            continue
        try:
            fraction = Decimal(raw) / Decimal(100)
        except InvalidOperation as wrong:
            message = f"{raw!r} is not a number of per cent."
            raise ValueError(message) from wrong
        kind = ShockKind(submitted.get(f"kind_{index}", ""))
        shocks.append(
            risk_service.Shock(
                kind=kind, target=submitted.get(f"target_{index}", ""), shock=fraction
            )
        )
    return shocks


@router.post("/risk/scenarios/{scenario_id}/withdraw", summary="Withdraw a scenario")
async def withdraw_scenario(
    scenario_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was withdrawn.")
    scenario = await session.scalar(
        select(RiskScenario)
        .join(Portfolio, Portfolio.id == RiskScenario.portfolio_id)
        .where(RiskScenario.id == scenario_id, Portfolio.user_id == user.id)
    )
    if scenario is None:
        return _problem(request, "No such scenario.")
    try:
        await risk_service.withdraw_scenario(session, actor=user, scenario=scenario)
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    return RedirectResponse("/risk", status_code=HTTP_303_SEE_OTHER)


# -- Reading ----------------------------------------------------------------------------------


async def _book_of(session: Any, *, user_id: uuid.UUID) -> Portfolio | None:
    found: Portfolio | None = await session.scalar(
        select(Portfolio)
        .where(Portfolio.user_id == user_id, Portfolio.archived_at.is_(None))
        .order_by(Portfolio.created_at)
        .limit(1)
    )
    return found


def _requested_date(request: Request) -> date | None:
    raw = request.query_params.get("as_of", "").strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


async def _latest_close(session: Any, *, portfolio: Portfolio) -> date:
    """The last day the platform has a price for anything in this book, as the portfolio
    page defaults to, and for its reason: a book shown at today's date is unpriced every
    evening and all weekend."""
    latest = await session.scalar(
        select(func.max(PriceBar.bar_date))
        .join(Transaction, Transaction.security_id == PriceBar.security_id)
        .where(Transaction.portfolio_id == portfolio.id)
    )
    return latest or datetime.now(UTC).date()


async def _submitted(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}


def _problem(request: Request, message: str, *, status: int = HTTP_404_NOT_FOUND) -> Response:
    rendered: Response = render(
        request, "runs/problem.html", {"message": message}, status_code=status
    )
    return rendered


def _refused(request: Request, consequence: str) -> Response:
    return _problem(
        request,
        f"This form's security token was missing or had expired. {consequence}",
        status=HTTP_403_FORBIDDEN,
    )
