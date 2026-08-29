"""The sensitivity heatmap is a calculation output, and is pinned like one.

The tranche 7 exit criterion in the testing plan: **byte-identical for identical rows.**
A figure whose bytes drift between renders is a figure no test can pin and no diff can
review, so identity is asserted directly on the markup — alongside the properties that
make the drawing honest: every cell keeps its server-rendered number, the base case is
named in words rather than colour, and every colour is a token reference, never a literal
that one scheme would contradict.
"""

from __future__ import annotations

import re
from decimal import Decimal

from aer.services.valuation_view import GridView
from aer.web.charts import sensitivity_heatmap


def _grid(*, wacc_high_output: str = "48.20") -> GridView:
    """A three-by-two grid of the kind `_grids_for` pivots from stored cells."""
    x = (Decimal("0.020"), Decimal("0.025"), Decimal("0.030"))
    return GridView(
        label="Value per share against WACC and terminal growth",
        x_assumption="terminal_growth",
        y_assumption="wacc",
        output_name="value_per_share_gordon",
        output_unit="GBP",
        x_values=x,
        rows=(
            (
                Decimal("0.080"),
                (
                    (x[0], Decimal("40.10"), "calc-a"),
                    (x[1], Decimal("44.75"), "calc-b"),
                    (x[2], Decimal(wacc_high_output), "calc-c"),
                ),
            ),
            (
                Decimal("0.090"),
                (
                    (x[0], Decimal("34.20"), "calc-d"),
                    (x[1], Decimal("37.90"), "calc-e"),
                    (x[2], Decimal("41.05"), "calc-f"),
                ),
            ),
        ),
    )


class TestByteIdentity:
    def test_identical_rows_produce_identical_bytes(self) -> None:
        first = sensitivity_heatmap(_grid())
        second = sensitivity_heatmap(_grid())
        assert first.svg == second.svg
        assert first.alt_summary == second.alt_summary

    def test_a_changed_cell_changes_the_bytes(self) -> None:
        assert (
            sensitivity_heatmap(_grid()).svg
            != sensitivity_heatmap(_grid(wacc_high_output="48.21")).svg
        )


class TestWhatTheDrawingCarries:
    def test_every_cell_keeps_its_server_rendered_number(self) -> None:
        svg = sensitivity_heatmap(_grid()).svg
        for shown in ("40.10", "44.75", "48.20", "34.20", "37.90", "41.05"):
            assert shown in svg

    def test_both_axes_are_labelled_with_their_assumptions_and_values(self) -> None:
        svg = sensitivity_heatmap(_grid()).svg
        assert "terminal_growth" in svg
        assert "wacc" in svg
        for tick in ("0.02", "0.025", "0.03", "0.08", "0.09"):
            assert f">{tick}</text>" in svg

    def test_the_alt_summary_states_the_range_and_the_unit(self) -> None:
        summary = sensitivity_heatmap(_grid()).alt_summary
        assert "34.20" in summary
        assert "48.20" in summary
        assert "GBP" in summary
        assert "6 complete valuations" in summary

    def test_no_literal_colour_survives_a_scheme_flip(self) -> None:
        """Every paint is a token reference. A hex literal would be right in one scheme
        and wrong in the other, silently."""
        svg = sensitivity_heatmap(_grid()).svg
        assert re.search(r"#[0-9a-fA-F]{3,8}", svg) is None
        assert 'fill="var(--aer-verification)"' in svg

    def test_a_flat_grid_still_draws(self) -> None:
        """Every cell equal: no division by the empty span, one quiet fill throughout."""
        x = (Decimal("1"), Decimal("2"))
        flat = GridView(
            label="flat",
            x_assumption="a",
            y_assumption="b",
            output_name="value",
            output_unit="GBP",
            x_values=x,
            rows=((Decimal("1"), ((x[0], Decimal("5"), "c1"), (x[1], Decimal("5"), "c2"))),),
        )
        svg = sensitivity_heatmap(flat).svg
        assert svg.count('fill-opacity="0.05"') == 2


class TestTheBaseCase:
    def test_the_base_case_is_named_in_words_and_outlined(self) -> None:
        drawn = sensitivity_heatmap(_grid(), base_x=Decimal("0.025"), base_y=Decimal("0.080")).svg
        assert drawn.count("Base case") == 1
        assert 'stroke="var(--aer-decision)" stroke-width="3"' in drawn

    def test_no_anchor_is_guessed_when_no_coordinates_are_recorded(self) -> None:
        assert "Base case" not in sensitivity_heatmap(_grid()).svg

    def test_coordinates_off_the_grid_anchor_nothing(self) -> None:
        drawn = sensitivity_heatmap(_grid(), base_x=Decimal("0.023"), base_y=Decimal("0.085")).svg
        assert "Base case" not in drawn
