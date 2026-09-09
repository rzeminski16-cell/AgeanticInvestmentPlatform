"""What the sidebar contains, composed from one entry per tool and grouped by the shell.

Explicit, in the shape `db/models/__init__.py` settled for models and
`agents/registry.py` for capability: a tuple somebody edits, not a scan that discovers.
The reason is the same one ADR 0071 gives for `INSTALLED_TOOLS` — a navigation that
assembled itself from whatever happened to be importable would be a navigation nobody
could read, and the test below could only ever confirm it agreed with itself.

**`GROUPS` is where the menu is decided, and it is the only such place** (ADR 0112). A
tool contributes a `NavSection` of destinations and says nothing about where they sit; the
shell puts each section under a heading. That division is what stops nine tools from
producing nine headings — which is what happened, and which read as organisation at the
second tool and as a wall at the ninth: ten headings over eighteen links, seven of them
standing over a single link, six of those repeating the word underneath.

**The group names are a judgement, and they are meant to be argued with.** They are four
string literals in one tuple below, so rewording the menu is an edit to this file and
nothing else — no tool learns a new word, no route moves, no test asserts a heading's
prose. If "your book" is not how you think of it, the fix is three seconds long.

`UNLISTED` is the other half of that test. Every server-rendered page either appears in the
nav or is named there as deliberately reachable only from inside another page. A route in
neither is the failure this file exists to catch: a page shipped with no way to reach it,
which is indistinguishable from a page nobody finished.
"""

from __future__ import annotations

from typing import Final

from aer.web.decisions.nav import DECISIONS
from aer.web.monitor.nav import MONITOR
from aer.web.nav import NavGroup, NavItem, NavSection
from aer.web.overview.nav import OVERVIEW
from aer.web.review.nav import REVIEW
from aer.web.risk.nav import RISK
from aer.web.theses.nav import THESES
from aer.web.tools.registry import PORTFOLIO
from aer.web.watchlist.nav import WATCHLIST

__all__ = ["GROUPS", "NAV", "UNLISTED", "flat_items", "flat_sections"]

# The research tool's own destinations. When a second tool arrives it contributes its own
# NavSection from its own module and adds one line below, and nothing here changes.
RESEARCH: Final = NavSection(
    key="research",
    tool="research",
    items=(
        # The one item carrying a count. `badge_key` names it; `web/shell/badges.py`
        # decides what it counts, and the number arrives after the page does.
        NavItem(key="requests", label="Requests", href="/requests", badge_key="approvals"),
        # A literal href like any other item, matched by the same prefix logic and held
        # by the same drift test. What is behind it is a redirect rather than a page,
        # because "the run I am watching" is a question with an answer rather than a
        # place (ADR 0089) — and the answer is resolved by the same function the console
        # uses, so the link and the page cannot disagree about which run is current.
        NavItem(key="active-run", label="Active run", href="/runs/active"),
        NavItem(key="reports", label="Reports", href="/reports"),
        NavItem(key="skills", label="Skills", href="/skills"),
        NavItem(key="knowledge", label="Knowledge", href="/knowledge"),
    ),
)

PLATFORM: Final = NavSection(
    key="platform",
    tool="platform",
    items=(
        NavItem(key="settings", label="Settings", href="/settings"),
        NavItem(key="costs", label="Costs", href="/costs"),
        NavItem(key="health", label="Health", href="/healthz"),
        NavItem(key="api", label="API", href="/docs"),
    ),
)

# One import per tool, and one line here — inside the group the tool belongs to. That
# "inside" is the whole change (ADR 0112): a new tool's author has to decide where their
# work sits in somebody's day, and cannot answer by adding a heading.
#
# The four names are the arguable part, and they are grouped by what the operator is doing
# rather than by which tool implements it. `""` is a heading nobody sees: the home page is
# not a category, and "Overview · Overview" was the smallest version of the whole problem.
GROUPS: Final[tuple[NavGroup, ...]] = (
    NavGroup(key="start", label="", sections=(OVERVIEW,)),
    NavGroup(key="research", label="Research", sections=(RESEARCH,)),
    # What you own, and everything that follows from owning it: what it is worth, what it
    # exposes you to, what you decided, and how those decisions turned out.
    NavGroup(key="book", label="Your book", sections=(PORTFOLIO, RISK, DECISIONS, REVIEW)),
    # What you think, which is deliberately not the same thing. A thesis is a claim you
    # have written down, the monitor is the world disagreeing with one, and a watchlist is
    # a company you have an opinion about and no position in.
    NavGroup(key="beliefs", label="What you believe", sections=(THESES, MONITOR, WATCHLIST)),
    NavGroup(key="platform", label="Platform", sections=(PLATFORM,)),
)

# The name the rest of the application knows the navigation by. Kept because "the nav" is
# what a shell, a template and five tests call it, and because what changed is its shape
# rather than its job.
NAV: Final[tuple[NavGroup, ...]] = GROUPS


def flat_sections() -> tuple[NavSection, ...]:
    """Every tool's contribution, in the order the groups place them.

    What "which tools are in the menu?" means now that a group sits above them. Asked by
    the badge and attention registries, each of which refuses a provider owned by a tool
    the navigation has never heard of.
    """
    return tuple(section for group in NAV for section in group.sections)


def flat_items() -> tuple[NavItem, ...]:
    """Every item in declaration order, children included."""
    found: list[NavItem] = []
    for group in NAV:
        for item in group.items:
            found.append(item)
            found.extend(item.children)
    return tuple(found)


# Pages reached from inside another page rather than from the sidebar: a run's own
# sub-pages, a record's detail view, a form. Listing them is what turns "this route is not
# in the nav" from a shrug into a decision somebody made and can be argued with.
#
# **GET routes only.** `page_routes` in the drift test collects what an operator can *open*,
# so a POST-only endpoint — `/_shell/theme`, `/_shell/guidance`, every gate decision — is
# outside this mechanism entirely. Listing one here makes the test call it a stale excuse
# for a page that does not exist, which is exactly right: it is not a page.
#
# The shapes matter more than the count. `/runs/{job_id}/…` is a run console and every one
# of its pages is reached from the console itself; `/requests/{request_id}/…` likewise from
# a request. Wildcards are deliberately not supported: a prefix that swallowed a whole tree
# would stop the test noticing a new page under it, which is the one thing it is for.
UNLISTED: Final[frozenset[str]] = frozenset(
    {
        # Where the main menu used to live. A 308 to `/`, kept because the URL was in the
        # navigation and in whatever the operator bookmarked; 404ing it would be a lie
        # about a page that is right there.
        "/overview",
        # A planned tool, reached from the launcher on the main menu and from nowhere else,
        # is named here with a `PLANNED` row in `web/tools/registry.py`: a navigation
        # listing things nobody can use is worse than a launcher that shows the shape once.
        # Every row is `WORKING` today, so nothing is listed; the next tool starts here.
        # Liveness and readiness, reached by an operator or a probe, not by a person
        # browsing. `/healthz` is in the nav; `/readyz` is its unlinked sibling.
        "/readyz",
        # The shell's own fragment, fetched by the nav after the page renders. Not a
        # destination: opening it in a browser yields a handful of spans.
        "/_shell/badges",
        # The drawer's contents, fetched from an attention row. Its trigger keeps an
        # `href` to the run console, so with scripting off nobody ever reaches this URL.
        "/research/runs/{job_id}/preview",
        # Detail views, each reached from the listing above it.
        "/calculations/{calculation_id}",
        "/claims/{claim_id}",
        "/companies/{company_id}",
        "/knowledge/graph",
        "/reports/{report_id}",
        "/reports/{report_id}/preview",
        # One thesis, reached from the list above it.
        "/theses/{thesis_id}",
        # One finding, reached from the monitor's list and from the work list.
        "/monitor/findings/{finding_id}",
        # One decision, reached from the journal, from its thesis and from the work list.
        "/decisions/{decision_id}",
        # A reviewer's proposal and a confirmed review, each reached from the review list.
        "/review/passes/{pass_id}",
        "/review/{review_id}",
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
