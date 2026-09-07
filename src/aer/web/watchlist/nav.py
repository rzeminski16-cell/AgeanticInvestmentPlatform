"""The watchlist tool's own navigation entry.

Its own module, data only, for the reason `web/overview/nav.py` gives.
"""

from __future__ import annotations

from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = ["WATCHLIST"]

WATCHLIST: Final = NavSection(
    key="watchlist",
    label="Watchlist",
    tool="watchlist",
    items=(NavItem(key="watchlist", label="Watchlist", href="/watchlist"),),
)
