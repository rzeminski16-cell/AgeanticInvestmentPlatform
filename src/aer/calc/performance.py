"""Whether a book has done well, and what it is concentrated in.

:mod:`aer.calc.portfolio` answers what the book is *worth*. This answers the other half of
what a portfolio screen is for, and the two are different questions: a book can double in
value because it doubled, or because somebody paid a second one in.

**That distinction is the whole module.** A deposit is a flow, not a gain. Every figure
here is built so that paying money in cannot read as performance:

* **Time-weighted return** breaks the series at every external flow and chain-links the
  sub-periods, so the size and timing of the flows drop out entirely. It answers "how did
  the decisions do?" — which is the number a strategy is judged on, and the one that is
  comparable to an index.
* **Money-weighted return** is the internal rate of return over those same flows. It keeps
  exactly what the other discards, so it answers "how did *I* do?" — a person who added to
  a position before it rose earned more than the strategy did, and should be told so.

Both are shown because neither is the answer on its own, and a screen showing one is
quietly asserting which question the reader meant.

**Exposure is a weight over a group**, and a group nobody can name is not a group. Sector
is known only for names a research run has touched, so the caller reports what it knows and
names the rest — bucketing unclassified holdings as "other" would invent a category and
then weight it.

Pure and side-effect free, like everything in :mod:`aer.calc`: sourced quantities in,
sourced quantities out, no session, no clock, no price lookup. Where the value series comes
from is :mod:`aer.services.portfolio`'s business.
"""

from __future__ import annotations

# Imported at runtime rather than under `TYPE_CHECKING`, for the reason
# `aer.calc.portfolio` records: the replay harness resolves each traced function's
# annotations with `get_type_hints`, and a name only the type checker can see raises there.
from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import (
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    Unit,
    UnitMismatchError,
)

__all__ = [
    "YEARS",
    "exposure",
    "grouped_value",
    "investor_side",
    "money_weighted_return",
    "time_weighted_return",
    "top_holdings_share",
    "value_before_flows",
]

# How far a flow sits from the start of the measured period. The unit system's existing
# `year`, and a sourced quantity rather than a bare number, because the offsets enter the
# arithmetic and everything that enters the arithmetic is sourced: each one comes from a
# transaction's own trade date, and a reader asking "why is this discounted 1.49 years?"
# gets the row it came from.
YEARS: Final = Unit.base("year")

# The bracket the internal rate of return is searched in, and how far the search is taken.
#
# **Fixed, because a replayed calculation must land on the same digits.** A tolerance-only
# loop with a data-dependent iteration count would too, but only as long as nothing about
# the arithmetic library changes underneath it; a stated bracket and a stated stopping
# width are the two things a reader can check by hand.
#
# The lower bound stops just short of -1, where the discount factor is zero and the
# function is undefined rather than merely steep. The upper bound of 10,000% is far past
# anything a book produces and exists so that a genuine solution is bracketed rather than
# missed.
_RATE_FLOOR: Final = Decimal("-0.999999")
_RATE_CEILING: Final = Decimal(100)
_RATE_TOLERANCE: Final = Decimal("1e-12")
_RATE_ITERATIONS: Final = 200

# Both signs, which is what an internal rate of return needs: money in, and money back.
_BOTH_SIGNS: Final = 2


@traced(
    name="time_weighted_return",
    formula="twr = ∏(closing_i / opening_i) - 1",
    assumptions=(
        "The series is broken at every external cash flow, so no sub-period contains one. "
        "That is what makes this a true time-weighted return rather than an approximation "
        "of one.",
        "An opening value is the book *after* that date's flows and a closing value is the "
        "book *before* the next date's, so a deposit lands in the denominator of the "
        "period it funds and never in the numerator of the one before it.",
        "Sub-periods are of unequal length and are not weighted by it. That is the "
        "definition, not an omission: weighting by length is what a money-weighted return "
        "does.",
    ),
)
def time_weighted_return(
    _context: CalculationContext, *, openings: Sequence[Quantity], closings: Sequence[Quantity]
) -> Quantity:
    """How the holdings did, with the flows taken out.

    The number a strategy is judged on. Paying money in raises the opening value of the
    sub-period it funds and raises the closing value by the same amount, so it cancels —
    which is precisely why this figure can be compared with an index and the money-weighted
    one cannot.

    Raises:
        CalculationError: If the two series are of different lengths, if either is empty,
            or if any opening value is zero or negative — a period that starts with nothing
            has no return, and any number produced for it would be an artefact of the
            division rather than a fact about the book.
        UnitMismatchError: If the values are not all in one currency.
    """
    if len(openings) != len(closings):
        message = (
            f"There are {len(openings)} opening values and {len(closings)} closing ones. "
            "A sub-period has both ends or it is not a sub-period."
        )
        raise CalculationError(
            message, context={"openings": len(openings), "closings": len(closings)}
        )
    if not openings:
        message = (
            "There are no sub-periods to chain. A book with no measured interval has no "
            "return, which is not the same as a return of nothing."
        )
        raise CalculationError(message, context={"sub_periods": 0})

    _one_currency([*openings, *closings], what="a value series")

    factor = Decimal(1)
    for index, (opening, closing) in enumerate(zip(openings, closings, strict=True)):
        if opening.value <= 0:
            message = (
                f"Sub-period {index} opens at {opening.value} {opening.unit.symbol}. A "
                "return is a fraction of what was there at the start, and there is no "
                "fraction of nothing — the series should begin where the capital does."
            )
            raise CalculationError(
                message, context={"sub_period": index, "opening": str(opening.value)}
            )
        factor *= closing.value / opening.value

    return Quantity.of(factor - 1, DIMENSIONLESS)


@traced(
    name="money_weighted_return",
    formula="mwr solves: Σ flow_i / (1 + mwr) ^ years_i = 0",
    assumptions=(
        "Flows are stated from the investor's side: money into the book is negative, money "
        "out of it is positive, and the book's closing value is a final positive flow.",
        "Offsets are in years from the start of the measured period, on an actual/365 "
        "basis. Nothing here compounds more often than the offsets say.",
        "The rate returned is the one root inside the searched bracket. A series with more "
        "than one sign change can have several, and this reports the first the search "
        "brackets rather than asserting it is unique.",
    ),
)
def money_weighted_return(
    _context: CalculationContext, *, flows: Sequence[Quantity], years: Sequence[Quantity]
) -> Quantity:
    """What the book earned for the person who funded it, timing included.

    The internal rate of return over the external flows and the closing value. Where the
    time-weighted return deliberately discards the size and timing of the flows, this keeps
    them: adding to a position before it rose earns a higher number here and the same
    number there, and the difference between the two is the reader's own timing.

    Raises:
        CalculationError: If the series are of different lengths or empty, if any offset is
            negative, if every offset is zero — a rate needs an interval to be a rate over
            — if the flows are all one sign, or if no rate inside the searched bracket
            balances them.
        UnitMismatchError: If the flows are not all in one currency, or if the offsets are
            not in years.
    """
    if len(flows) != len(years):
        message = (
            f"There are {len(flows)} flows and {len(years)} offsets. Every flow is dated "
            "or none of them can be discounted."
        )
        raise CalculationError(message, context={"flows": len(flows), "years": len(years)})
    if not flows:
        message = (
            "There are no flows. A rate of return over nothing is not zero, it is a "
            "question about whether the transactions arrived."
        )
        raise CalculationError(message, context={"flows": 0})

    _one_currency(flows, what="a flow series")
    _require_years(years)

    if all(offset.value <= 0 for offset in years):
        message = (
            "Every flow sits at the start of the period, so no time has passed for a rate "
            "to be a rate over. A book measured across no interval has no return."
        )
        raise CalculationError(message, context={"years": 0})

    signs = {flow.value > 0 for flow in flows if flow.value != 0}
    if len(signs) < _BOTH_SIGNS:
        message = (
            "The flows are all one sign, so nothing was ever returned against what went "
            "in — or nothing went in. An internal rate of return needs both sides."
        )
        raise CalculationError(message, context={"flows": len(flows)})

    pairs = tuple((flow.value, offset.value) for flow, offset in zip(flows, years, strict=True))
    return Quantity.of(_solved_rate(pairs), DIMENSIONLESS)


@traced(
    name="value_before_flows",
    formula="before = value - Σ(flows)",
    assumptions=(
        "The flows are the external ones dated on the day the value is taken. Everything "
        "the holdings themselves produced is left in, because that is the return.",
        "A day with no external flow leaves the value untouched, and is recorded as such "
        "rather than skipped — no money moving is a fact about the day.",
    ),
)
def value_before_flows(
    _context: CalculationContext, *, value: Quantity, flows: Sequence[Quantity]
) -> Quantity:
    """The book at the end of a day, as it stood *before* that day's money moved.

    The sub-period's closing value, and the entire mechanism by which a top-up stops
    reading as performance: a deposit raises the value the day it lands, so leaving it in
    the numerator would credit the strategy with the operator's own money.

    Raises:
        UnitMismatchError: If a flow is not in the currency of the value.
    """
    total = value.value
    for flow in flows:
        if flow.unit != value.unit:
            message = (
                f"The book is valued in {value.unit.symbol} and a flow is in "
                f"{flow.unit.symbol}. Convert first, as a recorded calculation over a "
                "dated rate."
            )
            raise UnitMismatchError(
                message, context={"value": value.unit.symbol, "flow": flow.unit.symbol}
            )
        total -= flow.value
    return Quantity.of(total, value.unit)


@traced(
    name="investor_side",
    formula="investor = -book",
    assumptions=(
        "The ledger states a movement from the book's side: a deposit is money in. An "
        "internal rate of return is the person's, and the two are opposite by definition "
        "rather than by accident.",
    ),
)
def investor_side(_context: CalculationContext, *, amount: Quantity) -> Quantity:
    """One movement seen from the side of the person who funded the book.

    A deposit into the book is money out of their pocket, and so is the value a period
    opens with — capital they had committed before it began. Its own calculation rather
    than a negation folded into the caller, because a sign flip that leaves no record is
    the one arithmetic step nobody would think to check.
    """
    return Quantity.of(-amount.value, amount.unit)


@traced(
    name="grouped_value",
    formula="value = Σ(values)",
    assumptions=(
        "The group is the caller's: this adds what it is handed and does not decide what "
        "belongs together.",
        "Every member is already in one currency, converted through its own dated rate. "
        "Nothing here converts.",
    ),
)
def grouped_value(_context: CalculationContext, *, values: Sequence[Quantity]) -> Quantity:
    """What one group of positions is worth.

    Its own calculation rather than a sum folded into :func:`exposure`, so the currency
    amount a screen shows beside a share has a record of its own. A figure on a portfolio
    page with no lineage is one nobody could tell from a documented one.

    Raises:
        CalculationError: If the group is empty. An empty sum is zero, and zero here would
            be a figure somebody could act on standing in for the absence of one.
        UnitMismatchError: If the members are not all in one currency.
    """
    if not values:
        message = (
            "There is nothing in this group to add. An empty group worth zero is a figure "
            "standing in for the absence of one."
        )
        raise CalculationError(message, context={"values": 0})

    unit = _one_currency(values, what="an exposure group")
    return Quantity.of(sum((value.value for value in values), Decimal(0)), unit)


@traced(
    name="exposure",
    formula="exposure = value / net_assets",
    assumptions=(
        "The denominator includes cash, exactly as a single holding's weight does. A "
        "group weighted over securities alone overstates every group, silently.",
    ),
)
def exposure(_context: CalculationContext, *, value: Quantity, net_assets: Quantity) -> Quantity:
    """What share of the book sits in one group — a sector, a currency, a country.

    Raises:
        CalculationError: If net assets are zero or negative — a fraction of an empty book
            is undefined rather than nil, and rendering it as 0% would say something false
            about positions that exist.
        UnitMismatchError: If the group and the total are in different currencies.
    """
    if net_assets.value <= 0:
        message = (
            f"Net assets are {net_assets.value} {net_assets.unit.symbol}, so there is no "
            "fraction of them to take. An exposure against an empty or negative book is "
            "undefined rather than nil."
        )
        raise CalculationError(message, context={"net_assets": str(net_assets.value)})
    if value.unit != net_assets.unit:
        message = (
            f"The group is in {value.unit.symbol} and the total in "
            f"{net_assets.unit.symbol}. Convert first, as a recorded calculation over a "
            "dated rate."
        )
        raise UnitMismatchError(
            message, context={"group": value.unit.symbol, "total": net_assets.unit.symbol}
        )
    return value / net_assets


@traced(
    name="top_holdings_share",
    formula="share = Σ(the `count` largest weights)",
    assumptions=(
        "Every weight the book has is passed in, and the ranking happens here. A caller "
        "that pre-selected the five it thought were largest would be recording its own "
        "sort rather than the book's concentration.",
        "A book with fewer holdings than `count` reports all of them, which is the honest "
        "answer: its top five is everything it has.",
    ),
)
def top_holdings_share(
    _context: CalculationContext, *, weights: Sequence[Quantity], count: int
) -> Quantity:
    """How much of the book sits in its largest positions.

    Raises:
        CalculationError: If there are no weights, or if ``count`` is not positive.
        UnitMismatchError: If any weight carries a unit — a weight is a fraction, and a
            currency in this position is an argument passed in the wrong order.
    """
    if not weights:
        message = (
            "There are no weights to rank. A concentration figure over no holdings is not "
            "zero, it is a question about whether the book was read."
        )
        raise CalculationError(message, context={"weights": 0})
    if count <= 0:
        message = f"A top-{count} share is not a quantity. Ask for at least one holding."
        raise CalculationError(message, context={"count": count})

    for weight in weights:
        if weight.unit != DIMENSIONLESS:
            message = (
                f"A weight in {weight.unit.symbol} is not a fraction of the book. A value "
                "passed where a weight belongs makes the concentration a currency amount."
            )
            raise UnitMismatchError(message, context={"unit": weight.unit.symbol})

    ranked = sorted((weight.value for weight in weights), reverse=True)
    return Quantity.of(sum(ranked[:count], Decimal(0)), DIMENSIONLESS)


# -- Shared guards ---------------------------------------------------------------------------


def _solved_rate(pairs: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    """Bisect the discounted-flow function for the rate that balances it.

    Bisection rather than Newton's method, and that is the deliberate trade: it is slower
    and it cannot diverge. A rate that silently converged to the wrong root, or failed to
    converge and returned its last guess, would be a figure on a screen with no way for a
    reader to tell. The bracket is stated, the stopping width is stated, and a series the
    bracket does not straddle is refused rather than approximated.
    """
    low, high = _RATE_FLOOR, _RATE_CEILING
    at_low, at_high = _present_value(pairs, low), _present_value(pairs, high)

    if at_low == 0:
        return low
    if at_high == 0:
        return high
    if (at_low > 0) == (at_high > 0):
        message = (
            f"No rate between {low} and {high} balances these flows. That is usually a "
            "book whose whole value was withdrawn, or one whose closing value is missing "
            "from the series."
        )
        raise CalculationError(
            message, context={"at_floor": str(at_low), "at_ceiling": str(at_high)}
        )

    for _ in range(_RATE_ITERATIONS):
        if high - low <= _RATE_TOLERANCE:
            break
        middle = (low + high) / 2
        at_middle = _present_value(pairs, middle)
        if at_middle == 0:
            return middle
        if (at_middle > 0) == (at_low > 0):
            low, at_low = middle, at_middle
        else:
            high = middle

    return (low + high) / 2


def _present_value(pairs: Sequence[tuple[Decimal, Decimal]], rate: Decimal) -> Decimal:
    """The flows discounted to the start of the period at one rate."""
    factor_base = Decimal(1) + rate
    total = Decimal(0)
    for amount, offset in pairs:
        total += amount if offset == 0 else amount / (factor_base**offset)
    return total


def _one_currency(values: Sequence[Quantity], *, what: str) -> Unit:
    units = {value.unit for value in values}
    if len(units) > 1:
        symbols = sorted(unit.symbol for unit in units)
        message = (
            f"{what} in {len(units)} different units — {', '.join(symbols)}. Currencies "
            "never convert implicitly; convert first, as a recorded calculation over a "
            "dated rate."
        )
        raise UnitMismatchError(message, context={"units": ",".join(symbols)})
    unit = next(iter(units))
    if not unit.currencies:
        message = (
            f"{unit.symbol} is not a currency, and {what} is money. A share count or a "
            "ratio in this position is an argument passed in the wrong order."
        )
        raise UnitMismatchError(message, context={"unit": unit.symbol})
    return unit


def _require_years(offsets: Sequence[Quantity]) -> None:
    for index, offset in enumerate(offsets):
        if offset.unit != YEARS:
            message = (
                f"Offset {index} is in {offset.unit.symbol}, not years. A discount exponent "
                "with a currency in it is an argument passed in the wrong order."
            )
            raise UnitMismatchError(message, context={"unit": offset.unit.symbol})
        if offset.value < 0:
            message = (
                f"Offset {index} is {offset.value} years. A flow before the start of the "
                "period it is measured over is a period that begins in the wrong place."
            )
            raise CalculationError(message, context={"offset": str(offset.value)})
