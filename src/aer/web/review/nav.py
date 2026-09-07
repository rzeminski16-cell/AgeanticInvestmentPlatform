"""The review tool's own navigation entry: the review and the analytics it feeds.

Its own module, data only, for the reason `web/overview/nav.py` gives.
"""

from __future__ import annotations

from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = ["REVIEW"]

REVIEW: Final = NavSection(
    key="review",
    label="Review",
    tool="review",
    items=(
        NavItem(key="review", label="Post-trade review", href="/review"),
        NavItem(key="analytics", label="Decision analytics", href="/analytics"),
    ),
)
