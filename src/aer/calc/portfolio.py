"""What a book is worth, and what it cost, as at a date.

Six figures — quantity held, cost basis, market value, cash, net asset value and weight —
and **not one of them is a stored column** (ADR 0079). There is no ``positions`` table and
there will not be one. Every number here is a traced calculation over ``transactions``, so a
reader who disagrees with a holding gets a formula, the trades that fed it, and the grade of
evidence behind each, rather than a column and a shrug.

That is the whole argument of ADR 0079 in one sentence, and it is worth restating where the
arithmetic actually lives: a ``positions.market_value`` column is neither a stored fact nor a
recorded calculation, so it fails invariant 3 — and the moment it disagrees with a broker
statement, which is the entire reason a person opens this screen, there is nothing to
reconcile against.

**The cost basis convention is ADR 0081's and it is not a tax computation.** A pooled
average, per portfolio and per security, walked in trade-date order. It is the shape of a UK
Section 104 holding without the same-day rule, the thirty-day rule or share reorganisations,
so it answers "what did I pay for what I still hold?" and not "what is my chargeable gain?".
Every surface showing it has to say so.

**Order matters, and that is why this module walks rather than sums.** Buy at £10, sell,
then buy at £20 leaves a different cost from the same three trades in the other order. A
cost basis is a walk through history, not an aggregate over it.

Pure and side-effect free, like everything in :mod:`aer.calc`. It is handed sourced
quantities and hands back sourced quantities; it does not know what a session is, and it
cannot go and get a price. Where the rows come from is :mod:`aer.services.portfolio`'s
business, and where the as-of date comes from is the reader's.
"""

from __future__ import annotations

# Imported at runtime, not under `TYPE_CHECKING`, despite `from __future__ import
# annotations` making that look safe. The replay harness resolves each traced function's
# annotations with `get_type_hints` in order to coerce a stored parameter back into the type
# the guards demand — and a name only the type checker can see raises `NameError` there.
# `aer.calc.basic` and `aer.calc.prices` import it the same way for the same reason; the
# golden case for `pooled_cost` is what caught this one.
from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import (
    CalculationError,
    Quantity,
    Unit,
    UnitMismatchError,
)

__all__ = [
    "SHARES",
    "acquisition_cost",
    "cash_balance",
    "cash_movement",
    "dealt_cash_effect",
    "holding_value",
    "net_assets",
    "pooled_cost",
    "quantity_held",
    "unrealised",
    "weight",
]

# What a share count is measured in, matching :mod:`aer.calc.prices`.
#
# **One symbol for every security, and the pairing is the caller's job.** Two holdings'
# quantities are the same unit, so nothing here stops somebody adding Microsoft shares to
# Barclays shares — the unit system cannot tell them apart and inventing a symbol per ticker
# would make every price unit unique and every ratio uncomputable. What keeps them apart is
# that each of these functions is called *per security*, which is a structural property of
# the caller rather than a check available here.
SHARES: Final = Unit.base("shares")


@traced(
    name="quantity_held",
    formula="held = Σ movement_i",
    assumptions=(
        "Every movement of this security up to the as-of date is present. A holding "
        "computed from a partial history is wrong, not approximate.",
        "Quantities are signed: an acquisition is positive and a disposal negative.",
    ),
)
def quantity_held(_context: CalculationContext, *, movements: Sequence[Quantity]) -> Quantity:
    """How much of one security the book holds.

    Each movement is recorded as its own input, so a reader asking "why does this say 1,340
    shares?" gets the list of trades rather than a total.

    Raises:
        CalculationError: If the movements are empty, or if the total is negative — a book
            that appears to have sold what it never bought has trades missing, and a short
            position is not modelled. Computing through it would produce a negative holding
            whose market value is negative and whose weight is negative, none of which looks
            wrong enough to notice.
        UnitMismatchError: If the movements are not all in one unit.
    """
    total = _summed(movements, what="movement")
    if total.value < 0:
        message = (
            f"These movements net to {total.value} {total.unit.symbol}, which is less than "
            "nothing held. Either a disposal was entered before the acquisition it disposes "
            "of, or trades are missing. Shorting is not modelled, and a negative holding "
            "would price and weight as though it were ordinary."
        )
        raise CalculationError(message, context={"held": str(total.value)})
    return total


@traced(
    name="acquisition_cost",
    formula="cost = quantity * price + fees",
    assumptions=(
        "The dealing costs are those charged on this acquisition, in the dealing currency.",
        "The price is the one dealt at, not a closing price for the day.",
    ),
)
def acquisition_cost(
    _context: CalculationContext, *, quantity: Quantity, price: Quantity, fees: Quantity
) -> Quantity:
    """What one purchase added to the pool.

    Dealing costs are part of it, which is both the tax rule and the plain reading: money
    spent to acquire the shares is money the shares cost.

    Raises:
        CalculationError: If the quantity is not positive. A disposal removes cost at the
            pool's average rather than at its own price, so routing one through here would
            be the whole cost-basis convention quietly bypassed.
        UnitMismatchError: If the price is not per-share, or the fees are in another
            currency.
    """
    if quantity.value <= 0:
        message = (
            f"An acquisition cost was asked for a quantity of {quantity.value}. A disposal "
            "takes cost out of the pool at the average, not at the price it was sold for — "
            "see ADR 0081."
        )
        raise CalculationError(message, context={"quantity": str(quantity.value)})

    consideration = quantity * price
    _require_same_currency(consideration, fees, what="the dealing costs")
    return consideration + fees


@traced(
    name="dealt_cash_effect",
    formula="cash = -(quantity * price) - fees",
    assumptions=(
        "Cash moves on this transaction. Settlement timing is not modelled: a trade counts "
        "against cash from its trade date.",
    ),
)
def dealt_cash_effect(
    _context: CalculationContext, *, quantity: Quantity, price: Quantity, fees: Quantity
) -> Quantity:
    """What one dealt transaction did to the cash balance.

    **The sign falls out of the quantity, which is why there is one function and not two.**
    A purchase has a positive quantity, so the consideration is negated to cash out; a sale
    has a negative one, so the same expression is cash in. Dealing costs are subtracted
    either way, because a sale costs money too.

    Raises:
        UnitMismatchError: If the price is not per-share, or the fees are in another
            currency.
    """
    consideration = quantity * price
    _require_same_currency(consideration, fees, what="the dealing costs")
    return -consideration - fees


@traced(
    name="cash_movement",
    formula="cash = amount - fees",
    assumptions=("The amount is signed: money in is positive and money out is negative.",),
)
def cash_movement(_context: CalculationContext, *, amount: Quantity, fees: Quantity) -> Quantity:
    """What one cash transaction did to the balance — a deposit, a dividend, a charge.

    Separate from :func:`dealt_cash_effect` rather than a branch inside it, because the two
    have different formulas and the formula is the record. A conditional expression in a
    ``formula`` string is a record that does not say what happened.
    """
    _require_same_currency(amount, fees, what="the fee")
    return amount - fees


@traced(
    name="cash_balance",
    formula="balance = Σ effect_i",
    assumptions=(
        "One currency. A balance across two would need a rate and a date, which is a "
        "conversion rather than a balance.",
    ),
)
def cash_balance(_context: CalculationContext, *, effects: Sequence[Quantity]) -> Quantity:
    """The cash the book holds in one currency, as at the as-of date.

    **Cash is a position, and leaving it out is not a rounding error.** Without it every
    weight on the page is a fraction of the wrong denominator — silently, and in the
    direction that overstates every holding.

    Raises:
        CalculationError: If there are no effects to sum.
        UnitMismatchError: If they are not all in one currency.
    """
    return _summed(effects, what="cash effect")


@traced(
    name="pooled_cost",
    formula=(
        "walk in trade-date order: an acquisition adds its cost and its units to the pool; "
        "a disposal removes pool_cost * |units| / pool_units and those units. "
        "cost basis = the pool's remaining cost"
    ),
    assumptions=(
        "A pooled average, per portfolio and per security — the shape of a UK Section 104 "
        "holding (ADR 0081).",
        "**Not a tax computation.** No same-day rule, no thirty-day rule, and no share "
        "reorganisation. It answers what was paid for what is still held.",
        "A disposal's own dealing costs do not touch the pool.",
        "The sequences are in trade-date order. The answer depends on that order.",
    ),
)
def pooled_cost(
    _context: CalculationContext,
    *,
    movements: Sequence[Quantity],
    acquisition_costs: Sequence[Quantity],
) -> Quantity:
    """What the shares still held cost, under ADR 0081's convention.

    The two sequences are parallel and in trade-date order: ``acquisition_costs[i]`` is what
    ``movements[i]`` cost when that movement was a purchase, and must be zero when it was a
    sale — a disposal takes cost out at the pool's average rather than at the price it
    fetched, and a sale carrying a cost is a caller who has confused proceeds with cost.

    Every movement and every cost is recorded as its own input, so the walk is reproducible
    from the ledger without the code that ran it.

    Raises:
        CalculationError: If the sequences differ in length or are empty; if a disposal
            carries a non-zero cost; if a disposal exceeds what the pool holds; or if the
            costs are not all in one currency.
        UnitMismatchError: If the movements are not all in one unit.
    """
    if len(movements) != len(acquisition_costs):
        message = (
            f"{len(movements)} movements and {len(acquisition_costs)} costs. The two are "
            "paired by position, so a mismatch means some trade's cost belongs to a "
            "different trade."
        )
        raise CalculationError(
            message,
            context={"movements": len(movements), "costs": len(acquisition_costs)},
        )
    if not movements:
        message = (
            "A cost basis needs at least one trade. Nothing held cost nothing, and "
            "saying so needs a holding to say it about."
        )
        raise CalculationError(message, context={"movements": 0})

    currency = _one_currency(acquisition_costs)
    # Called for its refusal rather than its answer: the output is money, but a movement
    # list mixing shares with something else is a caller pooling two different things.
    _one_unit(movements)

    pool_units = Decimal(0)
    pool_cost = Decimal(0)

    for index, (movement, cost) in enumerate(zip(movements, acquisition_costs, strict=True)):
        if movement.value > 0:
            pool_units += movement.value
            pool_cost += cost.value
            continue

        if movement.value == 0:
            message = (
                f"Trade {index} moves nothing. A zero-quantity trade changes no answer while "
                "looking like a record of something, which is the one shape a reconciliation "
                "cannot spot."
            )
            raise CalculationError(message, context={"index": index})

        if cost.value != 0:
            message = (
                f"Trade {index} is a disposal of {abs(movement.value)} and carries a cost of "
                f"{cost.value}. A disposal removes cost at the pool's average; a cost here "
                "would put the price it sold for into the basis of what is still held."
            )
            raise CalculationError(message, context={"index": index, "cost": str(cost.value)})

        sold = -movement.value
        if sold > pool_units:
            message = (
                f"Trade {index} disposes of {sold} and the pool holds {pool_units}. Either a "
                "disposal was entered before its acquisition or trades are missing — and "
                "either way the average this would divide by is not the book's."
            )
            raise CalculationError(
                message,
                context={"index": index, "disposed": str(sold), "held": str(pool_units)},
            )
        # Removed at the average, which is the convention. Computed as a share of the pool
        # rather than as `average * sold`, so a pool that is fully disposed of comes back to
        # exactly zero instead of to a rounding residue. `pool_units` cannot be zero here:
        # `sold` is strictly positive, and a positive disposal against an empty pool was
        # refused above.
        pool_cost -= pool_cost * (sold / pool_units)
        pool_units -= sold

    return Quantity.of(pool_cost, currency)


@traced(
    name="holding_value",
    formula="value = quantity * price",
    assumptions=(
        "The price is the last close on or before the as-of date, in the security's own "
        "quote currency.",
        "A mid-market close, not a price anything could be dealt at. A holding is marked, "
        "not liquidated.",
    ),
)
def holding_value(_context: CalculationContext, *, quantity: Quantity, price: Quantity) -> Quantity:
    """What one holding is worth at the mark.

    Arithmetically the same as :func:`aer.calc.prices.market_capitalisation` and deliberately
    not that function. The name is part of the record, and a portfolio row whose ledger entry
    read "market_capitalisation" would say something untrue about what was being valued.

    Raises:
        UnitMismatchError: If the price is not per-share or the quantity is not in shares.
        CalculationError: If the quantity is negative.
    """
    if quantity.unit != SHARES:
        message = (
            f"The holding is in {quantity.unit.symbol}, not shares. Cash is valued at its "
            "balance; only a security is valued at a price."
        )
        raise UnitMismatchError(message, context={"unit": quantity.unit.symbol})
    if dict(price.unit.dimensions).get("shares") != -1:
        message = (
            f"The price is in {price.unit.symbol}, which is not a price per share. A figure "
            "in plain currency is a total, and multiplying a total by a share count gives a "
            "number a hundred million times too large."
        )
        raise UnitMismatchError(message, context={"unit": price.unit.symbol})
    if quantity.value < 0:
        message = f"The holding is {quantity.value} shares. Shorting is not modelled."
        raise CalculationError(message, context={"quantity": str(quantity.value)})

    return quantity * price


@traced(
    name="net_assets",
    formula="nav = Σ holding_i + Σ cash_j",
    assumptions=(
        "Every figure is already in the book's reporting currency. Conversion happens "
        "before this, as its own recorded calculation over a dated rate (ADR 0078).",
        "Nothing is owed. Margin, accrued charges and unsettled obligations are not "
        "modelled, so this is assets rather than net assets on a leveraged book.",
    ),
)
def net_assets(
    _context: CalculationContext, *, holdings: Sequence[Quantity], cash: Sequence[Quantity]
) -> Quantity:
    """What the whole book is worth.

    **Cash is in the sum, and the assumption above says why the name is still not quite
    right.** A book with borrowings has liabilities this figure cannot see, so it is called
    net assets because that is what a reader expects a portfolio total to be called, and the
    limitation is stated rather than hidden in the word.

    Raises:
        CalculationError: If there is nothing at all to sum.
        UnitMismatchError: If the figures are not all in one currency.
    """
    everything = [*holdings, *cash]
    if not everything:
        message = (
            "A net asset value needs something in it. An empty book has no value to state — "
            "not zero, which is a figure somebody could act on."
        )
        raise CalculationError(message, context={"holdings": 0, "cash": 0})
    return _summed(everything, what="component")


@traced(
    name="portfolio_weight",
    formula="weight = value / net_assets",
    assumptions=(
        "The denominator includes cash. A weight over securities alone overstates every "
        "holding, silently.",
    ),
)
def weight(_context: CalculationContext, *, value: Quantity, net_assets: Quantity) -> Quantity:
    """One holding as a fraction of the book.

    Raises:
        CalculationError: If net assets are zero or negative — a fraction of nothing is not
            zero, it is undefined, and rendering it as 0% would say something false about a
            position that exists.
        UnitMismatchError: If the two are in different currencies.
    """
    if net_assets.value <= 0:
        message = (
            f"Net assets are {net_assets.value} {net_assets.unit.symbol}, so there is no "
            "fraction of them to take. A weight against an empty or negative book is "
            "undefined rather than nil."
        )
        raise CalculationError(message, context={"net_assets": str(net_assets.value)})
    _require_same_currency(value, net_assets, what="net assets")
    return value / net_assets


@traced(
    name="unrealised",
    formula="unrealised = value - cost",
    assumptions=(
        "The cost is the pooled average of ADR 0081, so this is not a chargeable gain.",
        "Nothing is accrued: an unpaid dividend already gone ex is not in either figure.",
    ),
)
def unrealised(_context: CalculationContext, *, value: Quantity, cost: Quantity) -> Quantity:
    """What a holding has made or lost on paper.

    **Not a gain for tax.** The cost side is a pooled average without the same-day rule, the
    thirty-day rule or share reorganisations (ADR 0081), so this answers "am I up on this?"
    and not "what do I owe?".

    Raises:
        UnitMismatchError: If the two are in different currencies.
    """
    _require_same_currency(value, cost, what="the cost basis")
    return value - cost


# -- Shared guards ---------------------------------------------------------------------------


def _summed(values: Sequence[Quantity], *, what: str) -> Quantity:
    """Add a sequence, refusing an empty one and a mixed-unit one.

    An empty sum returning zero is the failure worth naming: "no trades" and "trades that
    net to nothing" are different states, and the second is a real answer while the first is
    a question about whether the data arrived.
    """
    if not values:
        message = (
            f"There are no {what}s to add. An empty sum is zero, and zero here would be a "
            "figure somebody could act on standing in for an answer nobody has."
        )
        raise CalculationError(message, context={"what": what})

    unit = _one_unit(values)
    return Quantity.of(sum((value.value for value in values), Decimal(0)), unit)


def _one_unit(values: Sequence[Quantity]) -> Unit:
    units = {value.unit for value in values}
    if len(units) > 1:
        symbols = sorted(unit.symbol for unit in units)
        message = (
            f"These figures are in {len(units)} different units — {', '.join(symbols)}. "
            "Currencies never convert implicitly; convert first, as a recorded calculation "
            "over a dated rate."
        )
        raise UnitMismatchError(message, context={"units": ",".join(symbols)})
    return next(iter(units))


def _one_currency(values: Sequence[Quantity]) -> Unit:
    unit = _one_unit(values)
    if not unit.currencies:
        message = (
            f"{unit.symbol} is not a currency, and a cost basis is money. A share count or "
            "a ratio in this position is an argument passed in the wrong order."
        )
        raise UnitMismatchError(message, context={"unit": unit.symbol})
    return unit


def _require_same_currency(left: Quantity, right: Quantity, *, what: str) -> None:
    if left.unit != right.unit:
        message = (
            f"The consideration is in {left.unit.symbol} and {what} in {right.unit.symbol}. "
            "Two currencies in one transaction is a conversion somebody has to date and "
            "record, not an addition."
        )
        raise UnitMismatchError(
            message, context={"left": left.unit.symbol, "right": right.unit.symbol}
        )
