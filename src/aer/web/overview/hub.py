"""The verdict Today leads with: the book in one sentence, four figures, and the strip.

Page specification §1, corrected 28 September 2026, and the page guide's *The hub*: **a hub
leads with state; an inbox leads with events.** So the page opens on what is true of the book —
how much of it still has a reason behind it — and the conviction strip makes that visible in
two seconds: every position by weight, coloured by whether its reasoning holds, each colour
named beside it and each segment named for a screen reader.

Everything is read on the way here, through the reads the portfolio page makes (§2): the book
at its last close, the exposure over it, and the company record's thesis states
(:func:`aer.web.portfolio.dashboard.validity_for`). Nothing is stored. The two sums the verdict
states — how much of the book rests on reasons that are not standing, and the cash the strip
leaves out — are struck by :func:`aer.calc.performance.share_of_the_book` over the book's own
weights, never added up in a template.

**The strip is a bar of weights, not a chart** (§1's *must not show a chart* means prices or
performance). Its segments are sized to the positions alone, so it fills its width; the cash
it leaves out is said beneath it rather than drawn as an empty stretch that would read as a
position nobody named.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

import structlog

from aer.calc.performance import share_of_the_book
from aer.calc.units import CalculationError, Quantity
from aer.core.dates import format_date
from aer.errors import AerError
from aer.services import calculations as calculation_service
from aer.services import company_record as record_service
from aer.services import daily_pass
from aer.services import performance as performance_service
from aer.services import portfolio as portfolio_service
from aer.services.overview import spend_since, start_of_month
from aer.web import figures
from aer.web.portfolio import dashboard
from aer.web.portfolio.pages import percent, pounds
from aer.web.verdict import Count

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.calc.engine import CalculationContext
    from aer.config import Settings
    from aer.db.models import Portfolio, User

__all__ = ["STRIP_STATES", "BookVerdict", "HubFigure", "Segment", "book_verdict"]

_log = structlog.get_logger("aer.web.overview.hub")

# The strip's three colours and the words that go with each, in the legend and in every
# segment's spoken name. *Under review* and *no thesis* share a colour, as drawn: both are a
# position whose reason is not standing, and neither has been shown to be wrong.
STRIP_STATES: Final[dict[str, tuple[str, str]]] = {
    "holds": ("holds", "success"),
    "broke": ("a premise broke", "failure"),
    "doubt": ("under review or no thesis", "warning"),
}

NO_FIGURE: Final = "—"

# Words for the small numbers a sentence starts with, as the verdicts elsewhere write them.
_WORDS: Final = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


@dataclass(frozen=True, slots=True)
class HubFigure:
    """One of the verdict's figures: what it is, the figure, one line beneath, its tone."""

    key: str
    label: str
    value: str
    note: str
    tone: str = ""
    href: str = ""


@dataclass(frozen=True, slots=True)
class Segment:
    """One position in the strip: sized, coloured, named, and a link to its page."""

    name: str
    width: str
    weight: str
    state: str
    href: str

    @property
    def words(self) -> str:
        return STRIP_STATES[self.state][0]

    @property
    def tone(self) -> str:
        return STRIP_STATES[self.state][1]

    @property
    def spoken(self) -> str:
        return f"{self.name}, {self.weight} of the book, {self.words}"


@dataclass(frozen=True, slots=True)
class BookVerdict:
    """What the page leads with."""

    headline: str
    detail: str
    needs_attention: bool
    figures: tuple[HubFigure, ...]
    strip: tuple[Segment, ...] = ()
    largest: str = ""
    cash: str = ""
    valued_at: str = ""
    """The close the book's figures were struck at, for the footer's *Valued at the close
    of …* — empty where nothing was valued."""

    @property
    def legend(self) -> tuple[tuple[str, str], ...]:
        """The colours the strip uses, each with its word, in a fixed order."""
        used = {segment.state for segment in self.strip}
        return tuple(STRIP_STATES[state] for state in STRIP_STATES if state in used)


async def book_verdict(
    session: AsyncSession, *, user: User, settings: Settings, now: datetime
) -> BookVerdict:
    """The book's verdict, or what the operator holds instead of one."""
    spend = await _spent(session, settings=settings, now=now)
    check = await _next_check(session, user=user, now=now)
    book = await portfolio_service.default_book(session, user_id=user.id)
    if book is None:
        return BookVerdict(
            headline="There is no book yet.",
            detail=(
                "Research stands on its own until you record what you hold. Then this page "
                "says whether the reasons you bought for are still standing."
            ),
            needs_attention=False,
            figures=(
                HubFigure(
                    key="book",
                    label="Book",
                    value=NO_FIGURE,
                    note="Nothing recorded yet",
                    href="/portfolio",
                ),
                check,
                spend,
            ),
        )
    try:
        as_of = await portfolio_service.latest_close(session, portfolio=book)
        context = calculation_service.new_context()
        view = await portfolio_service.book_as_at(session, context, portfolio=book, as_of=as_of)
        exposure = await performance_service.exposure_as_at(
            session, context, portfolio=book, as_of=as_of, view=view
        )
        readings = await dashboard.validity_for(
            session, user=user, view=view, exposure=exposure, now=now
        )
    except AerError as problem:
        _log.info("hub.book_unreadable", reason=str(problem))
        return BookVerdict(
            headline="The book could not be read.",
            detail=f"{problem} The portfolio page has the whole of what went wrong.",
            needs_attention=True,
            figures=(check, spend),
        )

    positions = [row for row in view.holdings if row.problem != portfolio_service.CLOSED]
    states = {row.security.id: _strip_state(readings.get(row.security.id)) for row in positions}
    holding = sum(1 for state in states.values() if state == "holds")
    broke = sum(1 for state in states.values() if state == "broke")
    unwritten = sum(
        1
        for row in positions
        if states[row.security.id] == "doubt"
        and (reading := readings.get(row.security.id)) is not None
        and reading.thesis_state == "none"
    )
    reviewing = sum(1 for state in states.values() if state == "doubt") - unwritten
    shown = (
        await _book_figure(session, book=book, view=view),
        _reasoning_figure(len(positions), holding=holding),
        check,
        spend,
    )
    if not positions:
        return BookVerdict(
            headline="Your book holds cash and no positions.",
            detail="When you hold something, this says whether the reason you bought it holds.",
            needs_attention=False,
            figures=shown,
            valued_at=format_date(view.as_of, "%-d %B %Y"),
        )

    needs = holding < len(positions)
    standing_aside = [
        row.weight.quantity
        for row in positions
        if row.weight is not None and states[row.security.id] != "holds"
    ]
    return BookVerdict(
        headline="Your book needs attention." if needs else "Every position's reasoning holds.",
        detail=_detail(
            len(positions),
            holding=holding,
            broke=broke,
            reviewing=reviewing,
            unwritten=unwritten,
            share=_share(context, standing_aside),
        ),
        needs_attention=needs,
        figures=shown,
        strip=_strip(positions, states=states, readings=readings),
        largest=_largest(positions, readings=readings),
        cash=_cash(context, view),
        valued_at=format_date(view.as_of, "%-d %B %Y"),
    )


def _strip_state(reading: dashboard.Validity | None) -> str:
    if reading is None or reading.thesis_state in {"none", "under_review"}:
        return "doubt"
    if reading.thesis_state == "broke":
        return "broke"
    return "holds"


def _worded(n: int) -> str:
    return _WORDS[n] if n < len(_WORDS) else f"{n:,}"


def _detail(
    total: int, *, holding: int, broke: int, reviewing: int, unwritten: int, share: str
) -> str:
    """*Nine of 11 positions still hold their reasoning. Two theses broke and one has nothing
    written down — together 15.9% of the book.*"""
    if holding == total:
        if total == 1:
            return "Your one position still holds its reasoning."
        return f"All {_worded(total)} positions still hold their reasoning."
    if total == 1:
        # One position is a name's worth of news, and a count of one reads as a riddle.
        state = (
            "Its thesis broke"
            if broke
            else "Its thesis is under review"
            if reviewing
            else "Nothing is written down about why you hold it"
        )
        return f"{state} — {share} of the book." if share else f"{state}."
    lead = (
        f"None of your {_worded(total)} positions still holds its reasoning."
        if holding == 0
        else f"{_worded(holding).capitalize()} of {_worded(total)} positions still "
        f"{'holds its' if holding == 1 else 'hold their'} reasoning."
    )
    clauses = [
        Count(broke, "thesis broke", "theses broke").worded() if broke else "",
        Count(reviewing, "is under review", "are under review").worded() if reviewing else "",
        Count(unwritten, "has nothing written down", "have nothing written down").worded()
        if unwritten
        else "",
    ]
    said = [clause for clause in clauses if clause]
    joined = said[0] if len(said) == 1 else f"{', '.join(said[:-1])} and {said[-1]}"
    aside = total - holding
    tail = f" — {'together ' if aside > 1 else ''}{share} of the book." if share else "."
    return f"{lead} {joined[0].upper()}{joined[1:]}{tail}"


def _share(context: CalculationContext, weights: list[Quantity]) -> str:
    """The share of the book the named holdings are, said as a percentage, or ``""``."""
    if not weights:
        return ""
    try:
        return percent(share_of_the_book(context, weights=weights).value).lstrip("+")
    except CalculationError as problem:
        _log.info("hub.share_unstruck", reason=str(problem))
        return ""


def _strip(
    positions: list[portfolio_service.HoldingRow],
    *,
    states: dict[uuid.UUID, str],
    readings: dict[uuid.UUID, dashboard.Validity],
) -> tuple[Segment, ...]:
    """Every weighed position, largest first, sized to the positions alone.

    The width is layout rather than a figure — how much of the bar a segment takes — and it is
    the only arithmetic here; the weight each segment is named with is the book's own.
    """
    weighed = sorted(
        (row for row in positions if row.weight is not None and row.weight.value > 0),
        key=_weight_of,
        reverse=True,
    )
    invested = sum((_weight_of(row) for row in weighed), Decimal(0))
    if not invested:
        return ()
    return tuple(
        Segment(
            name=_name_of(row, readings),
            width=f"{(_weight_of(row) / invested * 100).quantize(Decimal('0.01'))}%",
            weight=percent(_weight_of(row)).lstrip("+"),
            state=states[row.security.id],
            href=f"/portfolio/positions/{row.security.id}",
        )
        for row in weighed
    )


def _weight_of(row: portfolio_service.HoldingRow) -> Decimal:
    return row.weight.value if row.weight is not None else Decimal(0)


def _name_of(
    row: portfolio_service.HoldingRow, readings: dict[uuid.UUID, dashboard.Validity]
) -> str:
    reading = readings.get(row.security.id)
    if reading is not None and reading.company_name:
        return reading.company_name
    return row.security.name or row.security.ticker


def _largest(
    positions: list[portfolio_service.HoldingRow],
    *,
    readings: dict[uuid.UUID, dashboard.Validity],
) -> str:
    weighed = [row for row in positions if row.weight is not None]
    if not weighed:
        return ""
    top = max(weighed, key=_weight_of)
    return f"{_name_of(top, readings)} {percent(_weight_of(top)).lstrip('+')}"


def _cash(context: CalculationContext, view: portfolio_service.PortfolioView) -> str:
    share = _share(context, [row.weight.quantity for row in view.cash if row.weight is not None])
    return f"{share} cash is not shown" if share else ""


async def _book_figure(
    session: AsyncSession, *, book: Portfolio, view: portfolio_service.PortfolioView
) -> HubFigure:
    if not view.is_complete or view.net_assets is None:
        return HubFigure(
            key="book",
            label="Book",
            value=NO_FIGURE,
            note="Withheld: a position could not be valued",
            href="/portfolio",
        )
    change = await dashboard.days_move(
        session, book=book, as_of=view.as_of, latest=view.net_assets.value
    )
    return HubFigure(
        key="book",
        label="Book",
        value=pounds(view.net_assets.value, book.base_currency),
        note=f"{percent(change)} on the day" if change is not None else "No earlier close",
        href="/portfolio",
    )


def _reasoning_figure(total: int, *, holding: int) -> HubFigure:
    if not total:
        return HubFigure(
            key="reasoning",
            label="Reasoning current",
            value=NO_FIGURE,
            note="No positions",
            href="/portfolio",
        )
    aside = total - holding
    return HubFigure(
        key="reasoning",
        label="Reasoning current",
        value=f"{holding} of {total}",
        note=(
            f"{_worded(aside)} {'needs' if aside == 1 else 'need'} a decision or a thesis"
            if aside
            else "every position holds"
        ),
        tone="warning" if aside else "",
        href="/portfolio",
    )


async def _next_check(session: AsyncSession, *, user: User, now: datetime) -> HubFigure:
    """When the record is next read: the soonest company at its cadence, else the daily pass."""
    records = await record_service.records_for(session, user=user, now=now)
    dated = [
        (record.next_check_at, record) for record in records if record.next_check_at is not None
    ]
    if dated:
        when, soonest = min(dated, key=lambda pair: pair[0])
        return HubFigure(
            key="next_check",
            label="Next check",
            value=format_date(when.date(), "%-d %B"),
            note=f"{soonest.name}, {soonest.cadence}" if soonest.cadence else soonest.name,
            tone="warning" if when < now else "",
            href="/companies",
        )
    last = await daily_pass.last_pass(session, user_id=user.id)
    if last is None or last.finished_at is None:
        # §1's state: *No check scheduled*, leading to the platform where the pass is set.
        return HubFigure(
            key="next_check",
            label="Next check",
            value=NO_FIGURE,
            note="No check scheduled",
            tone="warning",
            href="/settings",
        )
    due = last.finished_at + daily_pass.CADENCE
    return HubFigure(
        key="next_check",
        label="Next check",
        value=format_date(due.date(), "%-d %B"),
        note="The daily pass",
        tone="warning" if due + daily_pass.GRACE < now else "",
        href="/settings",
    )


async def _spent(session: AsyncSession, *, settings: Settings, now: datetime) -> HubFigure:
    since = start_of_month(now)
    spent = await spend_since(session, since=since)
    return HubFigure(
        key="spent",
        label="Spent this month",
        value=figures.pounds(spent),
        note=f"of a {figures.pounds(settings.monthly_budget_gbp)} ceiling",
        href="/costs",
    )
