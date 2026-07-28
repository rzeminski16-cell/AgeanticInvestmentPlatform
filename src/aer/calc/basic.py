"""The first calculations, and the shape every later one follows.

Six functions. None of them is difficult, and that is the point: a growth rate is four
lines of arithmetic, and the reason it lives here rather than in a prompt is that four
lines of arithmetic are *verifiable* whereas a sentence asking for a growth rate is not.
Phase 3's discounted cash flow will be forty lines in this same shape.

**Guards, and why they refuse rather than return zero.** Several of these have inputs for
which no meaningful answer exists — growth from a base of zero, a compound rate over zero
years, a weighted average whose weights sum to nothing. Every one of them raises. Returning
zero, or ``None``, or infinity, produces a figure that flows into the next calculation and
eventually into a report, where nobody can tell it apart from a real one. A refusal stops
at the point where the cause is still obvious.

**Sign changes have no compound rate.** Revenue going from -50 to +100 did not grow at any
percentage; the question is malformed. :func:`cagr` says so instead of returning a number
derived from a fractional power of a negative, which is not real.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import (
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    Unit,
    UnitMismatchError,
)

__all__ = [
    "cagr",
    "growth_rate",
    "margin",
    "ratio",
    "weighted_average",
    "yoy_series",
]

_ONE = Quantity.of(1)


@traced(
    name="growth_rate",
    formula="growth = (end - start) / |start|",
    assumptions=("The two values cover comparable periods of equal length.",),
)
def growth_rate(_context: CalculationContext, *, start: Quantity, end: Quantity) -> Quantity:
    """Simple period-on-period growth, as a fraction.

    The denominator is the **absolute** value of the start. Dividing by a negative base
    gives growth the wrong sign — a loss narrowing from -100 to -50 is an improvement, and
    ``(-50 - -100) / -100`` calls it -50%.

    Raises:
        UnitMismatchError: If the two quantities are in different units.
        CalculationError: If the starting value is zero.
    """
    if start.unit != end.unit:
        message = (
            f"Cannot compute growth from {start.unit.symbol} to {end.unit.symbol}. Growth "
            "is a comparison between two measures of the same thing."
        )
        raise UnitMismatchError(
            message, context={"start": start.unit.symbol, "end": end.unit.symbol}
        )

    if start.value == 0:
        message = (
            "Growth from a base of zero is undefined — every increase is infinite. Report "
            "the absolute change instead, or say the base period had none."
        )
        raise CalculationError(message, context={"end": str(end.value)})

    return (end - start) / abs(start)


@traced(
    name="cagr",
    formula="cagr = (end / start) ^ (1 / years) - 1",
    assumptions=(
        "Compounding is annual and the periods are of equal length.",
        "The series has no sign change; a CAGR across one is undefined.",
    ),
)
def cagr(_context: CalculationContext, *, start: Quantity, end: Quantity, years: int) -> Quantity:
    """Compound annual growth rate, as a fraction.

    ``years`` is the number of compounding periods between the two observations — four
    annual figures span three years, not four. It is a parameter rather than a sourced
    input: it is a structural property of which two facts were chosen, not a measurement,
    and it is recorded on the calculation either way.

    Raises:
        UnitMismatchError: If the two quantities are in different units.
        CalculationError: On a zero or negative starting value, a non-positive number of
            years, or a sign change between the endpoints.
    """
    if start.unit != end.unit:
        message = (
            f"Cannot compound {start.unit.symbol} into {end.unit.symbol}. A CAGR compares "
            "one measure with itself at two dates."
        )
        raise UnitMismatchError(
            message, context={"start": start.unit.symbol, "end": end.unit.symbol}
        )

    if years <= 0:
        message = (
            f"A CAGR needs at least one compounding period, not {years}. Four annual "
            "figures span three years."
        )
        raise CalculationError(message, context={"years": years})

    if start.value <= 0 or end.value <= 0:
        # Not merely a domain guard for the fractional power. A series that crosses zero
        # has no compound rate at all: there is no constant percentage that takes -50 to
        # +100, and any number returned here would be an artefact of the arithmetic rather
        # than a fact about the business.
        message = (
            f"A CAGR from {start.value} to {end.value} is undefined. A compound rate "
            "requires both endpoints to be positive; a series that crosses zero has no "
            "constant growth rate, and quoting one would be inventing a figure."
        )
        raise CalculationError(message, context={"start": str(start.value), "end": str(end.value)})

    return (end / start).power(Decimal(1) / Decimal(years)) - _ONE


@traced(
    name="ratio",
    formula="ratio = numerator / denominator",
)
def ratio(_context: CalculationContext, *, numerator: Quantity, denominator: Quantity) -> Quantity:
    """One quantity over another, carrying whatever unit results.

    Like over like gives a pure number: a current ratio is dimensionless. Unlike over
    unlike keeps both: earnings over shares is USD/shares, and nothing downstream can then
    add it to a plain dollar figure by accident.

    Raises:
        CalculationError: If the denominator is zero.
    """
    return numerator / denominator


@traced(
    name="margin",
    formula="margin = part / whole",
    assumptions=("Both figures cover the same period.",),
)
def margin(_context: CalculationContext, *, part: Quantity, whole: Quantity) -> Quantity:
    """A margin, as a fraction of the whole.

    Distinct from :func:`ratio` despite the identical arithmetic, because a margin makes a
    stronger claim: that the two figures are in the same unit and the first is a component
    of the second. Enforcing that here means a gross margin computed from revenue in
    dollars and cost in pounds fails, instead of quietly producing USD/GBP.

    Raises:
        UnitMismatchError: If the two quantities are in different units.
        CalculationError: If the whole is zero.
    """
    if part.unit != whole.unit:
        message = (
            f"A margin of {part.unit.symbol} over {whole.unit.symbol} is not a margin. "
            "Both figures must measure the same thing in the same unit."
        )
        raise UnitMismatchError(
            message, context={"part": part.unit.symbol, "whole": whole.unit.symbol}
        )

    if whole.value == 0:
        message = "A margin on a base of zero is undefined."
        raise CalculationError(message, context={"part": str(part.value)})

    return part / whole


@traced(
    name="weighted_average",
    formula="weighted_average = sum(value_i * weight_i) / sum(weight_i)",
    assumptions=("Weights are comparable and non-negative.",),
)
def weighted_average(
    _context: CalculationContext, *, values: Sequence[Quantity], weights: Sequence[Quantity]
) -> Quantity:
    """The weighted mean of a series.

    Weights need not sum to one — they are normalised by their own total, so raw market
    capitalisations or revenues can be passed directly. Each value and each weight is
    recorded as its own input, so a peer average can be traced back to which company
    contributed what.

    Raises:
        CalculationError: If the two sequences differ in length, are empty, contain mixed
            units, or the weights sum to zero.
        UnitMismatchError: If the values are not all in the same unit.
    """
    if len(values) != len(weights):
        message = (
            f"{len(values)} values and {len(weights)} weights. A weighted average needs "
            "one weight per value; a mismatch means one of the two lists is missing "
            "something, and guessing which would silently drop a company."
        )
        raise CalculationError(message, context={"values": len(values), "weights": len(weights)})

    if not values:
        message = "A weighted average of nothing is undefined."
        raise CalculationError(message, context={"values": 0})

    value_unit = values[0].unit
    for index, value in enumerate(values):
        if value.unit != value_unit:
            message = (
                f"Value {index} is in {value.unit.symbol} but the first is in "
                f"{value_unit.symbol}. Averaging across units produces a number that "
                "measures nothing."
            )
            raise UnitMismatchError(
                message, context={"index": index, "expected": value_unit.symbol}
            )

    weight_unit = weights[0].unit
    for index, weight in enumerate(weights):
        if weight.unit != weight_unit:
            message = (
                f"Weight {index} is in {weight.unit.symbol} but the first is in "
                f"{weight_unit.symbol}. Weights must be commensurable to be summed."
            )
            raise UnitMismatchError(
                message, context={"index": index, "expected": weight_unit.symbol}
            )

    total_weight = Quantity(value=Decimal(0), unit=weight_unit)
    for weight in weights:
        total_weight = total_weight + weight

    if total_weight.value == 0:
        message = (
            "The weights sum to zero, so there is nothing to average by. This usually "
            "means every peer was excluded, or the weighting column was empty."
        )
        raise CalculationError(message, context={"count": len(weights)})

    weighted = Quantity(value=Decimal(0), unit=value_unit * weight_unit)
    for value, weight in zip(values, weights, strict=True):
        weighted = weighted + value * weight

    return weighted / total_weight


@traced(
    name="yoy_series",
    formula="yoy_i = (value_i - value_{i-1}) / |value_{i-1}|, reported as the mean",
    assumptions=(
        "The series is in chronological order, oldest first.",
        "Consecutive observations are one period apart.",
    ),
)
def yoy_series(_context: CalculationContext, *, values: Sequence[Quantity]) -> Quantity:
    """The mean of the period-on-period growth rates across a series.

    Returns the arithmetic mean of the individual growth rates, which is deliberately
    **not** the same as a CAGR and answers a different question: the mean says how much a
    typical year moved, the CAGR says what constant rate would have produced the endpoints.
    A volatile series has a mean well above its CAGR, and the gap is itself informative —
    which is why both exist rather than one standing in for the other.

    Raises:
        CalculationError: If there are fewer than two observations, or any value except
            the last is zero.
        UnitMismatchError: If the observations are not all in the same unit.
    """
    minimum_observations = 2
    if len(values) < minimum_observations:
        message = (
            f"A year-on-year series needs at least two observations, not {len(values)}. "
            "One observation has nothing to be compared with."
        )
        raise CalculationError(message, context={"observations": len(values)})

    unit = values[0].unit
    for index, value in enumerate(values):
        if value.unit != unit:
            message = (
                f"Observation {index} is in {value.unit.symbol} but the series starts in "
                f"{unit.symbol}. A series that changes unit part-way is two series."
            )
            raise UnitMismatchError(message, context={"index": index, "expected": unit.symbol})

    total = Quantity(value=Decimal(0), unit=DIMENSIONLESS)
    steps = 0
    for previous, current in pairwise(values):
        if previous.value == 0:
            message = (
                f"Observation {steps} is zero, so the growth into the next period is "
                "undefined. Report the absolute change across that step instead."
            )
            raise CalculationError(message, context={"index": steps})
        total = total + (current - previous) / abs(previous)
        steps += 1

    return total / Quantity.of(steps)


def periods_between(observations: int) -> int:
    """Compounding periods spanned by a number of observations.

    Four annual figures span three years. Trivial, and worth naming: the off-by-one here
    is the most common arithmetic error in a growth calculation, and it produces a CAGR
    that is wrong by roughly a third rather than obviously broken.
    """
    if observations < 1:
        message = f"{observations} observations span no periods."
        raise CalculationError(message, context={"observations": observations})
    return observations - 1


def as_percent(quantity: Quantity, *, places: int = 2) -> Decimal:
    """A dimensionless fraction rendered as a percentage, for presentation only.

    Never fed back into arithmetic: 0.15 and 15 are the same rate in different clothes,
    and a codebase that lets both circulate eventually adds one to the other. This returns
    a bare ``Decimal`` precisely so it is obvious that it has left the unit system and is
    on its way to a template.

    Raises:
        UnitMismatchError: If the quantity is not dimensionless.
    """
    if not quantity.unit.is_dimensionless:
        message = (
            f"{quantity.unit.symbol} is not a rate, so it has no percentage form. Only a "
            "dimensionless quantity does."
        )
        raise UnitMismatchError(message, context={"unit": quantity.unit.symbol})

    hundred = Quantity.of(100, Unit())
    return (quantity * hundred).round_to(places).value
