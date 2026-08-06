"""The chart builders: pure functions, held to the three chart rules.

Byte-stability, honest placeholders, and the licence split (ADR 0043) — all testable
without a database, because the builders take typed inputs and return strings. The
``svg.fonttype: none`` pin makes labels searchable in the SVG source, which is what lets
these tests assert on geometry by reading the text the chart carries.
"""

from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal

from aer.charts import (
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
    SegmentShare,
    SeriesPoint,
    ValueBand,
    football_field,
    football_field_with_comps,
    price_relative,
    revenue_margin_history,
    scenario_bridge,
    segment_mix,
    sensitivity_heatmap,
    svg_data_uri,
)
from aer.sections.render import CitationRef


def _ref(number: int) -> CitationRef:
    return CitationRef(kind="calculation", identifier=f"calc-{number}", label=f"Figure {number}")


def _revenue_input() -> RevenueMarginInput:
    return RevenueMarginInput(
        currency="USD",
        revenue=tuple(
            SeriesPoint(
                period=f"FY{year}", value=Decimal(year) * 1_000_000_000, citation=_ref(year)
            )
            for year in (2020, 2021, 2022)
        ),
        margins=(
            MarginSeries(
                label="Net margin",
                points=tuple(
                    SeriesPoint(
                        period=f"FY{year}", value=Decimal("0.31"), citation=_ref(100 + year)
                    )
                    for year in (2020, 2021, 2022)
                ),
            ),
        ),
    )


def _bridge_input() -> ScenarioBridgeInput:
    return ScenarioBridgeInput(
        currency="USD",
        cases=(
            ScenarioBar(
                key="bear", label="Bear", value_per_share=Decimal("210.50"), citation=_ref(1)
            ),
            ScenarioBar(
                key="base", label="Base", value_per_share=Decimal("280.00"), citation=_ref(2)
            ),
            ScenarioBar(
                key="bull", label="Bull", value_per_share=Decimal("341.25"), citation=_ref(3)
            ),
        ),
    )


def _heatmap_input() -> HeatmapInput:
    return HeatmapInput(
        label="Value per share against WACC and terminal growth",
        x_label="WACC",
        y_label="Terminal growth",
        output_label="Value per share",
        output_unit="USD",
        cells=tuple(
            HeatmapCell(
                x=Decimal(f"0.0{x}"),
                y=Decimal(f"0.0{y}"),
                value=Decimal(100 + 10 * x + y),
                citation=_ref(x * 10 + y),
            )
            for x in (7, 8, 9)
            for y in (1, 2, 3)
        ),
    )


def _field_input(*, licence_note: str = "") -> FootballFieldInput:
    return FootballFieldInput(
        currency="USD",
        bands=(
            ValueBand(
                label="DCF, terminal methods",
                low=Decimal("250"),
                high=Decimal("310"),
                citations=(_ref(1), _ref(2)),
            ),
            ValueBand(
                label="Scenario range",
                low=Decimal("210"),
                high=Decimal("341"),
                citations=(_ref(3), _ref(4)),
            ),
        ),
        licence_note=licence_note,
    )


class TestByteStability:
    def test_every_builder_renders_identical_bytes_twice(self):
        pairs = [
            (
                revenue_margin_history(_revenue_input(), hashsalt="job-1"),
                revenue_margin_history(_revenue_input(), hashsalt="job-1"),
            ),
            (
                scenario_bridge(_bridge_input(), hashsalt="job-1"),
                scenario_bridge(_bridge_input(), hashsalt="job-1"),
            ),
            (
                sensitivity_heatmap(_heatmap_input(), hashsalt="job-1"),
                sensitivity_heatmap(_heatmap_input(), hashsalt="job-1"),
            ),
            (
                football_field(_field_input(), hashsalt="job-1"),
                football_field(_field_input(), hashsalt="job-1"),
            ),
            (
                segment_mix(SegmentMixInput(), hashsalt="job-1"),
                segment_mix(SegmentMixInput(), hashsalt="job-1"),
            ),
        ]
        for first, second in pairs:
            assert first.svg == second.svg, first.key

    def test_the_salt_actually_reaches_the_svg(self):
        """Two jobs' charts differ only in their salted element ids — proof the salt is
        applied, which is what makes the bytes a function of the job rather than the
        process that happened to render them."""
        one = scenario_bridge(_bridge_input(), hashsalt="job-1")
        other = scenario_bridge(_bridge_input(), hashsalt="job-2")
        assert one.svg != other.svg

    def test_no_creation_date_is_embedded(self):
        chart = revenue_margin_history(_revenue_input(), hashsalt="job-1")
        assert "dc:date" not in chart.svg


class TestHonestPlaceholders:
    def test_every_empty_input_renders_the_placeholder_never_an_axis(self):
        charts = [
            revenue_margin_history(RevenueMarginInput(currency="USD"), hashsalt="j"),
            segment_mix(SegmentMixInput(), hashsalt="j"),
            scenario_bridge(ScenarioBridgeInput(), hashsalt="j"),
            sensitivity_heatmap(HeatmapInput(), hashsalt="j"),
            football_field(FootballFieldInput(), hashsalt="j"),
            price_relative(PriceRelativeInput(), hashsalt="j"),
        ]
        for chart in charts:
            assert chart.placeholder, chart.key
            assert chart.citations == (), chart.key
            # No tick labels: the placeholder is a note in a border, not an empty graph
            # a reader could mistake for data.
            assert "xtick" not in chart.svg, chart.key

    def test_the_placeholder_says_what_was_not_recorded(self):
        chart = segment_mix(SegmentMixInput(), hashsalt="j")
        assert "No structured segment facts were recorded" in chart.caption
        assert "deliberately empty" in chart.caption


class TestTheLicenceSplit:
    """ADR 0043: the withheld-figures rule applies to geometry."""

    def test_the_exportable_field_carries_no_comps_geometry_and_says_why(self):
        note = "Comparable multiples are licensed for internal use and are withheld here."
        chart = football_field(_field_input(licence_note=note), hashsalt="j")

        assert chart.exportable
        assert "Comps" not in chart.svg  # no comps band label anywhere in the geometry
        assert note in chart.caption
        assert chart.licence_note == note

    def test_the_input_type_has_no_field_that_could_carry_a_comps_band(self):
        assert "comps" not in {field.lower() for field in FootballFieldInput.__slots__}

    def test_the_internal_variant_is_born_non_exportable(self):
        chart = football_field_with_comps(
            _field_input(),
            comps_band=ValueBand(
                label="Comps (EV/EBITDA)",
                low=Decimal("230"),
                high=Decimal("300"),
                citations=(_ref(9),),
            ),
            hashsalt="j",
        )
        assert not chart.exportable
        assert "Comps (EV/EBITDA)" in chart.svg  # the band is genuinely drawn here
        assert "Internal use only" in chart.caption

    def test_the_price_chart_is_born_non_exportable_even_as_a_placeholder(self):
        empty = price_relative(PriceRelativeInput(), hashsalt="j")
        assert not empty.exportable

        drawn = price_relative(
            PriceRelativeInput(
                currency="USD",
                series=(
                    PriceSeries(
                        label="MSFT",
                        points=tuple(
                            PricePoint(at=date(2022, 1, day), value=Decimal(300 + day))
                            for day in range(1, 15)
                        ),
                    ),
                ),
                licence_note="Licensed market data.",
            ),
            hashsalt="j",
        )
        assert not drawn.exportable
        assert "Internal use only" in drawn.caption
        assert "Licensed market data." in drawn.caption


class TestCitations:
    def test_citations_arrive_deduplicated_in_first_appearance_order(self):
        repeated = _ref(1)
        chart = scenario_bridge(
            ScenarioBridgeInput(
                cases=(
                    ScenarioBar(key="a", label="A", value_per_share=Decimal(1), citation=repeated),
                    ScenarioBar(key="b", label="B", value_per_share=Decimal(2), citation=_ref(2)),
                    ScenarioBar(key="c", label="C", value_per_share=Decimal(3), citation=repeated),
                ),
            ),
            hashsalt="j",
        )
        assert chart.citations == (repeated, _ref(2))

    def test_the_heatmap_cites_every_cell(self):
        chart = sensitivity_heatmap(_heatmap_input(), hashsalt="j")
        assert len(chart.citations) == 9
        assert all(ref.kind == "calculation" for ref in chart.citations)


class TestTheDataUri:
    def test_it_round_trips_the_svg(self):
        chart = segment_mix(
            SegmentMixInput(
                period="FY2022",
                segments=(
                    SegmentShare(label="Cloud", share=Decimal("0.55"), citation=_ref(1)),
                    SegmentShare(label="Devices", share=Decimal("0.45"), citation=_ref(2)),
                ),
            ),
            hashsalt="j",
        )
        uri = svg_data_uri(chart.svg)
        prefix = "data:image/svg+xml;base64,"
        assert uri.startswith(prefix)
        assert base64.b64decode(uri[len(prefix) :]).decode("utf-8") == chart.svg
