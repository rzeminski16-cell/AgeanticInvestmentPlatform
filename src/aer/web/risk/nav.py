"""The risk tool's own navigation entry.

Its own module, data only, for the reason `web/overview/nav.py` gives.
"""

from __future__ import annotations

from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = ["RISK"]

RISK: Final = NavSection(
    key="risk",
    label="Risk",
    tool="risk",
    items=(NavItem(key="risk", label="Risk", href="/risk"),),
)
