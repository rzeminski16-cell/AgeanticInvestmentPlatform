"""The Companies list and the company page (page specification §4 and §5).

**Companies is the watchlist in the specification's sense**: everything the account knows
about, why it is there, and when it is next looked at — three populations kept apart, sorted
by what has been neglected. The company page is everything known about one of them and
every action that concerns it: what you believe, what you hold, the record, a question, and
the four actions — three of which exist and one of which, the workbook, does not and says so.

**Every state on both pages is read from the record on the way to it**
(:mod:`aer.services.company_record`): whether a company is held from the book's own walk,
whether its report is current from the report's columns, whether its thesis is under review
from the open findings. Nothing here is stored and nothing here is a figure a report rests
on; the figures that appear — a holding's value, a report's cost — are the book's and the
run's, formatted by the same door those pages use.

The company page also carries what the research tool has concluded about the listing over
time — the approved-report timeline, the valuation history and the catalyst outcomes — which
was the whole of this page before §5 gave it the rest. It moved here from ``web/pages.py``
with its catalyst form, unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_422_UNPROCESSABLE_CONTENT,
)

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.charts import (
    ValuationHistoryInput,
    ValuationRangePoint,
    svg_data_uri,
    valuation_history,
)
from aer.core.enums import CatalystOutcomeKind, JobStatus, PremiseStatus, WatchCadence
from aer.db.models import Company, Finding, Job, Report, Security
from aer.errors import ValidationError
from aer.services import catalyst_resolutions as catalyst_service
from aer.services import company_record as record_service
from aer.services import history as history_service
from aer.services import refresh as refresh_service
from aer.services import thesis_monitor
from aer.services import watchlist as watchlist_service
from aer.services.ask import companies_with_a_record
from aer.services.company_record import CompanyRecord
from aer.web import figures, vocabulary
from aer.web import verdict as verdicts
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.pages import problem_page
from aer.web.portfolio.pages import pounds, shares
from aer.web.templating import render
from aer.web.theses.pages import premise_rows

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.companies")

# The filter row's words (§4). Keys are the service's; the words are the page's.
FILTER_WORDS: Final[dict[str, str]] = {
    "all": "All",
    "held": "Held",
    "researched": "Researched, not owned",
    "closed": "Closed",
    "no_report": "No report",
    "overdue": "Overdue",
}

# How each read state is shown. The thesis states borrow the portfolio page's judgement:
# *no thesis* is the strongest wording on the page, because a holding nobody has written a
# reason for is the one the loop exists to catch.
THESIS_TONES: Final[dict[str, vocabulary.Tone]] = {
    "holds": vocabulary.Tone.SUCCESS,
    "under_review": vocabulary.Tone.WARNING,
    "none": vocabulary.Tone.WARNING,
    "retired": vocabulary.Tone.MUTED,
}
REPORT_TONES: Final[dict[str, vocabulary.Tone]] = {
    "current": vocabulary.Tone.SUCCESS,
    "stale": vocabulary.Tone.WARNING,
    "none": vocabulary.Tone.MUTED,
}
CADENCE_WORDS: Final[dict[str, str]] = {
    WatchCadence.MONTHLY.value: "monthly",
    WatchCadence.QUARTERLY.value: "quarterly",
}

# What the page says where a figure would be and there is none. The same dash the book uses.
NO_FIGURE: Final = "—"


# -- Companies (§4) --------------------------------------------------------------------------


@router.get("/companies", response_class=HTMLResponse, summary="Companies")
async def companies_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Everything the account knows about, oldest-looked-at first."""
    now = datetime.now(UTC)
    show = request.query_params.get("show", "all")
    if show not in record_service.FILTERS:
        show = "all"
    records = await record_service.records_for(session, user=user, now=now)
    shown = record_service.filtered(records, show, now=now)
    estimate = refresh_service.estimate_refresh(settings)
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "companies/index.html",
        {
            "verdict": _list_verdict(records, now=now),
            "rows": [_row(record, now=now, refresh_label=estimate.label) for record in shown],
            "total": len(records),
            "show": show,
            "filters": [
                {
                    "key": key,
                    "label": FILTER_WORDS[key],
                    "count": len(record_service.filtered(records, key, now=now)),
                    "is_current": key == show,
                }
                for key in record_service.FILTERS
            ],
            "cadences": [
                {"value": value, "label": label.capitalize()}
                for value, label in CADENCE_WORDS.items()
            ],
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


def _list_verdict(records: list[CompanyRecord], *, now: datetime) -> verdicts.Verdict:
    counted = {
        key: sum(1 for record in records if record.population == key)
        for key in record_service.POPULATIONS
    }
    overdue = sum(1 for record in records if record.is_overdue(now))
    never = sum(1 for record in records if record.last_looked_at is None)
    clauses: list[verdicts.Count | str] = [
        verdicts.Count(counted[record_service.HELD], "company is held", "companies are held"),
        verdicts.Count(
            counted[record_service.RESEARCHED],
            "researched and not owned",
            "researched and not owned",
        ),
        verdicts.Count(
            counted[record_service.CLOSED_WATCHING], "closed and watched", "closed and watched"
        ),
        verdicts.Count(overdue, "is overdue a look", "are overdue a look"),
        verdicts.Count(never, "has never been looked at", "have never been looked at"),
    ]
    tone = vocabulary.Tone.WARNING if overdue or never else vocabulary.Tone.INFO
    return verdicts.sentence(
        clauses,
        when_none=(
            "Nothing is being watched yet. A company enters the record when you research it, "
            "hold it, write a thesis about it, or follow it."
        ),
        tone=tone if records else vocabulary.Tone.MUTED,
    )


def _row(record: CompanyRecord, *, now: datetime, refresh_label: str) -> dict[str, Any]:
    """One line of the table, already worded."""
    looked = record.last_looked_at
    return {
        "key": record.key,
        "name": record.name,
        "href": record.href,
        "listing": f"{record.ticker} · {record.exchange}",
        "population": record.population,
        "population_words": record.population_words,
        "why": record.watch.why if record.watch is not None and record.watch.why else "",
        "closed_on": f"{record.closed_on:%d %B %Y}" if record.closed_on else "",
        "last_looked_at": f"{looked:%d %B %Y}" if looked is not None else "Never",
        "never_looked_at": looked is None,
        "is_overdue": record.is_overdue(now),
        "next_check": (
            f"{record.next_check_at:%d %B %Y}" if record.next_check_at is not None else ""
        ),
        "cadence": CADENCE_WORDS.get(record.cadence, NO_FIGURE),
        "cadence_value": record.cadence,
        "entry_id": str(record.watch.id) if record.watch is not None else "",
        "thesis_state": record.thesis_state,
        "thesis_words": record_service.THESIS_STATES[record.thesis_state],
        "thesis_tone": THESIS_TONES[record.thesis_state].value,
        "open_findings": record.open_findings,
        "report_state": record.report_state,
        "report_words": record_service.REPORT_STATES[record.report_state],
        "report_tone": REPORT_TONES[record.report_state].value,
        "report_href": f"/reports/{record.report.id}" if record.report is not None else "",
        "run_state": record.run_state,
        "run_href": f"/runs/{record.run.id}" if record.run is not None else "",
        # A refresh is offered where one could start: the current report, with no run
        # already going on its request. The route refuses anything the row got wrong.
        "refresh_href": (
            f"/reports/{record.report.id}/refresh"
            if record.report is not None
            and record.report.is_current
            and record.run_state != "running"
            else ""
        ),
        "refresh_label": refresh_label,
    }


@router.post("/companies/{entry_id}/cadence", summary="Change how often a company is looked at")
async def change_cadence(
    entry_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The row's *change cadence* action (§4). Monthly or quarterly, and the next check
    moves with it from now."""
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return problem_page(
            request,
            "This form's security token was missing or had expired. The cadence was not changed.",
            status=HTTP_403_FORBIDDEN,
            back="/companies",
        )
    entry = await watchlist_service.entry_of(session, entry_id, user_id=user.id)
    if entry is None:
        return problem_page(
            request,
            "That is not a listing this account follows.",
            status=HTTP_404_NOT_FOUND,
            back="/companies",
        )
    try:
        await record_service.set_cadence(
            session, entry=entry, cadence=submitted.get("cadence", "").strip().lower()
        )
        await session.commit()
    except ValueError as refused:
        await session.rollback()
        return problem_page(
            request, str(refused), status=HTTP_422_UNPROCESSABLE_CONTENT, back="/companies"
        )
    _log.info("companies.cadence_changed", entry_id=str(entry.id), cadence=entry.cadence)
    return RedirectResponse("/companies", status_code=HTTP_303_SEE_OTHER)


# -- Company (§5) ----------------------------------------------------------------------------


@router.get(
    "/companies/{company_id}", response_class=HTMLResponse, summary="Everything about one company"
)
async def company_page(
    request: Request,
    company_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Everything known about one company, and every action that concerns it.

    A destination for a question rather than a daily home. What you believe sits beside
    what you hold and above it in the reading order, and nothing on the page is a
    recommendation: the report's own view is the report's, stated in the record's row.
    """
    company = await record_service.company_of(session, user=user, company_id=company_id)
    if company is None:
        return problem_page(
            request, "That company is not in your record.", status=HTTP_404_NOT_FOUND
        )
    now = datetime.now(UTC)
    record = await record_service.record_of(session, user=user, company=company, now=now)
    history = await _history(session, company)
    findings = [
        row
        for row in await thesis_monitor.findings_for(session, user_id=user.id, open_only=True)
        if record.thesis is not None and row.thesis_id == record.thesis.id
    ]
    listing = await _listing_of(session, record)
    can_ask = any(row.id == company.id for row in await companies_with_a_record(session, user=user))
    refresh_refusal = (
        await refresh_service.refusal_to_refresh(session, report=record.report, user=user)
        if record.report is not None
        else "Nothing to refresh: this company has no report yet."
    )
    estimate = refresh_service.estimate_refresh(settings)

    token = new_csrf_token(settings)
    page: Response = render(
        request,
        "companies/detail.html",
        {
            "company": company,
            "record": record,
            "verdict": _record_verdict(record),
            "header": _header(record),
            "believe": _believe(record, findings, company_id=company.id),
            "hold": _hold(record),
            "rows": await _record_rows(session, record, history=history),
            "can_ask": can_ask,
            "refresh_refusal": refresh_refusal,
            "refresh_label": estimate.label,
            "refresh_ceiling": settings.refresh_budget_gbp,
            "actions": _actions(record, listing=listing, company_id=company.id),
            **history,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(page, token)
    return page


def _record_verdict(record: CompanyRecord) -> verdicts.Verdict:
    """The sentence the page leads with: what the record says, in the order §5 reads it."""
    clauses: list[verdicts.Count | str] = [record.population_words]
    if record.thesis_state == "none":
        clauses.append("nothing is written down about why")
    elif record.thesis_state == "under_review":
        clauses.append("the thesis is under review")
    elif record.thesis_state == "retired":
        clauses.append("the thesis was retired")
    else:
        clauses.append("the thesis holds")
    if record.report is None:
        clauses.append("never researched")
    else:
        approved = record.report.approved_at
        dated = f", approved {approved:%d %B %Y}" if approved is not None else ""
        clauses.append(f"the report is {record.report_state}{dated}")
    if record.run_state == "running":
        clauses.append("a run is going")
    tone = (
        vocabulary.Tone.WARNING
        if record.thesis_state in {"none", "under_review"} or record.report_state != "current"
        else vocabulary.Tone.INFO
    )
    return verdicts.sentence(clauses, when_none="Nothing is known yet", tone=tone)


def _header(record: CompanyRecord) -> dict[str, Any]:
    """§5.1: the state labels, the weight if held, the cadence, and the price block."""
    price = record.price
    return {
        "states": [record.population_words],
        "weight": (
            f"{record.holding.weight.value * 100:.1f}% of the book"
            if record.holding is not None and record.holding.weight is not None
            else ""
        ),
        "cadence": CADENCE_WORDS.get(record.cadence, ""),
        "price": (
            {
                "close": pounds(price.close, price.currency),
                "move": _move_words(price.move_pct),
                "is_down": price.move_pct is not None and price.move_pct < 0,
                "dated": f"{price.bar_date:%d %B %Y}",
            }
            if price is not None
            else None
        ),
    }


def _move_words(move: Decimal | None) -> str:
    if move is None:
        return "no prior close to move from"
    scaled = (move * 100).quantize(Decimal("0.1"))
    sign = "-" if scaled < 0 else "+"
    return f"{sign}{abs(scaled):,.1f}% on the day"


def _believe(
    record: CompanyRecord, findings: list[Finding], *, company_id: uuid.UUID
) -> dict[str, Any]:
    """§5.2: each premise with its test and its status, and the line that says what the
    state means. Statuses are what the monitor last read (ADR 0079), never re-measured."""
    thesis = record.thesis
    if thesis is None:
        return {
            "thesis": None,
            "was_retired": record.thesis_state == "retired",
            "write_href": f"/theses?company={company_id}",
            "premises": [],
            "state_line": "",
        }
    latest: dict[uuid.UUID, Finding] = {}
    for finding in findings:  # newest first, so the first seen is the latest reading
        if finding.judgement_id is not None:
            latest.setdefault(finding.judgement_id, finding)
    premises: list[dict[str, Any]] = []
    for row in premise_rows(thesis):
        reading = latest.get(row.judgement_id)
        if reading is not None and reading.status is not None:
            words = vocabulary.PREMISE_STATES[reading.status]
            status = {"label": words.label, "tone": words.tone.value, "detail": words.detail}
        elif row.is_tested:
            status = {"label": "Not yet read", "tone": vocabulary.Tone.MUTED.value, "detail": ""}
        else:
            status = {
                "label": "Reviewed by a person",
                "tone": vocabulary.Tone.MUTED.value,
                "detail": "",
            }
        premises.append({"row": row, "status": status})
    broke = [
        finding
        for finding in findings
        if finding.status is PremiseStatus.CONTRADICTED or finding.opens_gate
    ]
    if broke:
        count = len({finding.judgement_id for finding in broke})
        newest = max(finding.created_at for finding in broke)
        state_line = (
            f"{count} premise{'s' if count != 1 else ''} broke, the latest on "
            f"{newest:%d %B}. Until you revise or withdraw it, this thesis is marked under "
            "review."
        )
    elif findings:
        state_line = "Every premise holds as the monitor last read it."
    elif any(row["row"].is_tested for row in premises):
        state_line = "Nothing has been read against these premises yet."
    else:
        state_line = "Every premise here is reviewed by a person; the monitor has nothing to read."
    return {
        "thesis": thesis,
        "was_retired": False,
        "write_href": f"/theses?company={company_id}",
        "premises": premises,
        "state_line": state_line,
    }


def _hold(record: CompanyRecord) -> dict[str, Any]:
    """§5.3: the position, or the sentence that says why there is none."""
    holding = record.holding
    if holding is not None:
        base = holding.security.quote_currency
        return {
            "is_held": True,
            "security_id": str(holding.security.id),
            "quantity": shares(holding.quantity.value) if holding.quantity else NO_FIGURE,
            "value": _money(holding.value, fallback=base),
            "cost": _money(holding.cost, fallback=base),
            "average": _money(holding.average, fallback=base),
            "unrealised": _money(holding.unrealised, fallback=base),
            "is_down": bool(holding.unrealised and holding.unrealised.value < 0),
            "weight": f"{holding.weight.value * 100:.1f}%" if holding.weight else NO_FIGURE,
            "weight_share": float(holding.weight.value) if holding.weight else 0.0,
            "problem": holding.problem,
            "not_held": "",
        }
    researched = ""
    if record.report is not None:
        dated = record.report.approved_at or record.report.as_of_date
        researched = f"Researched {dated:%d %B %Y}"
    if record.closed_on is not None:
        opening = f"Closed on {record.closed_on:%d %B %Y}."
    elif researched:
        opening = f"Not held. {researched}"
    else:
        opening = "Not held, and never researched."
    passed = record.passed
    if passed is not None:
        reason = passed.judgement.basis or passed.statement
        opening = (
            f"{opening}; you declined on {passed.judgement.held_at:%d %B %Y} because: {reason}"
        )
    elif researched and record.closed_on is None:
        opening = (
            f"{opening}. No pass is recorded; a reason for not holding it is not written down."
        )
    return {
        "is_held": False,
        "security_id": "",
        "not_held": opening,
    }


def _money(figure: Any, *, fallback: str) -> str:
    """A book figure to the penny in its own currency, or the dash."""
    if figure is None:
        return NO_FIGURE
    currency = next(iter(figure.unit.currencies), fallback)
    return pounds(figure.value, currency)


async def _record_rows(
    session: DbSession, record: CompanyRecord, *, history: dict[str, Any]
) -> list[dict[str, Any]]:
    """§5.4: Report, Decisions and Monitor, each with a summary, a detail line, a figure."""
    rows: list[dict[str, Any]] = [await _report_row(session, record, history=history)]
    decisions = record.decisions
    rows.append(
        {
            "key": "decisions",
            "title": "Decisions",
            "summary": (
                f"{decisions} recorded" if decisions else "Nothing decided about this company"
            ),
            "detail": (
                f"The latest was a pass on {record.passed.judgement.held_at:%d %B %Y}."
                if record.passed is not None
                else "A decision is written before its outcome is known, and a pass is one."
            ),
            "figure": str(decisions),
            "figure_label": "decisions",
            "href": "/decisions",
            "tone": vocabulary.Tone.INFO.value if decisions else vocabulary.Tone.MUTED.value,
        }
    )
    watch = record.watch
    checked = (
        f"Last read {watch.last_checked_at:%d %B %Y}"
        if watch is not None and watch.last_checked_at is not None
        else "Never read by the daily pass"
    )
    due = f"; next due {record.next_check_at:%d %B %Y}" if record.next_check_at is not None else ""
    rows.append(
        {
            "key": "monitor",
            "title": "Monitor",
            "summary": (
                f"{record.open_findings} open finding{'s' if record.open_findings != 1 else ''}"
                if record.open_findings
                else "Nothing open"
            ),
            "detail": f"{checked}{due}.",
            "figure": CADENCE_WORDS.get(record.cadence, "no cadence"),
            "figure_label": "cadence",
            "href": "/monitor",
            "tone": (
                vocabulary.Tone.WARNING.value
                if record.open_findings
                else vocabulary.Tone.MUTED.value
            ),
        }
    )
    return rows


async def _report_row(
    session: DbSession, record: CompanyRecord, *, history: dict[str, Any]
) -> dict[str, Any]:
    """The Report row's states (§5.6): never researched, running, refused, current, stale."""
    report = record.report
    run = record.run
    earlier = max(len(history["timeline"]) - (1 if report is not None else 0), 0)
    earlier_words = (
        f" {earlier} earlier report{'s' if earlier != 1 else ''} stay reachable below."
        if earlier
        else ""
    )
    if run is not None and record.run_state == "running":
        words = vocabulary.JOB_STATES[run.status]
        return {
            "key": "report",
            "title": "Report",
            "summary": f"Running — {words.label.lower()}",
            "detail": words.detail or "The run stops at each gate for you.",
            "figure": figures.pounds(run.total_cost_gbp),
            "figure_label": "spent so far",
            "href": f"/runs/{run.id}",
            "tone": words.tone.value,
        }
    if run is not None and record.run_state in {"refused", "stopped"} and report is None:
        words = vocabulary.JOB_STATES[run.status]
        message = ""
        if isinstance(run.error, dict):
            message = str(run.error.get("message", ""))
        return {
            "key": "report",
            "title": "Report",
            "summary": (
                "Refused at a gate" if run.status is not JobStatus.BUDGET_EXCEEDED else words.label
            ),
            "detail": message or words.detail or "The run's console names what refused it.",
            "figure": figures.pounds(run.total_cost_gbp),
            "figure_label": "spent",
            "href": f"/runs/{run.id}",
            "tone": vocabulary.Tone.REFUSAL.value,
        }
    if report is None:
        return {
            "key": "report",
            "title": "Report",
            "summary": "Never researched",
            "detail": "A research request is priced on its form before anything is spent.",
            "figure": NO_FIGURE,
            "figure_label": "",
            "href": "/requests/new",
            "tone": vocabulary.Tone.MUTED.value,
        }
    job = await session.get(Job, report.job_id)
    newest = history["timeline"][0] if history["timeline"] else None
    view = (
        f"{newest.rating or 'no view reached'}, {newest.valuation_range}."
        if newest is not None
        else "The report's view is on its own page."
    )
    approved = f"approved {report.approved_at:%d %B %Y}" if report.approved_at else "approved"
    if record.report_state == "stale":
        window = CADENCE_WORDS.get(record.cadence, "quarterly")
        summary = f"Stale — {approved}, older than the {window} window"
    else:
        summary = f"Current — {approved}"
    return {
        "key": "report",
        "title": "Report",
        "summary": summary,
        "detail": f"{view}{earlier_words}",
        "figure": figures.pounds(job.total_cost_gbp) if job is not None else NO_FIGURE,
        "figure_label": "cost",
        "href": f"/reports/{report.id}",
        "tone": REPORT_TONES[record.report_state].value,
    }


def _actions(record: CompanyRecord, *, listing: str, company_id: uuid.UUID) -> dict[str, Any]:
    """§5.6: the four actions, one of them honest about not existing."""
    thesis = record.thesis
    return {
        "refresh_href": f"/reports/{record.report.id}/refresh" if record.report else "",
        "thesis_href": f"/theses/{thesis.id}" if thesis is not None else "",
        "write_href": f"/theses?company={company_id}",
        "decision_href": f"/decisions?security={listing}" if listing else "/decisions",
        "position_href": (
            f"/portfolio/positions/{record.holding.security.id}"
            if record.holding is not None
            else ""
        ),
    }


async def _listing_of(session: DbSession, record: CompanyRecord) -> str:
    """The `TICKER.EXCHANGE` the decision form's pre-trade check reads, or blank."""
    if record.holding is not None:
        security = record.holding.security
        return f"{security.ticker}.{security.exchange}"
    if record.company is None:
        return ""
    found = await session.scalar(
        select(Security)
        .where(Security.company_id == record.company.id, Security.is_active.is_(True))
        .order_by(Security.created_at)
        .limit(1)
    )
    return f"{found.ticker}.{found.exchange}" if found is not None else ""


# -- The research history (§2.7 of the plan, unchanged) --------------------------------------


async def _history(session: DbSession, company: Company) -> dict[str, Any]:
    """The timeline, the valuation chart and the catalyst outcomes, as the page had them.

    Approved reports only — the history a decision could rest on. The valuation chart is
    the deterministic exportable builder salted with the company id, so the page shows
    the same bytes on every load.
    """
    views = await history_service.valuation_history_for(session, company_id=company.id)
    chart = valuation_history(
        ValuationHistoryInput(
            currency=next(
                (view.valuation_currency for view in views if view.valuation_currency), ""
            ),
            points=tuple(
                ValuationRangePoint(
                    as_of=view.as_of_date,
                    low=Decimal(view.valuation_low),
                    high=Decimal(view.valuation_high),
                )
                for view in views
                if view.valuation_low is not None and view.valuation_high is not None
            ),
        ),
        hashsalt=str(company.id),
    )
    today = datetime.now(UTC).date()
    catalyst_rows: list[Any] = []
    for view in reversed(views):  # newest report's catalysts first
        prior = await session.get(Report, view.report_id)
        if prior is None:  # pragma: no cover -- the view was built from this row
            continue
        catalyst_rows.extend(
            await history_service.catalyst_outcomes_for(session, prior=prior, as_of=today)
        )
    resolutions = await catalyst_service.resolutions_for(session, company_id=company.id)
    # The labels a resolution may attach to: passed or undated windows nobody has
    # answered yet. Pending ones wait — resolving a window that has not closed would be
    # recording the future.
    unresolved = sorted(
        {
            outcome.label
            for outcome in catalyst_rows
            if outcome.status != "pending" and outcome.label not in resolutions
        }
    )
    return {
        "timeline": list(reversed(views)),
        "chart_uri": svg_data_uri(chart.svg),
        "chart_caption": chart.caption,
        "chart_is_placeholder": chart.placeholder,
        "catalyst_outcomes": catalyst_rows,
        "resolutions": resolutions,
        "unresolved_labels": unresolved,
        "outcome_kinds": list(CatalystOutcomeKind),
    }


@router.post(
    "/companies/{company_id}/catalyst-resolutions",
    response_class=HTMLResponse,
    summary="Record what happened to a catalyst",
)
async def resolve_catalyst(
    request: Request,
    company_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The operator's answer to a closed window (K4). Never a model's.

    The service validates everything that matters — the label must name a catalyst an
    approved report proposed, the reason must not be blank — so this route decides
    nothing beyond ownership and the CSRF token, the export form's own division.
    """
    company = await history_service.company_for_user(
        session, company_id=company_id, user_id=user.id
    )
    if company is None:
        return problem_page(
            request, "That company is not in your record.", status=HTTP_404_NOT_FOUND
        )

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return problem_page(
            request,
            "This form's security token was missing or had expired. Nothing was recorded.",
            status=HTTP_403_FORBIDDEN,
            back=f"/companies/{company_id}",
        )

    try:
        outcome = CatalystOutcomeKind(submitted.get("outcome", ""))
    except ValueError:
        return problem_page(
            request,
            "The outcome must be one of: occurred, did not occur, superseded.",
            status=HTTP_422_UNPROCESSABLE_CONTENT,
            back=f"/companies/{company_id}",
        )
    try:
        await catalyst_service.record_catalyst_resolution(
            session,
            company_id=company.id,
            label=submitted.get("label", ""),
            outcome=outcome,
            reason=submitted.get("reason", ""),
            actor=user,
        )
    except ValidationError as exc:
        return problem_page(
            request,
            exc.message,
            status=HTTP_422_UNPROCESSABLE_CONTENT,
            back=f"/companies/{company_id}",
        )

    await session.commit()
    return RedirectResponse(f"/companies/{company_id}", status_code=HTTP_303_SEE_OTHER)


# -- Reading ---------------------------------------------------------------------------------


async def _submitted(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}
