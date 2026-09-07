"""What the company did with its cash, as figures a writer can name.

The confirmation run's Capital Allocation section (roadmap §2.1, 2026-09-05) was refused on
both attempts, and the reasons were four sentences the writer had every right to want to
write: dividends paid *rose by* so much, repurchases *rose by* so much, debt repayments
*fell by* so much, and buybacks and dividends *together* came to so much. Every one of
those numbers was the writer's own arithmetic over figures the pack held — a difference, a
sum — and the numeral rule refused each as a figure no fact or calculation stood behind.
The rule was right: the platform owns every number (the one rule in `CLAUDE.md`), and a
subtraction done in prose is a number nobody can trace. But a section about capital
allocation that may not say what changed is a section that cannot do its job.

So the arithmetic moves here, where it is recorded. Six figures, each traced with its
formula and its inputs' sources, each struck per period in the analysis pass beside the
ratios and the quality signals:

* **shareholder distributions** — share repurchases plus dividends paid, the cash returned;
* **distributions to operating cash flow** — that sum over the year's operating cash flow,
  the share of what the business generated that went back to holders;
* **the year-on-year change** in dividends paid, share repurchases, repayments of debt and
  capital expenditure — closing less opening, in the line's own currency, so a writer can
  name the movement rather than compute it.

The four cash-flow lines are :data:`~aer.core.concepts.MAGNITUDE_CONCEPTS`: payments,
held as positive amounts whatever sign the filer tagged, which is what makes the sum a sum
and a positive change a rise. Nothing here carries a threshold or a direction — these are
figures, not signals; what a rise in buybacks means is the writer's to argue and the
reader's to weigh.

Every function is pure and every refusal is a row. A line a filing does not report leaves
its figure absent naming the concept; a change with no prior period says so; operating
cash flow at or below zero refuses the ratio in the guard's own words. A unit mismatch is
never swallowed — see :data:`_NEVER_SWALLOWED`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.statements import StatementSet
from aer.calc.units import (
    CalculationError,
    Quantity,
    UnitMismatchError,
    UnsourcedValueError,
)

__all__ = [
    "CASH_USE_DEFINITIONS",
    "CashUseDefinition",
    "CashUseFigure",
    "assess_cash_uses",
    "capital_expenditure_change",
    "distributions_to_operating_cash_flow",
    "dividends_paid_change",
    "repayments_of_debt_change",
    "share_repurchases_change",
    "shareholder_distributions",
]

# A mapping error, never an absent figure: two lines of one statement disagreeing about
# what they measure has to propagate out of the module whose job is to notice problems.
_NEVER_SWALLOWED: Final = (UnitMismatchError, UnsourcedValueError)


@dataclass(frozen=True, slots=True)
class CashUseFigure:
    """One figure: its value, or why there isn't one."""

    key: str
    label: str
    quantity: Quantity | None
    # Empty when the figure computed. Otherwise the reason in words, naming the concepts
    # the filing did not report, the period it lacked, or what the guard refused and why.
    absent_because: str = ""
    missing: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.quantity is not None

    @property
    def value(self) -> Decimal | None:
        return self.quantity.value if self.quantity is not None else None


@dataclass(frozen=True, slots=True)
class CashUseDefinition:
    """A figure, the concepts it needs, and how to compute it.

    A table rather than a chain of calls, for the reason the ratio suite is one: "what does
    this platform strike about capital allocation, and from what?" is a value that can be
    inspected and tested, and a new figure is a row.
    """

    key: str
    label: str
    needs: tuple[str, ...]
    compute: Callable[[CalculationContext, Mapping[str, Quantity]], Quantity]
    # What the figure says, in the words a reader of the ledger sees beside it.
    note: str = ""
    # Whether this compares two periods. Reported absent with that as the reason on a
    # single-period run, rather than omitted and looking complete.
    needs_prior: bool = False


# -- The figures, each traced in its own right ------------------------------------------------


@traced(
    name="shareholder_distributions",
    formula="distributions = share repurchases + dividends paid",
    assumptions=(
        "Both lines are payments, held as positive magnitudes as the cash-flow statement "
        "reports them, so the sum is the cash returned to holders in the period.",
    ),
)
def shareholder_distributions(
    _context: CalculationContext, *, share_repurchases: Quantity, dividends_paid: Quantity
) -> Quantity:
    """Cash returned to shareholders: buybacks and dividends together.

    Raises:
        UnitMismatchError: If the two lines are in different currencies.
    """
    return share_repurchases + dividends_paid


@traced(
    name="distributions_to_operating_cash_flow",
    formula="distributions to OCF = (share repurchases + dividends paid) / operating cash flow",
    assumptions=(
        "Operating cash flow is positive; a business that generated no cash returned a "
        "share of nothing, and the ratio is refused rather than printed.",
    ),
)
def distributions_to_operating_cash_flow(
    _context: CalculationContext,
    *,
    share_repurchases: Quantity,
    dividends_paid: Quantity,
    operating_cash_flow: Quantity,
) -> Quantity:
    """The share of the year's operating cash flow returned to holders, as a fraction.

    Above one means the company returned more than it generated — funded from the balance
    sheet or from borrowing — which is exactly the case a reader wants pointed at.

    Raises:
        CalculationError: If operating cash flow is zero or negative.
        UnitMismatchError: If the lines are in different currencies.
    """
    if operating_cash_flow.value <= 0:
        message = (
            f"Distributions to operating cash flow is not meaningful when operating cash "
            f"flow is {operating_cash_flow.value} {operating_cash_flow.unit.symbol}: a "
            "business that generated no cash returned a share of nothing."
        )
        raise CalculationError(
            message, context={"operating_cash_flow": str(operating_cash_flow.value)}
        )
    return (share_repurchases + dividends_paid) / operating_cash_flow


def _change(closing: Quantity, opening: Quantity) -> Quantity:
    """Closing less opening, in the line's own unit: positive is a rise."""
    return closing - opening


@traced(
    name="dividends_paid_change",
    formula="change in dividends paid = dividends paid (this period) - dividends paid (prior)",
    assumptions=("The two periods are consecutive fiscal years of equal length.",),
)
def dividends_paid_change(
    _context: CalculationContext, *, opening: Quantity, closing: Quantity
) -> Quantity:
    """How much more, or less, was paid in dividends than the year before."""
    return _change(closing, opening)


@traced(
    name="share_repurchases_change",
    formula="change in repurchases = share repurchases (this period) - share repurchases (prior)",
    assumptions=("The two periods are consecutive fiscal years of equal length.",),
)
def share_repurchases_change(
    _context: CalculationContext, *, opening: Quantity, closing: Quantity
) -> Quantity:
    """How much more, or less, was spent on buybacks than the year before."""
    return _change(closing, opening)


@traced(
    name="repayments_of_debt_change",
    formula="change in repayments = repayments of debt (this period) - repayments of debt (prior)",
    assumptions=("The two periods are consecutive fiscal years of equal length.",),
)
def repayments_of_debt_change(
    _context: CalculationContext, *, opening: Quantity, closing: Quantity
) -> Quantity:
    """How much more, or less, debt was repaid than the year before."""
    return _change(closing, opening)


@traced(
    name="capital_expenditure_change",
    formula="change in capex = capital expenditure (this period) - capital expenditure (prior)",
    assumptions=("The two periods are consecutive fiscal years of equal length.",),
)
def capital_expenditure_change(
    _context: CalculationContext, *, opening: Quantity, closing: Quantity
) -> Quantity:
    """How much more, or less, was invested in the asset base than the year before."""
    return _change(closing, opening)


# -- The table -------------------------------------------------------------------------------


def _paired(concept: str) -> Callable[[CalculationContext, Mapping[str, Quantity]], Quantity]:
    """The change function for one line, called with the two periods' values."""
    functions = {
        "dividends_paid": dividends_paid_change,
        "share_repurchases": share_repurchases_change,
        "repayments_of_debt": repayments_of_debt_change,
        "capital_expenditure": capital_expenditure_change,
    }
    function = functions[concept]
    return lambda ctx, v: function(ctx, opening=v["opening"], closing=v["closing"])


CASH_USE_DEFINITIONS: Final[tuple[CashUseDefinition, ...]] = (
    CashUseDefinition(
        key="shareholder_distributions",
        label="Shareholder distributions",
        needs=("share_repurchases", "dividends_paid"),
        compute=lambda ctx, v: shareholder_distributions(
            ctx, share_repurchases=v["share_repurchases"], dividends_paid=v["dividends_paid"]
        ),
        note="Cash returned to holders in the period: buybacks and dividends together.",
    ),
    CashUseDefinition(
        key="distributions_to_operating_cash_flow",
        label="Distributions to operating cash flow",
        needs=("share_repurchases", "dividends_paid", "operating_cash_flow"),
        compute=lambda ctx, v: distributions_to_operating_cash_flow(
            ctx,
            share_repurchases=v["share_repurchases"],
            dividends_paid=v["dividends_paid"],
            operating_cash_flow=v["operating_cash_flow"],
        ),
        note=(
            "The share of the year's operating cash flow returned to holders. Above one, "
            "the company returned more than it generated."
        ),
    ),
    CashUseDefinition(
        key="dividends_paid_change",
        label="Change in dividends paid",
        needs=("dividends_paid",),
        compute=_paired("dividends_paid"),
        note="This year's dividends paid less last year's; positive is a rise.",
        needs_prior=True,
    ),
    CashUseDefinition(
        key="share_repurchases_change",
        label="Change in share repurchases",
        needs=("share_repurchases",),
        compute=_paired("share_repurchases"),
        note="This year's buybacks less last year's; positive is a rise.",
        needs_prior=True,
    ),
    CashUseDefinition(
        key="repayments_of_debt_change",
        label="Change in repayments of debt",
        needs=("repayments_of_debt",),
        compute=_paired("repayments_of_debt"),
        note="This year's debt repayments less last year's; positive is a rise.",
        needs_prior=True,
    ),
    CashUseDefinition(
        key="capital_expenditure_change",
        label="Change in capital expenditure",
        needs=("capital_expenditure",),
        compute=_paired("capital_expenditure"),
        note="This year's capital expenditure less last year's; positive is a rise.",
        needs_prior=True,
    ),
)


# -- The pass ----------------------------------------------------------------------------------


def assess_cash_uses(
    context: CalculationContext,
    statements: StatementSet,
    *,
    prior: StatementSet | None = None,
) -> tuple[CashUseFigure, ...]:
    """Every capital-allocation figure, computed or explained.

    One row per definition, always: a suite that returned only what it could compute would
    make a filing with two figures indistinguishable from one with two figures and four
    unanswered questions.

    Args:
        prior: The preceding period's statements. Without it the four changes are reported
            absent with that as the reason. The changes are struck in *this* period's pass
            and carry this period's stamp: a change is a figure of the year it closes.

    Raises:
        UnitMismatchError: If two lines a figure needs are in different currencies. Never
            reported as an absent figure — see :data:`_NEVER_SWALLOWED`.
        UnsourcedValueError: If a line reached here without provenance.
    """
    return tuple(
        _paired_figure(context, definition, statements, prior)
        if definition.needs_prior
        else _single_figure(context, definition, statements)
        for definition in CASH_USE_DEFINITIONS
    )


def _single_figure(
    context: CalculationContext,
    definition: CashUseDefinition,
    statements: StatementSet,
) -> CashUseFigure:
    values, missing = _gather(definition.needs, statements)
    if missing:
        return _absent(
            definition,
            f"{definition.label} needs {', '.join(definition.needs)}, and this filing does "
            f"not report {', '.join(missing)}.",
            missing=missing,
        )
    return _compute(context, definition, values)


def _paired_figure(
    context: CalculationContext,
    definition: CashUseDefinition,
    statements: StatementSet,
    prior: StatementSet | None,
) -> CashUseFigure:
    if prior is None:
        return _absent(
            definition,
            f"{definition.label} compares two periods, and only one period's statements "
            "were supplied. The level is reported; the movement is not.",
        )
    [concept] = definition.needs
    closing = statements.get(concept)
    opening = prior.get(concept)
    if closing is None or opening is None:
        return _absent(
            definition,
            f"{definition.label} needs {concept} in both periods, and it is missing from "
            f"{'this' if closing is None else 'the prior'} period.",
            missing=(concept,),
        )
    return _compute(context, definition, {"opening": opening, "closing": closing})


def _gather(
    needs: tuple[str, ...], statements: StatementSet
) -> tuple[dict[str, Quantity], tuple[str, ...]]:
    values: dict[str, Quantity] = {}
    missing: list[str] = []
    for concept in needs:
        found = statements.get(concept)
        if found is None:
            missing.append(concept)
        else:
            values[concept] = found
    return values, tuple(missing)


def _compute(
    context: CalculationContext,
    definition: CashUseDefinition,
    values: Mapping[str, Quantity],
) -> CashUseFigure:
    try:
        computed = definition.compute(context, values)
    except _NEVER_SWALLOWED:
        raise
    except CalculationError as refused:
        # The guard's own words: one description of the condition, the one the check made.
        return _absent(definition, refused.message)
    return CashUseFigure(key=definition.key, label=definition.label, quantity=computed)


def _absent(
    definition: CashUseDefinition, reason: str, *, missing: tuple[str, ...] = ()
) -> CashUseFigure:
    return CashUseFigure(
        key=definition.key,
        label=definition.label,
        quantity=None,
        absent_because=reason,
        missing=missing,
    )
