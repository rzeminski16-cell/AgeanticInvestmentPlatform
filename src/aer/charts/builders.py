"""The six charts, each a pure function from recorded rows to an SVG figure.

Every builder follows one shape: empty input renders the honest placeholder — a bordered
note saying what was not recorded, never an invented axis — and populated input renders
geometry whose every series traces to the citations its points carry. The caption is
written here, deterministically, because a caption an agent could word is a caption that
could drift from the picture above it.

Decimals are converted to floats only at the drawing boundary. That is presentation, not
arithmetic: no figure in a chart is *computed* here, only placed, and the numbers a reader
can act on remain the recorded ``Decimal`` rows the citations point at.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from aer.charts.model import (
    Chart,
    FootballFieldInput,
    HeatmapInput,
    PriceRelativeInput,
    RevenueMarginInput,
    ScenarioBridgeInput,
    SegmentMixInput,
    ValuationHistoryInput,
    ValueBand,
)
from aer.charts.style import AMBER, INK, MUTED, PALETTE, RULE, pinned_context, render_svg

if TYPE_CHECKING:
    from aer.sections.render import CitationRef

__all__ = [
    "football_field",
    "football_field_with_comps",
    "price_relative",
    "revenue_margin_history",
    "scenario_bridge",
    "segment_mix",
    "sensitivity_heatmap",
    "valuation_history",
]

# One canvas for the whole pack. A pack whose exhibits jump in size reads as assembled
# from screenshots.
_SIZE = (6.4, 3.1)


def revenue_margin_history(data: RevenueMarginInput, *, hashsalt: str) -> Chart:
    """Revenue by period as bars, margins as lines on their own percentage axis."""
    key, title = "revenue_margin_history", "Revenue and margin history"
    if data.is_empty:
        return _placeholder(
            key=key,
            title=title,
            message="No revenue or margin history was recorded for this run.",
            hashsalt=hashsalt,
        )

    citations = _ordered(
        [point.citation for point in data.revenue]
        + [point.citation for series in data.margins for point in series.points]
    )

    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=_SIZE)
        axis = figure.add_subplot()

        periods = [point.period for point in data.revenue]
        if data.revenue:
            scale, suffix = _money_scale(max(point.value for point in data.revenue))
            axis.bar(
                periods,
                [float(point.value / scale) for point in data.revenue],
                color=PALETTE[0],
                width=0.55,
                label=f"Revenue ({data.currency}{suffix})",
            )
            axis.set_ylabel(f"Revenue, {data.currency}{suffix}")

        if any(series.points for series in data.margins):
            margin_axis = axis.twinx()
            margin_axis.grid(False)
            margin_axis.spines["right"].set_visible(True)
            margin_axis.spines["right"].set_color(RULE)
            for index, series in enumerate(m for m in data.margins if m.points):
                margin_axis.plot(
                    [point.period for point in series.points],
                    [float(point.value) * 100 for point in series.points],
                    color=PALETTE[(index + 1) % len(PALETTE)],
                    marker="o",
                    markersize=3,
                    linewidth=1.4,
                    label=series.label,
                )
            margin_axis.set_ylabel("Margin, %")
            handles, labels = axis.get_legend_handles_labels()
            more_handles, more_labels = margin_axis.get_legend_handles_labels()
            axis.legend(handles + more_handles, labels + more_labels, loc="upper left")

        axis.set_title(title)

    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=(
            f"Revenue by fiscal period in {data.currency}, with recorded margin "
            f"trajectories. Every bar and point is a stored figure."
        ),
        citations=citations,
    )


def segment_mix(data: SegmentMixInput, *, hashsalt: str) -> Chart:
    """Where the revenue comes from, when the filing said so in a structured form.

    The bars are the filing's own segment revenue figures, scaled for the axis exactly as
    the revenue history's bars are. Not shares: a percentage would be arithmetic no
    calculation row recorded, whereas each of these values is a stored fact whose citation
    the bar carries.
    """
    key, title = "segment_mix", "Segment mix"
    if data.is_empty:
        return _placeholder(
            key=key,
            title=title,
            message=(
                "No structured segment facts were recorded for this run, so this exhibit "
                "is deliberately empty rather than estimated from prose."
            ),
            hashsalt=hashsalt,
        )

    ordered = sorted(data.segments, key=lambda segment: segment.value, reverse=True)
    scale, suffix = _money_scale(max(segment.value for segment in ordered))
    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=_SIZE)
        axis = figure.add_subplot()
        labels = [segment.label for segment in ordered]
        axis.barh(
            list(reversed(labels)),
            [float(segment.value / scale) for segment in reversed(ordered)],
            color=PALETTE[0],
            height=0.6,
        )
        unit_label = f"{data.currency}{suffix}"
        axis.set_xlabel(f"Revenue, {unit_label}" if unit_label else "Revenue")
        axis.set_title(f"{title} — {data.period}" if data.period else title)

    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=f"Revenue by reported segment{f', {data.period}' if data.period else ''}.",
        citations=_ordered([segment.citation for segment in ordered]),
    )


def scenario_bridge(data: ScenarioBridgeInput, *, hashsalt: str) -> Chart:
    """The cases side by side: what each one values the business at, per share."""
    key, title = "scenario_bridge", "Scenario bridge"
    if data.is_empty:
        return _placeholder(
            key=key,
            title=title,
            message="No per-scenario valuations were recorded for this run.",
            hashsalt=hashsalt,
        )

    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=_SIZE)
        axis = figure.add_subplot()
        labels = [case.label for case in data.cases]
        values = [float(case.value_per_share) for case in data.cases]
        bars = axis.bar(labels, values, color=PALETTE[0], width=0.5)
        axis.bar_label(bars, fmt="%.2f", color=MUTED, fontsize=8)
        axis.set_ylabel(f"Value per share, {data.currency}" if data.currency else "Value per share")
        axis.set_title(title)

    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=(
            "Value per share under each recorded scenario, every case priced from the "
            "same base assumptions with its own stated overrides."
        ),
        citations=_ordered([case.citation for case in data.cases]),
    )


def sensitivity_heatmap(data: HeatmapInput, *, hashsalt: str) -> Chart:
    """The stored sensitivity grid as shading, every cell a recorded calculation."""
    key, title = "sensitivity_heatmap", "Sensitivity heatmap"
    if data.is_empty:
        return _placeholder(
            key=key,
            title=title,
            message="No sensitivity grid was recorded for this run.",
            hashsalt=hashsalt,
        )

    x_values = sorted({cell.x for cell in data.cells})
    y_values = sorted({cell.y for cell in data.cells})
    by_point = {(cell.x, cell.y): cell for cell in data.cells}

    grid = [
        [float(by_point[(x, y)].value) if (x, y) in by_point else float("nan") for x in x_values]
        for y in y_values
    ]

    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=_SIZE)
        axis = figure.add_subplot()
        axis.grid(False)
        mesh = axis.imshow(grid, cmap="Blues", aspect="auto", origin="lower")
        axis.set_xticks(range(len(x_values)), [_trim(x) for x in x_values])
        axis.set_yticks(range(len(y_values)), [_trim(y) for y in y_values])
        axis.set_xlabel(data.x_label)
        axis.set_ylabel(data.y_label)
        for row_index, y in enumerate(y_values):
            for column_index, x in enumerate(x_values):
                cell = by_point.get((x, y))
                if cell is not None:
                    # The label is a reading of the cell, never the record: the record is
                    # the calculation the cell cites, at full precision. A stored value
                    # printed to twelve places overran its neighbours and was unreadable
                    # on the first live run; the label is compact, and its ink is chosen
                    # against the shade it sits on rather than fixed to a colour that
                    # vanishes on the darker half of the scale.
                    shade = mesh.cmap(_position(float(cell.value), *mesh.get_clim()))
                    axis.text(
                        column_index,
                        row_index,
                        _compact(cell.value),
                        ha="center",
                        va="center",
                        fontsize=8,
                        color=_ink_on(shade),
                    )
        figure.colorbar(mesh, ax=axis, shrink=0.85)
        axis.set_title(data.label or title)

    ordered_cells = [by_point[(x, y)] for y in y_values for x in x_values if (x, y) in by_point]
    unit = f" ({data.output_unit})" if data.output_unit else ""
    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=(
            f"{data.output_label or 'Output'}{unit} across {data.x_label} and "
            f"{data.y_label}. Each cell is a recorded calculation, not an interpolation."
        ),
        citations=_ordered([cell.citation for cell in ordered_cells]),
    )


def football_field(data: FootballFieldInput, *, hashsalt: str) -> Chart:
    """The exportable field: ranges from our own calculations, and nothing licensed.

    There is no argument that could carry a comps band — see
    :class:`~aer.charts.model.FootballFieldInput`. When the run built a comparables
    table, ``licence_note`` says why its band is absent, in the caption, where the
    absence would otherwise read as an oversight.
    """
    key, title = "football_field", "Valuation football field"
    if data.is_empty:
        return _placeholder(
            key=key,
            title=title,
            message="No valuation ranges were recorded for this run.",
            hashsalt=hashsalt,
        )

    figure = _field_figure(
        data.bands, currency=data.currency, title=title, hashsalt=hashsalt, highlight_last=False
    )
    caption = "Value-per-share ranges from this run's own recorded calculations."
    if data.licence_note:
        caption = f"{caption} {data.licence_note}"
    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=caption,
        citations=_ordered([ref for band in data.bands for ref in band.citations]),
        licence_note=data.licence_note,
    )


def football_field_with_comps(
    data: FootballFieldInput, *, comps_band: ValueBand, hashsalt: str
) -> Chart:
    """The internal variant: the same field with the licensed comparables band.

    Born internal-only. The comps band derives from licensed market data (ADR 0030
    route 2), so this chart renders solely on the valuation surface and
    ``exportable=False`` is set here rather than trusted to the caller.
    """
    key, title = "football_field_internal", "Valuation football field (internal)"
    bands = (*data.bands, comps_band)
    figure = _field_figure(
        bands, currency=data.currency, title=title, hashsalt=hashsalt, highlight_last=True
    )
    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=(
            "Value-per-share ranges including the comparables band. Internal use only: "
            "the comps band derives from licensed market data."
        ),
        citations=_ordered(
            [ref for band in data.bands for ref in band.citations] + list(comps_band.citations)
        ),
        exportable=False,
    )


def price_relative(data: PriceRelativeInput, *, hashsalt: str) -> Chart:
    """The licensed price chart. Internal-only by construction, like the comps field."""
    key, title = "price_relative", "Price and relative performance (internal)"
    if data.is_empty:
        return _placeholder(
            key=key,
            title=title,
            message="No price series was recorded for this run.",
            hashsalt=hashsalt,
            exportable=False,
        )

    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=_SIZE)
        axis = figure.add_subplot()
        for index, series in enumerate(data.series):
            axis.plot(
                # Matplotlib plots dates natively; its stubs just do not say so.
                [point.at for point in series.points],  # type: ignore[arg-type]
                [float(point.value) for point in series.points],
                color=PALETTE[index % len(PALETTE)],
                linewidth=1.2,
                label=series.label,
            )
        axis.legend(loc="upper left")
        axis.set_ylabel(f"Adjusted close, {data.currency}" if data.currency else "Adjusted close")
        axis.set_title(title)
        figure.autofmt_xdate(rotation=0, ha="center")

    caption = "Adjusted close over the review window. Internal use only: licensed market data."
    if data.licence_note:
        caption = f"{caption} {data.licence_note}"
    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=caption,
        exportable=False,
        licence_note=data.licence_note,
    )


def valuation_history(data: ValuationHistoryInput, *, hashsalt: str) -> Chart:
    """Approved per-share ranges over time — how the view moved between reports.

    Exportable: every band end is a figure from one of this account's own approved
    reports. The caption names the population; the company page beside it lists the
    reports themselves, so this chart carries no markers of its own.
    """
    key, title = "valuation_history", "Valuation range history"
    if data.is_empty:
        return _placeholder(
            key=key,
            title=title,
            message="No approved report has recorded a valuation range yet.",
            hashsalt=hashsalt,
        )

    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=_SIZE)
        axis = figure.add_subplot()
        positions = range(len(data.points))
        for index, point in enumerate(data.points):
            low, high = float(point.low), float(point.high)
            axis.vlines(index, low, high, color=PALETTE[0], linewidth=6, alpha=0.85)
            axis.text(
                index,
                high,
                f" {point.high:.2f}",
                ha="center",
                va="bottom",
                fontsize=7,
                color=MUTED,
            )
            axis.text(
                index, low, f" {point.low:.2f}", ha="center", va="top", fontsize=7, color=MUTED
            )
        axis.set_xticks(list(positions), [point.as_of.isoformat() for point in data.points])
        axis.set_ylabel(f"Value per share, {data.currency}" if data.currency else "Value per share")
        axis.margins(y=0.18)
        axis.set_title(title)

    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=(
            f"Per-share valuation ranges from {len(data.points)} approved report(s), "
            "in as-of date order."
        ),
    )


# -- Shared drawing ----------------------------------------------------------------------------


def _field_figure(
    bands: tuple[ValueBand, ...],
    *,
    currency: str,
    title: str,
    hashsalt: str,
    highlight_last: bool,
) -> Figure:
    """The range bars both field variants share. ``highlight_last`` marks the licensed
    comps band amber on the internal variant, so it cannot be mistaken for our own work."""
    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=_SIZE)
        axis = figure.add_subplot()
        for index, band in enumerate(bands):
            low, high = float(band.low), float(band.high)
            is_highlighted = highlight_last and index == len(bands) - 1
            axis.barh(
                index,
                max(high - low, 1e-9),
                left=low,
                height=0.45,
                color=AMBER if is_highlighted else PALETTE[0],
                alpha=0.85,
            )
            axis.text(high, index, f"  {band.high:.2f}", va="center", fontsize=7, color=MUTED)
            axis.text(
                low, index, f"{band.low:.2f}  ", va="center", ha="right", fontsize=7, color=MUTED
            )
        axis.set_yticks(range(len(bands)), [band.label for band in bands])
        axis.set_xlabel(f"Value per share, {currency}" if currency else "Value per share")
        axis.set_title(title)
        axis.margins(x=0.12)
    return figure


def _placeholder(
    *, key: str, title: str, message: str, hashsalt: str, exportable: bool = True
) -> Chart:
    """The honest empty exhibit: a note in a border, and no axes to misread.

    A placeholder still renders as an image so the pack keeps its shape — a silently
    missing exhibit reads as a binding error, and an empty pair of axes reads as data.
    """
    with pinned_context(hashsalt=hashsalt):
        figure = Figure(figsize=(_SIZE[0], 1.6))
        axis = figure.add_subplot()
        axis.set_axis_off()
        axis.add_patch(
            Rectangle((0.01, 0.06), 0.98, 0.88, fill=False, edgecolor=RULE, linewidth=1.0)
        )
        axis.text(0.5, 0.62, title, ha="center", va="center", fontsize=10, color=MUTED)
        axis.text(0.5, 0.34, message, ha="center", va="center", fontsize=8, color=MUTED, wrap=True)

    return Chart(
        key=key,
        title=title,
        svg=render_svg(figure, hashsalt=hashsalt),
        caption=message,
        placeholder=True,
        exportable=exportable,
    )


def _ordered(citations: list[CitationRef]) -> tuple[CitationRef, ...]:
    """First-appearance order, deduplicated — the order the caption's markers will take."""
    seen: list[CitationRef] = []
    for citation in citations:
        if citation not in seen:
            seen.append(citation)
    return tuple(seen)


def _money_scale(largest: Decimal) -> tuple[Decimal, str]:
    if largest >= Decimal("1e9"):
        return Decimal("1e9"), "bn"
    if largest >= Decimal("1e6"):
        return Decimal("1e6"), "m"
    return Decimal(1), ""


def _trim(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _compact(value: Decimal) -> str:
    """A figure at the precision a glance can take in, scaled to its magnitude.

    Hundreds and above to the unit, tens to a tenth, units to a hundredth, and anything
    smaller to a thousandth — a value per share reads ``412``, a ratio ``0.073``. Decimal
    throughout: the label is derived from the stored figure, never from a float of it.
    """
    magnitude = abs(value)
    places = next(
        (places for floor, places in _LABEL_PLACES if magnitude >= floor), _SMALLEST_LABEL_PLACES
    )
    return f"{value:,.{places}f}"


# Decimal places by the magnitude a label's value reaches, largest floor first.
_LABEL_PLACES: Final = ((Decimal(100), 0), (Decimal(10), 1), (Decimal(1), 2))
_SMALLEST_LABEL_PLACES: Final = 3

# Relative luminance below which a cell is dark enough to need white ink. Mid-scale rather
# than at the halfway point of the colormap: the "Blues" ramp darkens late, and dark ink on
# its upper third was what made the first live run's grid unreadable.
_DARK_SHADE: Final = 0.55


def _position(value: float, low: float, high: float) -> float:
    """Where a value sits on the colour scale, as the fraction the colormap is indexed by."""
    return 0.5 if high == low else (value - low) / (high - low)


def _ink_on(shade: tuple[float, float, float, float]) -> str:
    """Ink that stays legible on a colormap cell: dark on the light half, white on the dark."""
    red, green, blue, _alpha = shade
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return "white" if luminance < _DARK_SHADE else INK
