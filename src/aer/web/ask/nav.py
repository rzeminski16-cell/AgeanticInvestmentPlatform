"""Ask's navigation entry: data only, for the reason `web/overview/nav.py` gives."""

from __future__ import annotations

from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = ["ASK"]

ASK: Final = NavSection(
    key="ask",
    tool="ask",
    items=(NavItem(key="ask", label="Ask", href="/ask"),),
)
