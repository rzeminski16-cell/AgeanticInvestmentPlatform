"""What a planned position would do to the book that already exists (F3).

Consequences, never instructions. The operator states a planned weight on the request form
— the whole position after the trade, as a fraction of the book — and these functions say
what the book's concentration looks like with that fraction in it. Nothing here multiplies
a weight by a net asset value: every answer is a fraction of the book, so no money amount for
the position is ever computed (ADR 0104 keeps a *decision's* size a sentence for that reason,
and ADR 0129 keeps this section to fractions).

**One assumption runs through the three book figures, and it is stated on each: the position
is funded from cash.** Nothing else in the book is sold to pay for it, so every other holding
keeps its weight and the only weights that move are this listing's and the cash. A trade
funded by selling something else is a different trade, and the operator would say so.

The two horizon figures are about the company rather than the book: how much of today's
enterprise value — `aer.calc.comps.market_enterprise_value`, the observed one — the explicit
forecast's discounted free cash flow recovers, and the year it recovers all of it, if it
does within the forecast. Only the explicit years count. The terminal value is what the
market is paying for beyond them, and folding it in would make every payback the last
forecast year.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from aer.calc.engine import CalculationContext, traced
from aer.calc.performance import YEARS
from aer.calc.units import DIMENSIONLESS, CalculationError, Quantity, UnitMismatchError

__all__ = [
    "cash_weight_after",
    "exposure_after",
    "forecast_recovery",
    "payback_year",
    "top_holdings_share_after",
]

_FUNDED_FROM_CASH = (
    "The position is funded from cash, and only cash: nothing else in the book is sold to "
    "pay for it, so every other holding keeps its weight. A trade funded by selling "
    "something else is a different trade, and the operator would say so."
)


@traced(
    name="cash_weight_after",
    formula="cash weight after = cash weight - (planned weight - current weight)",
    assumptions=(
        _FUNDED_FROM_CASH,
        "A negative answer is reported, not clipped: it says the cash on hand does not "
        "cover the trade, which is a fact about the book the reader needs before anything "
        "else on the page.",
    ),
)
def cash_weight_after(
    _context: CalculationContext,
    *,
    cash_weight: Quantity,
    current_weight: Quantity,
    planned_weight: Quantity,
) -> Quantity:
    """The cash left as a share of the book once the position is at its planned weight."""
    _require_fraction(cash_weight, what="the cash weight")
    _require_fraction(current_weight, what="the current weight")
    _require_fraction(planned_weight, what="the planned weight")
    return cash_weight - (planned_weight - current_weight)


@traced(
    name="top_holdings_share_after",
    formula=(
        "share after = Σ(the `count` largest weights among the other holdings and the "
        "planned weight)"
    ),
    assumptions=(
        _FUNDED_FROM_CASH,
        "The same ranking `top_holdings_share` records for the book as it stands, over the "
        "weights as they would be; the before and after are comparable because the "
        "arithmetic is the same.",
        "A book with fewer holdings than `count` reports all of them and the planned "
        "position, which is the honest answer: its top five is everything it would have.",
    ),
)
def top_holdings_share_after(
    _context: CalculationContext,
    *,
    other_weights: Sequence[Quantity],
    planned_weight: Quantity,
    count: int,
) -> Quantity:
    """How much of the book would sit in its largest positions with this one at its plan.

    ``other_weights`` are every holding's weight except this listing's own: the planned
    weight replaces whatever the book holds of it today.

    Raises:
        CalculationError: If ``count`` is not positive.
        UnitMismatchError: If any weight carries a unit — a weight is a fraction.
    """
    if count <= 0:
        message = f"A top-{count} share is not a quantity. Ask for at least one holding."
        raise CalculationError(message, context={"count": count})
    _require_fraction(planned_weight, what="the planned weight")
    for weight in other_weights:
        _require_fraction(weight, what="a holding's weight")

    ranked = sorted(
        [weight.value for weight in other_weights] + [planned_weight.value], reverse=True
    )
    return Quantity.of(sum(ranked[:count], Decimal(0)), DIMENSIONLESS)


@traced(
    name="exposure_after",
    formula="exposure after = sector share + planned weight - current weight",
    assumptions=(
        _FUNDED_FROM_CASH,
        "The sector is the filer's own classification of this listing, the same grouping "
        "the book's exposure bands use; a listing its filer has not classified has no "
        "sector to move.",
    ),
)
def exposure_after(
    _context: CalculationContext,
    *,
    sector_share: Quantity,
    current_weight: Quantity,
    planned_weight: Quantity,
    sector: str,  # noqa: ARG001 -- recorded as a parameter by @traced, read from the row
) -> Quantity:
    """The share of the book in this listing's sector once the position is at its plan.

    ``sector`` names the group, recorded as a structural parameter rather than evidence —
    the same footing as ``measure`` on an implied upside — so a reader of the row knows
    which band moved without walking back to the exposure it was computed from.
    """
    _require_fraction(sector_share, what="the sector's share")
    _require_fraction(current_weight, what="the current weight")
    _require_fraction(planned_weight, what="the planned weight")
    return sector_share + planned_weight - current_weight


@traced(
    name="forecast_recovery",
    formula=("recovery = Σ(present value of forecast free cash flow) / market enterprise value"),
    assumptions=(
        "Only the explicit forecast years count. The terminal value is what the market is "
        "paying for beyond them, and folding it in would make every payback the last "
        "forecast year.",
        "The present values are the valuation's own, discounted at its recorded cost of "
        "capital; this states what fraction of today's price they add up to and nothing "
        "about whether that fraction is enough.",
    ),
)
def forecast_recovery(
    _context: CalculationContext,
    *,
    present_values: Sequence[Quantity],
    market_enterprise_value: Quantity,
) -> Quantity:
    """How much of today's enterprise value the explicit forecast's cash flows recover.

    Raises:
        CalculationError: If there are no forecast years, or the enterprise value is not
            positive — a fraction of nothing is undefined rather than nil.
        UnitMismatchError: If a present value is in a different currency from the value.
    """
    total = _summed_in(present_values, like=market_enterprise_value)
    if market_enterprise_value.value <= 0:
        message = (
            f"The market enterprise value is {market_enterprise_value.value} "
            f"{market_enterprise_value.unit.symbol}, so there is no fraction of it to recover."
        )
        raise CalculationError(message, context={"value": str(market_enterprise_value.value)})
    return Quantity.of(total / market_enterprise_value.value, DIMENSIONLESS)


@traced(
    name="payback_year",
    formula=(
        "payback year = the first forecast year in which the cumulative present value of "
        "free cash flow reaches the market enterprise value"
    ),
    assumptions=(
        "Only the explicit forecast years count, for the reason `forecast_recovery` gives; "
        "a payback the forecast does not reach is refused rather than extrapolated, and the "
        "recovery figure says how far the forecast got.",
        "Whole years: the cash flow of a year is taken as arriving with it, which is the "
        "convention the discount factors already assume.",
    ),
)
def payback_year(
    _context: CalculationContext,
    *,
    present_values: Sequence[Quantity],
    market_enterprise_value: Quantity,
) -> Quantity:
    """The forecast year by which discounted free cash flow has recovered today's price.

    Raises:
        CalculationError: If the forecast never reaches it. The caller asks
            :func:`forecast_recovery` first and states the fraction instead; this is the
            refusal for a caller that did not.
        UnitMismatchError: If a present value is in a different currency from the value.
    """
    _summed_in(present_values, like=market_enterprise_value)
    cumulative = Decimal(0)
    for year, present_value in enumerate(present_values, start=1):
        cumulative += present_value.value
        if cumulative >= market_enterprise_value.value:
            return Quantity.of(year, YEARS)
    message = (
        f"The {len(present_values)}-year forecast recovers {cumulative} of the "
        f"{market_enterprise_value.value} {market_enterprise_value.unit.symbol} the market "
        "pays for the enterprise, so the payback lies beyond it. State the recovery "
        "instead of a year."
    )
    raise CalculationError(
        message,
        context={"years": len(present_values), "recovered": str(cumulative)},
    )


def _summed_in(values: Sequence[Quantity], *, like: Quantity) -> Decimal:
    """Add the present values, refusing an empty forecast and a mixed currency."""
    if not values:
        message = (
            "There are no forecast years to add. A recovery over no forecast is not zero, "
            "it is a question about whether the valuation ran."
        )
        raise CalculationError(message, context={"years": 0})
    for value in values:
        if value.unit != like.unit:
            message = (
                f"A present value is in {value.unit.symbol} and the enterprise value in "
                f"{like.unit.symbol}. Convert first, as a recorded calculation over a dated "
                "rate."
            )
            raise UnitMismatchError(
                message, context={"present_value": value.unit.symbol, "value": like.unit.symbol}
            )
    return sum((value.value for value in values), Decimal(0))


def _require_fraction(quantity: Quantity, *, what: str) -> None:
    if quantity.unit != DIMENSIONLESS:
        message = (
            f"{what.capitalize()} is in {quantity.unit.symbol}, and a weight is a fraction of "
            "the book. A value passed where a weight belongs makes the answer a currency "
            "amount."
        )
        raise UnitMismatchError(message, context={"unit": quantity.unit.symbol})
