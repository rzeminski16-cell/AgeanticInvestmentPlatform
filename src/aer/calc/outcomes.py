"""What a forecast driver actually did, measured from the filings that later arrived.

`docs/archive/knowledge-graph.md` K3. A prior run's confirmed assumption — revenue growth of 9%,
an EBIT margin of 30% — is a number somebody agreed to before the year it forecast had
happened. Once that year's filings are in the store, whether it held is **arithmetic**,
and arithmetic belongs here: every realised value and every delta below goes through the
same ``@traced`` machinery as the ratios the platform reports, so a figure in the
prior-run comparison is a recorded calculation like any other (invariant 3).

**Commensurability is the design constraint.** Each realised driver uses exactly the line
concepts its proposal derivation used (`aer.services.assumption_proposals`): the assumed
EBIT margin was a trailing mean of ``operating_income / revenue``, so the realised one is
that same ratio for the measured year, computed by the same :func:`~aer.calc.basic.ratio`
every other margin goes through. A delta between two differently-derived quantities would
be a number that looks like an error and measures a methodology change.

**A driver that cannot be measured says why.** ``terminal_growth`` and ``exit_multiple``
are judgements about a horizon no single filing closes — no filed year can falsify a
perpetuity — and a driver whose line a filer did not report is unmeasurable for that
year. Both come back as reasons, never as zeros: a zero delta claims the forecast was
exactly right, which is the one thing an absence of data cannot show.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from aer.calc.basic import growth_rate, ratio
from aer.calc.engine import CalculationContext, traced
from aer.calc.statements import StatementSet, subtotal_difference
from aer.calc.units import CalculationError, Quantity

__all__ = [
    "MEASURABLE_DRIVERS",
    "UNMEASURABLE_JUDGEMENTS",
    "assumption_delta",
    "episode_cost",
    "episode_proceeds",
    "realised_driver",
    "realised_return",
]

# The six drivers a filing can answer — the same list `assumption_proposals` derives, for
# the same reason: these are ratios of filed lines. Order is presentation order.
MEASURABLE_DRIVERS: Final[tuple[str, ...]] = (
    "revenue_growth",
    "ebit_margin",
    "capex_intensity",
    "depreciation_intensity",
    "working_capital_intensity",
    "tax_rate",
)

# Confirmed by a person, unfalsifiable by any one filed year. Stated here so the reader of
# an outcome listing sees "cannot be measured, and here is why" rather than an absence.
UNMEASURABLE_JUDGEMENTS: Final[dict[str, str]] = {
    "terminal_growth": (
        "a perpetuity growth rate describes the years beyond every forecast; no single "
        "filed year can confirm or falsify it"
    ),
    "exit_multiple": (
        "an exit multiple is a claim about a future market price, and filings carry no "
        "market prices"
    ),
}


@traced(
    name="assumption_delta",
    formula="delta = actual - assumed",
    assumptions=(
        "Assumed and actual are the same driver derived over the same line concepts, so "
        "the difference measures the forecast rather than a methodology change.",
    ),
)
def assumption_delta(
    _context: CalculationContext, *, assumed: Quantity, actual: Quantity
) -> Quantity:
    """How far the realised driver landed from the confirmed assumption.

    Positive means the business did more of the thing than assumed — grew faster, spent
    more, taxed heavier. Whether that was good news is the driver's business, not the
    sign's, and no reader should be handed an "error" that secretly encodes a judgement.
    """
    return actual - assumed


@traced(
    name="episode_cost",
    formula="cost = sum(acquisition_cost_i)",
    assumptions=("Every acquisition cost is in the book's currency, converted at its own date.",),
)
def episode_cost(_context: CalculationContext, *, costs: Sequence[Quantity]) -> Quantity:
    """What a closed position cost in total: every purchase's consideration and dealing costs.

    Raises:
        CalculationError: If there is nothing to sum — a closed position was bought.
        UnitMismatchError: If the costs are not all in one currency.
    """
    return _same_currency_sum(costs, what="acquisition costs")


@traced(
    name="episode_proceeds",
    formula="proceeds = sum(sale_cash_effect_i) + sum(dividend_i)",
    assumptions=("Every effect is in the book's currency, converted at its own date.",),
)
def episode_proceeds(_context: CalculationContext, *, effects: Sequence[Quantity]) -> Quantity:
    """What a closed position returned in cash: every sale's net effect and every dividend.

    Raises:
        CalculationError: If there is nothing to sum — a closed position was sold.
        UnitMismatchError: If the effects are not all in one currency.
    """
    return _same_currency_sum(effects, what="proceeds")


def _same_currency_sum(values: Sequence[Quantity], *, what: str) -> Quantity:
    if not values:
        message = f"There are no {what} to sum; a closed position has at least one."
        raise CalculationError(message, context={"what": what})
    total = values[0]
    for value in values[1:]:
        total = total + value
    return total


@traced(
    name="realised_return",
    formula="realised_return = (proceeds - cost) / cost",
    assumptions=(
        "Proceeds and cost are in the same currency, each converted at its own trade's date.",
        "Proceeds include every sale and every dividend inside the episode; cost includes "
        "every purchase's consideration and dealing costs.",
    ),
)
def realised_return(
    _context: CalculationContext, *, proceeds: Quantity, cost: Quantity
) -> Quantity:
    """What a closed position returned on what it cost, as a fraction (ADR 0105).

    The one figure the post-trade reviewer is handed about money, and the only place in the
    platform a realised gain is computed. Deliberately a fraction of cost rather than an
    annualised rate: annualising a four-month holding invents eight months that did not
    happen, and the holding period is shown beside it as its own figure.

    Raises:
        UnitMismatchError: If proceeds and cost are in different currencies. Never coerced —
            the caller converts each flow at its own date first.
        CalculationError: If the cost is nil, which a closed position cannot have had.
    """
    if cost.value <= 0:
        message = (
            f"A realised return over a cost of {cost.value} is undefined. A position that "
            "closed was paid for; a nil or negative cost is a walk that did not add up."
        )
        raise CalculationError(message, context={"cost": str(cost.value)})
    return (proceeds - cost) / cost


def realised_driver(
    context: CalculationContext,
    name: str,
    *,
    statements: StatementSet,
    previous: StatementSet | None,
) -> Quantity | str:
    """The driver's realised value for one fiscal year, or the reason there is none.

    ``statements`` is the measured year; ``previous`` is the year before it, which only
    the growth rate needs. A string return is a *stated reason* — the callers turn it
    into a row that says why, because a driver silently missing from an outcome listing
    reads as a defect (the same convention as the proposal derivations).
    """
    if name == "revenue_growth":
        return _realised_growth(context, statements=statements, previous=previous)
    if name == "working_capital_intensity":
        return _realised_working_capital(context, statements=statements)
    return _realised_ratio(context, name, statements=statements)


def _realised_growth(
    context: CalculationContext, *, statements: StatementSet, previous: StatementSet | None
) -> Quantity | str:
    if previous is None:
        return (
            "revenue growth needs the preceding fiscal year's revenue as its base, "
            "and no full year before the measured one is in the store"
        )
    start = previous.get("revenue")
    end = statements.get("revenue")
    if start is None or end is None:
        return "revenue is missing from the measured year or its base year"
    if start.value <= 0:
        return (
            f"revenue in the base year is {start.value}, and a growth rate from a "
            "non-positive base is not a rate"
        )
    return growth_rate(context, start=start, end=end)


def _realised_working_capital(
    context: CalculationContext, *, statements: StatementSet
) -> Quantity | str:
    assets = statements.get("current_assets")
    liabilities = statements.get("current_liabilities")
    revenue = statements.get("revenue")
    if assets is None or liabilities is None or revenue is None:
        return (
            "working capital intensity needs current assets, current liabilities and "
            "revenue, and the measured year does not carry all three"
        )
    if revenue.value <= 0:
        return f"revenue in the measured year is {revenue.value}, so no intensity exists"
    working_capital = subtotal_difference(context, minuend=assets, subtrahend=liabilities)
    return ratio(context, numerator=working_capital, denominator=revenue)


def _realised_ratio(
    context: CalculationContext, name: str, *, statements: StatementSet
) -> Quantity | str:
    pair = _RATIO_LINES.get(name)
    if pair is None:
        return f"the concept map cannot place a driver named {name!r}"
    top_concept, bottom_concept = pair
    top = statements.get(top_concept)
    bottom = statements.get(bottom_concept)
    if top is None or bottom is None:
        missing = top_concept if top is None else bottom_concept
        return f"the measured year does not carry {missing.replace('_', ' ')}"
    if bottom.value <= 0:
        return (
            f"{bottom_concept.replace('_', ' ')} in the measured year is {bottom.value}, "
            "so the ratio is not meaningful"
        )
    return ratio(context, numerator=top, denominator=bottom)


# Driver to (numerator, denominator) — the same lines the proposal derivations average.
_RATIO_LINES: Final[dict[str, tuple[str, str]]] = {
    "ebit_margin": ("operating_income", "revenue"),
    "capex_intensity": ("capital_expenditure", "revenue"),
    "depreciation_intensity": ("depreciation_and_amortisation", "revenue"),
    "tax_rate": ("income_tax_expense", "pre_tax_income"),
}
