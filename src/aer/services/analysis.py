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

__all__ = ["AnalysisOutcome", "PeriodAnalysis", "analyse_company"]

_log = structlog.get_logger("aer.services.analysis")

# How many annual periods to analyse, most recent first. Enough for a trend and for the
# paired earnings-quality signals; bounded because every period is a full statement
# assembly and the ledger is written in one transaction.
MAX_PERIODS: Final = 5

# The fiscal period a full-year statement carries. Quarterly facts describe three months and
# would silently mix with annual ones in the same statement — an income statement built from
# one quarter's revenue and a year's operating income is not wrong so much as meaningless.
ANNUAL: Final = "FY"


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
        statements = assemble(context, _quantities(rows))
        unplaced.update(statements.unplaced)
        periods.append(
            PeriodAnalysis(
                period_end=period_end,
                fiscal_year=next((row.fiscal_year for row in rows if row.fiscal_year), None),
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
    _log.info(
        "analysis.completed",
        company_id=str(company_id),
        periods=len(outcome.periods),
        ratios=sum(len(period.computed_ratios) for period in outcome.periods),
        derivations=len(context.records),
        unplaced=len(outcome.unplaced_concepts),
    )
    return outcome


async def _annual_facts(
    session: AsyncSession, *, company_id: uuid.UUID, request: ResearchRequest
) -> dict[date, list[FinancialFact]]:
    """The company's full-year facts by period, one observation per concept.

    Point-in-time filtered on ``filed_date`` when the request asks for it: the store holds
    everything every run has ever fetched, so a run as at 2022 must not read a 2024 filing
    that happens to be sitting beside it.
    """
    statement = select(FinancialFact).where(
        FinancialFact.company_id == company_id,
        FinancialFact.fiscal_period == ANNUAL,
    )
    if request.point_in_time:
        statement = statement.where(FinancialFact.filed_date <= request.as_of_date)

    rows = list(await session.scalars(statement))

    # The most recently filed observation of each concept-period wins, with the accession
    # breaking a same-day tie — the same ordering `select_point_in_time` applies at
    # acquisition, applied again because facts accumulate in the store across runs.
    winners: dict[tuple[date, str], FinancialFact] = {}
    for row in rows:
        key = (row.period_end, row.concept)
        held = winners.get(key)
        if held is None or (row.filed_date, row.accession or "") > (
            held.filed_date,
            held.accession or "",
        ):
            winners[key] = row

    grouped: dict[date, list[FinancialFact]] = {}
    for (period_end, _), row in winners.items():
        grouped.setdefault(period_end, []).append(row)
    return grouped


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
