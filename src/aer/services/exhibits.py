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

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.calc.dcf import TerminalMethod
from aer.calc.units import SourceKind, SourceTable
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
    SegmentRevenue,
    SeriesPoint,
    ValueBand,
    football_field,
    football_field_with_comps,
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

    Returns only the exhibits with data behind them (gap R11). A live note printed four
    apologies — "No sensitivity grid was recorded for this run." — around one rendered
    chart, and a picture of absence tells a reader nothing the coverage notice does not
    already say honestly. An exhibit without data is omitted from the report entirely;
    the placeholders still render on the internal surfaces, where a missing binding would
    otherwise read as an error.

    Args:
        licence_note: The comparables licence note, when the run built a peer set. It
            reaches the football field's caption, where the absence of a comps band
            would otherwise read as an oversight rather than a licence decision.
    """
    salt = str(job.id)
    built = (
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
    charts = tuple(chart for chart in built if not chart.placeholder)
    if not charts:
        return ()

    _log.info(
        "exhibits.assembled",
        job_id=str(job.id),
        rendered=[chart.key for chart in charts],
        omitted=[chart.key for chart in built if chart.placeholder],
    )
    return charts


async def sensitivity_chart(session: AsyncSession, *, job: Job) -> Chart:
    """The stored sensitivity grid, drawn for the valuation surface.

    The same builder the report's exhibits use, salted the same way, so the page and the
    document show one drawing — and the byte-identity the testing plan pins for the report
    covers the page for free. A run with no stored grid gets the honest placeholder, which
    the valuation handler declines to render rather than framing a picture of absence.
    """
    return sensitivity_heatmap(await _heatmap_input(session, job=job), hashsalt=str(job.id))


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
    charts: list[Chart] = [
        price_relative(await _price_input(session, request=request), hashsalt=salt)
    ]

    comps_band = await _comps_band_for(session, job=job)
    if comps_band is not None:
        charts.append(
            football_field_with_comps(
                await _field_input(session, job=job, request=request, licence_note=""),
                comps_band=comps_band,
                hashsalt=salt,
            )
        )
    return tuple(charts)


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
                # The consolidated line. A segment's revenue here would win the year in
                # `by_period` below and shrink the bar to one segment's size.
                FinancialFact.dimension_axis.is_(None),
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
    """The inputs that are rows in ``financial_facts``, and only those.

    These ids are looked up in that one table to find a period label, so the question is
    which relation holds the row rather than which guarantee it carries. ``kind == "fact"``
    was never the right test: three relations mint that kind, and a listing id asked of
    ``financial_facts`` answers nothing while looking like an input that simply had no
    period. Rows written before ADR 0076 carry no table and are still admitted on kind,
    which is the same guess the resolver makes for them and no worse.
    """
    found: list[str] = []
    for raw in calculation.inputs or []:
        source: dict[str, Any] = raw.get("source") or {} if isinstance(raw, dict) else {}
        if not source.get("id"):
            continue
        table = source.get("table")
        if table == SourceTable.FINANCIAL_FACTS.value or (
            not table and source.get("kind") == SourceKind.FACT.value
        ):
            found.append(str(source["id"]))
    return found


def _period_label(fact: FinancialFact) -> str:
    year = fact.fiscal_year if fact.fiscal_year is not None else fact.period_end.year
    return f"FY{year}"


# -- Segments ----------------------------------------------------------------------------------

# Which axis to draw when the filing tags more than one breakdown, most meaningful first:
# the reportable segments the company itself defines, then the product split, then
# geography. Deterministic preference rather than "most members" because the axis chosen
# decides what the exhibit *is*, and that should not flip when a filer adds a region.
_SEGMENT_AXES = (
    "us-gaap:StatementBusinessSegmentsAxis",
    "srt:ProductOrServiceAxis",
    "srt:StatementGeographicalAxis",
)

# Members that are bookkeeping rather than segments. An elimination row is negative
# plumbing between segments, and a bar of it would render the exhibit unreadable.
_NOT_A_SEGMENT = ("Elimination",)


async def _segment_input(session: AsyncSession, *, job: Job) -> SegmentMixInput:
    """The latest full year's revenue by segment, from the run's dimensioned facts.

    One axis and one period, chosen deterministically: the most recent fiscal year that
    has any dimensioned revenue, and the first axis in `_SEGMENT_AXES` present in it.
    Values are the stored facts, passed through unchanged — the builder draws them as
    they are, so nothing here is computed on the way through.
    """
    rows = list(
        await session.scalars(
            select(FinancialFact)
            .join(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
            .where(
                SourceDocument.job_id == job.id,
                FinancialFact.concept == "revenue",
                FinancialFact.fiscal_period == "FY",
                FinancialFact.dimension_axis.is_not(None),
            )
            .order_by(FinancialFact.period_end.desc(), FinancialFact.dimension_member)
        )
    )
    rows = [
        row
        for row in rows
        if not any(marker in (row.dimension_member or "") for marker in _NOT_A_SEGMENT)
    ]
    if not rows:
        return SegmentMixInput()

    latest = max(row.period_end for row in rows)
    in_period = [row for row in rows if row.period_end == latest]
    axes = {row.dimension_axis for row in in_period if row.dimension_axis is not None}
    if not axes:  # pragma: no cover -- the query requires an axis on every row
        return SegmentMixInput()
    axis = next((name for name in _SEGMENT_AXES if name in axes), min(axes))

    # One row per member, the latest filed winning — the same restatement rule the
    # revenue history applies.
    by_member: dict[str, FinancialFact] = {}
    for row in in_period:
        if row.dimension_axis != axis or row.dimension_member is None:
            continue
        held = by_member.get(row.dimension_member)
        if held is None or row.filed_date > held.filed_date:
            by_member[row.dimension_member] = row
    by_member = _without_subtotals(by_member)

    segments = tuple(
        SegmentRevenue(
            label=_segment_label(member),
            value=row.value,
            citation=CitationRef(
                kind="source_document",
                identifier=str(row.source_document_id),
                label=f"{_segment_label(member)} revenue {_period_label(row)}",
            ),
        )
        for member, row in sorted(by_member.items())
    )
    units = {row.unit for row in by_member.values()}
    representative = next(iter(by_member.values()))
    return SegmentMixInput(
        # Left blank when the segments disagree on unit — a mislabelled axis is worse
        # than an unlabelled one.
        currency=units.pop() if len(units) == 1 else "",
        period=_period_label(representative),
        segments=segments,
    )


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Words a filer glued a lower-case conjunction into, mapped to their spaced forms —
# Apple's ``WearablesHomeandAccessoriesMember`` printed as "Wearables Homeand
# Accessories" on the live chart (gap A55). A table of the glued words themselves
# rather than a rule, because splitting every embedded "and" would mangle Ireland,
# England and every other geography a segment axis carries.
_GLUED_WORDS: dict[str, str] = {
    "Homeand": "Home and",
}

# The largest member set the subtotal check will reason about. Subset sums grow as
# powers of two, and no real segmentation carries more members than this.
_SUBTOTAL_MEMBER_LIMIT = 12

# A subtotal is a sum of at least this many components; below it there is nothing to
# aggregate, and a set that small has no room for a subtotal beside its parts.
_SUBTOTAL_MINIMUM_PARTS = 2


def _segment_label(member: str) -> str:
    """A reader's name for a member qname: ``aapl:GreaterChinaSegmentMember`` → "Greater China".

    Deterministic string surgery, plus one small repair table: strip the namespace
    prefix and the conventional ``Member`` suffixes, space the camel-case words, then
    respace any word the filer glued a conjunction into. Initialisms survive because
    the boundary needs a lower-case letter on its left — ``IPhone`` stays ``IPhone``
    rather than becoming ``I Phone``.
    """
    local = member.split(":", 1)[-1]
    local = local.removesuffix("SegmentMember").removesuffix("Member")
    spaced = _CAMEL_BOUNDARY.sub(" ", local)
    return " ".join(_GLUED_WORDS.get(word, word) for word in spaced.split(" "))


def _without_subtotals(by_member: dict[str, FinancialFact]) -> dict[str, FinancialFact]:
    """The members with any subtotal of the others removed, recognised by arithmetic.

    The live chart drew Apple's "Product" total beside the very product lines it sums —
    the ``ProductOrServiceAxis`` carries both — so every real segment was flattened by a
    bar that double-counts them (gap A55). A *name* cannot decide which member is the
    aggregate: a filer disaggregating only into Product and Service is segmented by
    exactly those two. Arithmetic can: a member whose value equals, exactly, the sum of
    two or more of the other members is a subtotal of them. Filed figures are rounded to
    whole units at source, so an exact match by coincidence is vanishingly unlikely —
    and the consequence of a wrong drop is a missing bar, never a wrong number.
    """
    if not _SUBTOTAL_MINIMUM_PARTS < len(by_member) <= _SUBTOTAL_MEMBER_LIMIT:
        return by_member
    values = {member: row.value for member, row in by_member.items()}
    dropped = {
        member
        for member in values
        if _sums_from(values[member], [values[m] for m in values if m != member])
    }
    if not dropped or len(dropped) == len(by_member):
        return by_member
    _log.info("exhibits.segment_subtotals_suppressed", members=sorted(dropped))
    return {member: row for member, row in by_member.items() if member not in dropped}


def _sums_from(target: Decimal, others: list[Decimal]) -> bool:
    """Whether some two-or-more of ``others`` sum exactly to ``target``."""
    from itertools import combinations  # noqa: PLC0415 -- the one caller, bounded above

    return any(
        sum(chosen) == target
        for size in range(_SUBTOTAL_MINIMUM_PARTS, len(others) + 1)
        for chosen in combinations(others, size)
    )


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


# -- The comparables band (internal only) ------------------------------------------------------

# The implied-value calculations the comps build records, one per band end. Read back by
# name like the scenario chart reads `value_per_share`: the rows are the lineage, and a
# band drawn from anything else would be a range no citation can defend.
_IMPLIED_VALUE_NAMES = (
    "implied_value_per_share_from_ev_multiple",
    "implied_value_per_share_from_price_multiple",
)

_IMPLIED_VALUE_LABELS = {
    "implied_value_per_share_from_ev_multiple": "Comps (enterprise multiple)",
    "implied_value_per_share_from_price_multiple": "Comps (per-share multiple)",
}


async def _comps_band_for(session: AsyncSession, *, job: Job) -> ValueBand | None:
    """The comps band from the run's recorded implied-value calculations, or nothing.

    The band's ends are the extremes of what the run recorded, and each end cites its
    row. ``None`` when the comps build recorded no implied values — a run with no priced
    peer — which leaves the internal field off the surface rather than empty.
    """
    rows = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job.id, Calculation.name.in_(_IMPLIED_VALUE_NAMES))
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )
    # One band, from one multiple: the comps build works through its preference order and
    # records the first that could be applied, so mixing names here would mix bases.
    for name in _IMPLIED_VALUE_NAMES:
        matching = [row for row in rows if row.name == name]
        if not matching:
            continue
        ordered = sorted(matching, key=lambda row: row.output_value)
        low, high = ordered[0], ordered[-1]
        return ValueBand(
            label=_IMPLIED_VALUE_LABELS[name],
            low=low.output_value,
            high=high.output_value,
            citations=tuple(
                CitationRef(
                    kind="calculation",
                    identifier=str(row.id),
                    label=f"Implied value per share ({row.output_value})",
                )
                for row in (low, high)
            ),
        )
    return None


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
