"""What could go wrong with the book, as arithmetic.

ADR 0080 refused the risk analyst every number and named them: volatility, drawdown,
expected shortfall, a position's contribution to the book's risk, scenario profit and loss.
ADR 0106 settled the series they are measured over — the weights the book holds now, over
the daily returns the holdings had — and this module is the figures.

**Ex-ante, and every figure says so.** The book's return series is the weighted sum of its
holdings' returns with today's weights held fixed, which answers "if the book stayed as it
is, how would it have moved?" and not "how did it move?". The second is
:mod:`aer.calc.performance`'s question and a different number.

**Nothing here chooses a scenario.** :func:`scenario_pnl` applies shocks it is handed; what
the shocks are and what they reach is the operator's statement and the service's matching.

Pure and side-effect free, like everything in :mod:`aer.calc`: sourced quantities in,
sourced quantities out, no session, no clock, no price lookup.
"""

from __future__ import annotations

# Imported at runtime rather than under `TYPE_CHECKING`, for the reason `aer.calc.portfolio`
# records: the replay harness resolves each traced function's annotations with
# `get_type_hints`, and a name only the type checker can see raises there.
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, localcontext
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.prices import MIN_RETURN_OBSERVATIONS, Frequency, InsufficientHistoryError
from aer.calc.units import (
    CALC_CONTEXT,
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
)

__all__ = [
    "DEFAULT_TAIL_PER_CENT",
    "MIN_TAIL_OBSERVATIONS",
    "PERIODS_PER_YEAR",
    "annualised_volatility",
    "combined_shock",
    "cumulative_index",
    "expected_shortfall",
    "max_drawdown",
    "position_pnl",
    "risk_contribution",
    "scenario_impact",
    "scenario_pnl",
    "static_weight_returns",
]

# How many returns a year holds at each frequency. Trading days rather than calendar days
# for the daily figure: a volatility annualised over 365 counts weekends the market did not
# trade, and the convention every published figure uses is 252.
PERIODS_PER_YEAR: Final[dict[Frequency, int]] = {
    Frequency.DAILY: 252,
    Frequency.WEEKLY: 52,
    Frequency.MONTHLY: 12,
}

# The tail an expected shortfall averages over, in whole per cent — an integer because it
# is a recorded parameter of the calculation and the engine takes parameters as plain
# values, never as bare decimals. Twenty observations at five per cent put one return in
# the tail, which is the floor below which the figure is a single day wearing a statistic's
# name.
DEFAULT_TAIL_PER_CENT: Final = 5
MIN_TAIL_OBSERVATIONS: Final = 20


# -- The series -----------------------------------------------------------------------------


def static_weight_returns(
    weights: Mapping[str, Quantity],
    returns: Mapping[str, Sequence[tuple[date, Quantity]]],
    *,
    source: SourceRef,
) -> tuple[tuple[date, Quantity], ...]:
    """The book's return on each date, with today's weights held fixed (ADR 0106 §1).

    ``weights`` and ``returns`` are keyed the same way, one entry per measured holding.
    Only the dates every holding traded are kept: a London listing and a US one keep
    different holidays, and a day one of them did not print is a day the book's return
    would be missing a term rather than a day it was zero.

    Weights that do not sum to one are the ordinary case — cash and any unmeasured holding
    earn nothing here — and are used as given. The caller states the coverage.

    **Each return carries a source**, because the variance and the drawdown are traced over
    them and the engine refuses a number that cannot say where it came from. Like
    :func:`aer.calc.prices.simple_returns`, this is a helper rather than a traced figure:
    a record per date would be two hundred rows saying "weighted sum" and nothing else.

    Raises:
        CalculationError: If the two mappings do not name the same holdings, or either is
            empty, or a weight or a return is not dimensionless.
        InsufficientHistoryError: If fewer than :data:`MIN_RETURN_OBSERVATIONS` dates are
            shared by every holding.
    """
    if not weights or set(weights) != set(returns):
        message = (
            "The weights and the return series must name the same holdings, and at least "
            f"one: weights for {sorted(weights)} against returns for {sorted(returns)}."
        )
        raise CalculationError(
            message, context={"weights": sorted(weights), "returns": sorted(returns)}
        )
    for key, weight in weights.items():
        _require_dimensionless(weight, what=f"the weight of {key}")

    by_holding = {key: dict(series) for key, series in returns.items()}
    shared: set[date] | None = None
    for series in by_holding.values():
        shared = set(series) if shared is None else shared & set(series)
    dates = sorted(shared or ())
    if len(dates) < MIN_RETURN_OBSERVATIONS:
        message = (
            f"The holdings share {len(dates)} trading day(s), and a book's return series "
            f"needs at least {MIN_RETURN_OBSERVATIONS}. Check the price histories cover the "
            "same window."
        )
        raise InsufficientHistoryError(
            message, context={"shared": len(dates), "minimum": MIN_RETURN_OBSERVATIONS}
        )

    out: list[tuple[date, Quantity]] = []
    for when in dates:
        total = Decimal(0)
        for key, series in by_holding.items():
            observed = series[when]
            _require_dimensionless(observed, what=f"the return of {key} on {when.isoformat()}")
            with localcontext(CALC_CONTEXT):
                total += weights[key].value * observed.value
        out.append((when, Quantity.of(total, DIMENSIONLESS, source=source)))
    return tuple(out)


def cumulative_index(
    returns: Sequence[tuple[date, Quantity]], *, source: SourceRef
) -> tuple[Quantity, ...]:
    """An index starting at one, compounded through the returns. What a drawdown is over.

    The first element is the starting level, so a series of ``n`` returns gives ``n + 1``
    levels and a drawdown from the very first day is measurable.

    Raises:
        CalculationError: If a return is not dimensionless or is -100% or worse — an index
            that reaches nil has no later level a fall could be measured from.
    """
    levels = [Quantity.of(1, DIMENSIONLESS, source=source)]
    running = Decimal(1)
    for when, observed in returns:
        _require_dimensionless(observed, what=f"the return on {when.isoformat()}")
        if observed.value <= -1:
            message = (
                f"The return on {when.isoformat()} is {observed.value}, which takes the index "
                "to nil or below. No level after it could be measured from."
            )
            raise CalculationError(message, context={"on": when.isoformat()})
        with localcontext(CALC_CONTEXT):
            running = running * (Decimal(1) + observed.value)
        levels.append(Quantity.of(running, DIMENSIONLESS, source=source))
    return tuple(levels)


# -- The figures ----------------------------------------------------------------------------


@traced(
    name="annualised_volatility",
    formula="volatility = sqrt(variance * periods_per_year)",
    assumptions=(
        "Returns are independent across periods, so variance scales with their number.",
        "The variance is of the return series in each listing's own currency; currency "
        "moves are an exposure, not folded into this figure (ADR 0106).",
    ),
)
def annualised_volatility(
    _context: CalculationContext, *, variance: Quantity, periods_per_year: int
) -> Quantity:
    """The standard deviation of returns, scaled to a year.

    Raises:
        UnitMismatchError: If the variance is not dimensionless — a variance of prices
            rather than of returns.
        CalculationError: If the variance is negative, or the periods are not positive.
    """
    _require_dimensionless(variance, what="a variance of returns")
    if variance.value < 0:
        message = f"A variance of {variance.value} is not one; a variance is never negative."
        raise CalculationError(message, context={"variance": str(variance.value)})
    if periods_per_year <= 0:
        message = f"{periods_per_year} periods a year is not a frequency."
        raise CalculationError(message, context={"periods_per_year": periods_per_year})
    with localcontext(CALC_CONTEXT):
        return Quantity.of((variance.value * Decimal(periods_per_year)).sqrt(), DIMENSIONLESS)


@traced(
    name="max_drawdown",
    formula="max_drawdown = min over t of (level_t / max over s ≤ t of level_s - 1)",
    assumptions=(
        "Measured on an index compounded through the book's return series with today's "
        "weights held fixed, not on the book's realised history (ADR 0106).",
    ),
)
def max_drawdown(_context: CalculationContext, *, levels: Sequence[Quantity]) -> Quantity:
    """The worst peak-to-trough fall, as a fraction at or below zero.

    Raises:
        CalculationError: If there are no levels, or any level is not positive.
        UnitMismatchError: If the levels are not all in one unit.
    """
    if not levels:
        message = "A drawdown over no levels is undefined."
        raise CalculationError(message)
    _require_one_unit(levels, what="a drawdown")
    worst = Decimal(0)
    peak = Decimal(0)
    with localcontext(CALC_CONTEXT):
        for level in levels:
            if level.value <= 0:
                message = (
                    f"A level of {level.value} is not one an index reaches; every level is "
                    "positive."
                )
                raise CalculationError(message, context={"level": str(level.value)})
            peak = max(peak, level.value)
            fall = level.value / peak - Decimal(1)
            worst = min(worst, fall)
    return Quantity.of(worst, DIMENSIONLESS)


@traced(
    name="expected_shortfall",
    formula="expected_shortfall = mean of the worst ceil(n * tail_per_cent / 100) returns",
    assumptions=(
        "Historical: the tail is the returns that happened, with no distribution fitted.",
        "The tail fraction is a parameter, and the figure changes with it.",
    ),
)
def expected_shortfall(
    _context: CalculationContext, *, observations: Sequence[Quantity], tail_per_cent: int
) -> Quantity:
    """The average of the worst ``tail_per_cent`` of returns, as a return.

    Negative for a series with losses in its tail, which every real one has: the figure is
    a return, not a loss, so a reader adds it to nothing and multiplies it by nothing to
    read it.

    Raises:
        InsufficientHistoryError: If there are fewer than :data:`MIN_TAIL_OBSERVATIONS`.
        CalculationError: If the tail is not strictly between nil and a hundred per cent.
        UnitMismatchError: If the observations are not all in one unit.
    """
    if not 0 < tail_per_cent < 100:  # noqa: PLR2004 -- per cent, and the bounds are the point
        message = (
            f"A tail of {tail_per_cent} per cent is not a share of the observations; it lies "
            "strictly between 0 and 100."
        )
        raise CalculationError(message, context={"tail_per_cent": tail_per_cent})
    if len(observations) < MIN_TAIL_OBSERVATIONS:
        message = (
            f"An expected shortfall over {len(observations)} observation(s) is a single day "
            f"wearing a statistic's name. At least {MIN_TAIL_OBSERVATIONS} are needed."
        )
        raise InsufficientHistoryError(
            message,
            context={"observations": len(observations), "minimum": MIN_TAIL_OBSERVATIONS},
        )
    unit = _require_one_unit(observations, what="an expected shortfall")
    with localcontext(CALC_CONTEXT):
        count = int(
            (Decimal(len(observations)) * Decimal(tail_per_cent) / Decimal(100)).to_integral_value(
                rounding="ROUND_CEILING"
            )
        )
        worst = sorted(item.value for item in observations)[:count]
        return Quantity.of(sum(worst, Decimal(0)) / Decimal(count), unit)


@traced(
    name="risk_contribution",
    formula="contribution = weight * beta_to_book",
    assumptions=(
        "The beta is measured against the book's own return series, so the contributions of "
        "the measured holdings sum to one.",
    ),
)
def risk_contribution(
    _context: CalculationContext, *, weight: Quantity, beta_to_book: Quantity
) -> Quantity:
    """One holding's share of the book's variance.

    Raises:
        UnitMismatchError: If either input is not dimensionless.
    """
    _require_dimensionless(weight, what="a weight")
    _require_dimensionless(beta_to_book, what="a beta")
    return weight * beta_to_book


@traced(
    name="combined_shock",
    formula="shock = ∏(1 + shock_i) - 1",
    assumptions=("Shocks that reach the same position compound rather than add.",),
)
def combined_shock(_context: CalculationContext, *, shocks: Sequence[Quantity]) -> Quantity:
    """What a position takes when more than one shock reaches it.

    Raises:
        CalculationError: If there are no shocks, or one is -100% or worse.
        UnitMismatchError: If a shock is not dimensionless.
    """
    if not shocks:
        message = "A combined shock over no shocks is not a shock."
        raise CalculationError(message)
    factor = Decimal(1)
    with localcontext(CALC_CONTEXT):
        for shock in shocks:
            _require_dimensionless(shock, what="a shock")
            if shock.value <= -1:
                message = (
                    f"A shock of {shock.value} takes a position to nil or below; the largest "
                    "fall is -1."
                )
                raise CalculationError(message, context={"shock": str(shock.value)})
            factor *= Decimal(1) + shock.value
        return Quantity.of(factor - Decimal(1), DIMENSIONLESS)


@traced(
    name="scenario_pnl",
    formula="pnl = Σ value_i * shock_i",
    assumptions=(
        "Each value is the position's worth in the book's currency as at the date, and each "
        "shock is the fraction the scenario moves it by; a position the scenario does not "
        "reach is not in the sum.",
    ),
)
def scenario_pnl(
    _context: CalculationContext, *, values: Sequence[Quantity], shocks: Sequence[Quantity]
) -> Quantity:
    """What a stated scenario does to the book, in its currency.

    Raises:
        CalculationError: If the two sequences differ in length, or the scenario reaches
            nothing — a profit and loss over no positions has no currency to be in.
        UnitMismatchError: If the values are not all one currency, or a shock has a unit.
    """
    if len(values) != len(shocks):
        message = f"{len(values)} values against {len(shocks)} shocks; a scenario pairs them."
        raise CalculationError(message, context={"values": len(values), "shocks": len(shocks)})
    if not values:
        message = "This scenario reaches no position in the book, so it has no profit and loss."
        raise CalculationError(message)
    unit = _require_one_unit(values, what="a scenario profit and loss")
    if not unit.currencies:
        message = f"Positions are valued in a currency, not in {unit.symbol}."
        raise UnitMismatchError(message, context={"unit": unit.symbol})
    total = Decimal(0)
    with localcontext(CALC_CONTEXT):
        for value, shock in zip(values, shocks, strict=True):
            _require_dimensionless(shock, what="a shock")
            total += value.value * shock.value
    return Quantity.of(total, unit)


@traced(
    name="position_pnl",
    formula="pnl_i = value_i * shock_i",
    assumptions=(
        "The value is the position's worth in the book's currency as at the date, and the "
        "shock is the combined fraction the scenario moves it by.",
    ),
)
def position_pnl(_context: CalculationContext, *, value: Quantity, shock: Quantity) -> Quantity:
    """What a stated scenario does to one position, in the book's currency.

    One term of :func:`scenario_pnl`, recorded on its own so a page can show the scenario
    position by position — each row a calculation, and the rows summing to the total.

    Raises:
        UnitMismatchError: If the value is not in a currency, or the shock has a unit.
    """
    if not value.unit.currencies:
        message = f"A position is valued in a currency, not in {value.unit.symbol}."
        raise UnitMismatchError(message, context={"unit": value.unit.symbol})
    _require_dimensionless(shock, what="a shock")
    with localcontext(CALC_CONTEXT):
        return Quantity.of(value.value * shock.value, value.unit)


@traced(
    name="scenario_impact",
    formula="impact = pnl / net_assets",
    assumptions=("Net assets are the book's as at the same date, cash included.",),
)
def scenario_impact(
    _context: CalculationContext, *, pnl: Quantity, net_assets: Quantity
) -> Quantity:
    """A scenario's profit and loss as a share of the book.

    Raises:
        UnitMismatchError: If the two are not in the same currency.
        CalculationError: If net assets are not positive.
    """
    if pnl.unit != net_assets.unit:
        message = (
            f"A profit and loss in {pnl.unit.symbol} against net assets in "
            f"{net_assets.unit.symbol}; both are the book's currency."
        )
        raise UnitMismatchError(
            message, context={"pnl": pnl.unit.symbol, "net_assets": net_assets.unit.symbol}
        )
    if net_assets.value <= 0:
        message = f"Net assets of {net_assets.value} leave no whole for a loss to be a share of."
        raise CalculationError(message, context={"net_assets": str(net_assets.value)})
    return pnl / net_assets


# -- Guards ---------------------------------------------------------------------------------


def _require_dimensionless(quantity: Quantity, *, what: str) -> None:
    if quantity.unit != DIMENSIONLESS:
        message = f"{what} carries the unit {quantity.unit.symbol}; a fraction has none."
        raise UnitMismatchError(message, context={"unit": quantity.unit.symbol})


def _require_one_unit(values: Sequence[Quantity], *, what: str) -> Unit:
    units = {item.unit for item in values}
    if len(units) == 1:
        return units.pop()
    message = (
        f"{what} needs one unit and these values carry "
        f"{', '.join(sorted(unit.symbol for unit in units))}."
    )
    raise UnitMismatchError(message, context={"units": sorted(unit.symbol for unit in units)})
