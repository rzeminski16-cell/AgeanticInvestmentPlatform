"""What a chart is made from, and what one is.

Inputs are frozen dataclasses of :class:`~decimal.Decimal` values, each point carrying the
citation of the row it was read from — a calculation id or a source document id, exactly
the two kinds a text figure cites. A chart that could not say where its geometry came from
would be a picture, not a figure.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from aer.sections.render import CitationRef

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
    "svg_data_uri",
]


@dataclass(frozen=True, slots=True)
class Chart:
    """One rendered exhibit: the geometry, and everything that makes it a figure.

    ``citations`` are in caption order, deduplicated, and resolve through the same
    footnote machinery as a text figure — the assembler numbers them globally and the
    notes section lists what each marker rests on.

    ``exportable`` is set by the builder, not the caller: a chart whose geometry derives
    from licensed data is born internal-only and no argument can change that.
    """

    key: str
    title: str
    svg: str
    caption: str
    citations: tuple[CitationRef, ...] = ()
    exportable: bool = True
    placeholder: bool = False
    licence_note: str = ""


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One value at one labelled period, and the row it came from."""

    period: str
    value: Decimal
    citation: CitationRef


@dataclass(frozen=True, slots=True)
class MarginSeries:
    label: str
    points: tuple[SeriesPoint, ...]


@dataclass(frozen=True, slots=True)
class RevenueMarginInput:
    currency: str
    revenue: tuple[SeriesPoint, ...] = ()
    margins: tuple[MarginSeries, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.revenue and not any(series.points for series in self.margins)


@dataclass(frozen=True, slots=True)
class SegmentRevenue:
    """One segment's revenue as the filing tagged it — a stored fact, not a derived share.

    The builder draws the bars from these values directly. Percentages would be
    arithmetic nobody recorded, and invariant 3 does not stop applying because the
    figure is a picture.
    """

    label: str
    value: Decimal
    citation: CitationRef


@dataclass(frozen=True, slots=True)
class SegmentMixInput:
    currency: str = ""
    period: str = ""
    segments: tuple[SegmentRevenue, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.segments


@dataclass(frozen=True, slots=True)
class ScenarioBar:
    key: str
    label: str
    value_per_share: Decimal
    citation: CitationRef


@dataclass(frozen=True, slots=True)
class ScenarioBridgeInput:
    currency: str = ""
    cases: tuple[ScenarioBar, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.cases


@dataclass(frozen=True, slots=True)
class HeatmapCell:
    x: Decimal
    y: Decimal
    value: Decimal
    citation: CitationRef


@dataclass(frozen=True, slots=True)
class HeatmapInput:
    label: str = ""
    x_label: str = ""
    y_label: str = ""
    output_label: str = ""
    output_unit: str = ""
    cells: tuple[HeatmapCell, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.cells


@dataclass(frozen=True, slots=True)
class ValueBand:
    """One low-high range on the football field, and the rows behind its ends."""

    label: str
    low: Decimal
    high: Decimal
    citations: tuple[CitationRef, ...]


@dataclass(frozen=True, slots=True)
class FootballFieldInput:
    """The exportable field: bands from our own calculations, and nothing else.

    There is deliberately no field that could carry a comps band — the internal variant
    takes one as a separate argument to a differently named builder. A licence note is
    the exportable surface's only trace of the comparables work (ADR 0034).
    """

    currency: str = ""
    bands: tuple[ValueBand, ...] = ()
    licence_note: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.bands


@dataclass(frozen=True, slots=True)
class PricePoint:
    at: date
    value: Decimal


@dataclass(frozen=True, slots=True)
class PriceSeries:
    label: str
    points: tuple[PricePoint, ...]


@dataclass(frozen=True, slots=True)
class PriceRelativeInput:
    """Licensed geometry. Only the internal price chart accepts one of these."""

    currency: str = ""
    series: tuple[PriceSeries, ...] = ()
    licence_note: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(series.points for series in self.series)


@dataclass(frozen=True, slots=True)
class ValuationRangePoint:
    """One approved report's per-share range, on the day it was taken."""

    as_of: date
    low: Decimal
    high: Decimal
    label: str = ""


@dataclass(frozen=True, slots=True)
class ValuationHistoryInput:
    """The company page's range-over-time chart. Own recorded figures only."""

    currency: str = ""
    points: tuple[ValuationRangePoint, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.points


def svg_data_uri(svg: str) -> str:
    """The SVG as a self-contained ``img`` source.

    A data URI rather than inline markup: the page stays one file, and an ``img`` cannot
    script even in a viewer that would let an inline SVG try.
    """
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
