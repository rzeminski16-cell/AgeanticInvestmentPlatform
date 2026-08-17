"""The chart pack: deterministic Matplotlib, and a chart is a figure.

**A chart is a figure (ADR 0043).** The withheld-figures rule applies to pixels: a price
line is a price series and a comps band is a set of multiples, whether they arrive as
digits or as geometry. So the builders here split into an exportable set — drawn only from
our own recorded calculations and facts — and an internal-only set that renders solely on
the valuation surface, which already shows licensed figures. The split is enforced by
signature: the exportable football field has no parameter that could carry a comps band.

**Deterministic, byte for byte.** Every builder is a pure function from typed inputs to an
SVG string: pinned style, fixed dimensions, a caller-supplied ``svg.hashsalt``, and no date
metadata. Rendering the same rows twice yields the same bytes, which is what lets task 48
hash and archive a report whose charts are part of the record.

Nothing in this package touches the database. Reading the run's rows into these inputs is
:mod:`aer.services.exhibits`' job.
"""

from aer.charts.builders import (
    football_field,
    football_field_with_comps,
    price_relative,
    revenue_margin_history,
    scenario_bridge,
    segment_mix,
    sensitivity_heatmap,
    valuation_history,
)
from aer.charts.model import (
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
    ValuationHistoryInput,
    ValuationRangePoint,
    ValueBand,
    svg_data_uri,
)

__all__ = [
    "Chart",
    "FootballFieldInput",
    "HeatmapCell",
    "HeatmapInput",
    "MarginSeries",
    "PricePoint",
    "PriceRelativeInput",
    "PriceSeries",
    "RevenueMarginInput",
    "ScenarioBar",
    "ScenarioBridgeInput",
    "SegmentMixInput",
    "SegmentRevenue",
    "SeriesPoint",
    "ValuationHistoryInput",
    "ValuationRangePoint",
    "ValueBand",
    "football_field",
    "football_field_with_comps",
    "price_relative",
    "revenue_margin_history",
    "scenario_bridge",
    "segment_mix",
    "sensitivity_heatmap",
    "svg_data_uri",
    "valuation_history",
]
