"""Currency conversion: which rate, and the arithmetic that uses it.

A UK-listed company reporting in dollars and a US-listed peer reporting in dollars are
comparable. The same company against a sterling-reporting peer is not, until something
converts one of them — and *that conversion is where a valuation quietly goes wrong*, in
three distinct ways this module exists to prevent.

**The rate is applied upside down.** A number roughly the right size and wrong by the
square of the rate, which looks plausible for any pair near parity and is catastrophic for
one that is not. Prevented in :mod:`aer.calc.units`: a rate is a quantity whose unit is
``quote/base``, and converting checks that applying it produces the unit that was asked for.

**The rate comes from after the as-of date.** A valuation as at 30 June that converts at
September's rate has used information nobody had, and the error is invisible because the
number looks like a rate. Prevented here, in :func:`select_rate`, which refuses an
observation later than the as-of date rather than sorting it to the front.

**The rate is stale and nobody notices.** A pair with no observation for six weeks is a
rate table with a hole in it, not a currency that stopped moving. :func:`select_rate`
refuses beyond :data:`MAX_STALENESS_DAYS` rather than reaching further back, because a run
that stops is recoverable and a report converted at a rate from another quarter is not.

**Every conversion is a recorded calculation with the rate as an input.** Never an inline
multiply. A reviewer asking "what rate did this use, and where did it come from?" gets an
answer from the calculation ledger rather than from reading the code that produced it.

Pure and side-effect free. It is given rates; it does not go and get them. Where rates come
from is `docs/data-sources/bank-of-england-iadb.md`, and at the time of writing that
question is **not settled** -- see `docs/adr/0026`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import CalculationError, Quantity, SourceRef, Unit, UnsourcedValueError

__all__ = [
    "MAX_STALENESS_DAYS",
    "ROUND_TRIP_TOLERANCE",
    "FxRate",
    "LookAheadRateError",
    "NoRateAvailableError",
    "StaleRateError",
    "convert",
    "convert_at",
    "invert",
    "round_trips",
    "select_rate",
]


class NoRateAvailableError(CalculationError):
    """No usable observation for this currency pair at this date."""

    code = "no_rate_available"


class LookAheadRateError(NoRateAvailableError):
    """Every observation for this pair is later than the as-of date.

    Its own class rather than a message, because this is the failure that matters: a run
    that silently reached forward would produce a defensible-looking number derived from
    information the as-of date says nobody had.
    """

    code = "look_ahead_rate"


class StaleRateError(NoRateAvailableError):
    """The nearest observation on or before the as-of date is too old to use."""

    code = "stale_rate"


# How far back :func:`select_rate` will reach for an observation.
#
# Seven days, not one: published rate series skip weekends and bank holidays, and a run as
# at a Sunday would otherwise fail for a reason that has nothing to do with the data. Not
# thirty: a pair with no observation for a month is a rate table with a hole in it, and
# converting a whole balance sheet at a rate from another month is the kind of error that
# survives review because the number looks like a rate.
MAX_STALENESS_DAYS: Final = 7

# How far a value may move on a round trip through two rates and still be called unchanged.
#
# Not zero, and not a rounding artefact either: converting at 34 significant figures and
# back reproduces the input almost exactly, but an inverted rate is a division and the last
# place does not always return. One part in ten million is far below anything that changes
# a figure anybody reads and far above the arithmetic's own noise.
ROUND_TRIP_TOLERANCE: Final = Decimal("0.0000001")


@dataclass(frozen=True, slots=True)
class FxRate:
    """One published exchange-rate observation.

    ``rate`` is a :class:`~aer.calc.units.Quantity` in ``quote/base``, so a GBP/USD rate
    converts a dollar amount into pounds. Carrying the unit on the rate itself is what makes
    an upside-down application a raised error rather than a plausible wrong number.

    ``observed_on`` is the date the rate was *for*, not the date it was fetched. The two
    differ by up to a day for end-of-day series, and point-in-time selection needs the first.
    """

    base: str
    quote: str
    rate: Quantity
    observed_on: date

    def __post_init__(self) -> None:
        if self.rate.source is None:
            message = (
                f"The {self.base}/{self.quote} rate for {self.observed_on.isoformat()} has no "
                "source. A rate nobody can point at is an assumption pretending to be a fact, "
                "and every figure converted with it inherits that."
            )
            raise UnsourcedValueError(message, context={"base": self.base, "quote": self.quote})

        expected = Unit.currency(self.quote) / Unit.currency(self.base)
        if self.rate.unit != expected:
            message = (
                f"A {self.base} to {self.quote} rate must be stated in {expected.symbol}, not "
                f"{self.rate.unit.symbol}. A rate whose unit disagrees with its pair is one "
                "that will be applied the wrong way up."
            )
            raise CalculationError(
                message,
                context={
                    "base": self.base,
                    "quote": self.quote,
                    "unit": self.rate.unit.symbol,
                    "expected": expected.symbol,
                },
            )

        if self.rate.value <= 0:
            message = (
                f"The {self.base}/{self.quote} rate for {self.observed_on.isoformat()} is "
                f"{self.rate.value}. An exchange rate is a positive number; zero and negative "
                "rates are parse failures, not observations."
            )
            raise CalculationError(message, context={"rate": str(self.rate.value)})

    @property
    def pair(self) -> tuple[str, str]:
        return (self.base, self.quote)

    @property
    def source(self) -> SourceRef:
        """Where the rate came from. Never ``None`` -- ``__post_init__`` refuses that."""
        source = self.rate.source
        if source is None:  # pragma: no cover -- unreachable past __post_init__
            message = f"The {self.base}/{self.quote} rate lost its source."
            raise UnsourcedValueError(message, context={"base": self.base, "quote": self.quote})
        return source


def select_rate(
    rates: tuple[FxRate, ...] | list[FxRate],
    *,
    base: str,
    quote: str,
    as_of: date,
    max_staleness_days: int = MAX_STALENESS_DAYS,
) -> FxRate:
    """The rate for this pair as at ``as_of``: the most recent observation not after it.

    **Anything observed after ``as_of`` is refused, not ranked below.** This is invariant 4
    at the point it would be broken. Sorting a later observation to the back would leave it
    one code change away from being chosen, and the symptom of that change would be a
    valuation that is subtly, defensibly wrong.

    Args:
        rates: Observations for any pairs; those for other pairs are ignored.
        base: The currency being converted *from*.
        quote: The currency being converted *into*.
        as_of: The run's as-of date. No observation later than this may be used.
        max_staleness_days: How far back to reach. See :data:`MAX_STALENESS_DAYS`.

    Raises:
        LookAheadRateError: Every observation for the pair is later than ``as_of``.
        StaleRateError: The nearest usable observation is older than ``max_staleness_days``.
        NoRateAvailableError: There are no observations for the pair at all.
    """
    for_pair = [rate for rate in rates if rate.pair == (base, quote)]
    if not for_pair:
        message = (
            f"No {base}/{quote} rate was supplied. A conversion needs a rate that came from "
            "somewhere; there is no default and no fallback pair."
        )
        raise NoRateAvailableError(message, context={"base": base, "quote": quote})

    usable = [rate for rate in for_pair if rate.observed_on <= as_of]
    if not usable:
        earliest = min(rate.observed_on for rate in for_pair)
        message = (
            f"Every {base}/{quote} rate supplied was observed after {as_of.isoformat()} — the "
            f"earliest is {earliest.isoformat()}. Converting at one of them would put "
            "information into this run that nobody had on the as-of date."
        )
        raise LookAheadRateError(
            message,
            context={
                "base": base,
                "quote": quote,
                "as_of": as_of.isoformat(),
                "earliest_available": earliest.isoformat(),
            },
        )

    # Ties broken on the source reference so the choice is total: two observations of one
    # pair on one date is a disagreement for `aer.core.disagreement` to settle, and picking
    # by argument order would make this function's answer depend on load order.
    chosen = max(usable, key=lambda rate: (rate.observed_on, str(rate.source)))

    staleness = (as_of - chosen.observed_on).days
    if staleness > max_staleness_days:
        message = (
            f"The most recent {base}/{quote} rate on or before {as_of.isoformat()} is from "
            f"{chosen.observed_on.isoformat()}, {staleness} days earlier. That is a gap in the "
            "rate series rather than a currency that stopped moving, so the conversion stops "
            "here instead of using it."
        )
        raise StaleRateError(
            message,
            context={
                "base": base,
                "quote": quote,
                "as_of": as_of.isoformat(),
                "observed_on": chosen.observed_on.isoformat(),
                "staleness_days": staleness,
                "limit_days": max_staleness_days,
            },
        )

    return chosen


@traced(
    name="fx_convert",
    formula="converted = amount * rate",
    assumptions=(
        "The rate is stated in quote/base and was observed on or before the as-of date.",
        "A single rate is applied to the whole amount: no intraday or transaction-level "
        "rate is modelled.",
    ),
)
def convert(
    _context: CalculationContext, *, amount: Quantity, rate: Quantity, into: str
) -> Quantity:
    """An amount in one currency, in another.

    Traced, so the converted figure resolves to a calculation whose inputs are the amount and
    the rate, each with its own source. **This is the only way a currency changes in this
    platform** — an inline multiply would produce a number no reviewer could attribute to a
    rate, which is the same as a number nobody can check.

    Args:
        into: The currency wanted. Stated by the caller rather than inferred from the rate,
            and that is the whole point: inferring it would make ``amount.unit * rate.unit``
            true by construction, so a rate applied the wrong way up would produce
            ``USD^2/GBP`` and no complaint. Naming the target is what turns that into a
            raised error.

    Raises:
        UnitMismatchError: If applying the rate does not produce ``into`` — in practice, if
            the rate is upside down or for the wrong pair.
        UnsourcedValueError: If the rate has no source.
    """
    if not amount.unit.currencies:
        message = (
            f"{amount.unit.symbol} is not a currency, so there is nothing to convert. A share "
            "count and a ratio are the same number in every currency."
        )
        raise CalculationError(message, context={"unit": amount.unit.symbol})

    return amount.convert(Unit.currency(into), rate=rate)


def convert_at(context: CalculationContext, *, amount: Quantity, rate: FxRate) -> Quantity:
    """:func:`convert`, with the pair and the direction taken from the observation itself.

    The form nearly every caller wants: a selected :class:`FxRate` already knows what it
    converts from and into, and repeating that at the call site is a chance to disagree with
    it. Refuses an amount that is not in the rate's base currency, because converting dollars
    with a EUR/GBP rate is a mistake no unit check downstream can attribute back to here.
    """
    held = amount.unit.currencies
    if held != (rate.base,):
        message = (
            f"This is a {rate.base} to {rate.quote} rate, and the amount is in "
            f"{amount.unit.symbol}. A rate for a pair the amount is not in converts nothing; "
            "select the rate for the currency actually held."
        )
        raise CalculationError(
            message,
            context={"amount_unit": amount.unit.symbol, "base": rate.base, "quote": rate.quote},
        )

    return convert(context, amount=amount, rate=rate.rate, into=rate.quote)


def invert(rate: FxRate) -> FxRate:
    """The same observation the other way round: a GBP/USD rate as a USD/GBP one.

    **The inverted rate keeps the original's source**, because it is the same observation
    read in the other direction rather than a second piece of evidence. A round trip through
    a rate and its inverse therefore traces to one published figure, which is what it is.

    Raises:
        CalculationError: If the rate is zero, which :class:`FxRate` already refuses.
    """
    one = Quantity.of(Decimal(1), Unit.parse("pure"), source=rate.source)
    # The source is re-attached rather than inherited: quantity arithmetic drops it, because
    # in general a computed value's provenance is the calculation that produced it and only
    # `@traced` can supply that. Here the computation is a reading of one observation, so the
    # observation is the provenance -- see the docstring.
    reciprocal = replace(one / rate.rate, source=rate.source)
    return FxRate(
        base=rate.quote,
        quote=rate.base,
        rate=reciprocal,
        observed_on=rate.observed_on,
    )


def round_trips(
    original: Quantity, returned: Quantity, *, tolerance: Decimal = ROUND_TRIP_TOLERANCE
) -> bool:
    """Whether converting out and back reproduced the original within ``tolerance``.

    A property worth being able to state rather than a conversion step: it is what a test
    asserts, and what an operator checks when a converted statement looks wrong. Compared
    relatively, because "out by a penny" means something different on a thousand pounds and
    on a billion.
    """
    if original.unit != returned.unit:
        return False
    if original.value == 0:
        return returned.value == 0
    return abs(returned.value - original.value) / abs(original.value) <= tolerance
