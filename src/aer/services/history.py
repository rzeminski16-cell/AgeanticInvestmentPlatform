"""What was concluded about a company before, and what changed — read, never re-judged.

Everything here is a deterministic read of approved reports and their runs' recorded
section rows. **Only approved reports count as history**: a draft was never agreed to and
a rejected run was explicitly declined, so neither may quietly become "what we used to
think". The filter is ``Report.immutable`` — the flag only an approval can set.

The prior-run comparison section (position 900, ``token_budget = 0``) is built from this
module. Handing it to a model would be asking for a paraphrase of rows it cannot improve
on, and every paraphrase of a prior conclusion is a chance to move it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import TerminalMethod
from aer.calc.engine import CalculationContext
from aer.calc.outcomes import (
    MEASURABLE_DRIVERS,
    UNMEASURABLE_JUDGEMENTS,
    assumption_delta,
    realised_driver,
)
from aer.calc.statements import StatementSet, assemble
from aer.calc.units import Quantity, SourceRef
from aer.db.models import (
    Assumption,
    Calculation,
    Company,
    Report,
    ReportSection,
    ResearchRequest,
    SectionStatus,
    WorkOrder,
)
from aer.services.analysis import annual_facts, quantities_of
from aer.services.calculations import new_context, persist_context
from aer.services.subject import name_of

__all__ = [
    "PRIOR_DIGEST_LIMIT",
    "AssumptionOutcome",
    "CatalystOutcome",
    "DriverAccuracy",
    "PriorDigest",
    "PriorReportView",
    "approved_reports_for",
    "assumption_outcomes_for",
    "catalyst_outcomes_for",
    "company_for_user",
    "driver_accuracy_for",
    "prior_comparison_content",
    "prior_digest_for",
    "prior_risks_for",
    "timing_deadline",
    "valuation_history_for",
]

_log = structlog.get_logger("aer.services.history")

# How prior free-text catalyst timings become dates, where they honestly can. Anything
# these shapes do not match stays "undated" rather than being guessed onto a calendar.
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR = re.compile(r"^(?:FY\s*)?(\d{4})$", re.IGNORECASE)
_QUARTER = re.compile(r"^Q([1-4])\s*(\d{4})$", re.IGNORECASE)
_HALF = re.compile(r"^H([12])\s*(\d{4})$", re.IGNORECASE)

_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


@dataclass(frozen=True, slots=True)
class PriorReportView:
    """One approved report, as the history surfaces show it."""

    report_id: uuid.UUID
    job_id: uuid.UUID
    as_of_date: date
    rating: str | None
    confidence: float | None
    valuation_low: str | None
    valuation_high: str | None
    valuation_currency: str | None

    @property
    def valuation_range(self) -> str:
        if self.valuation_low is None or self.valuation_high is None:
            return "not recorded"
        currency = f" {self.valuation_currency}" if self.valuation_currency else ""
        return f"{self.valuation_low} to {self.valuation_high}{currency} per share"


@dataclass(frozen=True, slots=True)
class CatalystOutcome:
    """A prior catalyst, and whether its window has closed by the new as-of date."""

    label: str
    expected_timing: str
    rationale: str
    status: str  # "passed" | "pending" | "undated"
    prior_report_id: uuid.UUID


async def company_for_user(
    session: AsyncSession, *, company_id: uuid.UUID, user_id: uuid.UUID
) -> Company | None:
    """The company, if this user has researched it.

    Companies are shared rows keyed by listing, so visibility follows from the user's own
    requests: a company nobody asked this platform about on this account does not exist
    for them. One answer for "not yours" and "not there", as everywhere else.
    """
    company = await session.get(Company, company_id)
    if company is None:
        return None
    theirs = await session.scalar(
        select(ResearchRequest.id)
        .join(WorkOrder, WorkOrder.id == ResearchRequest.id)
        .where(
            WorkOrder.user_id == user_id,
            ResearchRequest.ticker == company.ticker,
            ResearchRequest.exchange == company.exchange,
        )
        .limit(1)
    )
    return company if theirs is not None else None


async def approved_reports_for(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    before: date | None = None,
) -> list[Report]:
    """Every approved report on this company, newest first.

    ``before`` bounds the history for a comparison: a run comparing itself against
    reports as-of its own date or later would be comparing against the future.
    """
    query = (
        select(Report)
        .where(Report.company_id == company_id, Report.immutable.is_(True))
        .order_by(Report.as_of_date.desc(), Report.created_at.desc())
    )
    if before is not None:
        query = query.where(Report.as_of_date < before)
    return list(await session.scalars(query))


def report_view(report: Report) -> PriorReportView:
    return PriorReportView(
        report_id=report.id,
        job_id=report.job_id,
        as_of_date=report.as_of_date,
        rating=report.rating,
        confidence=report.confidence,
        valuation_low=(_trim(report.valuation_low) if report.valuation_low is not None else None),
        valuation_high=(
            _trim(report.valuation_high) if report.valuation_high is not None else None
        ),
        valuation_currency=report.valuation_currency,
    )


# The states an assumption outcome can be in. Strings rather than an enum because they
# travel into section content (JSONB) and a renderer reads them as text.
MEASURED: Final = "measured"
NOT_YET_OBSERVABLE: Final = "not_yet_observable"
NOT_MEASURABLE: Final = "not_measurable"
SKIPPED: Final = "skipped"


@dataclass(frozen=True, slots=True)
class AssumptionOutcome:
    """One confirmed assumption of a prior run, measured against what was later filed.

    ``assumed``, ``actual`` and ``delta`` are rendered strings — the comparison section
    and the vault read them, and the recorded calculations behind them carry the exact
    values. ``basis`` states which fiscal year was measured and how, because a delta
    whose provenance a reader cannot restate is a number they cannot argue with.
    """

    name: str
    status: str
    assumed: str
    basis: str
    actual: str | None = None
    delta: str | None = None
    prior_report_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class DriverAccuracy:
    """One driver's record across every measured prior run of a company."""

    name: str
    measured: int
    mean_absolute_delta: str


async def assumption_outcomes_for(
    session: AsyncSession,
    context: CalculationContext,
    *,
    prior: Report,
    as_of: date | None,
    point_in_time: bool,
) -> list[AssumptionOutcome]:
    """The prior run's confirmed assumptions, each measured, waiting, or explained.

    The measured year is the **first full fiscal year after the prior run's as-of date**
    — the first year the forecast actually forecast. An assumption held flat across five
    years is one number; its first realised year is the cleanest single measurement, and
    the basis says exactly that rather than implying more.

    Every realised value and delta goes through ``context`` as a traced calculation, so a
    caller that puts these figures in front of a reader persists the context and the
    numbers are recorded calculations (invariant 3). Only **approved** assumptions are
    measured: a proposal nobody confirmed was never the run's forecast.
    """
    if prior.company_id is None:
        return []
    assumptions = list(
        await session.scalars(
            select(Assumption)
            .where(Assumption.request_id == prior.request_id, Assumption.approved.is_(True))
            .order_by(Assumption.name)
        )
    )
    if not assumptions:
        return []

    measured_year, previous_year, year_label = await _measured_year(
        session,
        context,
        company_id=prior.company_id,
        after=prior.as_of_date,
        as_of=as_of,
        point_in_time=point_in_time,
    )

    ordered = sorted(
        assumptions,
        key=lambda row: (
            MEASURABLE_DRIVERS.index(row.name)
            if row.name in MEASURABLE_DRIVERS
            else len(MEASURABLE_DRIVERS)
        ),
    )
    outcomes: list[AssumptionOutcome] = []
    for row in ordered:
        outcomes.append(
            _outcome_for(
                context,
                row,
                prior=prior,
                measured_year=measured_year,
                previous_year=previous_year,
                year_label=year_label,
            )
        )
    return outcomes


def _outcome_for(
    context: CalculationContext,
    row: Assumption,
    *,
    prior: Report,
    measured_year: StatementSet | None,
    previous_year: StatementSet | None,
    year_label: str,
) -> AssumptionOutcome:
    assumed_text = _trim(row.value)
    if row.name in UNMEASURABLE_JUDGEMENTS:
        return AssumptionOutcome(
            name=row.name,
            status=NOT_MEASURABLE,
            assumed=assumed_text,
            basis=f"cannot be measured from filings: {UNMEASURABLE_JUDGEMENTS[row.name]}",
            prior_report_id=prior.id,
        )
    if row.name not in MEASURABLE_DRIVERS:
        return AssumptionOutcome(
            name=row.name,
            status=SKIPPED,
            assumed=assumed_text,
            basis=(
                f"skipped: the concept map cannot place an assumption named {row.name!r}, "
                "so no filed line answers it"
            ),
            prior_report_id=prior.id,
        )
    if measured_year is None:
        return AssumptionOutcome(
            name=row.name,
            status=NOT_YET_OBSERVABLE,
            assumed=assumed_text,
            basis=(
                "not yet observable: no full fiscal year after "
                f"{prior.as_of_date.isoformat()} is in the store, so the first forecast "
                "year has not been filed"
            ),
            prior_report_id=prior.id,
        )

    actual = realised_driver(context, row.name, statements=measured_year, previous=previous_year)
    if isinstance(actual, str):
        return AssumptionOutcome(
            name=row.name,
            status=SKIPPED,
            assumed=assumed_text,
            basis=f"skipped for {year_label}: {actual}",
            prior_report_id=prior.id,
        )

    assumed_quantity = Quantity(
        value=row.value,
        unit=actual.unit,
        source=SourceRef.assumption(row.id, label=row.name),
    )
    delta = assumption_delta(context, assumed=assumed_quantity, actual=actual)
    return AssumptionOutcome(
        name=row.name,
        status=MEASURED,
        assumed=assumed_text,
        actual=_trim(_rounded_outcome(actual.value)),
        delta=_trim(_rounded_outcome(delta.value)),
        basis=(
            f"realised over {year_label}, the first full fiscal year after the prior "
            "run's as-of date, from the same filed lines the proposal derivation used"
        ),
        prior_report_id=prior.id,
    )


async def _measured_year(
    session: AsyncSession,
    context: CalculationContext,
    *,
    company_id: uuid.UUID,
    after: date,
    as_of: date | None,
    point_in_time: bool,
) -> tuple[StatementSet | None, StatementSet | None, str]:
    """The first full fiscal year after ``after``, assembled, with its predecessor.

    Selection and assembly go through :func:`aer.services.analysis.annual_facts` and
    :func:`~aer.calc.statements.assemble` — the exact path the proposal derivations used —
    so assumed and actual are commensurable by construction.
    """
    facts = await annual_facts(
        session, company_id=company_id, as_of=as_of, point_in_time=point_in_time
    )
    if as_of is not None:
        facts = {period: rows for period, rows in facts.items() if period <= as_of}
    future = sorted(period for period in facts if period > after)
    if not future:
        return None, None, ""
    target = future[0]
    prior_periods = sorted(period for period in facts if period < target)
    previous = assemble(context, quantities_of(facts[prior_periods[-1]])) if prior_periods else None
    measured = assemble(context, quantities_of(facts[target]))
    return measured, previous, f"the fiscal year ending {target.isoformat()}"


def _rounded_outcome(value: Decimal) -> Decimal:
    """Six places, matching the proposal derivations' own rounding."""
    return value.quantize(Decimal("0.000001"))


async def driver_accuracy_for(
    session: AsyncSession, *, company_id: uuid.UUID
) -> list[DriverAccuracy]:
    """Each driver's measured count and mean absolute delta, over every prior run.

    Read for the company note, which is an evergreen projection: the measurement uses
    everything the store holds (``as_of=None``), and the context is deliberately thrown
    away — nothing here reaches a report, and the recorded copies live with the runs
    whose comparison sections measured the same deltas.
    """
    context = CalculationContext(code_version="projection")
    deltas: dict[str, list[Decimal]] = {}
    for prior in await approved_reports_for(session, company_id=company_id):
        outcomes = await assumption_outcomes_for(
            session, context, prior=prior, as_of=None, point_in_time=False
        )
        for outcome in outcomes:
            if outcome.status == MEASURED and outcome.delta is not None:
                deltas.setdefault(outcome.name, []).append(abs(Decimal(outcome.delta)))

    accuracy: list[DriverAccuracy] = []
    for name in MEASURABLE_DRIVERS:
        observed = deltas.get(name)
        if not observed:
            continue
        mean = (sum(observed, Decimal(0)) / Decimal(len(observed))).quantize(Decimal("0.000001"))
        accuracy.append(
            DriverAccuracy(name=name, measured=len(observed), mean_absolute_delta=_trim(mean))
        )
    return accuracy


# How many prior reports feed forward into a new run's planner (K2). Three, not "all":
# the digest goes into a prompt, and a company researched twenty times would otherwise
# drown the request it is meant to inform. The newest views are the ones a planner would
# actually re-examine; anything older is in the vault for a person to read.
PRIOR_DIGEST_LIMIT: Final = 3


@dataclass(frozen=True, slots=True)
class PriorDigest:
    """One prior approved report, compact enough to hand a planner as hypothesis material.

    Every field is a *rendered string* rather than a value: the digest exists to be read,
    not computed with, and a planner handed a bare Decimal is one prompt-edit away from
    quoting it as a figure. The catalyst lines carry the calendar status already judged
    against the new run's as-of date, so the model is never asked to do date arithmetic.
    """

    report_id: uuid.UUID
    as_of_date: date
    rating: str
    confidence: str
    valuation_range: str
    named_risks: tuple[str, ...]
    catalyst_lines: tuple[str, ...]


async def prior_digest_for(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    before: date,
    limit: int = PRIOR_DIGEST_LIMIT,
) -> list[PriorDigest]:
    """The last ``limit`` approved reports before ``before``, newest first. Rows only.

    Built for the planner's feed-forward (K2): prior research may shape which questions a
    plan asks and may never support a claim, so what leaves here is a summary of recorded
    conclusions — never an excerpt of evidence, which would invite citing it.
    """
    digests: list[PriorDigest] = []
    for prior in (await approved_reports_for(session, company_id=company_id, before=before))[
        :limit
    ]:
        view = report_view(prior)
        catalyst_lines = tuple(
            f"{outcome.label} (expected {outcome.expected_timing}) — "
            f"{_CATALYST_STATUS[outcome.status]}"
            for outcome in await catalyst_outcomes_for(session, prior=prior, as_of=before)
        )
        risks = tuple(
            f"{risk['risk']}: {risk['why_it_matters']}"
            for risk in await prior_risks_for(session, prior=prior)
        )
        digests.append(
            PriorDigest(
                report_id=prior.id,
                as_of_date=prior.as_of_date,
                rating=view.rating or "no view reached",
                confidence=(
                    f"{view.confidence:.0%}" if view.confidence is not None else "not recorded"
                ),
                valuation_range=view.valuation_range,
                named_risks=risks,
                catalyst_lines=catalyst_lines,
            )
        )
    return digests


async def valuation_history_for(
    session: AsyncSession, *, company_id: uuid.UUID
) -> list[PriorReportView]:
    """The approved reports oldest-first, for a chart whose x axis is time."""
    reports = await approved_reports_for(session, company_id=company_id)
    return [report_view(report) for report in reversed(reports)]


# -- Reading a prior run's own sections back ---------------------------------------------------


# What makes an item a catalyst or a risk: the fields it carries, never the section it
# sits in. The same convention as the renderer's citation keys — sections are rows, so a
# reader that named one would make the next section a code change (and the source scan in
# `tests/test_report_sections.py` holds every module to that). A custom section whose
# items carry these fields joins history for free, exactly as it gains citations.
CATALYST_FIELDS = frozenset({"label", "expected_timing", "rationale"})
RISK_FIELDS = frozenset({"risk", "why_it_matters"})


async def catalyst_outcomes_for(
    session: AsyncSession, *, prior: Report, as_of: date
) -> list[CatalystOutcome]:
    """The prior report's catalysts, each marked passed, pending or undated.

    The timing is the prior analyst's free text; it becomes a date only where one of the
    unambiguous shapes matches (ISO date, year, quarter, half). "Passed" is a statement
    about the calendar — the window closed before the new as-of date — never about
    whether the event happened, which no query can know.
    """
    outcomes: list[CatalystOutcome] = []
    for item in await _items_shaped(session, job_id=prior.job_id, required=CATALYST_FIELDS):
        timing = str(item.get("expected_timing", ""))
        deadline = timing_deadline(timing)
        status = "undated" if deadline is None else ("passed" if deadline < as_of else "pending")
        outcomes.append(
            CatalystOutcome(
                label=str(item.get("label", "")),
                expected_timing=timing,
                rationale=str(item.get("rationale", "")),
                status=status,
                prior_report_id=prior.id,
            )
        )
    return outcomes


async def prior_risks_for(session: AsyncSession, *, prior: Report) -> list[dict[str, str]]:
    """The prior report's key risks, verbatim, each carrying the prior report id."""
    risks: list[dict[str, str]] = []
    for item in await _items_shaped(session, job_id=prior.job_id, required=RISK_FIELDS):
        risks.append(
            {
                "risk": str(item.get("risk", "")),
                "why_it_matters": str(item.get("why_it_matters", "")),
                "prior_report_id": str(prior.id),
            }
        )
    return risks


async def _items_shaped(
    session: AsyncSession, *, job_id: uuid.UUID, required: frozenset[str]
) -> list[dict[str, Any]]:
    """Every list item across the run's generated sections carrying the required fields.

    Sections in position order, items in their list order, so the walk is deterministic.
    """
    rows = list(
        await session.scalars(
            select(ReportSection)
            .where(
                ReportSection.job_id == job_id,
                ReportSection.status == SectionStatus.GENERATED,
            )
            .order_by(ReportSection.position)
        )
    )
    found: list[dict[str, Any]] = []
    for row in rows:
        content = row.content if isinstance(row.content, dict) else {}
        for value in content.values():
            if not isinstance(value, list):
                continue
            found.extend(
                item for item in value if isinstance(item, dict) and required <= item.keys()
            )
    return found


def timing_deadline(text: str) -> date | None:
    """The last day a stated timing could still be honoured, or ``None``.

    Public because the Obsidian exporter dates catalyst notes with exactly this parse:
    two readers of the same free text must not disagree about when its window closes.
    """
    cleaned = text.strip()
    if match := _ISO.match(cleaned):
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    if match := _YEAR.match(cleaned):
        return date(int(match.group(1)), 12, 31)
    if match := _QUARTER.match(cleaned):
        month, day = _QUARTER_END[int(match.group(1))]
        return date(int(match.group(2)), month, day)
    if match := _HALF.match(cleaned):
        return (
            date(int(match.group(2)), 6, 30)
            if match.group(1) == "1"
            else date(int(match.group(2)), 12, 31)
        )
    return None


# -- The comparison section --------------------------------------------------------------------


async def prior_comparison_content(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    request: ResearchRequest,
) -> dict[str, Any]:
    """The ``prior_research_comparison`` content, from rows alone.

    A first run states so in one sentence. Later runs compare the most recent prior
    approved report's view, confidence and valuation range against this run's recorded
    state, then walk every prior report's catalysts (dated against this run's as-of) and
    key risks — each row carrying the ``prior_report_id`` a reader can follow.
    """
    company = await session.scalar(
        select(Company).where(
            Company.ticker == request.ticker, Company.exchange == request.exchange
        )
    )
    priors = (
        await approved_reports_for(
            session, company_id=company.id, before=request.work_order.as_of_date
        )
        if company is not None
        else []
    )
    # The filer's own name for itself, not the one typed into the form (gap A67). This
    # sentence is where an operator's typo reached a live report, three lines under a
    # front matter that had the resolved name right.
    subject = name_of(request, company)

    if not priors:
        return {
            "commentary": (
                f"This is the first research run for {subject} "
                f"({request.ticker}). No prior approved report exists to compare against."
            ),
        }

    latest = report_view(priors[0])
    comparisons: list[dict[str, str]] = [
        {
            "aspect": "Non-binding view",
            "prior": latest.rating or "no view reached",
            "current": "Recorded at this run's approval.",
            "prior_report_id": str(latest.report_id),
        },
        {
            "aspect": "Confidence",
            "prior": (
                f"{latest.confidence:.0%}" if latest.confidence is not None else "not recorded"
            ),
            "current": "Recorded at this run's approval.",
            "prior_report_id": str(latest.report_id),
        },
        {
            "aspect": "Valuation range",
            "prior": latest.valuation_range,
            "current": await _current_valuation(session, job_id=job_id),
            "prior_report_id": str(latest.report_id),
        },
    ]

    # Every realised driver and delta below is a traced calculation in this context,
    # persisted against the run before the content leaves this function — a figure in a
    # report must be a recorded calculation (invariant 3), and these reach the report.
    outcome_context = new_context()

    for prior in priors:
        for outcome in await catalyst_outcomes_for(
            session, prior=prior, as_of=request.work_order.as_of_date
        ):
            comparisons.append(
                {
                    "aspect": f"Catalyst — {outcome.label}",
                    "prior": f"Expected {outcome.expected_timing}: {outcome.rationale}",
                    "current": _CATALYST_STATUS[outcome.status],
                    "prior_report_id": str(outcome.prior_report_id),
                }
            )
        for risk in await prior_risks_for(session, prior=prior):
            comparisons.append(
                {
                    "aspect": f"Risk — {risk['risk']}",
                    "prior": risk["why_it_matters"],
                    "current": "Carried into this run's key-risks review.",
                    "prior_report_id": risk["prior_report_id"],
                }
            )
        for measured in await assumption_outcomes_for(
            session,
            outcome_context,
            prior=prior,
            as_of=request.work_order.as_of_date,
            point_in_time=request.work_order.point_in_time,
        ):
            comparisons.append(_assumption_row(measured))

    if outcome_context.records:
        await persist_context(session, outcome_context, job_id=job_id)

    _log.info(
        "history.comparison_built",
        job_id=str(job_id),
        priors=len(priors),
        rows=len(comparisons),
    )
    return {
        "commentary": (
            f"{len(priors)} prior approved report(s) exist for {subject} "
            f"({request.ticker}); the most recent is as of {latest.as_of_date.isoformat()}. "
            "Every row below names the prior report it was read from."
        ),
        "comparisons": comparisons,
    }


def _assumption_row(outcome: AssumptionOutcome) -> dict[str, str]:
    """One outcome as a comparison row — the existing shape, so no contract changes."""
    if outcome.status == MEASURED:
        current = (
            f"Realised {outcome.actual}; delta {outcome.delta} against the assumption "
            f"({outcome.basis})."
        )
    else:
        current = outcome.basis[:1].upper() + outcome.basis[1:] + "."
    return {
        "aspect": f"Assumption — {outcome.name.replace('_', ' ')}",
        "prior": f"Confirmed at {outcome.assumed}, held flat across the forecast.",
        "current": current,
        "prior_report_id": str(outcome.prior_report_id or ""),
    }


_CATALYST_STATUS = {
    "passed": "The stated window has passed by this run's as-of date.",
    "pending": "Still within its stated window at this run's as-of date.",
    "undated": "No calendar date could be read from the stated timing.",
}


async def _current_valuation(session: AsyncSession, *, job_id: uuid.UUID) -> str:
    """This run's base-case per-share range, from its own recorded rows, or honesty.

    The same read the football field makes: the base case's two terminal methods bound
    the range. Absent rows mean the run has not valued the business (yet, or at all),
    and the row says so rather than borrowing a number from anywhere else.
    """
    rows = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job_id, Calculation.name == "value_per_share")
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )
    found = []
    for method in (TerminalMethod.GORDON_GROWTH, TerminalMethod.EXIT_MULTIPLE):
        matching = [
            row
            for row in rows
            if str(row.parameters.get("case", "base")) == "base"
            and str(row.parameters.get("method", "")) == method.value
        ]
        if matching:
            found.append(matching[-1])
    if not found:
        return "Not computed at the time this section was drafted."
    low = _trim(min(row.output_value for row in found))
    high = _trim(max(row.output_value for row in found))
    unit = found[0].output_unit
    if low == high:
        return f"{low} {unit} (one terminal method recorded)"
    return f"{low} to {high} {unit}"


def _trim(value: Decimal) -> str:
    """``241.500000000000`` reads as false precision; the stored scale is not a claim."""
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text
