"""Deterministic server-drawn figures.

A chart here is code, not judgement: the same rows produce the same bytes, which is what
lets a test pin the figure the way it pins any other calculation output. Nothing is
computed that the ledger did not already record — the heatmap *shows* a stored grid, it
never re-runs one — and no client script, canvas or tooltip holds a value the markup does
not carry as text.

Colour comes from the token system's custom properties (``var(--aer-…)``), so one byte
sequence renders correctly in both schemes, and colour is never the only carrier: every
cell holds its server-rendered number, and the base case is named in words.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from aer.services.valuation_view import GridView

__all__ = ["HeatmapFigure", "sensitivity_heatmap"]

# Geometry in whole SVG units, so no attribute ever needs a float formatted. The label
# gutter is sized for a five-digit axis value; longer values still render, merely snug.
_CELL_W = 96
_CELL_H = 48
_LEFT = 104
_TOP = 56

# Six ordered steps of one hue. The ceiling is low enough that primary ink stays readable
# on the deepest fill in both schemes; the ordering, not the absolute darkness, is the
# information — the numbers carry the values.
_FILL_STEPS = ("0.05", "0.13", "0.21", "0.29", "0.37", "0.45")


@dataclass(frozen=True, slots=True)
class HeatmapFigure:
    """The drawn grid and the sentence that stands in for it.

    ``svg`` is a complete inline element with its own ``role`` and label; ``alt_summary``
    repeats the figure's one takeaway for contexts that render no image at all.
    """

    svg: str
    alt_summary: str


def sensitivity_heatmap(
    grid: GridView, *, base_x: Decimal | None = None, base_y: Decimal | None = None
) -> HeatmapFigure:
    """Draw a stored sensitivity grid as an inline SVG, deterministically.

    ``base_x`` and ``base_y`` name the recorded base-case coordinates, when the run's
    confirmed assumptions land exactly on a grid line; the matching cell is outlined and
    captioned "Base case" in words. Passing neither draws no anchor — the figure never
    guesses which cell the operator meant.
    """
    outputs = [output for _, cells in grid.rows for _, output, _ in cells]
    floor, ceiling = min(outputs), max(outputs)
    span = ceiling - floor

    width = _LEFT + _CELL_W * len(grid.x_values) + 16
    height = _TOP + _CELL_H * len(grid.rows) + 16

    parts: list[str] = []
    label = escape(
        f"Sensitivity of {grid.output_name} to {grid.x_assumption} and {grid.y_assumption}"
    )
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{label}" '
        f'xmlns="http://www.w3.org/2000/svg">'
    )

    # Axis titles: each axis is labelled with the assumption it varies, in words.
    parts.append(
        f'<text x="{_LEFT}" y="16" font-size="13" fill="var(--aer-ink-muted)">'
        f"{escape(grid.x_assumption)} →</text>"
    )
    parts.append(
        f'<text x="8" y="{_TOP - 10}" font-size="13" fill="var(--aer-ink-muted)">'
        f"{escape(grid.y_assumption)} ↓</text>"
    )

    # Column headers.
    for column, x_value in enumerate(grid.x_values):
        parts.append(
            f'<text x="{_LEFT + _CELL_W * column + _CELL_W // 2}" y="{_TOP - 10}" '
            f'font-size="13" text-anchor="middle" fill="var(--aer-ink)">'
            f"{escape(_plain(x_value))}</text>"
        )

    base_cell: tuple[int, int] | None = None
    for row_index, (y_value, cells) in enumerate(grid.rows):
        cy = _TOP + _CELL_H * row_index
        parts.append(
            f'<text x="{_LEFT - 10}" y="{cy + _CELL_H // 2 + 4}" font-size="13" '
            f'text-anchor="end" fill="var(--aer-ink)">{escape(_plain(y_value))}</text>'
        )
        for column, (x_value, output, _calculation_id) in enumerate(cells):
            cx = _LEFT + _CELL_W * column
            parts.append(
                f'<rect x="{cx}" y="{cy}" width="{_CELL_W}" height="{_CELL_H}" '
                f'fill="var(--aer-verification)" fill-opacity="{_step(output, floor, span)}" '
                f'stroke="var(--aer-line)" stroke-width="1"/>'
            )
            is_base = (
                base_x is not None
                and base_y is not None
                and x_value == base_x
                and y_value == base_y
            )
            value_y = cy + _CELL_H // 2 + (0 if is_base else 5)
            parts.append(
                f'<text x="{cx + _CELL_W // 2}" y="{value_y}" font-size="14" '
                f'text-anchor="middle" fill="var(--aer-ink)">{escape(_display(output))}</text>'
            )
            if is_base:
                base_cell = (cx, cy)
                parts.append(
                    f'<text x="{cx + _CELL_W // 2}" y="{cy + _CELL_H - 8}" font-size="11" '
                    f'text-anchor="middle" fill="var(--aer-decision)">Base case</text>'
                )

    # The anchor outline last, so no neighbouring cell border paints over it.
    if base_cell is not None:
        parts.append(
            f'<rect x="{base_cell[0]}" y="{base_cell[1]}" width="{_CELL_W}" '
            f'height="{_CELL_H}" fill="none" stroke="var(--aer-decision)" stroke-width="3"/>'
        )

    parts.append("</svg>")

    summary = (
        f"Sensitivity of {grid.output_name} to {grid.x_assumption} and "
        f"{grid.y_assumption}: from {_display(floor)} to {_display(ceiling)} "
        f"{grid.output_unit} across {sum(len(cells) for _, cells in grid.rows)} "
        "complete valuations. Darker cells are higher values; every cell shows its number."
    )
    return HeatmapFigure(svg="".join(parts), alt_summary=summary)


def _step(output: Decimal, floor: Decimal, span: Decimal) -> str:
    """Which of the six ordered fills this value gets. A flat grid sits on the first."""
    if span == 0:
        return _FILL_STEPS[0]
    index = int((output - floor) * len(_FILL_STEPS) / span)
    return _FILL_STEPS[min(index, len(_FILL_STEPS) - 1)]


def _display(value: Decimal) -> str:
    """A cell's number: at most two decimal places, exact below that.

    The figure is a reading aid — the adjacent table keeps the full stored precision, so
    rounding here loses nothing a reader cannot reach in one glance sideways.
    """
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def _plain(value: Decimal) -> str:
    """An axis value exactly as stored, minus exponent notation and trailing noise."""
    return format(value.normalize(), "f")
