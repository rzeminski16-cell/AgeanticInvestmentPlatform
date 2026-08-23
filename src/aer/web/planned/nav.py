"""The tools this platform is going to have, as rows rather than as a promise.

A navigation that only listed what was finished would describe a research tool, which is
what this codebase stops being. A navigation with dead links would be worse. So a planned
tool is a registered row with a real URL, a real page and an honest answer: what it will
do, what has to exist before it can, and which record decided it.

**Each one occupies the URL it will keep.** `/watchlist` is a placeholder today and the
watchlist tomorrow, so nothing linking to it ever has to move — no redirect, no stale
bookmark, no second name for one destination. That is also what lets the nav drift test
work unchanged: every href here is a literal route, because `pages.py` registers one per
row rather than a single parameterised catch-all.

**Deleting a row is how a tool ships.** When the real one arrives it contributes its own
`NavSection` in the shape `web/overview/nav.py` uses, and the line here goes. Nothing about
this module is meant to survive; it exists so that the sixteen screens that are not built
are visible as a shape rather than absent as a surprise.

Data only, and no heavy imports, for the reason `web/overview/nav.py` gives: the shell
composes the nav and the router imports the shell, so a section declared beside a router
closes a loop paid for on every request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from aer.web.shell.nav import NavItem, NavSection

__all__ = ["OVERSIGHT", "PORTFOLIO", "PlannedTool", "planned_tools", "resolve_planned"]


@dataclass(frozen=True, slots=True)
class PlannedTool:
    """One tool that does not exist yet, described well enough to be worth reading.

    ``needs`` is the field that stops this being a "coming soon" page. An operator who
    opens one of these is asking why it is not here, and "the attestation tables, and the
    two clocks" is an answer; "we are working on it" is not.
    """

    key: str
    label: str
    section: str
    summary: str
    needs: str
    adr: str

    @property
    def href(self) -> str:
        """The URL it holds now and keeps when it is built."""
        return f"/{self.key}"


# One row per screen the design note commits to. The wording is the record's, so a reader
# who follows the ADR finds the same claim rather than a paraphrase of it.
_PLANNED: Final[tuple[PlannedTool, ...]] = (
    PlannedTool(
        key="watchlist",
        label="Watchlist",
        section="portfolio",
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
    PlannedTool(
        key="theses",
        label="Theses",
        section="portfolio",
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
    PlannedTool(
        key="decisions",
        label="Decisions",
        section="portfolio",
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
    PlannedTool(
        key="positions",
        label="Positions",
        section="portfolio",
        summary=(
            "What the book says you hold, at what cost, as at a date — with executions and "
            "net asset value behind it."
        ),
        needs=(
            "Attestations and their two grades: a fill price is not filed, not chosen and "
            "not calculated, so it needs the fourth record class (ADR 0069)."
        ),
        adr="0069",
    ),
    PlannedTool(
        key="monitor",
        label="Monitor",
        section="oversight",
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
    PlannedTool(
        key="risk",
        label="Risk",
        section="oversight",
        summary=(
            "What the portfolio is exposed to and what a stated scenario would do to it, "
            "commented on rather than scored."
        ),
        needs=(
            "Positions, and the rate store — every exposure crosses a currency, and ADR "
            "0078 makes a rate a dated observation with a source rather than a constant."
        ),
        adr="0076",
    ),
    PlannedTool(
        key="review",
        label="Post-trade review",
        section="oversight",
        summary=(
            "How a decision was made, scored against the process it was supposed to "
            "follow — deliberately not against whether it made money."
        ),
        needs="Decisions and positions: a review needs both the reasoning and the outcome.",
        adr="0077",
    ),
    PlannedTool(
        key="analytics",
        label="Decision analytics",
        section="oversight",
        summary=(
            "What your decisions have in common — where the process holds and where it "
            "keeps bending in the same direction."
        ),
        needs="Enough reviewed decisions to say anything at all, which is the honest bound.",
        adr="0077",
    ),
)


def planned_tools() -> tuple[PlannedTool, ...]:
    """Every planned tool, in declaration order."""
    return _PLANNED


def resolve_planned(key: str) -> PlannedTool | None:
    """The row for a key, or ``None``."""
    return next((tool for tool in _PLANNED if tool.key == key), None)


def _section(key: str, label: str) -> NavSection:
    return NavSection(
        key=key,
        label=label,
        # Not a tool's own key: none of these is a registered tool yet, and claiming one
        # would put a section in the nav that `registered_badges` and the attention
        # registry would then be checked against.
        tool="planned",
        items=tuple(
            NavItem(key=tool.key, label=tool.label, href=tool.href)
            for tool in _PLANNED
            if tool.section == key
        ),
    )


# What you might hold, why, the act, and the result — the lifecycle of a position, in the
# order it happens.
PORTFOLIO: Final = _section("portfolio", "Portfolio")

# What you do about what you hold.
OVERSIGHT: Final = _section("oversight", "Oversight")
