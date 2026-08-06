"""Reading a run's rows into chart inputs — the only bridge between ledger and geometry.

The builders in :mod:`aer.charts` are pure; this module is where the database meets them.
Everything here is a read of what the run recorded — facts, calculations, scenarios,
sensitivity grids, price bars — and nothing is computed on the way through: a chart whose
service layer did arithmetic would be a figure with no calculation row behind it, which is
the thing invariant 3 forbids.

**The exportable set is assembled here and only here.** ``exportable_charts_for`` renders
the five charts a report may carry; the internal set — licensed geometry — comes from
``internal_charts_for`` and is consumed solely by the valuation surface. The assembler
refuses a non-exportable chart outright, so wiring the wrong function to the wrong surface
fails loudly rather than leaking a price line into an export (ADR 0043).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.calc.dcf import TerminalMethod
from aer.charts import (
    Chart,
    FootballFieldInput,
    HeatmapCell,
    HeatmapInput,
    MarginSeries,
    PricePoint,
    PriceRelativeInput,
    PriceSeries,
    RevenueMarginInput,
    ScenarioBar,
    ScenarioBridgeInput,
    SegmentMixInput,
    SeriesPoint,
    ValueBand,
    football_field,
    price_relative,
    revenue_margin_history,
    scenario_bridge,
    segment_mix,
    sensitivity_heatmap,
)
from aer.core.enums import Provider
from aer.db.models import (
    Calculation,
    Company,
    FinancialFact,
    Job,
    ResearchRequest,
    Security,
    Sensitivity,
    SourceDocument,
)
from aer.fetch.policy import DEFAULT_POLICIES
from aer.sections.render import CitationRef
from aer.services.prices import adjusted_series_for
from aer.services.scenarios import scenarios_for_request

if TYPE_CHECKING:
    from decimal import Decimal

__all__ = ["exportable_charts_for", "internal_charts_for"]

_log = structlog.get_logger("aer.services.exhibits")

# The margin calculations the history chart looks for, in the order they stack.
MARGIN_NAMES = ("gross_margin", "operating_margin", "net_margin")

_MARGIN_LABELS = {
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
}

# How many fiscal periods of history the chart shows. More is noise at report scale.
_HISTORY_PERIODS = 6

# The internal price chart's window, back from the as-of date.
_PRICE_WINDOW_DAYS = 365


async def exportable_charts_for(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    licence_note: str = "",
) -> tuple[Chart, ...]:
    """The report's chart pack, from the run's own rows.

    Returns the five exportable exhibits — or nothing at all when the run recorded no
    chart-feeding rows of any kind. A report from a run that computed nothing gains
    nothing from six pictures of absence; a report where *some* exhibits have data keeps
    the placeholders, because there a missing exhibit would read as a binding error.

    Args:
        licence_note: The comparables licence note, when the run built a peer set. It
            reaches the football field's caption, where the absence of a comps band
            would otherwise read as an oversight rather than a licence decision.
    """
    salt = str(job.id)
    charts = (
        revenue_margin_history(
            await _revenue_margin_input(session, job=job, request=request), hashsalt=salt
        ),
        segment_mix(await _segment_input(session, job=job), hashsalt=salt),
        scenario_bridge(await _scenario_input(session, job=job, request=request), hashsalt=salt),
        sensitivity_heatmap(await _heatmap_input(session, job=job), hashsalt=salt),
        football_field(
            await _field_input(session, job=job, request=request, licence_note=licence_note),
            hashsalt=salt,
        ),
    )
    if all(chart.placeholder for chart in charts):
        return ()

    _log.info(
        "exhibits.assembled",
        job_id=str(job.id),
        rendered=[chart.key for chart in charts if not chart.placeholder],
        placeholders=[chart.key for chart in charts if chart.placeholder],
    )
    return charts


async def internal_charts_for(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
) -> tuple[Chart, ...]:
    """The licensed set, for the valuation surface only.

    Every chart returned here has ``exportable=False`` set by its builder, and the report
    assembler refuses such a chart outright — the containment is structural, not a matter
    of which template remembers the rule.
    """
    salt = str(job.id)
    return (price_relative(await _price_input(session, request=request), hashsalt=salt),)


# -- Revenue and margins -----------------------------------------------------------------------


async def _revenue_margin_input(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> RevenueMarginInput:
    """Full-year revenue facts this run acquired, and the margin calculations it ran.

    A margin calculation carries no period of its own — its period is its inputs', so it
    is recovered from the fact rows the calculation's recorded inputs cite. A margin whose
    period cannot be recovered is left off the chart rather than guessed onto a year.
    """
    facts = list(
        await session.scalars(
            select(FinancialFact)
            .join(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
            .where(
                SourceDocument.job_id == job.id,
                FinancialFact.concept == "revenue",
                FinancialFact.fiscal_period == "FY",
            )
            .order_by(FinancialFact.period_end)
        )
    )
    # One point per period, the latest filed figure winning — a restatement supersedes.
    by_period = {fact.period_end: fact for fact in facts}
    kept = sorted(by_period.values(), key=lambda fact: fact.period_end)[-_HISTORY_PERIODS:]

    revenue = tuple(
        SeriesPoint(
            period=_period_label(fact),
            value=fact.value,
            citation=CitationRef(
                kind="source_document",
                identifier=str(fact.source_document_id),
                label=f"Revenue {_period_label(fact)}",
            ),
        )
        for fact in kept
    )

    margins = await _margin_series(session, job=job)
    return RevenueMarginInput(
        currency=request.reporting_currency or request.base_currency,
        revenue=revenue,
        margins=margins,
    )


async def _margin_series(session: AsyncSession, *, job: Job) -> tuple[MarginSeries, ...]:
    calculations = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job.id, Calculation.name.in_(MARGIN_NAMES))
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )
    if not calculations:
        return ()

    fact_ids = {fact_id for calculation in calculations for fact_id in _fact_input_ids(calculation)}
    periods: dict[str, str] = {}
    if fact_ids:
        rows = await session.scalars(select(FinancialFact).where(FinancialFact.id.in_(fact_ids)))
        periods = {str(row.id): _period_label(row) for row in rows}

    series: list[MarginSeries] = []
    for name in MARGIN_NAMES:
        points: dict[str, SeriesPoint] = {}
        for calculation in (c for c in calculations if c.name == name):
            labels = sorted(
                {periods[fact_id] for fact_id in _fact_input_ids(calculation) if fact_id in periods}
            )
            if len(labels) != 1:
                # Inputs from no period, or from several: this margin cannot be placed on
                # a year honestly, so it is not placed at all.
                continue
            points[labels[0]] = SeriesPoint(
                period=labels[0],
                value=calculation.output_value,
                citation=CitationRef(
                    kind="calculation",
                    identifier=str(calculation.id),
                    label=f"{_MARGIN_LABELS[name]} {labels[0]}",
                ),
            )
        if points:
            series.append(
                MarginSeries(
                    label=_MARGIN_LABELS[name],
                    points=tuple(points[label] for label in sorted(points)),
                )
            )
    return tuple(series)


def _fact_input_ids(calculation: Calculation) -> list[str]:
    found: list[str] = []
    for raw in calculation.inputs or []:
        source: dict[str, Any] = raw.get("source") or {} if isinstance(raw, dict) else {}
        if source.get("kind") == "fact" and source.get("id"):
            found.append(str(source["id"]))
    return found


def _period_label(fact: FinancialFact) -> str:
    year = fact.fiscal_year if fact.fiscal_year is not None else fact.period_end.year
    return f"FY{year}"


# -- Segments ----------------------------------------------------------------------------------


async def _segment_input(session: AsyncSession, *, job: Job) -> SegmentMixInput:
    """Structured segment facts, when the schema can hold them.

    Today it cannot: ``financial_facts`` keys one value per concept per period, so two
    segments' revenue cannot coexist as rows and no pipeline records them. The chart
    therefore renders its honest placeholder, and this function is the single seam to fill
    when dimensioned facts arrive — the builder and the report plumbing are already live.
    """
    del session, job
    return SegmentMixInput()


# -- Scenarios ---------------------------------------------------------------------------------


async def _scenario_input(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> ScenarioBridgeInput:
    """One bar per scenario whose valuation the ledger can attribute.

    Attribution is the ``case`` parameter task 47 added to the DCF outcome calculations:
    rows recorded before it exist carry no case and honestly cannot appear here.
    """
    scenarios = await scenarios_for_request(session, request.id)
    if not scenarios:
        return ScenarioBridgeInput()

    rows = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job.id, Calculation.name == "value_per_share")
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )

    cases: list[ScenarioBar] = []
    for scenario in scenarios:
        row = _latest_for_case(rows, case=scenario.key)
        if row is None:
            continue
        cases.append(
            ScenarioBar(
                key=scenario.key,
                label=scenario.label,
                value_per_share=row.output_value,
                citation=CitationRef(
                    kind="calculation",
                    identifier=str(row.id),
                    label=f"Value per share — {scenario.label}",
                ),
            )
        )
    return ScenarioBridgeInput(currency=request.base_currency, cases=tuple(cases))


def _latest_for_case(rows: list[Calculation], *, case: str) -> Calculation | None:
    """The case's most recent per-share figure, Gordon growth preferred.

    Preferred, not merged: the two terminal methods are two answers, and a chart that
    mixed them across bars would compare cases on different bases.
    """
    for method in (TerminalMethod.GORDON_GROWTH, TerminalMethod.EXIT_MULTIPLE):
        matching = [
            row
            for row in rows
            if str(row.parameters.get("case", "base")) == case
            and str(row.parameters.get("method", "")) == method.value
        ]
        if matching:
            return matching[-1]
    return None


# -- Sensitivity -------------------------------------------------------------------------------


async def _heatmap_input(session: AsyncSession, *, job: Job) -> HeatmapInput:
    """The run's first stored sensitivity grid, cell for cell."""
    grid = await session.scalar(
        select(Sensitivity)
        .where(Sensitivity.job_id == job.id)
        .options(selectinload(Sensitivity.cells))
        .order_by(Sensitivity.created_at)
        .limit(1)
    )
    if grid is None:
        return HeatmapInput()

    return HeatmapInput(
        label=grid.label,
        x_label=grid.x_assumption,
        y_label=grid.y_assumption,
        output_label=grid.output_name,
        output_unit=grid.output_unit,
        cells=tuple(
            HeatmapCell(
                x=cell.x_value,
                y=cell.y_value,
                value=cell.output_value,
                citation=CitationRef(
                    kind="calculation",
                    identifier=str(cell.calculation_id),
                    label=f"{grid.output_name} at {cell.x_value}/{cell.y_value}",
                ),
            )
            for cell in grid.cells
        ),
    )


# -- The football field ------------------------------------------------------------------------


async def _field_input(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    licence_note: str,
) -> FootballFieldInput:
    """Ranges from the run's own per-share calculations, and nothing licensed.

    Two bands at most: the spread between the base case's two terminal methods, and the
    spread across the scenario cases. Both ends of each band are recorded rows.
    """
    rows = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job.id, Calculation.name == "value_per_share")
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )
    bands: list[ValueBand] = []

    base = [
        _latest_for_case_and_method(rows, case="base", method=method)
        for method in (TerminalMethod.GORDON_GROWTH, TerminalMethod.EXIT_MULTIPLE)
    ]
    found = [row for row in base if row is not None]
    if found:
        values = [(row.output_value, row) for row in found]
        low, high = min(v for v, _ in values), max(v for v, _ in values)
        bands.append(
            ValueBand(
                label="DCF, terminal methods",
                low=low,
                high=high,
                citations=tuple(
                    CitationRef(
                        kind="calculation",
                        identifier=str(row.id),
                        label=f"Value per share ({row.parameters.get('method', '')})",
                    )
                    for row in found
                ),
            )
        )

    scenario_input = await _scenario_input(session, job=job, request=request)
    if scenario_input.cases:
        values_by_case: list[tuple[Decimal, ScenarioBar]] = [
            (bar.value_per_share, bar) for bar in scenario_input.cases
        ]
        low = min(v for v, _ in values_by_case)
        high = max(v for v, _ in values_by_case)
        bands.append(
            ValueBand(
                label="Scenario range",
                low=low,
                high=high,
                citations=tuple(bar.citation for bar in scenario_input.cases),
            )
        )

    return FootballFieldInput(
        currency=request.base_currency,
        bands=tuple(bands),
        licence_note=licence_note,
    )


def _latest_for_case_and_method(
    rows: list[Calculation], *, case: str, method: TerminalMethod
) -> Calculation | None:
    matching = [
        row
        for row in rows
        if str(row.parameters.get("case", "base")) == case
        and str(row.parameters.get("method", "")) == method.value
    ]
    return matching[-1] if matching else None


# -- Prices (internal only) --------------------------------------------------------------------


async def _price_input(session: AsyncSession, *, request: ResearchRequest) -> PriceRelativeInput:
    """The stored adjusted series for the subject, clamped to the as-of date."""
    security = await session.scalar(
        select(Security)
        .join(Company, Company.id == Security.company_id)
        .where(Company.ticker == request.ticker, Company.exchange == request.exchange)
        .order_by(Security.created_at)
        .limit(1)
    )
    if security is None:
        return PriceRelativeInput()

    series = await adjusted_series_for(
        session,
        security,
        as_of=request.as_of_date,
        since=request.as_of_date - timedelta(days=_PRICE_WINDOW_DAYS),
    )
    if not series.bars:
        return PriceRelativeInput()

    return PriceRelativeInput(
        currency=series.currency,
        series=(
            PriceSeries(
                label=request.ticker,
                points=tuple(
                    PricePoint(at=bar.on, value=bar.split_adjusted_close) for bar in series.bars
                ),
            ),
        ),
        licence_note=DEFAULT_POLICIES[Provider.EODHD].licence_note,
    )
