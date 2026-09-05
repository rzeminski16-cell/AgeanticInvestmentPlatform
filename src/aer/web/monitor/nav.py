"""The monitor tool's own navigation entry.

Its own module, data only, for the reason `web/overview/nav.py` gives: the shell composes
the navigation and `templating.render()` imports the shell on every page, so a section
declared beside this tool's router would close a loop paid for on every request.
"""

from __future__ import annotations

from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = ["MONITOR"]

MONITOR: Final = NavSection(
    key="monitor",
    label="Monitor",
    tool="monitor",
    items=(NavItem(key="monitor", label="Monitor", href="/monitor"),),
)
