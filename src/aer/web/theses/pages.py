"""What you believe about a company, written down, with what would defeat it.

Two screens. The list is every open thesis, with a form to write a new one; the detail is
the thesis editor (page specification §10): each premise as its sentence and its test, both
editable, with the state the monitor last read beside it; a revision saved as new rows with
the old wording kept; a broken premise's three answers — revise, withdraw, keep — in a
panel that says what each does; and forms to add a premise and retire the thesis. A thesis
is a document a person wrote (ADR 0074), so the page renders prose, dates and names, and the
one number a premise may carry — its threshold — is shown beside the metric it tests, in
the unit it was stated in, and enters no arithmetic here.

**A premise says what would defeat it, or who will look again.** The add-premise form
asks the question ADR 0079 settles: a threshold code can test against a stored fact, or a
date by which a person reviews it. The form never invents the first for a premise that
only has the second, and a premise rendered as "reviewed by a person" is styled no lower
than one "tested by a threshold" — the unquantifiable premises are the ones that decide
whether a position works.

**Nothing here is regulated investment advice**, and the shell says so on every page.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.core.dates import format_date
from aer.core.enums import Decision, FindingAction, FindingKind, PremiseComparator, PremiseStatus
from aer.core.figures import plain_decimal
from aer.db.models import Company, Finding, Portfolio, Premise, Report, Thesis, User
from aer.errors import AerError, ValidationError
from aer.render import display
from aer.services import decisions as decision_service
from aer.services import history, post_trade, thesis_monitor
from aer.services import portfolio as portfolio_service
from aer.services import theses as thesis_service
from aer.services.approvals import payload_hash_for
from aer.services.thesis_monitor import (
    DIMENSIONLESS_UNITS,
    PERCENT_UNITS,
    measurable_metrics,
    resolve_metric,
    slug_of,
)
from aer.web import figures, vocabulary
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render

__all__ = [
    "PositionRow",
    "PremiseRow",
    "ReportRow",
    "defeat_words",
    "held_in_order",
    "measured_line",
    "measurement_words",
    "metric_label",
    "premise_rows",
    "premise_state",
    "router",
    "threshold_words",
]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.theses")

# How the form asks what would defeat a premise. Two answers, named for what each is.
DEFEAT_THRESHOLD: Final = "threshold"
DEFEAT_REVIEW: Final = "review"

# The units a threshold may be stated in, as the forms offer them: the monitor's own spelling
# as the value (`thesis_monitor.threshold_quantity` parses each), words on the screen. A
# threshold is never saved without one (§10's *must not*): a bare number cannot be compared
# with a stored fact.
UNIT_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("percent", "per cent"),
    ("ratio", "times, a multiple"),
    ("day", "days"),
    ("USD", "US dollars"),
    ("GBP", "pounds sterling"),
    ("EUR", "euros"),
)
_UNIT_WORDS: Final[dict[str, str]] = {"day": "days"}

# A date as the page says one: *3 March 2026*, through the portable formatter.
_DAY: Final = "%-d %B %Y"

# The groups the metric select is cut into, in the resolver's order of preference.
_METRIC_GROUPS: Final[tuple[tuple[str, str], ...]] = (
    ("ratio", "Ratios"),
    ("growth", "Growth on the year before"),
    ("level", "A line as filed"),
)


# -- A premise in words ------------------------------------------------------------------------


def metric_label(metric: str) -> str:
    """A premise's metric as a person reads it: *Revenue growth*, never ``revenue_growth``.

    A name the monitor cannot resolve is the operator's own words, shown as they typed them;
    the premise is still recorded, and the monitor reads it as unobservable (ADR 0079).
    """
    resolved = resolve_metric(metric)
    if resolved is None:
        return metric.strip()
    if resolved.kind == "ratio":
        return resolved.label
    name = figures.concept_name(resolved.key)
    return f"{name} growth" if resolved.kind == "growth" else name


def metric_groups() -> list[dict[str, Any]]:
    """Every metric the monitor can measure, as the select offers it (§10.1): labelled in
    words, grouped by what kind of measurement it is. Nothing the monitor cannot resolve is
    offered — the §10 *must not*."""
    grouped: dict[str, list[dict[str, str]]] = {kind: [] for kind, _ in _METRIC_GROUPS}
    for key in measurable_metrics():
        resolved = resolve_metric(key)
        assert resolved is not None, "the resolver resolves every name it lists"
        grouped[resolved.kind].append({"value": key, "label": metric_label(key)})
    return [{"label": label, "choices": grouped[kind]} for kind, label in _METRIC_GROUPS]


def _grouped(value: Decimal) -> str:
    """A threshold as the operator would write it: separated, without the column's zeros."""
    return f"{value.normalize():,f}"


def threshold_words(threshold: Decimal, unit: str) -> str:
    """A threshold in the unit it was stated in: *8.0%*, *2 times*, *30 days*, *50,000 USD*.

    Through the document's own formatters (`aer.render.display`), so a margin the report
    prints and the premise that tests it round the same way.
    """
    cleaned = unit.strip().lower()
    if cleaned in PERCENT_UNITS:
        return display.percentage(threshold / 100, in_table=True)
    if cleaned in DIMENSIONLESS_UNITS:
        return display.multiple(threshold)
    return f"{_grouped(threshold)} {_UNIT_WORDS.get(unit.strip(), unit.strip())}"


def _measured_value(value: Decimal, *, observed_unit: str, stated_unit: str) -> str:
    """What the monitor measured, in the unit the premise was stated in.

    A per-cent threshold was divided by a hundred before it met the measurement
    (`thesis_monitor.threshold_quantity`), so a dimensionless reading beside one is a fraction
    and prints as a percentage.
    """
    cleaned = stated_unit.strip().lower()
    if cleaned in PERCENT_UNITS:
        return display.percentage(value, in_table=True)
    if cleaned in DIMENSIONLESS_UNITS:
        return display.multiple(value)
    unit = observed_unit if observed_unit and observed_unit != "ratio" else stated_unit.strip()
    return f"{_grouped(value)} {_UNIT_WORDS.get(unit, unit)}"


def defeat_words(premise: Premise) -> str:
    """What would defeat a premise, in words: *Gross margin at least 68.0%*, or the date a
    person looks again by."""
    if premise.has_predicate and premise.comparator is not None and premise.threshold is not None:
        return (
            f"{metric_label(premise.metric or '')} "
            f"{thesis_service.COMPARATOR_WORDS[premise.comparator]} "
            f"{threshold_words(premise.threshold, premise.unit or '')}"
        )
    if premise.review_by is not None:
        return f"Reviewed by hand, next by {format_date(premise.review_by, _DAY)}"
    return "Reviewed by hand"


def premise_state(premise: Premise, reading: Finding | None) -> tuple[str, str]:
    """The label beside a premise (§10.1): *holds*, *broke* or *by hand*, and its tone.

    Read from the monitor's latest reading and never re-measured here (ADR 0079). Two more
    words than the specification's three, because both are true of a premise the three do
    not describe: a test the monitor has not read yet, and one it could not measure.
    """
    if not premise.has_predicate:
        return "by hand", vocabulary.Tone.MUTED.value
    if reading is None or reading.status is None:
        return "not read yet", vocabulary.Tone.MUTED.value
    if reading.status is PremiseStatus.UNOBSERVABLE:
        return "cannot be measured", vocabulary.Tone.WARNING.value
    if reading.status is PremiseStatus.CONTRADICTED:
        return "broke", vocabulary.Tone.FAILURE.value
    return "holds", vocabulary.Tone.SUCCESS.value


def _measurement(premise: Premise, reading: Finding | None) -> str:
    """What the reading measured, for which year — *4.1% for the year to 30 June 2026* — or
    nothing where it measured nothing."""
    observed = (reading.observed if reading is not None else None) or {}
    if "value" not in observed:
        return ""
    try:
        value = Decimal(str(observed["value"]))
        period = date.fromisoformat(str(observed.get("period_end", "")))
    except (ArithmeticError, ValueError):
        return ""
    shown = _measured_value(
        value, observed_unit=str(observed.get("unit") or ""), stated_unit=premise.unit or ""
    )
    return f"{shown} for the year to {format_date(period, _DAY)}"


def measured_line(premise: Premise, reading: Finding | None) -> str:
    """The line under a premise's test on the editor: what was measured, and when."""
    if not premise.has_predicate:
        return "Nothing files a number for this one. You will be asked on the date."
    if reading is None:
        return (
            "Not read yet. The monitor reads it against each annual filing that arrives "
            "after the premise was written."
        )
    read_on = format_date(reading.created_at, _DAY)
    measured = _measurement(premise, reading)
    if reading.status is PremiseStatus.UNOBSERVABLE or not measured:
        return f"Read on {read_on} and not measured: {reading.justification}"
    return f"Measured {measured}, read on {read_on}."


def measured_figure(premise: Premise, reading: Finding | None) -> str:
    """The value alone — *3.6%* — for a list that sets it against the test beside it, as the
    alert page's premises do (§12.4); empty where the reading measured nothing."""
    observed = (reading.observed if reading is not None else None) or {}
    if not premise.has_predicate or "value" not in observed:
        return ""
    try:
        value = Decimal(str(observed["value"]))
    except ArithmeticError:
        return ""
    return _measured_value(
        value, observed_unit=str(observed.get("unit") or ""), stated_unit=premise.unit or ""
    )


def measurement_words(premise: Premise, reading: Finding | None) -> str:
    """The same, as the clause a one-line summary carries — the company page's (§5.2)."""
    if not premise.has_predicate:
        return ""
    measured = _measurement(premise, reading)
    if measured:
        return f"measured {measured}"
    return "not read yet" if reading is None else "could not be measured"


@dataclass(frozen=True, slots=True)
class PremiseRow:
    """One premise as the detail page shows it."""

    judgement_id: uuid.UUID
    position: int
    statement: str
    basis: str
    held_by: str
    held_on: str
    defeated_by: str
    is_tested: bool
    is_withdrawn: bool
    withdrawn_on: str
    withdrawn_reason: str


def _row(premise: Premise) -> PremiseRow:
    judgement = premise.judgement
    return PremiseRow(
        judgement_id=premise.judgement_id,
        position=premise.position,
        statement=premise.statement,
        basis=judgement.basis,
        held_by=judgement.held_by,
        held_on=f"{judgement.held_at:%d %B %Y}",
        defeated_by=defeat_words(premise),
        is_tested=premise.has_predicate,
        is_withdrawn=judgement.is_withdrawn,
        withdrawn_on=f"{judgement.withdrawn_at:%d %B %Y}" if judgement.withdrawn_at else "",
        withdrawn_reason=judgement.withdrawn_reason or "",
    )


def premise_rows(thesis: Thesis) -> list[PremiseRow]:
    """The thesis's premises as a page shows them — here, and on a decision taken on them."""
    return [_row(premise) for premise in thesis.premises]


# -- The editor ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrokenReading:
    """A reading that broke a premise and is still waiting for the operator (§10.3)."""

    finding_id: uuid.UUID
    raised_on: str
    gate_is_decidable: bool
    payload_hash: str
    """What the gate binds its decision to: the finding as this page showed it."""


@dataclass(frozen=True, slots=True)
class EditorRow:
    """One held premise as the editor shows it (§10.1): the sentence and the test as the
    controls that revise them, the state beside them, and the line under them."""

    judgement_id: uuid.UUID
    number: int
    statement: str
    basis: str
    is_tested: bool
    metric: str
    metric_is_measurable: bool
    metric_label: str
    comparator: str
    threshold: str
    unit: str
    review_by: str
    defeat: str
    state: str
    tone: str
    measured: str
    kept: str
    history: tuple[str, ...]
    open_findings: tuple[BrokenReading, ...]
    broken: BrokenReading | None


@dataclass(frozen=True, slots=True)
class GivenUpRow:
    """A premise withdrawn rather than revised: struck through, with its reason (§10.3)."""

    judgement_id: uuid.UUID
    statement: str
    defeat: str
    held_on: str
    withdrawn_on: str
    reason: str


@dataclass(frozen=True, slots=True)
class Editor:
    """Everything the editor draws from the premises and the monitor's readings of them."""

    rows: tuple[EditorRow, ...]
    given_up: tuple[GivenUpRow, ...]
    revisions: int
    tested: int

    @property
    def broken(self) -> tuple[EditorRow, ...]:
        return tuple(row for row in self.rows if row.broken is not None)


def _reading_of(finding: Finding) -> BrokenReading:
    return BrokenReading(
        finding_id=finding.id,
        raised_on=format_date(finding.created_at, _DAY),
        gate_is_decidable=finding.gate_is_decidable,
        payload_hash=(
            payload_hash_for(thesis_monitor.finding_payload(finding))
            if finding.gate_is_decidable
            else ""
        ),
    )


def _kept(reading: Finding | None) -> str:
    """The sentence under a broken premise the operator chose to keep, from the act itself."""
    if reading is None or reading.status is not PremiseStatus.CONTRADICTED or reading.is_open:
        return ""
    act = reading.resolutions[-1]
    if act.action is not FindingAction.DISMISSED:
        return ""
    return f"You kept it on {format_date(act.resolved_at, _DAY)}: {act.reason}"


def _history(premise: Premise, by_judgement: dict[uuid.UUID, Premise]) -> tuple[str, ...]:
    """The premise's revisions as a narrative, oldest first — never as a diff (§10.3)."""
    chain = [premise]
    while chain[-1].judgement.supersedes_id in by_judgement:
        chain.append(by_judgement[chain[-1].judgement.supersedes_id])
    if len(chain) == 1:
        return ()
    chain.reverse()
    first = chain[0]
    lines = [f"On {format_date(first.judgement.held_at, _DAY)} you believed: {first.statement}"]
    lines.extend(
        f"On {format_date(after.judgement.held_at, _DAY)} you revised it, because "
        f"{before.judgement.withdrawn_reason}, to: {after.statement}"
        for before, after in pairwise(chain)
    )
    return tuple(lines)


def _root_position(premise: Premise, by_judgement: dict[uuid.UUID, Premise]) -> int:
    """Where a revised premise sits: in the place of the first wording it replaced."""
    while premise.judgement.supersedes_id in by_judgement:
        premise = by_judgement[premise.judgement.supersedes_id]
    return premise.position


def held_in_order(thesis: Thesis) -> list[Premise]:
    """The premises still held, each revision in the place of the wording it replaced.

    A revision is a new row at the end of the thesis (§10.3), so position order alone would
    move every revised premise to the bottom — the editor and the company page read this
    instead, and cannot list one thesis in two orders.
    """
    by_judgement = {premise.judgement_id: premise for premise in thesis.premises}
    return sorted(
        (premise for premise in thesis.premises if not premise.judgement.is_withdrawn),
        key=lambda premise: _root_position(premise, by_judgement),
    )


def editor_of(
    thesis: Thesis, findings: list[Finding], *, submitted: dict[str, str] | None = None
) -> Editor:
    """The editor's rows, from the premises and every finding on this thesis.

    ``findings`` is newest first, so the first reading seen for a premise is its latest.
    ``submitted`` is a refused revision's form: the page is drawn again with what the
    operator typed rather than what is stored, so a missing reason costs a line, not the edit.
    """
    typed = submitted or {}
    by_judgement = {premise.judgement_id: premise for premise in thesis.premises}
    revised = {
        premise.judgement.supersedes_id
        for premise in thesis.premises
        if premise.judgement.supersedes_id is not None
    }
    latest: dict[uuid.UUID, Finding] = {}
    open_by_premise: dict[uuid.UUID, list[Finding]] = {}
    for finding in findings:
        if finding.judgement_id is None or finding.thesis_id != thesis.id:
            continue
        if finding.kind is FindingKind.READING:
            latest.setdefault(finding.judgement_id, finding)
        if finding.is_open:
            open_by_premise.setdefault(finding.judgement_id, []).append(finding)

    held = held_in_order(thesis)
    rows: list[EditorRow] = []
    for number, premise in enumerate(held, start=1):
        jid = premise.judgement_id
        reading = latest.get(jid)
        state, tone = premise_state(premise, reading)
        waiting = open_by_premise.get(jid, [])
        broken = next(
            (
                finding
                for finding in waiting
                if finding.status is PremiseStatus.CONTRADICTED or finding.opens_gate
            ),
            None,
        )
        metric = premise.metric or ""
        measurable = resolve_metric(metric) is not None
        stored_key = slug_of(metric) if measurable else metric
        rows.append(
            EditorRow(
                judgement_id=jid,
                number=number,
                statement=typed.get(f"statement-{jid}", premise.statement),
                basis=premise.judgement.basis,
                is_tested=premise.has_predicate,
                metric=typed.get(f"metric-{jid}", stored_key),
                metric_is_measurable=measurable,
                metric_label=metric_label(metric),
                comparator=typed.get(
                    f"comparator-{jid}", premise.comparator.value if premise.comparator else ""
                ),
                threshold=typed.get(
                    f"threshold-{jid}",
                    plain_decimal(premise.threshold) if premise.threshold is not None else "",
                ),
                unit=typed.get(f"unit-{jid}", premise.unit or ""),
                review_by=typed.get(
                    f"review_by-{jid}",
                    premise.review_by.isoformat() if premise.review_by is not None else "",
                ),
                defeat=defeat_words(premise),
                state=state,
                tone=tone,
                measured=measured_line(premise, reading),
                kept=_kept(reading),
                history=_history(premise, by_judgement),
                open_findings=tuple(_reading_of(finding) for finding in waiting),
                broken=_reading_of(broken) if broken is not None else None,
            )
        )
    given_up = tuple(
        GivenUpRow(
            judgement_id=premise.judgement_id,
            statement=premise.statement,
            defeat=defeat_words(premise),
            held_on=format_date(premise.judgement.held_at, _DAY),
            withdrawn_on=(
                format_date(premise.judgement.withdrawn_at, _DAY)
                if premise.judgement.withdrawn_at is not None
                else ""
            ),
            reason=premise.judgement.withdrawn_reason or "",
        )
        for premise in thesis.premises
        if premise.judgement.is_withdrawn and premise.judgement_id not in revised
    )
    return Editor(
        rows=tuple(rows),
        given_up=given_up,
        revisions=len(revised),
        tested=sum(1 for premise in held if premise.has_predicate),
    )


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One approved report on the thesis's subject, as the detail page lists it."""

    report_id: uuid.UUID
    as_of: str
    approved_on: str
    is_written_against: bool
    """The report the thesis names as what it was written against — one at most."""


@dataclass(frozen=True, slots=True)
class PositionRow:
    """One position the default book has held in the subject: open, or closed and dated.

    Queries over the subject, never foreign keys (ADR 0064): the thesis is about a company,
    the book deals in that company's listings, and the two meet only here on the page.
    """

    listing: str
    book: str
    is_open: bool
    opened_on: str
    closed_on: str
    trades: int
    href: str
    is_reviewed: bool


async def _reports_on(session: Any, thesis: Thesis) -> list[ReportRow]:
    """Every approved report on the subject, newest first, with the one written against."""
    if thesis.subject_kind != thesis_service.SUBJECT_COMPANY:  # pragma: no cover -- one kind
        return []
    rows = await history.approved_reports_for(session, company_id=thesis.subject_id)
    return [
        ReportRow(
            report_id=report.id,
            as_of=f"{report.as_of_date:%d %B %Y}",
            approved_on=f"{report.approved_at:%d %B %Y}" if report.approved_at else "",
            is_written_against=report.id == thesis.report_id,
        )
        for report in rows
    ]


async def _positions_in(
    session: Any, thesis: Thesis, *, user_id: uuid.UUID
) -> tuple[Portfolio | None, list[PositionRow]]:
    """What the default book has held in the subject: the open position first, then each
    closed one, newest close first, linking to its review where one exists."""
    book = await portfolio_service.default_book(session, user_id=user_id)
    if book is None:
        return None, []
    positions = await post_trade.positions_of(session, portfolio=book)
    rows = [
        PositionRow(
            listing=f"{held.security.ticker} on {held.security.exchange}",
            book=book.name,
            is_open=True,
            opened_on=f"{held.opened_on:%d %B %Y}",
            closed_on="",
            trades=len(held.trades),
            href="/portfolio",
            is_reviewed=False,
        )
        for held in positions.held
        if held.security.company_id == thesis.subject_id
    ]
    for episode in positions.closed:
        if episode.security.company_id != thesis.subject_id:
            continue
        review = await post_trade.review_for_episode(session, episode=episode)
        rows.append(
            PositionRow(
                listing=f"{episode.security.ticker} on {episode.security.exchange}",
                book=book.name,
                is_open=False,
                opened_on=f"{episode.opened_on:%d %B %Y}",
                closed_on=f"{episode.closed_on:%d %B %Y}",
                trades=len(episode.trades),
                href=f"/review/{review.judgement_id}" if review is not None else "/review",
                is_reviewed=review is not None,
            )
        )
    return book, rows


# -- The list --------------------------------------------------------------------------------


@router.get("/theses", response_class=HTMLResponse, summary="Theses")
async def theses_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Every open thesis, and the form to write one."""
    retired = request.query_params.get("retired") == "1"
    rows = await thesis_service.theses_for(session, user_id=user.id, retired=retired)
    companies = await thesis_service.companies_to_write_about(session)
    reports = await thesis_service.reports_to_write_against(session)
    named = [
        {
            "thesis": thesis,
            "subject": await thesis_service.subject_name(session, thesis),
            "held": sum(1 for row in thesis.premises if not row.judgement.is_withdrawn),
        }
        for thesis in rows
    ]
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "theses/index.html",
        {
            "rows": named,
            "showing_retired": retired,
            # The company page's *Write a thesis* arrives with its company chosen.
            "chosen": request.query_params.get("company", ""),
            "companies": [
                {"value": str(company.id), "label": f"{company.name} ({company.ticker})"}
                for company in companies
            ],
            "reports": [
                {"value": str(report.id), "label": _report_label(report, company)}
                for report, company in reports
            ],
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/theses", summary="Write a thesis")
async def write_thesis(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was written.")

    company = await _company(session, submitted.get("company_id", ""))
    if company is None:
        return _problem(
            request,
            "That company is not one the platform can resolve. A thesis is about a company "
            "the research tool has looked up, not about a ticker somebody typed.",
        )

    report = await _report(session, submitted.get("report_id", ""))
    if report is None and submitted.get("report_id", "").strip():
        return _problem(request, "That report is not one the platform holds.")
    if report is not None and report.company_id != company.id:
        return _problem(
            request,
            "That report is about a different company. A thesis is written against a report "
            "on its own subject, or against none.",
            status=422,
        )

    try:
        thesis = await thesis_service.write_thesis(
            session,
            user=user,
            company=company,
            title=submitted.get("title", ""),
            written_at=_date_at(submitted.get("written_on", "")),
            report_id=report.id if report is not None else None,
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


# -- One thesis ------------------------------------------------------------------------------


@router.get("/theses/{thesis_id}", response_class=HTMLResponse, summary="A thesis")
async def thesis_page(
    thesis_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")
    return await _editor_page(
        request,
        session,
        settings,
        user,
        thesis,
        notice=(
            "Nothing had changed, so nothing was saved."
            if request.query_params.get("unchanged") == "1"
            else ""
        ),
    )


async def _editor_page(
    request: Request,
    session: Any,
    settings: Any,
    user: User,
    thesis: Thesis,
    *,
    submitted: dict[str, str] | None = None,
    problem: str = "",
    notice: str = "",
    status: int = 200,
) -> Response:
    """The editor, drawn from the record — or, after a refused revision, from what was typed."""
    opened, closed = await thesis_monitor.findings_partitioned(session, user_id=user.id)
    findings = sorted(
        (finding for finding in (*opened, *closed) if finding.thesis_id == thesis.id),
        key=lambda finding: (finding.created_at, str(finding.id)),
        reverse=True,
    )
    editor = editor_of(thesis, findings, submitted=submitted)
    book, positions = await _positions_in(session, thesis, user_id=user.id)
    reports = await _reports_on(session, thesis)
    decisions = [
        {
            "id": row.judgement_id,
            "action": decision_service.ACTION_WORDS[row.action],
            "statement": row.statement,
            "decided_on": f"{row.judgement.held_at:%d %B %Y}",
            "is_withdrawn": row.judgement.is_withdrawn,
            "carried_out": len(row.transactions),
        }
        for row in await decision_service.decisions_of_thesis(session, thesis)
    ]
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "theses/detail.html",
        {
            "item": thesis,
            "subject": await thesis_service.subject_name(session, thesis),
            "editor": editor,
            "written_on": format_date(thesis.written_at or thesis.created_at, _DAY),
            "written_against": next((row for row in reports if row.is_written_against), None),
            "reports": reports,
            "book": book,
            "positions": positions,
            "metric_groups": metric_groups(),
            "unit_choices": [{"value": value, "label": label} for value, label in UNIT_CHOICES],
            "decisions": decisions,
            "comparators": [
                {"value": member.value, "label": label}
                for member, label in thesis_service.COMPARATOR_WORDS.items()
            ],
            "defeat_threshold": DEFEAT_THRESHOLD,
            "defeat_review": DEFEAT_REVIEW,
            "reason": (submitted or {}).get("reason", ""),
            "problem": problem,
            "notice": notice,
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
        status_code=status,
    )
    set_csrf_cookie(response, token)
    return response


@dataclass(frozen=True, slots=True)
class _Revision:
    statement: str
    predicate: thesis_service.Predicate | None
    review_by: date | None


def _revision_of(premise: Premise, submitted: dict[str, str]) -> _Revision | None:
    """What the editor's form says this premise should now be, or ``None`` if unchanged.

    A field the form did not send is the stored value, so a premise nobody touched is never
    revised. A premise keeps its kind: a tested one is revised to another test, a reviewed
    one to another date — changing which kind a premise is means withdrawing it and adding
    the other, which the page says.

    Raises:
        ValueError, InvalidOperation: If a threshold or a date does not parse.
        ValidationError: From the predicate, for a blank metric or unit.
    """
    jid = premise.judgement_id
    statement = submitted.get(f"statement-{jid}", premise.statement).strip()
    if premise.has_predicate:
        assert premise.comparator is not None
        assert premise.threshold is not None
        metric = submitted.get(f"metric-{jid}", premise.metric or "")
        comparator = PremiseComparator(submitted.get(f"comparator-{jid}", premise.comparator.value))
        raw = submitted.get(f"threshold-{jid}")
        threshold = premise.threshold if raw is None else _threshold_of(raw)
        unit = submitted.get(f"unit-{jid}", premise.unit or "")
        predicate = thesis_service.Predicate(
            metric=metric, comparator=comparator, threshold=threshold, unit=unit
        )
        unchanged = (
            statement == premise.statement
            and slug_of(metric) == slug_of(premise.metric or "")
            and comparator is premise.comparator
            and threshold == premise.threshold
            and unit.strip().lower() == (premise.unit or "").strip().lower()
        )
        return None if unchanged else _Revision(statement, predicate, None)
    raw_date = submitted.get(f"review_by-{jid}")
    review_by = (
        premise.review_by
        if raw_date is None
        else (date.fromisoformat(raw_date.strip()) if raw_date.strip() else None)
    )
    unchanged = statement == premise.statement and review_by == premise.review_by
    return None if unchanged else _Revision(statement, None, review_by)


async def _close_open_findings(
    session: Any,
    *,
    premise: Premise,
    actor: User,
    reason: str,
    withdrawn: bool,
    submitted: dict[str, str],
) -> None:
    """Close every open finding on a premise the operator has just answered.

    A reading about a wording nobody holds any more is not a question anybody is waiting
    on, so revising or withdrawing a premise closes its findings as a withdrawal, and keeping
    it closes them as seen. A contradiction whose pass is on record is decided at its gate,
    with the hash of the finding as this page showed it; anything else is resolved with the
    same reason (ADR 0078).
    """
    for finding in await thesis_monitor.findings_for(session, user_id=actor.id):
        if finding.judgement_id != premise.judgement_id:
            continue
        if finding.gate_is_decidable:
            await thesis_monitor.decide_finding(
                session,
                finding=finding,
                actor=actor,
                decision=Decision.APPROVED if withdrawn else Decision.REJECTED,
                reason=reason,
                payload_hash=submitted.get(f"payload_hash-{finding.id}", ""),
            )
        else:
            await thesis_monitor.resolve_finding(
                session,
                finding=finding,
                actor=actor,
                action=FindingAction.WITHDRAWN if withdrawn else FindingAction.DISMISSED,
                reason=reason,
            )


@router.post("/theses/{thesis_id}/revise", summary="Save a revision of the premises")
async def revise_thesis(
    thesis_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Every premise the editor changed, revised in one act with one reason (§10.3)."""
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was revised.")

    held = [premise for premise in thesis.premises if not premise.judgement.is_withdrawn]
    reason = submitted.get("reason", "")
    try:
        changed = [
            (premise, revision)
            for premise in held
            if (revision := _revision_of(premise, submitted)) is not None
        ]
        if not changed:
            return RedirectResponse(
                f"/theses/{thesis.id}?unchanged=1", status_code=HTTP_303_SEE_OTHER
            )
        for premise, revision in changed:
            await thesis_service.revise_premise(
                session,
                thesis=thesis,
                premise=premise,
                actor=user,
                statement=revision.statement,
                predicate=revision.predicate,
                review_by=revision.review_by,
                reason=reason,
            )
            await _close_open_findings(
                session,
                premise=premise,
                actor=user,
                reason=reason,
                withdrawn=True,
                submitted=submitted,
            )
        await session.commit()
    except (AerError, ValueError, InvalidOperation) as refused:
        await session.rollback()
        # The rollback expired every row this session holds, the user among them, and an
        # expired row read in an async session raises rather than reloading itself.
        await session.refresh(user)
        reloaded = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
        assert reloaded is not None
        return await _editor_page(
            request,
            session,
            settings,
            user,
            reloaded,
            submitted=submitted,
            problem=_revision_problem(refused),
            status=refused.http_status if isinstance(refused, AerError) else 422,
        )

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


def _revision_problem(refused: Exception) -> str:
    if isinstance(refused, AerError):
        return str(refused)
    return (
        "A threshold is a number and a review date is a date; one of the revised premises "
        "has something else. Nothing was saved."
    )


@router.post("/theses/{thesis_id}/premises", summary="Add a premise")
async def add_premise(
    thesis_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was added.")

    try:
        predicate, review_by = _what_defeats_it(submitted)
        await thesis_service.add_premise(
            session,
            thesis=thesis,
            actor=user,
            statement=submitted.get("statement", ""),
            basis=submitted.get("basis", ""),
            predicate=predicate,
            review_by=review_by,
            held_at=_date_at(submitted.get("held_on", "")),
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    except (ValueError, InvalidOperation) as malformed:
        await session.rollback()
        return _problem(request, f"That premise could not be recorded: {malformed}", status=400)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/theses/{thesis_id}/premises/{judgement_id}/withdraw", summary="Withdraw a premise")
async def withdraw_premise(  # noqa: PLR0917 -- two path parameters and the four every handler takes
    thesis_id: uuid.UUID,
    judgement_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The holder no longer holds it, and says why; whatever the monitor raised on it closes
    with the same reason. It stops being tested and stays in the history (§10.3)."""
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")
    premise = await thesis_service.premise_of(session, judgement_id, thesis=thesis)
    if premise is None:
        return _problem(request, "No such premise on this thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was withdrawn.")

    reason = submitted.get("reason", "")
    try:
        await thesis_service.withdraw_premise(session, premise=premise, actor=user, reason=reason)
        await _close_open_findings(
            session,
            premise=premise,
            actor=user,
            reason=reason,
            withdrawn=True,
            submitted=submitted,
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/theses/{thesis_id}/premises/{judgement_id}/keep", summary="Keep a broken premise")
async def keep_premise(  # noqa: PLR0917 -- two path parameters and the four every handler takes
    thesis_id: uuid.UUID,
    judgement_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The operator thinks the miss is temporary, and says why (§10.3).

    The premise stands and goes on being tested; the reading that broke it closes as seen,
    at its gate where the pass is on record. The next annual filing is read against it as
    any other would be, which is the date to look again.
    """
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")
    premise = await thesis_service.premise_of(session, judgement_id, thesis=thesis)
    if premise is None:
        return _problem(request, "No such premise on this thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was recorded.")

    waiting = [
        finding
        for finding in await thesis_monitor.findings_for(session, user_id=user.id)
        if finding.judgement_id == premise.judgement_id
    ]
    if not waiting:
        return _problem(
            request,
            "Nothing is waiting on this premise, so there is nothing to keep it against.",
            status=409,
        )
    reason = submitted.get("reason", "")
    try:
        if not reason.strip():
            message = (
                'Keeping a broken premise needs a reason. "I think the miss is temporary, '
                'because…" is the line a later review reads first.'
            )
            raise ValidationError(message, context={"field": "reason"})
        await _close_open_findings(
            session,
            premise=premise,
            actor=user,
            reason=reason,
            withdrawn=False,
            submitted=submitted,
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/theses/{thesis_id}/retire", summary="Retire a thesis")
async def retire_thesis(
    thesis_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    thesis = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "No such thesis.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was retired.")

    try:
        await thesis_service.retire_thesis(
            session, thesis=thesis, actor=user, reason=submitted.get("reason", "")
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/theses/{thesis.id}", status_code=HTTP_303_SEE_OTHER)


# -- Reading the form ------------------------------------------------------------------------


def _what_defeats_it(
    submitted: dict[str, str],
) -> tuple[thesis_service.Predicate | None, date | None]:
    """The form's answer to ADR 0079's question, as the two columns it may fill.

    The choice is a radio the operator made, and the other branch's fields are ignored
    rather than merged: a review date typed beside a threshold is a premise with two
    answers, and the one the operator chose is the one that counts.
    """
    chosen = submitted.get("defeated_by", DEFEAT_REVIEW)
    if chosen == DEFEAT_THRESHOLD:
        predicate = thesis_service.Predicate(
            metric=submitted.get("metric", ""),
            comparator=PremiseComparator(submitted.get("comparator", "")),
            threshold=_threshold_of(submitted.get("threshold", "")),
            unit=submitted.get("unit", ""),
        )
        return predicate, None
    raw_date = submitted.get("review_by", "").strip()
    return None, date.fromisoformat(raw_date) if raw_date else None


def _threshold_of(raw: str) -> Decimal:
    """A threshold as typed, with its thousands separators allowed.

    Raises:
        ValueError: If nothing was typed.
        InvalidOperation: If what was typed is not a number.
    """
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        message = "a threshold needs a number to compare against"
        raise ValueError(message)
    return Decimal(cleaned)


def _date_at(raw: str) -> datetime | None:
    """A date the operator typed, as the start of that day, or ``None`` for today."""
    if not raw.strip():
        return None
    return datetime.combine(date.fromisoformat(raw.strip()), datetime.min.time(), tzinfo=UTC)


async def _company(session: Any, raw: str) -> Company | None:
    try:
        identifier = uuid.UUID(raw.strip())
    except ValueError:
        return None
    found: Company | None = await session.get(Company, identifier)
    return found


def _report_label(report: Report, company: Company) -> str:
    return f"{company.name} ({company.ticker}) as of {report.as_of_date:%d %B %Y}"


async def _report(session: Any, raw: str) -> Report | None:
    """The approved report the form named, or ``None`` for none named or none such."""
    try:
        identifier = uuid.UUID(raw.strip())
    except ValueError:
        return None
    found: Report | None = await session.get(Report, identifier)
    # Approved and not withdrawn. A superseded report may still be what the operator read
    # and wrote the thesis against (page specification §13); a withdrawn one was wrong, and
    # nothing is held on its basis (ADR 0116).
    return found if found is not None and found.immutable and not found.is_withdrawn else None


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
