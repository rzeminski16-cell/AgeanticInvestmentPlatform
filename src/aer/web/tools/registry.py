"""Every tool this platform has or is going to have, and what state each one is in.

One row per tool, three states, and the state is what decides where it appears. A *working*
tool earns a section in the navigation. One *under construction* earns a section too,
because it is being built now and an operator should be able to watch it arrive. A
*planned* one appears on the main menu and nowhere else — a navigation listing seven things
nobody can use is worse than a launcher that shows the shape once and gets out of the way.

**Each row occupies the URL its tool will keep.** `/portfolio` is under construction today
and the portfolio tomorrow, so nothing linking to it ever has to move. The pages are
registered one route per row rather than behind a `/tools/{key}` catch-all, which is also
what lets the nav drift test go on comparing hrefs to routes instead of being taught to
resolve parameters.

**`needs` is the field that stops a placeholder being a progress bar.** Somebody opening
one is asking why it is not here, and "attestations and their two grades: a fill price is
not filed, not chosen and not calculated" is an answer.

This is ADR 0067's `ToolDefinition` in the only shape that is useful yet. A tool with its
own tables, workflows, agent roles and subject resolvers needs the full row; none of these
has any of that, and inventing the fields before there is a second tool to check them
against would be describing a boundary rather than enforcing one. The fields arrive with
the tool that needs them.

Data only, with no heavy imports, for the reason `web/overview/nav.py` gives: the shell
composes the navigation and a router imports the shell, so a section declared beside a
router closes a loop paid for on every request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from aer.web.nav import NavItem, NavSection

__all__ = [
    "INSTALLED_TOOLS",
    "PORTFOLIO",
    "Tool",
    "ToolStatus",
    "installed_tools",
    "resolve_tool",
    "tools_needing_a_page",
]


class ToolStatus(StrEnum):
    """What an operator can expect of a tool, in the words the launcher uses.

    Three, and the boundary between the last two is a decision rather than a shade.
    ``UNDER_CONSTRUCTION`` means somebody is building it now and the design is settled;
    ``PLANNED`` means the record exists and the work has not started. Promoting a tool from
    one to the other is a deliberate edit, not a mood.
    """

    WORKING = "Working"
    UNDER_CONSTRUCTION = "Under construction"
    PLANNED = "Planned"


@dataclass(frozen=True, slots=True)
class Tool:
    """One tool, in whatever state it is in."""

    key: str
    label: str
    status: ToolStatus
    href: str
    summary: str
    adr: str
    needs: str = ""
    """What has to exist before it can. Empty for a tool that already does."""

    action_label: str = ""
    action_href: str = ""
    """The one thing you most often want to *do* with this tool, if it can be done.

    Only a working tool may carry one, and `__post_init__` refuses otherwise: an action on
    a tool that does not exist is a button that goes nowhere, which is the failure the
    placeholder pages were built to avoid rather than to relocate.

    It is a field rather than a line in the launcher because the launcher is tool-agnostic
    and should stay that way. Exactly one tool works today; when the second does, its
    action appears because its row grew one, not because somebody remembered to edit a
    template.
    """

    def __post_init__(self) -> None:
        if self.action_label and not self.action_href:
            message = f"The tool {self.key!r} names an action with nowhere to go."
            raise ValueError(message)
        if self.action_label and self.status is not ToolStatus.WORKING:
            message = (
                f"The tool {self.key!r} is {self.status.value.lower()} and carries a "
                "primary action. An action on a tool that does not exist is a button that "
                "goes nowhere."
            )
            raise ValueError(message)

    @property
    def is_built(self) -> bool:
        return self.status is ToolStatus.WORKING


# The registry. One row per tool; a tool ships by changing its `status` and giving it a
# real page, and nothing else here moves.
INSTALLED_TOOLS: Final[tuple[Tool, ...]] = (
    Tool(
        key="research",
        label="Equity Research",
        status=ToolStatus.WORKING,
        # Its own landing surface rather than a page called "research": the tool *is* the
        # requests it runs, and a front door that listed them twice would be a second name
        # for one destination.
        href="/requests",
        summary=(
            "Commission an institutional-style research note on a UK or US listed company, "
            "under explicit approval at every gate, with every figure traceable to a "
            "formula and every fact to a hashed source."
        ),
        # The record that made this a *tool* rather than the whole application. What it
        # does was decided across the plan and sixty-odd records before that one.
        adr="0067",
        # The most common thing anybody does with this platform, kept one click from the
        # front door. The old landing page had this button and the launcher took it away;
        # a browser test noticed, which is what that test is for.
        action_label="Start a research request",
        action_href="/requests/new",
    ),
    Tool(
        key="portfolio",
        label="Portfolio",
        status=ToolStatus.UNDER_CONSTRUCTION,
        href="/portfolio",
        summary=(
            "What you hold, at what cost, as at a date — with the transactions behind it "
            "and every figure recomputed from them rather than stored."
        ),
        needs=(
            "A rate store, so a book that spans currencies converts through a dated "
            "observation with a source (ADR 0078); attestations and their two grades, "
            "because a fill price is not filed, not chosen and not calculated (ADR 0069); "
            "and the transaction table those grades hang off."
        ),
        adr="0079",
    ),
    Tool(
        key="watchlist",
        label="Watchlist",
        status=ToolStatus.PLANNED,
        href="/watchlist",
        summary=(
            "Companies you are following and have not commissioned research on, with the "
            "queue of what to research next and what it would cost."
        ),
        needs=(
            "A standing budget that is not one run's cap, and the two clocks — a watchlist "
            "is followed continuously and researched as at a date."
        ),
        adr="0071",
    ),
    Tool(
        key="theses",
        label="Theses",
        status=ToolStatus.PLANNED,
        href="/theses",
        summary=(
            "What you believe about a company and why, as a statement a person wrote, with "
            "the evidence it rests on and the questions that would defeat it."
        ),
        needs=(
            "The judgement table. A thesis is a view a named person held at a time, and "
            "ADR 0070 is the record that makes it storable without becoming evidence."
        ),
        adr="0075",
    ),
    Tool(
        key="decisions",
        label="Decisions",
        status=ToolStatus.PLANNED,
        href="/decisions",
        summary=(
            "What you decided to do about a thesis, when, and on what basis — the journal "
            "entry written before the outcome is known rather than after."
        ),
        needs=(
            "Judgements, and the reserved-field guard that keeps a conviction score from "
            "becoming a number something else can multiply (ADR 0070)."
        ),
        adr="0070",
    ),
    Tool(
        key="monitor",
        label="Monitor",
        status=ToolStatus.PLANNED,
        href="/monitor",
        summary=(
            "What has happened since a thesis was written that bears on it — findings the "
            "platform raises and never answers."
        ),
        needs=(
            "Theses and their predicates. A monitor with nothing to monitor against is an "
            "alert feed, which is the thing ADR 0075 refuses."
        ),
        adr="0075",
    ),
    Tool(
        key="risk",
        label="Risk",
        status=ToolStatus.PLANNED,
        href="/risk",
        summary=(
            "What the portfolio is exposed to and what a stated scenario would do to it, "
            "commented on rather than scored."
        ),
        needs=(
            "A book to be about, and the rate store — every exposure crosses a currency, "
            "and ADR 0078 makes a rate a dated observation rather than a constant."
        ),
        adr="0076",
    ),
    Tool(
        key="review",
        label="Post-trade review",
        status=ToolStatus.PLANNED,
        href="/review",
        summary=(
            "How a decision was made, scored against the process it was supposed to "
            "follow — deliberately not against whether it made money."
        ),
        needs="Decisions and positions: a review needs both the reasoning and the outcome.",
        adr="0077",
    ),
    Tool(
        key="analytics",
        label="Decision analytics",
        status=ToolStatus.PLANNED,
        href="/analytics",
        summary=(
            "What your decisions have in common — where the process holds and where it "
            "keeps bending in the same direction."
        ),
        needs="Enough reviewed decisions to say anything at all, which is the honest bound.",
        adr="0077",
    ),
)


def installed_tools() -> tuple[Tool, ...]:
    """Every tool, in the order the launcher shows them: working first."""
    return INSTALLED_TOOLS


def resolve_tool(key: str) -> Tool | None:
    """The row for a key, or ``None``."""
    return next((tool for tool in INSTALLED_TOOLS if tool.key == key), None)


def tools_needing_a_page() -> tuple[Tool, ...]:
    """The rows `pages.py` serves: everything that is not built yet.

    A working tool already has pages of its own and would be overwritten by a placeholder
    claiming its URL — which is the failure this function exists to make impossible rather
    than to remember.
    """
    return tuple(tool for tool in INSTALLED_TOOLS if not tool.is_built)


# The one section a tool under construction earns. It is in the navigation rather than only
# on the launcher because it is being built now, and watching it arrive is the point.
PORTFOLIO: Final = NavSection(
    key="portfolio",
    label="Portfolio",
    tool="portfolio",
    items=tuple(
        NavItem(key=tool.key, label=tool.label, href=tool.href)
        for tool in INSTALLED_TOOLS
        if tool.status is ToolStatus.UNDER_CONSTRUCTION
    ),
)
