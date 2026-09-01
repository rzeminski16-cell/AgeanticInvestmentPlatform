"""Whether the book has done well, and what it is concentrated in — rows in, figures out.

The seam between ``transactions``, :mod:`aer.services.portfolio` and
:mod:`aer.calc.performance`, and the same discipline as the book itself: **nothing is
stored and nothing is cached across requests** (ADR 0083). A return is computed on the way
to the screen out of the trades and the price history, so a reader who disagrees with it
gets the value series, the flows and the formula rather than a column.

**Two returns, because they answer two questions.** The time-weighted return breaks the
series at every external flow, so a deposit cannot read as performance; the money-weighted
return is the internal rate over those same flows, so a well-timed top-up can. Neither is
the answer alone, and a screen showing one is quietly asserting which the reader meant.

**A deposit and a withdrawal are the only external flows.** A dividend is money the
holdings produced and a fee is the cost of running them; both belong inside the return, not
beside it. A buy or a sell moves value between two lines of the same book and is not a flow
at all.

**The cost of a true time-weighted return is a valuation per flow date**, because that is
what "no flow inside a sub-period" means. It is bounded here rather than hoped about: past
:data:`MAX_VALUATION_POINTS` the time-weighted figure is refused with its reason, and the
money-weighted one — which needs only the period's endpoints — still stands.

**Exposure reports what it knows and names what it does not.** A sector is known only for
listings a research run has resolved to a company, so unclassified holdings are a named
group with its members listed, never an "other" bucket that invents a category and then
weights it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Final

import structlog

from aer.calc import performance as calc
from aer.calc.engine import CalculationContext
from aer.calc.prices import MINOR_UNITS
from aer.calc.units import CalculationError, Quantity, SourceRef, Unit, UnitMismatchError
from aer.core.enums import TransactionKind
from aer.db.models import Security, Transaction
from aer.services.portfolio import (
    CLOSED,
    EMPTY,
    Figure,
    HoldingRow,
    PortfolioView,
    book_as_at,
    graded_figure,
    in_base,
    transactions_in_force,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Portfolio

__all__ = [
    "CONCENTRATION_COUNT",
    "EXTERNAL_KINDS",
    "MAX_VALUATION_POINTS",
    "UNKNOWN_COUNTRY",
    "UNKNOWN_SECTOR",
    "ExposureBand",
    "ExposureSlice",
    "ExposureView",
    "PeriodReturn",
    "ReturnView",
    "exposure_as_at",
    "returns_as_at",
]

_log = structlog.get_logger("aer.services.performance")

# The money that enters or leaves the book from outside it. Everything else — a dividend
# the holdings produced, a fee for running them, a purchase that moved cash into stock — is
# inside the return rather than beside it, which is the whole reason a top-up does not read
# as performance.
EXTERNAL_KINDS: Final[frozenset[TransactionKind]] = frozenset(
    {TransactionKind.DEPOSIT, TransactionKind.WITHDRAWAL}
)

# How many book valuations one screen may ask for. A true time-weighted return needs one at
# every external flow date, and each is a full walk of every holding against the price
# history — so a book with hundreds of flow dates is hundreds of walks. Above this the
# time-weighted figure is refused *and says so*, rather than being silently approximated by
# a Dietz-style weighting that would answer a different question under the same label.
MAX_VALUATION_POINTS: Final = 120

# How many holdings the concentration figure covers. Five is the convention every fact
# sheet uses, and it is named here so the page and the calculation cannot disagree.
CONCENTRATION_COUNT: Final = 5

# What the bands say when the classification is absent. Named, with their members listed,
# so a reader sees which holdings are unaccounted for instead of a bucket that looks like a
# sector of its own and carries a weight.
UNKNOWN_SECTOR: Final = "Sector not known"
UNKNOWN_COUNTRY: Final = "Listing country not known"

# Days in the year the offsets are measured against. Actual/365 — stated rather than
# implied, because a rate is only comparable to another on the same basis.
_DAYS_IN_YEAR: Final = Decimal(365)

# Where a listing's country comes from. Explicit, and an exchange missing here is *named as
# unknown* rather than guessed, for the same reason `listings._QUOTE_CURRENCY` refuses one:
# a country invented for a venue would put a holding in a jurisdiction it does not trade in
# and the reader would have no way to tell.
_EXCHANGE_COUNTRY: Final[dict[str, str]] = {
    "NASDAQ": "United States",
    "NYSE": "United States",
    "LSE": "United Kingdom",
}

_NOTHING_RECORDED: Final = "Nothing has been recorded in this book, so there is nothing to measure."
_NO_FLOWS: Final = (
    "Nothing has been paid into this book, so there is no capital for a return to be a return on."
)
_NO_DENOMINATOR: Final = (
    "No exposure: the book nets to nothing or less, so there is no whole for a group to be "
    "a share of."
)


@dataclass(frozen=True, slots=True)
class PeriodReturn:
    """How the book did over one measured interval."""

    label: str
    begin: date
    end: date

    # ``None`` when the interval could not be measured: a valuation the prices would not
    # support, a book with no capital in it yet, or — for the time-weighted figure alone —
    # more flow dates than :data:`MAX_VALUATION_POINTS`. The reason is on the row, because
    # a blank cell reads as nil.
    time_weighted: Figure | None
    money_weighted: Figure | None
    problem: str = ""

    @property
    def is_measured(self) -> bool:
        return self.time_weighted is not None or self.money_weighted is not None


@dataclass(frozen=True, slots=True)
class ReturnView:
    """Every interval one screen shows, as at one date."""

    portfolio: Portfolio
    as_of: date

    # ``None`` for a book with no transactions at all. There is no inception until
    # something has been recorded, and a date invented for the empty case would make the
    # since-inception row look like a measurement.
    inception: date | None
    periods: tuple[PeriodReturn, ...]
    problem: str = ""

    @property
    def since_inception(self) -> PeriodReturn | None:
        """The whole-book row, which always leads. ``None`` only when nothing measured."""
        return self.periods[0] if self.periods else None


@dataclass(frozen=True, slots=True)
class ExposureSlice:
    """One group and what share of the book sits in it."""

    label: str
    value: Figure
    share: Figure
    members: tuple[str, ...]

    # False for the group that exists because a classification is missing. A surface reads
    # this rather than the label, so the distinction survives a rename.
    known: bool = True


@dataclass(frozen=True, slots=True)
class ExposureBand:
    """One way of cutting the book up — by holding, sector, currency or listing country."""

    kind: str
    title: str
    slices: tuple[ExposureSlice, ...]

    # Held apart from ``slices`` rather than sorted among them, so a surface cannot render
    # "not known" as though it were a sector with a weight of its own.
    unknown: ExposureSlice | None = None


@dataclass(frozen=True, slots=True)
class ExposureView:
    """What the book is concentrated in, as at one date."""

    portfolio: Portfolio
    as_of: date
    bands: tuple[ExposureBand, ...]

    # ``None`` when the book holds no priced securities: cash is a position and carries a
    # weight, but a concentration figure over cash alone answers nothing.
    top_holdings: Figure | None
    problem: str = ""


# -- Return ------------------------------------------------------------------------------


async def returns_as_at(
    session: AsyncSession,
    context: CalculationContext,
    *,
    portfolio: Portfolio,
    as_of: date,
) -> ReturnView:
    """Both returns, since inception and per calendar year.

    Per *calendar* year rather than per year from inception, deliberately: the rows a
    reader can compare against a market are calendar ones, and "the twelve months from the
    14th of March" is comparable to nothing. The current year runs to the as-of date and is
    labelled for it.

    Args:
        context: Where every calculation is recorded — passed in, so a caller that is a run
            can persist the ledger and a page load simply does not.
        as_of: The instant the book is measured to. Nothing after it enters any figure.
    """
    trades = await transactions_in_force(session, portfolio=portfolio, as_of=as_of)
    if not trades:
        return ReturnView(
            portfolio=portfolio,
            as_of=as_of,
            inception=None,
            periods=(),
            problem=_NOTHING_RECORDED,
        )

    inception = trades[0].trade_date
    flows = [trade for trade in trades if trade.kind in EXTERNAL_KINDS]
    if not flows:
        return ReturnView(
            portfolio=portfolio, as_of=as_of, inception=inception, periods=(), problem=_NO_FLOWS
        )

    flow_dates = sorted({trade.trade_date for trade in flows})
    valuations = _Valuations(session, context, portfolio=portfolio, budget=MAX_VALUATION_POINTS)
    periods = [
        await _period(
            valuations,
            context,
            label=label,
            begin=begin,
            end=end,
            flows=flows,
            flow_dates=flow_dates,
            base=Unit.currency(portfolio.base_currency),
        )
        for label, begin, end in _spans(inception=inception, as_of=as_of)
    ]

    _log.info(
        "portfolio.returns_computed",
        portfolio=str(portfolio.id),
        as_of=as_of.isoformat(),
        periods=len(periods),
        flow_dates=len(flow_dates),
        valuations=valuations.taken,
    )
    return ReturnView(portfolio=portfolio, as_of=as_of, inception=inception, periods=tuple(periods))


def _spans(*, inception: date, as_of: date) -> list[tuple[str, date, date]]:
    """Since inception, then one row per calendar year, newest year first.

    The since-inception row leads because it is the one figure about the whole book; the
    years follow in the order a reader scans them.
    """
    spans: list[tuple[str, date, date]] = [("Since inception", inception, as_of)]
    for year in range(as_of.year, inception.year - 1, -1):
        begin = max(inception, date(year, 1, 1))
        end = min(as_of, date(year, 12, 31))
        if begin > end:  # pragma: no cover -- the range cannot produce one
            continue
        label = f"{year} to date" if end != date(year, 12, 31) else str(year)
        spans.append((label, begin, end))
    return spans


class _Valuations:
    """The book's net assets on a date, computed once per date and bounded.

    A cache with a budget rather than a cache. The budget is what keeps a book with years
    of weekly deposits from turning one page load into six hundred walks of the holdings,
    and it is spent across every period on the screen because they share their dates.
    """

    def __init__(
        self,
        session: AsyncSession,
        context: CalculationContext,
        *,
        portfolio: Portfolio,
        budget: int,
    ) -> None:
        self._session = session
        self._context = context
        self._portfolio = portfolio
        self._budget = budget
        self._seen: dict[date, PortfolioView] = {}

    @property
    def taken(self) -> int:
        return len(self._seen)

    @property
    def session(self) -> AsyncSession:
        """The session the valuations run on. A flow in another currency converts through
        a dated rate, which is a query, and threading a second session through every helper
        to reach it would be two connections for one screen."""
        return self._session

    def affordable(self, dates: Iterable[date]) -> bool:
        """Whether valuing these dates stays inside what is left of the budget."""
        wanted = {on for on in dates if on not in self._seen}
        return len(self._seen) + len(wanted) <= self._budget

    async def on(self, on: date) -> PortfolioView:
        if on not in self._seen:
            self._seen[on] = await book_as_at(
                self._session, self._context, portfolio=self._portfolio, as_of=on
            )
        return self._seen[on]


async def _period(
    valuations: _Valuations,
    context: CalculationContext,
    *,
    label: str,
    begin: date,
    end: date,
    flows: Sequence[Transaction],
    flow_dates: Sequence[date],
    base: Unit,
) -> PeriodReturn:
    """One interval's two returns, each refused separately with its own reason.

    They fail independently on purpose. The money-weighted figure needs two valuations and
    the time-weighted one needs a valuation per flow, so a book too busy for the second
    still has the first — and a reader is better served by one number and a sentence than
    by two blanks.
    """
    # The instant before the interval: what the book brought into it. Flows *on* ``begin``
    # are inside the period, which is what makes a calendar year start where a reader
    # expects and makes the since-inception row start with an empty book.
    anchor = begin - timedelta(days=1)
    inside = [on for on in flow_dates if begin <= on <= end]

    time_weighted, twr_problem = await _time_weighted(
        valuations, context, anchor=anchor, end=end, inside=inside, flows=flows, base=base
    )
    money_weighted, mwr_problem = await _money_weighted(
        valuations, context, anchor=anchor, begin=begin, end=end, flows=flows, base=base
    )
    return PeriodReturn(
        label=label,
        begin=begin,
        end=end,
        time_weighted=time_weighted,
        money_weighted=money_weighted,
        problem=" ".join(problem for problem in (twr_problem, mwr_problem) if problem),
    )


async def _time_weighted(
    valuations: _Valuations,
    context: CalculationContext,
    *,
    anchor: date,
    end: date,
    inside: Sequence[date],
    flows: Sequence[Transaction],
    base: Unit,
) -> tuple[Figure | None, str]:
    """The chain-linked return, or the reason there is none."""
    breaks = [anchor, *(on for on in inside if on < end), end]
    if not valuations.affordable(breaks):
        return None, (
            f"No time-weighted return: money moved in or out on {len(inside)} dates in "
            "this period, and measuring one properly needs a valuation at each. Past "
            f"{MAX_VALUATION_POINTS} the page would spend longer valuing than a reader "
            "would wait. The money-weighted return is unaffected."
        )

    openings: list[Quantity] = []
    closings: list[Quantity] = []
    for opens_on, closes_on in pairwise(breaks):
        opening = await valuations.on(opens_on)
        closing = await valuations.on(closes_on)
        if opening.net_assets is None and not openings and opening.problem == EMPTY:
            # The book did not exist yet. A return on nothing is undefined rather than
            # zero, and capital being *established* is not performance — so the chain
            # starts where the capital does rather than reporting the first deposit as a
            # gain of everything.
            continue
        if opening.net_assets is None or closing.net_assets is None:
            unpriced = opening if opening.net_assets is None else closing
            return None, f"No time-weighted return: {unpriced.problem}"

        # The closing value is the book *before* that date's flows, so a deposit lands in
        # the denominator of the sub-period it funds and never in the numerator of the one
        # before it. That subtraction is the entire mechanism by which a top-up stops
        # reading as performance, which is why it is a recorded calculation rather than a
        # minus sign.
        try:
            dated = [
                await _flow_in_base(valuations, context, trade=trade, base=base)
                for trade in flows
                if trade.trade_date == closes_on
            ]
        except CalculationError as refused:
            return None, f"No time-weighted return: {refused}"
        openings.append(opening.net_assets.quantity)
        closings.append(
            calc.value_before_flows(context, value=closing.net_assets.quantity, flows=dated)
        )

    measured = _from_established_capital(openings, closings)
    if measured is None:
        return None, f"No time-weighted return: {_NO_FLOWS}"

    opened, closed = measured
    try:
        chained = calc.time_weighted_return(context, openings=opened, closings=closed)
    except (CalculationError, UnitMismatchError) as refused:
        return None, f"No time-weighted return: {refused}"
    return graded_figure(context, chained), ""


def _from_established_capital(
    openings: Sequence[Quantity], closings: Sequence[Quantity]
) -> tuple[list[Quantity], list[Quantity]] | None:
    """The chain from the first sub-period that opens with capital in it.

    A book's first sub-periods open at nothing, because the book did not exist yet, and a
    return on nothing is not zero — it is undefined. Capital being *established* is not
    performance, so those sub-periods are dropped here rather than divided by. Only the
    leading ones: a book that later fell to nothing and was refunded is a real event, and
    :func:`aer.calc.performance.time_weighted_return` refuses it loudly.
    """
    first = next((index for index, opening in enumerate(openings) if opening.value > 0), None)
    if first is None:
        return None
    return list(openings[first:]), list(closings[first:])


async def _money_weighted(
    valuations: _Valuations,
    context: CalculationContext,
    *,
    anchor: date,
    begin: date,
    end: date,
    flows: Sequence[Transaction],
    base: Unit,
) -> tuple[Figure | None, str]:
    """The internal rate over the period's flows, or the reason there is none."""
    opening = await valuations.on(anchor)
    closing = await valuations.on(end)
    if closing.net_assets is None:
        return None, f"No money-weighted return: {closing.problem}"

    amounts: list[Quantity] = []
    offsets: list[Quantity] = []

    # What the book brought in, as money the investor put to work on day one. Absent for
    # the since-inception row, where the book started empty and the first deposit is
    # already in the flows below.
    if opening.net_assets is not None and opening.net_assets.value != 0:
        committed = calc.investor_side(context, amount=opening.net_assets.quantity)
        amounts.append(committed)
        offsets.append(_years(anchor, anchor, like=committed))

    for trade in flows:
        if not begin <= trade.trade_date <= end:
            continue
        try:
            amount = await _flow_in_base(valuations, context, trade=trade, base=base)
        except CalculationError as refused:
            return None, f"No money-weighted return: {refused}"
        # Turned around because the flows are stated from the investor's side: money into
        # the book is money out of the person's pocket.
        moved = calc.investor_side(context, amount=amount)
        amounts.append(moved)
        offsets.append(_years(anchor, trade.trade_date, like=moved))

    amounts.append(closing.net_assets.quantity)
    offsets.append(_years(anchor, end, like=closing.net_assets.quantity))

    try:
        rate = calc.money_weighted_return(context, flows=amounts, years=offsets)
    except (CalculationError, UnitMismatchError) as refused:
        return None, f"No money-weighted return: {refused}"
    return graded_figure(context, rate), ""


async def _flow_in_base(
    valuations: _Valuations, context: CalculationContext, *, trade: Transaction, base: Unit
) -> Quantity:
    """One external flow in the book's reporting currency, at the flow's own date.

    A deposit in a currency the book does not report in converts through a *dated* rate, at
    the date the money moved rather than at the as-of date — a rate is a fact about a day,
    and using today's for a payment made two years ago would silently restate every flow
    the return is measured over.
    """
    amount = Quantity.of(
        trade.quantity,
        Unit.currency(trade.currency),
        source=SourceRef.attestation(
            trade.attestation_id,
            grade=trade.attestation.grade,
            label=f"{trade.kind.value} {trade.trade_date.isoformat()} amount",
        ),
    )
    if amount.unit == base:
        return amount
    return await in_base(
        valuations.session, context, amount=amount, base=base, as_of=trade.trade_date
    )


def _years(anchor: date, on: date, *, like: Quantity) -> Quantity:
    """How far a date sits from the start of the period, actual/365.

    Carries the source of the figure it is dating, because it *is* that row's date: a
    reader asking why a flow was discounted 1.49 years gets the transaction it came from.
    """
    return Quantity.of(Decimal((on - anchor).days) / _DAYS_IN_YEAR, calc.YEARS, source=like.source)


# -- Exposure ----------------------------------------------------------------------------


async def exposure_as_at(
    session: AsyncSession,
    context: CalculationContext,
    *,
    portfolio: Portfolio,
    as_of: date,
    view: PortfolioView | None = None,
) -> ExposureView:
    """What the book is concentrated in, cut four ways.

    Args:
        view: The book as at the same date, when the caller already has it. A page shows
            the holdings and their exposure together, and valuing the book twice for one
            screen would be the cost of ADR 0083 paid twice for nothing. **It must have
            been computed in this same ``context``**: its figures cite calculations by id,
            and grading a lineage this ledger does not hold would be a claim about the part
            that happened to be readable. Passing one from elsewhere raises rather than
            grading what it can reach.
    """
    book = view
    if book is None:
        book = await book_as_at(session, context, portfolio=portfolio, as_of=as_of)
    # Two states, one guard. A book that would not value has no denominator, and a book
    # that nets to nothing or less has one that no fraction can be taken of — the second
    # is a real state (a withdrawal entered without the sale that funded it) which
    # `book_as_at` reports *with* a figure and a problem beside it, so testing
    # completeness alone would let it through to a division the arithmetic refuses.
    if book.net_assets is None or book.net_assets.value <= 0:
        return ExposureView(
            portfolio=portfolio,
            as_of=as_of,
            bands=(),
            top_holdings=None,
            problem=f"No exposure: {book.problem}" if book.problem else _NO_DENOMINATOR,
        )

    total = book.net_assets.quantity
    priced = [
        row
        for row in book.holdings
        if row.problem != CLOSED and row.value is not None and row.weight is not None
    ]

    bands = (
        _band(context, kind="holding", title="By holding", groups=_by_holding(priced), total=total),
        _band(context, kind="sector", title="By sector", groups=_by_sector(priced), total=total),
        _band(
            context,
            kind="currency",
            title="By currency",
            groups=_by_currency(priced, book=book),
            total=total,
        ),
        _band(
            context,
            kind="country",
            title="By listing country",
            groups=_by_country(priced),
            total=total,
        ),
    )

    weights = [row.weight.quantity for row in priced if row.weight is not None]
    top = (
        graded_figure(
            context, calc.top_holdings_share(context, weights=weights, count=CONCENTRATION_COUNT)
        )
        if weights
        else None
    )

    _log.info(
        "portfolio.exposure_computed",
        portfolio=str(portfolio.id),
        as_of=as_of.isoformat(),
        holdings=len(priced),
        bands=len(bands),
    )
    return ExposureView(portfolio=portfolio, as_of=as_of, bands=bands, top_holdings=top)


@dataclass(frozen=True, slots=True)
class _Group:
    """One label's members and their values, before they become a slice."""

    label: str
    members: tuple[str, ...]
    values: tuple[Quantity, ...]
    known: bool = True


def _band(
    context: CalculationContext,
    *,
    kind: str,
    title: str,
    groups: Sequence[_Group],
    total: Quantity,
) -> ExposureBand:
    """One cut of the book, largest first, with the unclassified group held apart."""
    slices = sorted(
        (_slice(context, group=group, total=total) for group in groups if group.known),
        key=lambda row: row.share.value,
        reverse=True,
    )
    unknown = next(
        (_slice(context, group=group, total=total) for group in groups if not group.known), None
    )
    return ExposureBand(kind=kind, title=title, slices=tuple(slices), unknown=unknown)


def _slice(context: CalculationContext, *, group: _Group, total: Quantity) -> ExposureSlice:
    value = calc.grouped_value(context, values=list(group.values))
    share = calc.exposure(context, value=value, net_assets=total)
    return ExposureSlice(
        label=group.label,
        value=graded_figure(context, value),
        share=graded_figure(context, share),
        members=group.members,
        known=group.known,
    )


def _by_holding(rows: Sequence[HoldingRow]) -> list[_Group]:
    """One group per listing.

    Not a grouping at all, and that is the point: the band shape is shared, so the holdings
    band renders through the same code and carries the same lineage as the sector band.
    """
    return [
        _Group(label=row.security.ticker, members=(row.security.ticker,), values=(value,))
        for row, value in _valued(rows)
    ]


def _by_sector(rows: Sequence[HoldingRow]) -> list[_Group]:
    """By the company's SIC description, which exists only for names a run has resolved.

    Everything else is one *named* group with its members listed. Bucketing them as "other"
    would put an invented category on the page and give it a weight, and a reader would
    have no way to tell it from a sector the book is genuinely in.
    """
    return _grouped(rows, label_of=_sector_of, unknown=UNKNOWN_SECTOR)


def _by_country(rows: Sequence[HoldingRow]) -> list[_Group]:
    return _grouped(rows, label_of=_country_of, unknown=UNKNOWN_COUNTRY)


def _by_currency(rows: Sequence[HoldingRow], *, book: PortfolioView) -> list[_Group]:
    """By the currency each position is exposed to, cash included.

    Cash is a position (ADR 0083) and carries currency risk like any other, so leaving it
    out would understate the book's own currency and overstate every other. A holding's
    exposure is the currency its *listing* trades in, reduced to major units — a London
    listing quoted in pence is exposure to sterling, not to a unit called GBX.
    """
    groups = _grouped(rows, label_of=_currency_of, unknown="Currency not known")
    groups.extend(
        _Group(
            label=_major_currency(row.currency) or row.currency,
            members=(f"{row.currency} cash",),
            values=(row.in_base.quantity,),
        )
        for row in book.cash
        if row.in_base is not None
    )
    return _merged(groups)


def _grouped(
    rows: Sequence[HoldingRow],
    *,
    label_of: Callable[[Security], str | None],
    unknown: str,
) -> list[_Group]:
    """Fold the priced holdings into named groups, the unclassified ones held together."""
    named: dict[str, list[tuple[str, Quantity]]] = {}
    missing: list[tuple[str, Quantity]] = []
    for row, value in _valued(rows):
        label = label_of(row.security)
        if label:
            named.setdefault(label, []).append((row.security.ticker, value))
        else:
            missing.append((row.security.ticker, value))

    groups = [
        _Group(
            label=label,
            members=tuple(ticker for ticker, _ in members),
            values=tuple(value for _, value in members),
        )
        for label, members in named.items()
    ]
    if missing:
        groups.append(
            _Group(
                label=unknown,
                members=tuple(ticker for ticker, _ in missing),
                values=tuple(value for _, value in missing),
                known=False,
            )
        )
    return groups


def _merged(groups: Sequence[_Group]) -> list[_Group]:
    """Fold groups sharing a label into one. Cash and a listing can be the same currency."""
    folded: dict[tuple[str, bool], _Group] = {}
    for group in groups:
        key = (group.label, group.known)
        held = folded.get(key)
        folded[key] = (
            group
            if held is None
            else _Group(
                label=group.label,
                members=held.members + group.members,
                values=held.values + group.values,
                known=group.known,
            )
        )
    return list(folded.values())


def _valued(rows: Sequence[HoldingRow]) -> list[tuple[HoldingRow, Quantity]]:
    """The rows that carry a value, with it. Callers pass priced rows; this is the narrowing
    that lets the type checker see that, rather than an assertion that it is so."""
    return [(row, row.value.quantity) for row in rows if row.value is not None]


def _sector_of(security: Security) -> str | None:
    company = security.company
    if company is None:
        return None
    return (company.sic_description or "").strip() or None


def _country_of(security: Security) -> str | None:
    return _EXCHANGE_COUNTRY.get(security.exchange.strip().upper())


def _currency_of(security: Security) -> str | None:
    return _major_currency(security.quote_currency)


def _major_currency(code: str) -> str | None:
    """A quote currency reduced to the currency it is a unit of. ``GBX`` is sterling."""
    normalised = code.strip().upper()
    if not normalised:
        return None
    major, _ = MINOR_UNITS.get(normalised, (normalised, Decimal(1)))
    return major
