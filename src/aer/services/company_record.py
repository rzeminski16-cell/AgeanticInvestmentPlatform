"""What one account knows about each company, read from the record (page spec §4, §5).

The Companies page is the watchlist in the specification's sense: everything the account
knows about, why it is there, and when it is next looked at. Three populations, kept apart
— ``held``, ``researched, not owned``, ``closed, watching`` — because the middle one is the
record of ideas declined with reasons and is the population most likely to become useful,
and a list that merged the three would hide it.

**Every state here is read, never stored.** Whether a company is held comes from the book's
own walk (ADR 0083); whether its report is current from the report's columns (ADR 0116);
whether its thesis is under review from the open findings (ADR 0078); when it was last
looked at from the newest of the things that looked. A stored ``state`` column would be a
second answer to each of those questions, and the one that drifts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.changes import relative_change
from aer.core.enums import DecisionAction, JobStatus, PremiseStatus, WatchCadence
from aer.db.models import (
    Company,
    Decision,
    Finding,
    Job,
    PriceBar,
    Question,
    Report,
    ResearchRequest,
    Security,
    Thesis,
    Transaction,
    User,
    WatchlistEntry,
    WorkOrder,
)
from aer.services import calculations as calculation_service
from aer.services import decisions as decision_service
from aer.services import portfolio as portfolio_service
from aer.services import theses as thesis_service
from aer.services import thesis_monitor
from aer.services.ask import companies_with_a_record
from aer.services.history import company_for_user as history_company_for_user
from aer.services.portfolio import CLOSED, HoldingRow
from aer.services.reports import current_report
from aer.services.watchlist import entries_for

__all__ = [
    "FILTERS",
    "POPULATIONS",
    "REPORT_STATES",
    "THESIS_STATES",
    "CompanyRecord",
    "PriceGlance",
    "company_of",
    "filtered",
    "record_of",
    "records_for",
    "set_cadence",
]

_log = structlog.get_logger("aer.services.company_record")

# The three populations (§4), and the words each is shown by.
HELD: Final = "held"
RESEARCHED: Final = "researched_not_owned"
CLOSED_WATCHING: Final = "closed_watching"
POPULATIONS: Final[dict[str, str]] = {
    HELD: "held",
    RESEARCHED: "researched, not owned",
    CLOSED_WATCHING: "closed, watching",
}

# What the report is, in the page's three words (§4) — with the run's own states shown
# beside them on the company page (§5.6).
REPORT_STATES: Final[dict[str, str]] = {
    "current": "current",
    "stale": "stale",
    "none": "none",
}
THESIS_STATES: Final[dict[str, str]] = {
    "none": "none",
    "holds": "holds",
    "under_review": "under review",
    "retired": "retired",
}

# The filter row (§4). ``researched`` is never hidden by the default: "all" shows it.
FILTERS: Final[tuple[str, ...]] = (
    "all",
    "held",
    "researched",
    "closed",
    "no_report",
    "overdue",
)

# A report older than the cadence's window is stale (§4's third report state): how often
# the operator asked for the company to be looked at is the honest measure of how old is
# too old. A company nobody set a cadence for is read at the mechanism's ordinary quarter.
STALE_AFTER: Final[dict[str, timedelta]] = {
    WatchCadence.MONTHLY.value: timedelta(days=31),
    WatchCadence.QUARTERLY.value: timedelta(days=92),
}
DEFAULT_STALE_AFTER: Final = timedelta(days=92)


@dataclass(frozen=True, slots=True)
class PriceGlance:
    """The last close the platform holds for the company, and the day's move (§5.1)."""

    close: Decimal
    currency: str
    bar_date: date
    move_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    """One company as the account's record has it."""

    company: Company | None
    name: str
    ticker: str
    exchange: str
    population: str
    holding: HoldingRow | None
    closed_on: date | None
    watch: WatchlistEntry | None
    report: Report | None
    report_state: str
    run: Job | None
    run_state: str
    thesis: Thesis | None
    thesis_state: str
    open_findings: int
    decisions: int
    passed: Decision | None
    questions: int
    last_looked_at: datetime | None
    next_check_at: datetime | None
    cadence: str
    price: PriceGlance | None

    @property
    def key(self) -> str:
        return (
            str(self.company.id) if self.company is not None else f"{self.ticker}.{self.exchange}"
        )

    @property
    def href(self) -> str:
        """Where "open the company" goes: the company page, or the watchlist row for a
        listing the platform has never resolved."""
        if self.company is not None:
            return f"/companies/{self.company.id}"
        return "/watchlist"

    @property
    def population_words(self) -> str:
        return POPULATIONS[self.population]

    @property
    def is_held(self) -> bool:
        return self.population == HELD

    def is_overdue(self, now: datetime) -> bool:
        return self.next_check_at is not None and self.next_check_at < now


# -- Reading the record --------------------------------------------------------------------


async def records_for(
    session: AsyncSession, *, user: User, now: datetime | None = None
) -> list[CompanyRecord]:
    """Every company the account knows about, oldest-looked-at first (§4's *must not*).

    Never looked at sorts first: the point of the page is what has been neglected, and a
    company nobody has looked at is the most neglected of all.
    """
    moment = now or datetime.now(UTC)
    holdings, closed = await _book_positions(session, user=user)
    entries = {
        (entry.ticker.upper(), entry.exchange.upper()): entry
        for entry in await entries_for(session, user_id=user.id, include_withdrawn=False)
    }
    theses = await thesis_service.theses_for(session, user_id=user.id)
    retired = await thesis_service.theses_for(session, user_id=user.id, retired=True)
    findings = await thesis_monitor.findings_for(session, user_id=user.id, open_only=True)
    decisions = await decision_service.decisions_for(session, user_id=user.id)

    companies = {
        company.id: company for company in await companies_with_a_record(session, user=user)
    }
    # A listing the book holds and a company a thesis is about are in the record whether
    # or not anybody has researched them: the neglected ones are the point of the page.
    subjects = [row.security.company_id for row in (*holdings.values(), *closed.values())]
    subjects.extend(row.subject_id for row in (*theses, *retired) if row.subject_kind == "company")
    for subject_id in subjects:
        if subject_id is not None and subject_id not in companies:
            company = await session.get(Company, subject_id)
            if company is not None:
                companies[company.id] = company

    records: list[CompanyRecord] = []
    seen: set[tuple[str, str]] = set()
    for company in companies.values():
        listing = (company.ticker.upper(), company.exchange.upper())
        seen.add(listing)
        records.append(
            await _record(
                session,
                user=user,
                company=company,
                holding=holdings.get(company.id),
                closed_row=closed.get(company.id),
                entry=entries.get(listing),
                theses=theses,
                retired=retired,
                findings=findings,
                decisions=decisions,
                now=moment,
            )
        )
    # A followed listing the platform has never resolved: no company row yet, so it is
    # known by its listing alone — researched-not-owned with no report, until a run
    # resolves it (§4's page action creates exactly this).
    for listing, entry in entries.items():
        if listing in seen:
            continue
        records.append(_unresolved(entry))

    records.sort(key=lambda record: (record.last_looked_at or _NEVER, record.name.lower()))
    return records


async def company_of(session: AsyncSession, *, user: User, company_id: uuid.UUID) -> Company | None:
    """The company, if it is in this account's record at all — or ``None``.

    Wider than :func:`aer.services.history.company_for_user`, which answers for the
    research history alone: a company page is reached from a holding, a thesis and a
    watchlist row as much as from a report (§5's *reached from*), and a listing the book
    holds that nobody has researched yet is exactly the company the page most needs to
    show. One answer for "not yours" and "not there", as everywhere else.
    """
    company = await session.get(Company, company_id)
    if company is None:
        return None
    # `or` short-circuits across the awaits, so the cheap answers stop the walk early.
    known = (
        await history_company_for_user(session, company_id=company.id, user_id=user.id) is not None
        or any(row.id == company.id for row in await companies_with_a_record(session, user=user))
        or await _is_in_the_book(session, user=user, company=company)
        or await _is_a_subject(session, user=user, company=company)
        or await _is_followed(session, user=user, company=company)
    )
    return company if known else None


async def _is_in_the_book(session: AsyncSession, *, user: User, company: Company) -> bool:
    held, closed = await _book_positions(session, user=user)
    return company.id in held or company.id in closed


async def _is_a_subject(session: AsyncSession, *, user: User, company: Company) -> bool:
    theses = [
        *await thesis_service.theses_for(session, user_id=user.id),
        *await thesis_service.theses_for(session, user_id=user.id, retired=True),
    ]
    return any(row.subject_kind == "company" and row.subject_id == company.id for row in theses)


async def _is_followed(session: AsyncSession, *, user: User, company: Company) -> bool:
    listing = (company.ticker.upper(), company.exchange.upper())
    followed = await entries_for(session, user_id=user.id, include_withdrawn=False)
    return any((row.ticker.upper(), row.exchange.upper()) == listing for row in followed)


async def record_of(
    session: AsyncSession, *, user: User, company: Company, now: datetime | None = None
) -> CompanyRecord:
    """One company's record (§5)."""
    moment = now or datetime.now(UTC)
    holdings, closed = await _book_positions(session, user=user)
    entries = {
        (entry.ticker.upper(), entry.exchange.upper()): entry
        for entry in await entries_for(session, user_id=user.id, include_withdrawn=False)
    }
    return await _record(
        session,
        user=user,
        company=company,
        holding=holdings.get(company.id),
        closed_row=closed.get(company.id),
        entry=entries.get((company.ticker.upper(), company.exchange.upper())),
        theses=await thesis_service.theses_for(session, user_id=user.id),
        retired=await thesis_service.theses_for(session, user_id=user.id, retired=True),
        findings=await thesis_monitor.findings_for(session, user_id=user.id, open_only=True),
        decisions=await decision_service.decisions_for(session, user_id=user.id),
        now=moment,
    )


def filtered(records: list[CompanyRecord], show: str, *, now: datetime) -> list[CompanyRecord]:
    """The filter row's reading of the list. An unknown filter shows everything."""
    if show == "held":
        return [record for record in records if record.population == HELD]
    if show == "researched":
        return [record for record in records if record.population == RESEARCHED]
    if show == "closed":
        return [record for record in records if record.population == CLOSED_WATCHING]
    if show == "no_report":
        return [record for record in records if record.report_state == "none"]
    if show == "overdue":
        return [record for record in records if record.is_overdue(now)]
    return list(records)


async def set_cadence(
    session: AsyncSession, *, entry: WatchlistEntry, cadence: str, now: datetime | None = None
) -> WatchlistEntry:
    """Change how often a followed listing is due to be looked at (§4's row action).

    The next check moves with it, from now: a cadence shortened to monthly on a listing
    last read ten weeks ago is due at once, and one lengthened is due a quarter from now
    rather than on a date computed against the old rhythm.

    Raises:
        ValueError: A cadence that is not one of the two the platform reads at.
    """
    if cadence not in STALE_AFTER:
        message = f"{cadence!r} is not a cadence; the platform reads monthly or quarterly."
        raise ValueError(message)
    moment = now or datetime.now(UTC)
    entry.cadence = cadence
    entry.next_check_at = (entry.last_checked_at or moment) + STALE_AFTER[cadence]
    await session.flush()
    _log.info("watchlist.cadence_changed", entry_id=str(entry.id), cadence=cadence)
    return entry


# -- The pieces ------------------------------------------------------------------------------

_NEVER: Final = datetime(1, 1, 1, tzinfo=UTC)


async def _book_positions(
    session: AsyncSession, *, user: User
) -> tuple[dict[uuid.UUID, HoldingRow], dict[uuid.UUID, HoldingRow]]:
    """The book's open holdings and closed positions, by company, as at the last close."""
    book = await portfolio_service.default_book(session, user_id=user.id)
    if book is None:
        return {}, {}
    as_of = await portfolio_service.latest_close(session, portfolio=book)
    view = await portfolio_service.book_as_at(
        session, calculation_service.new_context(), portfolio=book, as_of=as_of
    )
    held: dict[uuid.UUID, HoldingRow] = {}
    closed: dict[uuid.UUID, HoldingRow] = {}
    for row in view.holdings:
        if row.security.company_id is None:
            continue
        if row.problem == CLOSED:
            closed[row.security.company_id] = row
        else:
            held[row.security.company_id] = row
    return held, closed


async def _record(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    holding: HoldingRow | None,
    closed_row: HoldingRow | None,
    entry: WatchlistEntry | None,
    theses: list[Thesis],
    retired: list[Thesis],
    findings: list[Finding],
    decisions: list[Decision],
    now: datetime,
) -> CompanyRecord:
    report = await current_report(session, company_id=company.id)
    run = await _latest_run(session, user=user, company=company)
    thesis = next(
        (row for row in theses if row.subject_kind == "company" and row.subject_id == company.id),
        None,
    )
    was_retired = any(
        row.subject_kind == "company" and row.subject_id == company.id for row in retired
    )
    thesis_findings = [row for row in findings if thesis is not None and row.thesis_id == thesis.id]
    theirs = {
        row.id for row in theses if row.subject_kind == "company" and row.subject_id == company.id
    }
    theirs |= {
        row.id for row in retired if row.subject_kind == "company" and row.subject_id == company.id
    }
    own_decisions = [row for row in decisions if row.thesis_id in theirs]
    passed = next((row for row in own_decisions if row.action is DecisionAction.PASS), None)
    questions = await session.scalar(
        select(func.count())
        .select_from(Question)
        .where(Question.user_id == user.id, Question.company_id == company.id)
    )
    cadence = entry.cadence if entry is not None else ""
    closed_on = await _closed_on(session, user=user, company=company) if closed_row else None

    if holding is not None:
        population = HELD
    elif closed_row is not None:
        population = CLOSED_WATCHING
    else:
        population = RESEARCHED

    return CompanyRecord(
        company=company,
        name=company.name,
        ticker=company.ticker,
        exchange=company.exchange,
        population=population,
        holding=holding,
        closed_on=closed_on,
        watch=entry,
        report=report,
        report_state=_report_state(report, cadence=cadence, now=now),
        run=run,
        run_state=_run_state(run, report),
        thesis=thesis,
        thesis_state=_thesis_state(thesis, thesis_findings, was_retired=was_retired),
        open_findings=len(thesis_findings),
        decisions=len(own_decisions),
        passed=passed,
        questions=int(questions or 0),
        last_looked_at=await _last_looked_at(
            session,
            user=user,
            company=company,
            report=report,
            run=run,
            entry=entry,
            findings=thesis_findings,
            decisions=own_decisions,
        ),
        next_check_at=entry.next_check_at if entry is not None else None,
        cadence=cadence,
        price=await _price_glance(session, company=company),
    )


def _unresolved(entry: WatchlistEntry) -> CompanyRecord:
    return CompanyRecord(
        company=None,
        name=entry.company_name,
        ticker=entry.ticker,
        exchange=entry.exchange,
        population=RESEARCHED,
        holding=None,
        closed_on=None,
        watch=entry,
        report=None,
        report_state="none",
        run=None,
        run_state="",
        thesis=None,
        thesis_state="none",
        open_findings=0,
        decisions=0,
        passed=None,
        questions=0,
        last_looked_at=entry.last_checked_at or entry.followed_at,
        next_check_at=entry.next_check_at,
        cadence=entry.cadence,
        price=None,
    )


def _report_state(report: Report | None, *, cadence: str, now: datetime) -> str:
    if report is None:
        return "none"
    dated = report.approved_at or datetime.combine(
        report.as_of_date, datetime.min.time(), tzinfo=UTC
    )
    window = STALE_AFTER.get(cadence, DEFAULT_STALE_AFTER)
    return "stale" if now - dated > window else "current"


def _run_state(run: Job | None, report: Report | None) -> str:
    """The run's own word beside the report's (§5.6): running, refused, or nothing."""
    if run is None:
        return ""
    if run.status in {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.AWAITING_APPROVAL,
        JobStatus.PAUSED,
    }:
        return "running"
    if run.status is JobStatus.BUDGET_EXCEEDED:
        return "stopped"
    if run.status is JobStatus.SUCCEEDED or (report is not None and report.job_id == run.id):
        return ""
    return "refused"


def _thesis_state(thesis: Thesis | None, findings: list[Finding], *, was_retired: bool) -> str:
    if thesis is None:
        return "retired" if was_retired else "none"
    if any(row.status is PremiseStatus.CONTRADICTED or row.opens_gate for row in findings):
        return "under_review"
    return "holds"


async def _latest_run(session: AsyncSession, *, user: User, company: Company) -> Job | None:
    found: Job | None = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .join(ResearchRequest, ResearchRequest.id == WorkOrder.id)
        .where(WorkOrder.user_id == user.id, ResearchRequest.company_id == company.id)
        .order_by(Job.started_at.desc().nullslast())
        .limit(1)
    )
    return found


async def _closed_on(session: AsyncSession, *, user: User, company: Company) -> date | None:
    """The last trade date of the listing that closed the position."""
    book = await portfolio_service.default_book(session, user_id=user.id)
    if book is None:
        return None
    found: date | None = await session.scalar(
        select(func.max(Transaction.trade_date))
        .join(Security, Security.id == Transaction.security_id)
        .where(Transaction.portfolio_id == book.id, Security.company_id == company.id)
    )
    return found


async def _last_looked_at(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    report: Report | None,
    run: Job | None,
    entry: WatchlistEntry | None,
    findings: list[Finding],
    decisions: list[Decision],
) -> datetime | None:
    """The newest of the moments something looked at the company."""
    moments: list[datetime] = []
    if report is not None and report.approved_at is not None:
        moments.append(report.approved_at)
    if run is not None:
        moments.extend(moment for moment in (run.finished_at, run.started_at) if moment is not None)
    if entry is not None:
        moments.extend(
            moment for moment in (entry.last_checked_at, entry.followed_at) if moment is not None
        )
    moments.extend(row.created_at for row in findings)
    moments.extend(row.judgement.held_at for row in decisions)
    asked = await session.scalar(
        select(func.max(Question.asked_at)).where(
            Question.user_id == user.id, Question.company_id == company.id
        )
    )
    if asked is not None:
        moments.append(asked)
    return max(moments) if moments else None


async def _price_glance(session: AsyncSession, *, company: Company) -> PriceGlance | None:
    """The last two closes on the company's listing, or ``None`` for no price series."""
    security = await session.scalar(
        select(Security)
        .where(Security.company_id == company.id, Security.is_active.is_(True))
        .order_by(Security.created_at)
        .limit(1)
    )
    if security is None:
        return None
    bars = list(
        await session.scalars(
            select(PriceBar)
            .where(PriceBar.security_id == security.id)
            .order_by(PriceBar.bar_date.desc())
            .limit(2)
        )
    )
    if not bars:
        return None
    latest, *earlier = bars
    move = relative_change(earlier[0].close, latest.close) if earlier else None
    return PriceGlance(
        close=latest.close,
        currency=security.quote_currency,
        bar_date=latest.bar_date,
        move_pct=move,
    )
