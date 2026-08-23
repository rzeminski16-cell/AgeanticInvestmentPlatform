"""Earnings quality: whether the reported profit and the cash have parted company.

Every figure here is computable from the statements alone, and every one of them is a
*question*, not a verdict. A company with an accruals ratio of 12% is not committing fraud;
it is a company whose profit ran ahead of its cash this year, and there are several innocent
reasons for that. The value of these metrics is that they make the question askable at all —
a report that never computes CFO over net income cannot notice when it has been below one
for four years running.

**A flag is "look at this", never "this is wrong".** Each signal declares which direction is
concerning and a threshold, both stated as constants with the reasoning beside them. The
thresholds are judgement, and judgement that lives in a named constant can be argued with;
judgement buried in an ``if`` cannot.

**What cannot be derived is listed, not omitted.** `docs/archive/PLAN.md` names R&D capitalisation
among the policy flags, and it is not computable from a sixty-two-concept vocabulary: an
increase in intangible assets cannot be separated from an acquisition without reading the
notes. It appears here as an unavailable signal with that reason, so a reader can tell "we
checked and it was fine" apart from "we never looked".

Two signals need a prior period and say so when they do not have one. A depreciation rate is
a number; a *falling* depreciation rate is the useful-life question, and only the second is
worth anything.

Pure and side-effect free.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from aer.calc.engine import CalculationContext, PeriodStamp, traced
from aer.calc.statements import StatementSet
from aer.calc.units import (
    CalculationError,
    Quantity,
    UnitMismatchError,
    UnsourcedValueError,
)

__all__ = [
    "QUALITY_DEFINITIONS",
    "UNAVAILABLE_SIGNALS",
    "Direction",
    "QualityDefinition",
    "QualitySignal",
    "Unavailable",
    "accruals_ratio",
    "assess_quality",
    "capex_to_depreciation",
    "cash_conversion",
    "depreciation_rate",
    "interest_capitalisation_gap",
    "level_change",
    "rate_change",
    "working_capital_intensity",
]


class Direction(StrEnum):
    """Which way a signal has to move before it is worth a second look."""

    HIGHER_IS_CONCERNING = "higher_is_concerning"
    LOWER_IS_CONCERNING = "lower_is_concerning"


# -- The thresholds, each a judgement stated where it can be argued with ----------------------

# Accruals above a tenth of the asset base.
#
# Sloan's original work found the highest-accrual decile of US listed companies
# systematically underperformed, and a tenth of total assets puts a company firmly in it for
# most sectors. Not a rule: a business genuinely growing its receivables to fund real sales
# growth sits here too, which is why this is a flag and not a conclusion.
ACCRUALS_CONCERN: Final = Decimal("0.10")

# Operating cash flow below reported profit.
#
# One is the natural line: below it, the profit did not arrive as cash this year. A single
# year below one is ordinary — working capital moves — and it is the persistence that
# matters, which is a comparison across periods this module does not yet make.
CASH_CONVERSION_CONCERN: Final = Decimal("1")

# Capital spending below the depreciation charge.
#
# Sustained, it means the asset base is being consumed faster than it is replaced, and the
# reported profit is partly a deferral of spending rather than a return. Below one for one
# year is a capex cycle; below one for five is a decision somebody made.
CAPEX_COVER_CONCERN: Final = Decimal("1")

# Cash interest paid running more than a tenth above the interest charged to profit.
#
# The gap is interest capitalised into the cost of an asset rather than expensed. Legitimate
# and disclosed, but it flatters both operating profit and interest cover, so a reader
# comparing this company's cover with a peer's needs to know it is happening. A tenth allows
# for timing between the charge and the payment.
INTEREST_CAPITALISATION_CONCERN: Final = Decimal("0.10")

# A depreciation rate falling by more than a tenth of itself between periods.
#
# The observable end of a useful-life extension: the same asset base, charged less. It can
# equally be a change in asset mix, which is why it asks rather than concludes.
DEPRECIATION_RATE_FALL_CONCERN: Final = Decimal("-0.10")

# Working capital absorbing more than a twentieth of additional revenue.
#
# Movement, not level: a business whose working-capital intensity is rising is one turning an
# increasing share of each pound of sales into stock and receivables rather than cash.
WORKING_CAPITAL_DRIFT_CONCERN: Final = Decimal("0.05")

_NEVER_SWALLOWED: Final = (UnitMismatchError, UnsourcedValueError)


@dataclass(frozen=True, slots=True)
class Unavailable:
    """Something `docs/archive/PLAN.md` asks for that the statements cannot answer.

    Listed rather than omitted. A reader has to be able to distinguish a check that passed
    from one that was never run, and an absent row says nothing at all.
    """

    key: str
    label: str
    why: str
    where_to_look: str


UNAVAILABLE_SIGNALS: Final[tuple[Unavailable, ...]] = (
    Unavailable(
        key="rd_capitalisation",
        label="Development cost capitalisation",
        why=(
            "An IFRS filer capitalising development costs increases intangible assets, and "
            "so does one making an acquisition. The two cannot be separated from the "
            "concepts this platform maps, and a metric that conflated them would flag every "
            "acquisitive company and miss every organic capitaliser."
        ),
        where_to_look=(
            "The intangible assets note, which splits additions between internally generated "
            "and acquired, and the accounting-policies note on development costs."
        ),
    ),
    Unavailable(
        key="useful_life_disclosure",
        label="Stated useful lives",
        why=(
            "The lives themselves are prose in the accounting-policies note, not a tagged "
            "figure. What is computable is the depreciation *rate* and its movement, which "
            "is the observable consequence of a life changing — see `depreciation_rate_change`."
        ),
        where_to_look="The property, plant and equipment accounting-policies note.",
    ),
    Unavailable(
        key="revenue_recognition_policy",
        label="Revenue recognition policy",
        why=(
            "Whether revenue is recognised at a point in time or over time changes what "
            "every margin in this suite means, and it is disclosed in prose."
        ),
        where_to_look="The revenue accounting-policies note and the critical-judgements note.",
    ),
)


@dataclass(frozen=True, slots=True)
class QualitySignal:
    """One earnings-quality figure, its verdict, and what it is asking about."""

    key: str
    label: str
    quantity: Quantity | None
    direction: Direction
    threshold: Decimal

    # What the number is asking, in a sentence a reader can act on. Shown beside it, because
    # "accruals ratio: 0.14" tells a reader nothing they can use.
    question: str

    absent_because: str = ""
    missing: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.quantity is not None

    @property
    def value(self) -> Decimal | None:
        return self.quantity.value if self.quantity is not None else None

    @property
    def flagged(self) -> bool:
        """Whether this crossed its threshold in the concerning direction.

        ``False`` for an absent signal, which is *not* the same as "fine" — read
        :attr:`present` first. A caller treating absence as a pass is the failure this
        docstring exists to prevent.
        """
        if self.quantity is None:
            return False
        if self.direction is Direction.HIGHER_IS_CONCERNING:
            return self.quantity.value > self.threshold
        return self.quantity.value < self.threshold


# -- The metrics ------------------------------------------------------------------------------


@traced(
    name="accruals_ratio",
    formula="accruals ratio = (net income - operating cash flow) / total assets",
    assumptions=(
        "The balance-sheet accruals definition, scaled by period-end total assets rather "
        "than average net operating assets. A vendor using the latter will report a "
        "different number for the same company.",
    ),
)
def accruals_ratio(
    _context: CalculationContext,
    *,
    net_income: Quantity,
    operating_cash_flow: Quantity,
    assets: Quantity,
) -> Quantity:
    """How much of the reported profit did not arrive as cash, against the asset base."""
    if assets.value <= 0:
        message = (
            f"Total assets are {assets.value}, so an accruals ratio against them is undefined."
        )
        raise CalculationError(message, context={"assets": str(assets.value)})
    return (net_income - operating_cash_flow) / assets


@traced(
    name="cash_conversion",
    formula="cash conversion = operating cash flow / net income",
)
def cash_conversion(
    _context: CalculationContext, *, operating_cash_flow: Quantity, net_income: Quantity
) -> Quantity:
    """Cash from operations per unit of reported profit.

    Raises:
        CalculationError: If net income is not positive. The ratio inverts its meaning
            against a loss — a company losing money and burning cash produces a *positive*
            conversion figure, which reads as healthy and is the opposite of the truth.
    """
    if net_income.value <= 0:
        message = (
            f"Net income is {net_income.value}. Cash conversion against a loss inverts its "
            "sign: a loss-making, cash-burning company scores positively, which is exactly "
            "backwards. Read the cash flow statement directly here."
        )
        raise CalculationError(message, context={"net_income": str(net_income.value)})
    return operating_cash_flow / net_income


@traced(
    name="working_capital_intensity",
    formula="intensity = (receivables + inventory - payables) / revenue",
    assumptions=(
        "Trade working capital only. Cash, debt and tax balances are excluded because they "
        "are financing and not the operating cycle.",
        "Balance-sheet figures are the period-end balance, not the average over the period.",
    ),
)
def working_capital_intensity(
    _context: CalculationContext,
    *,
    accounts_receivable: Quantity,
    inventory: Quantity,
    accounts_payable: Quantity,
    revenue: Quantity,
) -> Quantity:
    """How much of a period's revenue is tied up in the operating cycle."""
    if revenue.value <= 0:
        message = f"Revenue is {revenue.value}, so working-capital intensity is undefined."
        raise CalculationError(message, context={"revenue": str(revenue.value)})
    return (accounts_receivable + inventory - accounts_payable) / revenue


@traced(
    name="capex_to_depreciation",
    formula="capex cover = capital expenditure / depreciation and amortisation",
    assumptions=(
        "Capital expenditure is the payment for property, plant and equipment as the filer "
        "reported it — a positive magnitude, per the sign convention in `aer.core.concepts`.",
        "Depreciation and amortisation is the whole charge, including amortisation of "
        "intangibles that no capital expenditure line replaces.",
    ),
)
def capex_to_depreciation(
    _context: CalculationContext, *, capital_expenditure: Quantity, depreciation: Quantity
) -> Quantity:
    """Whether the asset base is being replaced as fast as it is being consumed."""
    if depreciation.value <= 0:
        message = (
            f"The depreciation charge is {depreciation.value}, so capex cover against it is "
            "undefined."
        )
        raise CalculationError(message, context={"depreciation": str(depreciation.value)})
    return capital_expenditure / depreciation


@traced(
    name="depreciation_rate",
    formula="depreciation rate = depreciation and amortisation / property, plant and equipment",
    assumptions=(
        "The charge includes amortisation of intangibles while the base is tangible assets "
        "only, so the level overstates the true rate. The *movement* is what this is for, "
        "and the overstatement largely cancels between periods.",
        "Balance-sheet figures are the period-end balance, not the average over the period.",
    ),
)
def depreciation_rate(
    _context: CalculationContext, *, depreciation: Quantity, property_plant_and_equipment: Quantity
) -> Quantity:
    """The charge as a fraction of the asset base carrying it."""
    if property_plant_and_equipment.value <= 0:
        message = (
            f"Property, plant and equipment is {property_plant_and_equipment.value}, so a "
            "depreciation rate against it is undefined."
        )
        raise CalculationError(message, context={"ppe": str(property_plant_and_equipment.value)})
    return depreciation / property_plant_and_equipment


@traced(
    name="interest_capitalisation_gap",
    formula="gap = (interest paid - interest expense) / interest expense",
    assumptions=(
        "Interest paid is the cash figure from the cash-flow statement and interest expense "
        "the charge to profit. Timing between the two contributes to the gap as well as "
        "capitalisation does.",
    ),
)
def interest_capitalisation_gap(
    _context: CalculationContext, *, interest_paid: Quantity, interest_expense: Quantity
) -> Quantity:
    """How far cash interest exceeds the interest charged against profit."""
    if interest_expense.value <= 0:
        message = (
            f"Interest expense is {interest_expense.value}. With no interest charged to "
            "profit there is nothing for the cash figure to exceed proportionally."
        )
        raise CalculationError(message, context={"interest_expense": str(interest_expense.value)})
    return (interest_paid - interest_expense) / interest_expense


@traced(
    name="level_change",
    formula="change = closing - opening",
    assumptions=("The two periods are of the same length and the same basis.",),
)
def level_change(_context: CalculationContext, *, opening: Quantity, closing: Quantity) -> Quantity:
    """The absolute movement in a level between two periods.

    Traced like everything else. It is one subtraction, and doing it inline would put a
    figure in a report whose provenance stopped at "the code did this".
    """
    return closing - opening


@traced(
    name="rate_change",
    formula="change = (closing - opening) / |opening|",
    assumptions=("The two periods are of the same length and the same basis.",),
)
def rate_change(_context: CalculationContext, *, opening: Quantity, closing: Quantity) -> Quantity:
    """The proportional movement in a rate between two periods.

    Raises:
        CalculationError: If the opening rate is zero, against which no proportional change
            exists.
    """
    if opening.value == 0:
        message = "The opening rate is zero, so a proportional change from it is undefined."
        raise CalculationError(message, context={"opening": str(opening.value)})
    return (closing - opening) / abs(opening)


# -- The tables -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QualityDefinition:
    """A signal, what it needs, and what it is asking."""

    key: str
    label: str
    needs: tuple[str, ...]
    direction: Direction
    threshold: Decimal
    question: str
    compute: Callable[[CalculationContext, Mapping[str, Quantity]], Quantity]

    # Whether this compares two periods. A single-period run reports these absent with that
    # as the reason, rather than omitting them and looking complete.
    needs_prior: bool = False


QUALITY_DEFINITIONS: Final[tuple[QualityDefinition, ...]] = (
    QualityDefinition(
        key="accruals_ratio",
        label="Accruals ratio",
        needs=("net_income", "operating_cash_flow", "assets"),
        direction=Direction.HIGHER_IS_CONCERNING,
        threshold=ACCRUALS_CONCERN,
        question="How much of this year's profit has not arrived as cash?",
        compute=lambda ctx, v: accruals_ratio(
            ctx,
            net_income=v["net_income"],
            operating_cash_flow=v["operating_cash_flow"],
            assets=v["assets"],
        ),
    ),
    QualityDefinition(
        key="cash_conversion",
        label="Cash conversion",
        needs=("operating_cash_flow", "net_income"),
        direction=Direction.LOWER_IS_CONCERNING,
        threshold=CASH_CONVERSION_CONCERN,
        question="Does the profit turn into cash, and how reliably?",
        compute=lambda ctx, v: cash_conversion(
            ctx, operating_cash_flow=v["operating_cash_flow"], net_income=v["net_income"]
        ),
    ),
    QualityDefinition(
        key="working_capital_intensity",
        label="Working-capital intensity",
        needs=("accounts_receivable", "inventory", "accounts_payable", "revenue"),
        # No level is inherently concerning: a supermarket runs negative and a shipbuilder
        # runs very high, and both are the business rather than a problem. The threshold sits
        # above anything ordinary so that the level is reported and rarely flagged; the
        # movement is what `working_capital_drift` asks about.
        direction=Direction.HIGHER_IS_CONCERNING,
        threshold=Decimal("0.50"),
        question="How much of each year's revenue is tied up in the operating cycle?",
        compute=lambda ctx, v: working_capital_intensity(
            ctx,
            accounts_receivable=v["accounts_receivable"],
            inventory=v["inventory"],
            accounts_payable=v["accounts_payable"],
            revenue=v["revenue"],
        ),
    ),
    QualityDefinition(
        key="capex_to_depreciation",
        label="Capex cover of depreciation",
        needs=("capital_expenditure", "depreciation_and_amortisation"),
        direction=Direction.LOWER_IS_CONCERNING,
        threshold=CAPEX_COVER_CONCERN,
        question="Is the asset base being replaced as fast as it is being consumed?",
        compute=lambda ctx, v: capex_to_depreciation(
            ctx,
            capital_expenditure=v["capital_expenditure"],
            depreciation=v["depreciation_and_amortisation"],
        ),
    ),
    QualityDefinition(
        key="interest_capitalisation_gap",
        label="Interest capitalisation gap",
        needs=("interest_paid", "interest_expense"),
        direction=Direction.HIGHER_IS_CONCERNING,
        threshold=INTEREST_CAPITALISATION_CONCERN,
        question="Is interest being capitalised into assets rather than charged to profit?",
        compute=lambda ctx, v: interest_capitalisation_gap(
            ctx, interest_paid=v["interest_paid"], interest_expense=v["interest_expense"]
        ),
    ),
    QualityDefinition(
        key="depreciation_rate",
        # The label says what the ratio measures (gap R16): the numerator is all D&A —
        # intangible amortisation included — over net PP&E, so an asset-light company
        # legitimately shows 0.65 to 0.88, and a label promising a fixed-asset depreciation
        # rate made a defensible figure read as alarming. The stored key stays, so the
        # ledger's history remains comparable across code versions.
        label="D&A to net PP&E",
        needs=("depreciation_and_amortisation", "property_plant_and_equipment"),
        # The level alone flags nothing: it varies by an order of magnitude across sectors.
        # The threshold is set below any plausible rate so the figure is reported for the
        # reader and `depreciation_rate_change` does the asking.
        direction=Direction.LOWER_IS_CONCERNING,
        threshold=Decimal("0"),
        question="What fraction of the tangible asset base is charged away each year?",
        compute=lambda ctx, v: depreciation_rate(
            ctx,
            depreciation=v["depreciation_and_amortisation"],
            property_plant_and_equipment=v["property_plant_and_equipment"],
        ),
    ),
)

# Signals that compare two periods. Separated because their inputs come from two statement
# sets, which is a different shape from everything above.
_PAIRED: Final[tuple[QualityDefinition, ...]] = (
    QualityDefinition(
        key="depreciation_rate_change",
        label="Change in D&A to net PP&E",
        needs=("depreciation_and_amortisation", "property_plant_and_equipment"),
        direction=Direction.LOWER_IS_CONCERNING,
        threshold=DEPRECIATION_RATE_FALL_CONCERN,
        question=(
            "Is the same asset base being charged away more slowly than it was — the "
            "observable end of a useful-life extension?"
        ),
        compute=lambda ctx, v: rate_change(ctx, opening=v["opening"], closing=v["closing"]),
        needs_prior=True,
    ),
    QualityDefinition(
        key="working_capital_drift",
        label="Working-capital drift",
        needs=("accounts_receivable", "inventory", "accounts_payable", "revenue"),
        direction=Direction.HIGHER_IS_CONCERNING,
        threshold=WORKING_CAPITAL_DRIFT_CONCERN,
        question=(
            "Is an increasing share of each pound of sales being turned into stock and "
            "receivables rather than cash?"
        ),
        compute=lambda ctx, v: level_change(ctx, opening=v["opening"], closing=v["closing"]),
        needs_prior=True,
    ),
)

# Which single-period signal each paired one is the movement in.
_PAIRED_BASE: Final[Mapping[str, str]] = {
    "depreciation_rate_change": "depreciation_rate",
    "working_capital_drift": "working_capital_intensity",
}


def assess_quality(
    context: CalculationContext,
    statements: StatementSet,
    *,
    prior: StatementSet | None = None,
    prior_period: PeriodStamp | None = None,
) -> tuple[QualitySignal, ...]:
    """Every earnings-quality signal, computed or explained.

    Args:
        prior: The preceding period. Without it the two comparison signals are reported
            absent with that as the reason, rather than omitted — a suite that silently
            shrinks when a period is missing is one whose completeness cannot be checked.

    Raises:
        UnitMismatchError: If two lines a signal needs are in different units. Never reported
            as an absent signal: that is a mapping error, and hiding it in the module whose
            job is to notice problems would be the wrong place of all places.
    """
    single = tuple(
        _single_period(context, definition, statements) for definition in QUALITY_DEFINITIONS
    )
    paired = tuple(
        _paired(context, definition, statements, prior, prior_period) for definition in _PAIRED
    )
    return single + paired


def _single_period(
    context: CalculationContext, definition: QualityDefinition, statements: StatementSet
) -> QualitySignal:
    values, missing = _gather(definition.needs, statements)
    if missing:
        return _absent(
            definition,
            f"{definition.label} needs {', '.join(definition.needs)}, and this filing does "
            f"not report {', '.join(missing)}.",
            missing=missing,
        )
    return _compute(context, definition, values)


def _paired(
    context: CalculationContext,
    definition: QualityDefinition,
    statements: StatementSet,
    prior: StatementSet | None,
    prior_period: PeriodStamp | None = None,
) -> QualitySignal:
    if prior is None:
        return _absent(
            definition,
            f"{definition.label} compares two periods, and only one period's statements "
            "were supplied. The level is reported; the movement is not.",
        )

    base_key = _PAIRED_BASE[definition.key]
    base = next(d for d in QUALITY_DEFINITIONS if d.key == base_key)

    closing_values, closing_missing = _gather(definition.needs, statements)
    opening_values, opening_missing = _gather(definition.needs, prior)
    missing = tuple(sorted(set(closing_missing) | set(opening_missing)))
    if missing:
        return _absent(
            definition,
            f"{definition.label} needs {', '.join(definition.needs)} in both periods, and "
            f"{', '.join(missing)} is missing from at least one.",
            missing=missing,
        )

    try:
        # The opening leg is a figure *of the prior period*, struck during this one's pass.
        # Stamping it with the closing period's label would put the previous year's number
        # under this year's heading on the approval page — and, since the prior period's
        # own pass already struck exactly this row, the stamp is the only thing that made
        # it look like a second one (gap R14).
        with context.stamped(prior_period):
            opening = base.compute(context, opening_values)
        ends = {
            "opening": opening,
            "closing": base.compute(context, closing_values),
        }
    except _NEVER_SWALLOWED:
        raise
    except CalculationError as refused:
        return _absent(definition, refused.message)

    return _compute(context, definition, ends)


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
    context: CalculationContext, definition: QualityDefinition, values: Mapping[str, Quantity]
) -> QualitySignal:
    try:
        computed = definition.compute(context, values)
    except _NEVER_SWALLOWED:
        raise
    except CalculationError as refused:
        return _absent(definition, refused.message)

    return QualitySignal(
        key=definition.key,
        label=definition.label,
        quantity=computed,
        direction=definition.direction,
        threshold=definition.threshold,
        question=definition.question,
    )


def _absent(
    definition: QualityDefinition, reason: str, *, missing: tuple[str, ...] = ()
) -> QualitySignal:
    return QualitySignal(
        key=definition.key,
        label=definition.label,
        quantity=None,
        direction=definition.direction,
        threshold=definition.threshold,
        question=definition.question,
        absent_because=reason,
        missing=missing,
    )
