"""What the menu contains, composed from one entry per tool and grouped by the shell.

Explicit, in the shape `db/models/__init__.py` settled for models and
`agents/registry.py` for capability: a tuple somebody edits, not a scan that discovers.
The reason is the same one ADR 0071 gives for `INSTALLED_TOOLS` — a navigation that
assembled itself from whatever happened to be importable would be a navigation nobody
could read, and the test below could only ever confirm it agreed with itself.

**`GROUPS` is where the menu is decided, and it is the only such place** (ADR 0112). A
tool contributes a `NavSection` of pages and says nothing about where they sit; the shell
puts each section inside a destination. That division is what stops nine tools from
producing nine entries — which is what happened, and which read as organisation at the
second tool and as a wall at the ninth: ten headings over eighteen links.

**The menu is the six destinations the operator approved on 28 September 2026** (ADR 0112,
amended): Today, Portfolio, Companies, Research and Review, with Platform set apart at the
foot. A destination is one link in the menu; the pages its sections contribute are the row
of tabs on its own pages, so nothing that was in the menu became unreachable by leaving it.

`UNLISTED` is the other half of the drift test. Every server-rendered page either is one of
a destination's pages or is named there as reached from inside another page. A route in
neither is the failure this file exists to catch: a page shipped with no way to reach it,
which is indistinguishable from a page nobody finished. `OWNERS` says which destination an
unlisted page lights, so a run's console is inside Research although no tab names it.
"""

from __future__ import annotations

from typing import Final

from aer.web.ask.nav import ASK
from aer.web.decisions.nav import DECISIONS
from aer.web.monitor.nav import MONITOR
from aer.web.nav import NavGroup, NavItem, NavSection
from aer.web.overview.nav import OVERVIEW
from aer.web.review.nav import REVIEW
from aer.web.risk.nav import RISK
from aer.web.theses.nav import THESES
from aer.web.tools.registry import PORTFOLIO
from aer.web.watchlist.nav import WATCHLIST

__all__ = ["GROUPS", "NAV", "OWNERS", "UNLISTED", "flat_items", "flat_sections"]

# The research tool's own pages. When a second tool arrives it contributes its own
# NavSection from its own module and adds one line below, and nothing here changes.
#
# The library first, because it is where the Research destination opens: the drawing's
# Research page is *Reports*, with commissioning one a control on it (page specification §9).
RESEARCH: Final = NavSection(
    key="research",
    tool="research",
    items=(
        NavItem(key="reports", label="Reports", href="/reports"),
        # The one item carrying a count. `badge_key` names it; `web/shell/badges.py`
        # decides what it counts, and the number arrives after the page does. The menu
        # draws it on the Research destination, which is on every page.
        NavItem(key="requests", label="Requests", href="/requests", badge_key="approvals"),
        # A literal href like any other item, matched by the same prefix logic and held
        # by the same drift test. What is behind it is a redirect rather than a page,
        # because "the run I am watching" is a question with an answer rather than a
        # place (ADR 0089) — and the answer is resolved by the same function the console
        # uses, so the link and the page cannot disagree about which run is current.
        NavItem(key="active-run", label="Active run", href="/runs/active"),
        # *Methods* is the page specification's word (§17): a section the operator writes
        # is a method of research, and "skill" was the implementation's name for it.
        NavItem(key="skills", label="Methods", href="/skills"),
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

# One import per tool, and one line here — inside the destination the tool belongs to. That
# "inside" is the whole change (ADR 0112): a new tool's author has to decide where their
# work sits in somebody's day, and cannot answer by adding an entry to the menu.
#
# The six are the information architecture's (02 §3), in the drawn order, and each page
# sits where its drawing lights it: a thesis, a question and the watchlist are about a
# company; a decision, the risk page and the monitor's findings are about the book.
GROUPS: Final[tuple[NavGroup, ...]] = (
    NavGroup(key="today", label="Today", href="/", icon="today", sections=(OVERVIEW,)),
    # What you own and everything that follows from owning it: what it exposes you to,
    # what you decided, and what the monitor found. The drawn decision and alert pages both
    # light Portfolio (page specification §11, §12).
    NavGroup(
        key="portfolio",
        label="Portfolio",
        href="/portfolio",
        icon="portfolio",
        sections=(PORTFOLIO, RISK, DECISIONS, MONITOR),
    ),
    # Companies *is* the watchlist (02 §6): the list, the queue that commissions research
    # from it, what you believe about each, and the questions asked of their records.
    NavGroup(
        key="companies",
        label="Companies",
        href="/companies",
        icon="companies",
        sections=(WATCHLIST, THESES, ASK),
    ),
    NavGroup(
        key="research", label="Research", href="/reports", icon="research", sections=(RESEARCH,)
    ),
    NavGroup(key="review", label="Review", href="/review", icon="review", sections=(REVIEW,)),
    # Set apart at the foot, as drawn: the platform is where the operator goes about the
    # tool rather than about their money.
    NavGroup(
        key="platform",
        label="Platform",
        href="/settings",
        icon="platform",
        foot=True,
        sections=(PLATFORM,),
    ),
)

# Which destination a page lights when no tab names it — the one mapping ADR 0112's
# amendment keeps beside the groups. A prefix covers the path itself and everything under
# it, and the longest wins. Only unlisted pages need a row: a page that is a destination's
# item lights that destination by being one.
OWNERS: Final[dict[str, str]] = {
    # A run's console and every surface under it, whichever run it is.
    "/runs": "research",
    # The drawer's contents, fetched from a row that previews a run.
    "/research": "research",
    # The second and third clicks behind a report's figures and sentences. A calculation
    # struck by the risk page or an answer is a minority, and its breadcrumb already leads
    # back to where it came from (ROADMAP §3.19 item 88).
    "/calculations": "research",
    "/claims": "research",
}

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


# Pages reached from inside another page rather than from the menu or a destination's tabs:
# a run's own sub-pages, a record's detail view, a form. Listing them is what turns "this
# route is not in the nav" from a shrug into a decision somebody made and can be argued with.
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
        # Where the search bar on every page submits: a company it names, the companies it
        # might mean, or the request form for one it does not know. Reached from the bar,
        # and lighting no destination because it belongs to none.
        "/search",
        # The drawer's contents, fetched from an attention row. Its trigger keeps an
        # `href` to the run console, so with scripting off nobody ever reaches this URL.
        "/research/runs/{job_id}/preview",
        # Detail views, each reached from the listing above it.
        "/calculations/{calculation_id}",
        "/claims/{claim_id}",
        "/companies/{company_id}",
        # One holding, reached from the book's own row (page specification §3).
        "/portfolio/positions/{security_id}",
        "/knowledge/graph",
        "/reports/{report_id}",
        "/reports/{report_id}/preview",
        # The reader's drawer contents, fetched from a marker. The marker keeps an `href` to
        # the note's own page, and asked for as a page this hands over to it.
        "/reports/{report_id}/notes/{number}",
        # One thesis, reached from the list above it.
        "/theses/{thesis_id}",
        # One finding, reached from the monitor's list and from the work list.
        "/monitor/findings/{finding_id}",
        # One decision, reached from the journal, from its thesis and from the work list.
        "/decisions/{decision_id}",
        # Recording one, reached from the journal, a company, a thesis and a monitor finding;
        # and the check beside the form, which the page asks for again as it changes.
        "/decisions/new",
        "/decisions/check",
        # A reviewer's proposal and a confirmed review, each reached from the review list.
        "/review/passes/{pass_id}",
        "/review/{review_id}",
        # One question, reached from the list of questions; one of its notes, from it.
        "/ask/{question_id}",
        "/ask/{question_id}/notes/{number}",
        # A request and everything done to one.
        "/requests/new",
        # The name a ticker resolves to, fetched by the new form as it is filled in.
        "/requests/resolve",
        "/requests/{request_id}",
        "/requests/{request_id}/assumptions",
        "/requests/{request_id}/assumptions/{assumption_id}",
        "/requests/{request_id}/edit",
        "/requests/{request_id}/remove",
        # The run console and its surfaces, all reached from the run itself.
        "/runs/{job_id}",
        "/runs/{job_id}/assumptions",
        # ADR 0133: reached from the valuation page, beside the figures it strikes again.
        "/runs/{job_id}/calculator",
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
