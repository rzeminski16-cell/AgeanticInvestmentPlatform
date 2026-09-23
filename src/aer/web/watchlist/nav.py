"""The watchlist tool's own navigation entry.

Its own module, data only, for the reason `web/overview/nav.py` gives.
"""

from __future__ import annotations

from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = ["WATCHLIST"]

# Companies is the watchlist in the specification's sense (02-information-architecture §6):
# everything the account knows about, why it is there and when it is next looked at. The
# Watchlist page beneath it is the queue that commissions research and the standing budget
# the queue spends — the form behind the list, not a second list.
WATCHLIST: Final = NavSection(
    key="watchlist",
    tool="watchlist",
    items=(
        NavItem(key="companies", label="Companies", href="/companies"),
        NavItem(key="watchlist", label="Watchlist", href="/watchlist"),
    ),
)
