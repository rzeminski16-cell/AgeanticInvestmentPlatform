"""The pinned style, and the one way a figure becomes bytes.

Byte-stability is the point of everything in this module. Matplotlib is deterministic
only when every degree of freedom is pinned: the font must be the DejaVu family it
bundles (a system font lookup would tie the bytes to the machine), the SVG must carry no
creation date, element ids must derive from a caller-supplied ``svg.hashsalt`` rather
than the process's entropy, and text must stay text (``svg.fonttype: none``) so glyph
paths cannot differ between font rasteriser versions. The palette matches the report
stylesheet, because an exhibit is a page of the report, not a visitor to it.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from io import BytesIO
from typing import TYPE_CHECKING, Any

import matplotlib

# The Agg backend is headless and import-order sensitive: set it before anything pulls in
# a GUI backend. Builders draw on `Figure` objects directly rather than through pyplot,
# so no global figure state exists to leak between two charts.
matplotlib.use("Agg")

if TYPE_CHECKING:
    from matplotlib.figure import Figure

__all__ = ["INK", "MUTED", "PALETTE", "RULE", "pinned_context", "render_svg"]

# The report stylesheet's colours (templates/report.html), so the exhibits read as part
# of the document.
INK = "#1a1c1e"
MUTED = "#5c6166"
RULE = "#c9ccd0"
ACCENT = "#16324f"
AMBER = "#8a5a00"

# Series colours, accent first. Six entries because the pack has no chart with more than
# six series; a seventh would recycle, which is a design smell the author should meet.
PALETTE = (ACCENT, AMBER, "#4a7ba6", "#7a8450", "#804a5f", "#5c6166")

_RC: dict[str, Any] = {
    "font.family": "DejaVu Sans",
    "font.size": 9.0,
    "figure.dpi": 96,
    "savefig.dpi": 96,
    "svg.fonttype": "none",
    "text.color": INK,
    "axes.edgecolor": RULE,
    "axes.labelcolor": MUTED,
    "axes.titlecolor": ACCENT,
    "axes.titlesize": 10.0,
    "axes.labelsize": 8.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": RULE,
    "grid.linewidth": 0.5,
    "grid.alpha": 0.6,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 8.0,
    "ytick.labelsize": 8.0,
    "legend.fontsize": 8.0,
    "legend.frameon": False,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
}


def rc_pinned(*, hashsalt: str) -> dict[str, Any]:
    """The full parameter set a builder renders under, salt included."""
    return {**_RC, "svg.hashsalt": hashsalt}


def pinned_context(*, hashsalt: str) -> AbstractContextManager[None]:
    """``rc_context`` under the pinned style.

    The one place the stubs' Literal-keyed rc dict is stepped around: the keys above are
    all real rc keys, held so by the fact that Matplotlib validates them at entry.
    """
    return matplotlib.rc_context(rc_pinned(hashsalt=hashsalt))  # type: ignore[arg-type]


def render_svg(figure: Figure, *, hashsalt: str) -> str:
    """One figure as an SVG document, stable byte for byte.

    Args:
        hashsalt: Seeds the ids Matplotlib writes into the SVG. Callers pass something
            stable for the report — the job id — so re-rendering an archived report
            reproduces its bytes exactly; without it the salt is the process id.
    """
    buffer = BytesIO()
    with pinned_context(hashsalt=hashsalt):
        # `Date: None` drops the dc:date element entirely. An SVG stamped with its own
        # creation time can never be byte-stable, and the date it would carry — render
        # time — is the wrong date anyway; the document's `generated_at` is the record.
        figure.savefig(buffer, format="svg", metadata={"Date": None}, bbox_inches="tight")
    return buffer.getvalue().decode("utf-8")
