"""The Companies list and the company page (page specification §4 and §5).

**Companies is the watchlist in the specification's sense**: everything the account knows
about, why it is there, and when it is next looked at — three populations kept apart, sorted
by what has been neglected. The company page is everything known about one of them and
every action that concerns it: what you believe, what you hold, the record, a question, and
the four actions — the workbook among them where one was archived with the report.

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

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlencode

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
    ValuationPoint,
    svg_data_uri,
    valuation_history,
)
from aer.core.dates import format_date
from aer.core.enums import (
    CatalystOutcomeKind,
    FindingKind,
    JobStatus,
    PremiseStatus,
    WatchCadence,
)
from aer.db.models import Company, Finding, Job, Report, Security
from aer.errors import ValidationError
from aer.render.document import NO_VIEW
from aer.services import catalyst_resolutions as catalyst_service
from aer.services import company_record as record_service
from aer.services import history as history_service
from aer.services import refresh as refresh_service
from aer.services import thesis_monitor
from aer.services import watchlist as watchlist_service
from aer.services.ask import companies_with_a_record
from aer.services.company_record import CompanyRecord
from aer.services.spend import spend_by_job
from aer.web import figures, vocabulary
from aer.web import verdict as verdicts
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.pages import problem_page
from aer.web.portfolio.pages import pounds, shares
from aer.web.templating import render
from aer.web.theses.pages import (
    defeat_words,
    held_in_order,
    measurement_words,
    premise_state,
)

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


# -- The search bar (02 §3's command bar) ----------------------------------------------------

# Long enough for a company's full legal name, and no longer: the bar reads a name or a
# ticker, and anything past this is not one.
SEARCH_LIMIT: Final = 120

# A query shaped like a ticker goes into the request form's ticker field rather than its name:
# letters, digits, a dot or a dash, and short. `BRK.B` and `RR.` are tickers; `Rolls-Royce
# Holdings` is not, and neither is anything with a space in it.
_TICKER_SHAPE: Final = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")


@router.get("/search", response_class=HTMLResponse, summary="Search")
async def search_page(request: Request, session: DbSession, user: CurrentUser) -> Response:
    """Where the search bar lands: the company it names, or the ones it might mean.

    A ticker or a name the account's record knows exactly jumps straight to that company's
    page, which is the bar's first promise. Anything else lists what it might mean, and every
    answer — none included — ends with the request form for the query, which is its second.
    The record is the Companies list's own (`records_for`), so the bar finds exactly what that
    page shows and nothing another operator holds.
    """
    query = " ".join(request.query_params.get("q", "").split())[:SEARCH_LIMIT]
    rows: list[dict[str, str]] = []
    if query:
        exact, partial = matches_for(await record_service.records_for(session, user=user), query)
        named = [record for record in exact if record.company is not None]
        if len(exact) == 1 and named:
            return RedirectResponse(named[0].href, status_code=HTTP_303_SEE_OTHER)
        rows = [
            {
                "name": record.name,
                "href": record.href,
                "listing": f"{record.ticker} · {record.exchange}",
                "population": record.population_words,
            }
            for record in (*exact, *partial)
        ]
    page: Response = render(
        request,
        "companies/search.html",
        {
            "query": query,
            "rows": rows,
            "heading": _search_heading(query, len(rows)),
            "subtitle": (
                "Every company you hold, watch or have researched, by name or ticker."
                if rows
                else "Nothing you hold, watch or have researched has that name or ticker."
            ),
            "research_href": research_href(query),
        },
    )
    return page


def _search_heading(query: str, found: int) -> str:
    if not found:
        return f"Nothing in your record is called “{query}”"
    return f"{found} {'company might be' if found == 1 else 'companies might be'} “{query}”"


def matches_for(
    records: list[CompanyRecord], query: str
) -> tuple[list[CompanyRecord], list[CompanyRecord]]:
    """The records a query names exactly, and the ones it only might mean, by name.

    Exact is the whole ticker or the whole name, in any case. Might-mean is a name containing
    the query or a ticker starting with it: *micro* is Microsoft, *MS* is MSFT, and *soft* is
    Microsoft too because a name is how people remember a company.
    """
    wanted = query.casefold()
    exact = [
        record for record in records if wanted in {record.ticker.casefold(), record.name.casefold()}
    ]
    named = {id(record) for record in exact}
    partial = [
        record
        for record in records
        if id(record) not in named
        and (wanted in record.name.casefold() or record.ticker.casefold().startswith(wanted))
    ]
    return exact, sorted(partial, key=lambda record: record.name.casefold())


def research_href(query: str) -> str:
    """The request form, with the query where the operator would have typed it."""
    if not query:
        return "/requests/new"
    field = "ticker" if _TICKER_SHAPE.match(query) else "company_name"
    return f"/requests/new?{urlencode({field: query})}"


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
    opened, closed = await thesis_monitor.findings_partitioned(session, user_id=user.id)
    findings = sorted(
        (
            row
            for row in (*opened, *closed)
            if record.thesis is not None and row.thesis_id == record.thesis.id
        ),
        key=lambda row: (row.created_at, str(row.id)),
        reverse=True,
    )
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
    """§5.2: each held premise with its test and its state, and the line that says what the
    state means. States are what the monitor last read (ADR 0079), never re-measured, and
    in the thesis editor's own words (`premise_state`), so the two pages cannot call one
    premise two things. ``findings`` is every finding on the thesis, newest first."""
    thesis = record.thesis
    if thesis is None:
        return {
            "thesis": None,
            "was_retired": record.thesis_state == "retired",
            "write_href": f"/theses?company={company_id}",
            "premises": [],
            "state_line": "",
            "meta": "",
        }
    latest: dict[uuid.UUID, Finding] = {}
    for finding in findings:
        if finding.judgement_id is not None and finding.kind is FindingKind.READING:
            latest.setdefault(finding.judgement_id, finding)
    premises: list[dict[str, Any]] = []
    for premise in held_in_order(thesis):
        reading = latest.get(premise.judgement_id)
        label, tone = premise_state(premise, reading)
        measured = measurement_words(premise, reading)
        premises.append(
            {
                "position": premise.position,
                "statement": premise.statement,
                "test": " · ".join(part for part in (defeat_words(premise), measured) if part),
                "label": label,
                "tone": tone,
            }
        )
    broke = [
        finding
        for finding in findings
        if finding.is_open and (finding.status is PremiseStatus.CONTRADICTED or finding.opens_gate)
    ]
    if broke:
        count = len({finding.judgement_id for finding in broke})
        newest = max(finding.created_at for finding in broke)
        opening = "One premise" if count == 1 else f"{count} premises"
        state_line = (
            f"{opening} broke on {format_date(newest, '%-d %B')}. Until you revise or "
            f"withdraw {'it' if count == 1 else 'them'}, this thesis is marked under review."
        )
    elif any(row["label"] == "holds" for row in premises):
        state_line = "Every premise the monitor has read holds."
    elif any(premise.has_predicate for premise in thesis.premises):
        state_line = "Nothing has been read against these premises yet."
    else:
        state_line = "Every premise here is reviewed by a person; the monitor has nothing to read."
    revisions = sum(1 for premise in thesis.premises if premise.judgement.supersedes_id)
    written = format_date(thesis.written_at or thesis.created_at, "%-d %B %Y")
    return {
        "thesis": thesis,
        "was_retired": False,
        "write_href": f"/theses?company={company_id}",
        "premises": premises,
        "state_line": state_line,
        "meta": f"written {written} · {revisions} revision{'s' if revisions != 1 else ''}",
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
            # A company nobody watches has no cadence to print, and "no cadence" above the
            # word "cadence" read as a stutter; the sentence says it once.
            "figure": CADENCE_WORDS.get(record.cadence, "not watched"),
            "figure_label": "cadence" if record.cadence in CADENCE_WORDS else "",
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
    # From the cost rows: a research run never writes `jobs.total_cost_gbp`.
    spent = await spend_by_job(session, [run.id]) if run is not None else {}
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
            "figure": figures.pounds(spent.get(run.id, Decimal(0))),
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
            "figure": figures.pounds(spent.get(run.id, Decimal(0))),
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
    # The masthead's two lines, in the masthead's words: what each method gives, and that
    # the report takes no side (ADR 0135).
    view = (
        f"What each method gives: {newest.valuation_text}. Non-binding view: "
        f"{newest.rating or NO_VIEW}."
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
        "figure": (
            figures.pounds((await spend_by_job(session, [job.id])).get(job.id, Decimal(0)))
            if job is not None
            else NO_FIGURE
        ),
        "figure_label": "cost",
        "href": f"/reports/{report.id}",
        "tone": REPORT_TONES[record.report_state].value,
    }


def _actions(record: CompanyRecord, *, listing: str, company_id: uuid.UUID) -> dict[str, Any]:
    """§5.6: the four actions. The workbook is a control where one was archived with the
    current report (F5), and otherwise a sentence saying why there is none — a control that
    opened nothing would be worse than the sentence."""
    thesis = record.thesis
    report = record.report
    has_workbook = report is not None and report.workbook_artefact_id is not None
    if has_workbook:
        workbook_absent = ""
    elif report is None:
        workbook_absent = "there is no report yet to take one from."
    else:
        workbook_absent = "none was archived with this report."
    return {
        "workbook_href": f"/api/reports/{report.id}/download/xlsx"
        if report is not None and has_workbook
        else "",
        "workbook_absent": workbook_absent,
        "refresh_href": f"/reports/{record.report.id}/refresh" if record.report else "",
        "thesis_href": f"/theses/{thesis.id}" if thesis is not None else "",
        "write_href": f"/theses?company={company_id}",
        # The record page, with what this page already knows chosen for the operator.
        "decision_href": "/decisions/new?"
        + "&".join(
            part
            for part in (
                f"security={listing}" if listing else "",
                f"thesis={thesis.id}" if thesis is not None else "",
            )
            if part
        ),
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
                (view.valuation.currency for view in views if view.valuation.currency), ""
            ),
            points=tuple(
                ValuationPoint(
                    as_of=view.as_of_date,
                    method=figure.label,
                    value=figure.value,
                    report=str(view.report_id),
                )
                for view in views
                for figure in view.valuation.figures
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
