"""What the sidebar contains, composed from one entry per tool.

Explicit, in the shape `db/models/__init__.py` settled for models and
`agents/registry.py` for capability: a tuple somebody edits, not a scan that discovers.
The reason is the same one ADR 0067 gives for `INSTALLED_TOOLS` — a navigation that
assembled itself from whatever happened to be importable would be a navigation nobody
could read, and the test below could only ever confirm it agreed with itself.

`UNLISTED` is the other half of that test. Every server-rendered page either appears in the
nav or is named there as deliberately reachable only from inside another page. A route in
neither is the failure this file exists to catch: a page shipped with no way to reach it,
which is indistinguishable from a page nobody finished.
"""

from __future__ import annotations

from typing import Final

from aer.web.overview.nav import OVERVIEW
from aer.web.shell.nav import NavItem, NavSection

__all__ = ["NAV", "UNLISTED", "flat_items"]

# The research tool's own destinations. When a second tool arrives it contributes its own
# NavSection from its own module and adds one line below, and nothing here changes.
RESEARCH: Final = NavSection(
    key="research",
    label="Research",
    tool="research",
    items=(
        # The one item carrying a count. `badge_key` names it; `web/shell/badges.py`
        # decides what it counts, and the number arrives after the page does.
        NavItem(key="requests", label="Requests", href="/requests", badge_key="approvals"),
        NavItem(key="reports", label="Reports", href="/reports"),
        NavItem(key="skills", label="Skills", href="/skills"),
        NavItem(key="knowledge", label="Knowledge", href="/knowledge"),
    ),
)

PLATFORM: Final = NavSection(
    key="platform",
    label="Platform",
    tool="platform",
    items=(
        NavItem(key="settings", label="Settings", href="/settings"),
        NavItem(key="costs", label="Costs", href="/costs"),
        NavItem(key="health", label="Health", href="/healthz"),
        NavItem(key="api", label="API", href="/docs"),
    ),
)

# One import per tool, and one line here. Overview is the first section this file did
# not declare itself, which is the whole claim the nav-as-data slice made.
NAV: Final[tuple[NavSection, ...]] = (OVERVIEW, RESEARCH, PLATFORM)


def flat_items() -> tuple[NavItem, ...]:
    """Every item in declaration order, children included."""
    found: list[NavItem] = []
    for section in NAV:
        for item in section.items:
            found.append(item)
            found.extend(item.children)
    return tuple(found)


# Pages reached from inside another page rather than from the sidebar: a run's own
# sub-pages, a record's detail view, a form. Listing them is what turns "this route is not
# in the nav" from a shrug into a decision somebody made and can be argued with.
#
# The shapes matter more than the count. `/runs/{job_id}/…` is a run console and every one
# of its pages is reached from the console itself; `/requests/{request_id}/…` likewise from
# a request. Wildcards are deliberately not supported: a prefix that swallowed a whole tree
# would stop the test noticing a new page under it, which is the one thing it is for.
UNLISTED: Final[frozenset[str]] = frozenset(
    {
        # The landing page, which is the brand link rather than a nav item.
        "/",
        # Liveness and readiness, reached by an operator or a probe, not by a person
        # browsing. `/healthz` is in the nav; `/readyz` is its unlinked sibling.
        "/readyz",
        # The shell's own fragment, fetched by the nav after the page renders. Not a
        # destination: opening it in a browser yields a handful of spans.
        "/_shell/badges",
        # The drawer's contents, fetched from an attention row. Its trigger keeps an
        # `href` to the run console, so with scripting off nobody ever reaches this URL.
        "/overview/runs/{job_id}/preview",
        # Detail views, each reached from the listing above it.
        "/calculations/{calculation_id}",
        "/claims/{claim_id}",
        "/companies/{company_id}",
        "/knowledge/graph",
        "/reports/{report_id}",
        "/reports/{report_id}/preview",
        # A request and everything done to one.
        "/requests/new",
        "/requests/{request_id}",
        "/requests/{request_id}/assumptions",
        "/requests/{request_id}/assumptions/{assumption_id}",
        "/requests/{request_id}/edit",
        "/requests/{request_id}/remove",
        # The run console and its surfaces, all reached from the run itself.
        "/runs/{job_id}",
        "/runs/{job_id}/assumptions",
        "/runs/{job_id}/claims",
        "/runs/{job_id}/financials",
        "/runs/{job_id}/footnotes/{number}",
        "/runs/{job_id}/peers",
        "/runs/{job_id}/plan",
        "/runs/{job_id}/preview",
        "/runs/{job_id}/review",
        "/runs/{job_id}/sector",
        "/runs/{job_id}/sources",
        "/runs/{job_id}/summary",
        "/runs/{job_id}/themes",
        "/runs/{job_id}/valuation",
        # Skill authoring, reached from the skills listing.
        "/skills/examples",
        "/skills/import",
        "/skills/new",
        "/skills/{key}",
        "/skills/{key}/export",
    }
)
