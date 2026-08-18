"""The run's financial analysis: stored facts in, recorded calculations out.

**The bridge that was missing.** Phase 3 built the statement assembler, the ratio suite and
the earnings-quality signals, tested them thoroughly, and nothing ever called them. Until
this module existed, a run's ``calculate`` step produced exactly one number — a revenue
CAGR — so the balance-sheet, cash-flow and earnings-quality sections had one figure between
them to write about, and the valuation page said the run had produced nothing. All of it
was true, and none of it was a missing feature: it was a missing call.

What happens here is ordinary orchestration. Facts are read from the run's own tables,
grouped into the periods they describe, and handed to the pure kernel; every derivation the
kernel performs lands in one :class:`~aer.calc.engine.CalculationContext` and is persisted
as a traceable row. No arithmetic lives in this module — that is the whole point of
:mod:`aer.calc` being pure — and no figure reaches a report except as a recorded
calculation over a stored fact.

**One filing's numbers are one period's numbers.** A company restates, amends and refiles,
so the same concept for the same period arrives more than once. The winner is the most
recently *filed* observation not later than the as-of date, which is the same rule
:func:`aer.sources.sec.pit.select_point_in_time` applies at acquisition and is applied again
here because the store accumulates across runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.calc.quality import QualitySignal, assess_quality
from aer.calc.ratios import RatioResult, compute_ratios
from aer.calc.statements import StatementSet, assemble
from aer.calc.units import CalculationError, Quantity, SourceRef, Unit
from aer.db.models import FinancialFact, ResearchRequest

__all__ = ["FORECAST_CONCEPTS", "AnalysisOutcome", "PeriodAnalysis", "analyse_company"]

_log = structlog.get_logger("aer.services.analysis")

# How many annual periods to analyse, most recent first. Enough for a trend and for the
# paired earnings-quality signals; bounded because every period is a full statement
# assembly and the ledger is written in one transaction.
MAX_PERIODS: Final = 5

# The fiscal period a full-year statement carries. Quarterly facts describe three months and
# would silently mix with annual ones in the same statement — an income statement built from
# one quarter's revenue and a year's operating income is not wrong so much as meaningless.
ANNUAL: Final = "FY"

# How long a duration has to be to be a year. **The label is not enough**, which is the whole
# of gap A45: `fiscal_period` is EDGAR's own `fp`, and `fp` describes the *filing*, not the
# fact — a 10-K carries its fourth-quarter durations and its cover-page instants under `FY`
# alongside the twelve-month figures. 52/53-week calendars run 364 or 371 days and a leap
# year 366, so the band is wide; a transition period after a fiscal-year change is months,
# and is not a year however it is labelled.
_ANNUAL_DAYS_MIN: Final = 300
_ANNUAL_DAYS_MAX: Final = 400

# What a driver needs before it can be averaged or grown across. Stated here so the log's
# "thin" list means the same thing the gate's refusal will mean; the derivations own the
# rule and `aer.services.assumption_proposals` states it again for its own messages.
_MIN_PERIODS_TO_AVERAGE: Final = 2

# The concepts a forecast's drivers are derived from (`aer.services.assumption_proposals`).
# Coverage of *these* is what decides whether the assumptions gate is a formality or nine
# boxes for an operator to type into, so the run measures it rather than discovering it at
# the gate — gap A46.
FORECAST_CONCEPTS: Final[tuple[str, ...]] = (
    "revenue",
    "operating_income",
    "capital_expenditure",
    "depreciation_and_amortisation",
    "current_assets",
    "current_liabilities",
    "income_tax_expense",
    "pre_tax_income",
)


@dataclass(frozen=True, slots=True)
class PeriodAnalysis:
    """One period: its statements, its ratios, and its quality signals."""

    period_end: date
    fiscal_year: int | None
    statements: StatementSet
    ratios: tuple[RatioResult, ...]
    quality: tuple[QualitySignal, ...]

    @property
    def computed_ratios(self) -> tuple[RatioResult, ...]:
        return tuple(item for item in self.ratios if item.quantity is not None)

    @property
    def failed_identities(self) -> tuple[Any, ...]:
        return self.statements.failed_identities


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """What the analysis came to, and what it could not reach.

    ``skipped`` is not an error list. A company with one filed year has no prior period and
    therefore no comparison signals; a run whose extraction found no annual facts has
    nothing to assemble. Both are ordinary, and both must be *said* rather than left as an
    absence a reader has to infer.
    """

    periods: tuple[PeriodAnalysis, ...] = ()
    skipped: tuple[str, ...] = ()
    unplaced_concepts: tuple[str, ...] = ()
    calculation_ids: tuple[uuid.UUID, ...] = field(default=())

    @property
    def latest(self) -> PeriodAnalysis | None:
        return self.periods[0] if self.periods else None

    @property
    def forecast_coverage(self) -> dict[str, int]:
        """How many assembled periods carry each concept a forecast driver needs.

        Gap A46: coverage was invisible until the assumptions gate, where it surfaced as
        "available for 1 period(s)" against a company that reports the line every year —
        by which point the run had spent its money and the operator had nine boxes to fill.
        A driver needs two periods to average or to grow across, so a count below two here
        is the gate's outcome, known at the step that could still be fixed.
        """
        return {
            concept: sum(
                1
                for period in self.periods
                if any(
                    concept in statement.present_concepts
                    for statement in period.statements.statements
                )
            )
            for concept in FORECAST_CONCEPTS
        }

    def as_dict(self) -> dict[str, Any]:
        """The step's recorded output. Counts and keys, never the figures themselves —
        those are calculation rows, and duplicating one into a step's JSON would create a
        second copy of a number with no formula behind it."""
        return {
            "periods": [
                {
                    "period_end": period.period_end.isoformat(),
                    "fiscal_year": period.fiscal_year,
                    "lines": sum(
                        len(statement.present_concepts)
                        for statement in period.statements.statements
                    ),
                    "ratios": len(period.computed_ratios),
                    "quality_signals": sum(
                        1 for signal in period.quality if signal.quantity is not None
                    ),
                    "failed_identities": [
                        check.name for check in period.statements.failed_identities
                    ],
                }
                for period in self.periods
            ],
            "calculations": len(self.calculation_ids),
            "skipped": list(self.skipped),
            "unplaced_concepts": list(self.unplaced_concepts),
            # Recorded on the step rather than only logged, so "why did the gate ask me for
            # six drivers?" is answerable from the run's own rows afterwards (A46).
            "forecast_coverage": self.forecast_coverage,
        }


async def analyse_company(
    session: AsyncSession,
    context: CalculationContext,
    *,
    company_id: uuid.UUID,
    request: ResearchRequest,
    max_periods: int = MAX_PERIODS,
) -> AnalysisOutcome:
    """Assemble statements, ratios and quality signals for a company's recent years.

    Args:
        context: The ledger every derivation is recorded in. Supplied rather than created so
            a caller can put this and its other calculations in one transaction — a run
            whose statements persisted and whose ratios did not would be a run with a
            traceable half of an answer.

    Nothing raises for want of data. A concept a filing does not report leaves its line
    absent with the reason attached, a ratio that needs it says which concept it wanted, and
    the outcome carries what was skipped. The one thing that does propagate is a unit
    mismatch: two lines in different currencies is a mapping error, and the module whose job
    is to notice problems is the wrong place to hide one.
    """
    facts = await _annual_facts(session, company_id=company_id, request=request)
    if not facts:
        return AnalysisOutcome(
            skipped=(
                "No annual facts are stored for this company at or before the as-of date, "
                "so no statements could be assembled.",
            )
        )

    ordered = sorted(facts, reverse=True)[:max_periods]
    periods: list[PeriodAnalysis] = []
    unplaced: set[str] = set()

    # Oldest first, so each period's `prior` is the one already built. The paired quality
    # signals compare against the preceding year and there is no other way to have it.
    previous: StatementSet | None = None
    for period_end in reversed(ordered):
        rows = facts[period_end]
        fiscal_year = next((row.fiscal_year for row in rows if row.fiscal_year), None)
        # Every derivation this pass strikes — subtotals, ratios, quality signals — is a
        # figure *of this fiscal year*, and the stamp is what lets a reader (and the
        # consistency check) tell an annual ratio from the quarterly fact beside it.
        label = f"FY{fiscal_year}" if fiscal_year else period_end.isoformat()
        start = min((row.period_start for row in rows if row.period_start), default=None)
        with context.period(label, start=start, end=period_end):
            statements = assemble(context, _quantities(rows))
            unplaced.update(statements.unplaced)
            periods.append(
                PeriodAnalysis(
                    period_end=period_end,
                    fiscal_year=fiscal_year,
                    statements=statements,
                    ratios=compute_ratios(context, statements),
                    quality=assess_quality(context, statements, prior=previous),
                )
            )
        previous = statements

    skipped: list[str] = []
    if len(periods) == 1:
        skipped.append(
            "Only one annual period is stored, so the earnings-quality signals that "
            "compare against the preceding year could not be computed."
        )

    outcome = AnalysisOutcome(
        # Most recent first, which is the order every reader wants and the opposite of the
        # order they had to be built in.
        periods=tuple(reversed(periods)),
        skipped=tuple(skipped),
        unplaced_concepts=tuple(sorted(unplaced)),
    )
    coverage = outcome.forecast_coverage
    _log.info(
        "analysis.completed",
        company_id=str(company_id),
        periods=len(outcome.periods),
        ratios=sum(len(period.computed_ratios) for period in outcome.periods),
        derivations=len(context.records),
        unplaced=len(outcome.unplaced_concepts),
        # Named, not counted: which line a company does not report is the whole diagnosis,
        # and a bare "3 of 8" sends whoever reads it back to the database (A46).
        thin_for_forecast=sorted(
            concept for concept, periods in coverage.items() if periods < _MIN_PERIODS_TO_AVERAGE
        ),
    )
    return outcome


async def _annual_facts(
    session: AsyncSession, *, company_id: uuid.UUID, request: ResearchRequest
) -> dict[date, list[FinancialFact]]:
    """The company's full-year facts by period, one observation per concept.

    Point-in-time filtered on ``filed_date`` when the request asks for it: the store holds
    everything every run has ever fetched, so a run as at 2022 must not read a 2024 filing
    that happens to be sitting beside it.

    **A period is a fiscal year only when a full-year duration ends on it** — gap A45, and
    the reason a live AMZN run reached the assumptions gate with six drivers each derivable
    from a single period. Selecting on ``fiscal_period == "FY"`` alone admits everything a
    10-K files under that label, including its cover-page instants:
    ``dei:EntityCommonStockSharesOutstanding`` is dated the day the filing was signed, so
    every annual report minted a *period* two or three weeks after the year end containing
    one fact and no revenue. With one such period per filing and only
    :data:`MAX_PERIODS` taken, the newest window filled with cover dates and the fiscal
    years fell out of it. The same label also covers fourth-quarter stub durations, which
    tie with the twelve-month figure on ``(period_end, concept)`` and can win it — a
    quarter's revenue standing in for a year's, which is worse than the absence.

    So the durations decide which periods exist, and instants join a period only when one
    does. A balance sheet is dated its own year end, so nothing that belongs is lost;
    what goes is the furniture that never described a year in the first place.
    """
    statement = select(FinancialFact).where(
        FinancialFact.company_id == company_id,
        FinancialFact.fiscal_period == ANNUAL,
        # Consolidated figures only. A dimensioned fact is one segment's slice of a line,
        # and letting it compete here would let a segment win a period from the aggregate
        # and put a fraction of the company through every ratio.
        FinancialFact.dimension_axis.is_(None),
    )
    if request.point_in_time:
        statement = statement.where(FinancialFact.filed_date <= request.as_of_date)

    rows = list(await session.scalars(statement))

    # The fiscal years this company actually reported, read off the durations that span one.
    fiscal_year_ends = {row.period_end for row in rows if _spans_a_year(row)}

    # The most recently filed observation of each concept-period wins, with the accession
    # breaking a same-day tie — the same ordering `select_point_in_time` applies at
    # acquisition, applied again because facts accumulate in the store across runs.
    winners: dict[tuple[date, str], FinancialFact] = {}
    admitted = 0
    for row in rows:
        if row.period_end not in fiscal_year_ends:
            continue
        if row.period_start is not None and not _spans_a_year(row):
            # A shorter duration ending on the year end: the fourth quarter, filed under
            # `FY` by the annual report that carries it. Dropped rather than deduped
            # against, because it ties with the twelve-month figure on this key and a tie
            # is decided by whichever row was read first.
            continue
        admitted += 1
        key = (row.period_end, row.concept)
        held = winners.get(key)
        if held is None or (row.filed_date, row.accession or "") > (
            held.filed_date,
            held.accession or "",
        ):
            winners[key] = row

    if len(rows) != admitted:
        _log.info(
            "analysis.periods_resolved",
            company_id=str(company_id),
            fiscal_years=len(fiscal_year_ends),
            facts_considered=len(rows),
            facts_admitted=admitted,
        )

    grouped: dict[date, list[FinancialFact]] = {}
    for (period_end, _), row in winners.items():
        grouped.setdefault(period_end, []).append(row)
    return grouped


def _spans_a_year(row: FinancialFact) -> bool:
    """Whether this fact describes a full year, from its own dates rather than its label.

    An instant — a balance-sheet line, a share count — has no start and describes a moment,
    so it is never a year by itself. See :func:`_annual_facts` for why the distinction
    decides which periods exist at all.
    """
    if row.period_start is None:
        return False
    return _ANNUAL_DAYS_MIN <= (row.period_end - row.period_start).days <= _ANNUAL_DAYS_MAX


def _quantities(rows: Sequence[FinancialFact]) -> Mapping[str, Quantity]:
    """One period's facts as sourced quantities, keyed by canonical concept.

    Every quantity carries a :class:`~aer.calc.units.SourceRef` naming its fact row, which
    is what makes each derived subtotal traceable to the filed numbers underneath it. A row
    whose unit the algebra cannot parse is dropped with a warning rather than failing the
    run: one unrecognised unit costs a line, and raising would cost the whole analysis.
    """
    quantities: dict[str, Quantity] = {}
    for row in rows:
        try:
            unit = Unit.parse(row.unit)
        except (CalculationError, ValueError):
            _log.warning(
                "analysis.unit_unparsed",
                fact_id=str(row.id),
                concept=row.concept,
                unit=row.unit,
            )
            continue
        quantities[row.concept] = Quantity(
            value=row.value,
            unit=unit,
            source=SourceRef.fact(row.id, label=row.concept),
        )
    return quantities
