"""Overview's own navigation entry.

Its own module, and the reason is an import cycle rather than tidiness.
`web/shell/registry.py` composes the nav; `templating.render()` imports the shell on every
page; the Overview router imports `render`. A section declared beside the router would
close that loop and be paid for on every request in the product.

So the rule the shell has always implied is now visible: **a tool contributes a section
from a module that holds data and imports nothing heavy.** Its pages live elsewhere.
"""

from __future__ import annotations

from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = ["OVERVIEW"]

OVERVIEW: Final = NavSection(
    key="overview",
    label="Overview",
    tool="overview",
    items=(NavItem(key="overview", label="Overview", href="/"),),
)
