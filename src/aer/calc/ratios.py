"""The ratios computable from filings alone, and the ones this filing cannot support.

Seventeen figures across six families. None of the arithmetic is hard — a current ratio is
one division — and that is exactly why it lives in Python: one division is *checkable*,
whereas a sentence asking for a current ratio is not.

**An absent input produces an absent ratio, never a zero.** A filer that did not report
inventory has no quick ratio, and :class:`RatioResult` says which concepts were missing. The
alternative is a suite where every company has every ratio and some of the numbers are
invented — and nothing downstream can tell those apart from the real ones.

**An undefined ratio is absent too, with the reason the guard gave.** Return on equity at
negative equity, interest cover with no interest expense, a leverage multiple on negative
EBITDA: each is a question with no meaningful answer, and each is reported as one. The
primitives themselves still *raise* — see :mod:`aer.calc.basic` on why — and it is this
module that turns a refusal into a row an operator can read.

**A unit mismatch is not an undefined ratio and is never swallowed.** It means two lines of
one statement disagree about what they measure, which is a mapping error and a bug. It
propagates. So does an unsourced value. Invariant 5 has no exceptions here.

**Balance-sheet figures are period-end, not averages.** A textbook return on equity divides
by average equity over the year. This suite has one period's statements, so it uses the
closing balance and *says so in every affected calculation's recorded assumptions* — a
reader comparing this ROE with a data vendor's should know which of the two conventions
produced the difference before concluding either is wrong.

Pure and side-effect free. It is given a :class:`~aer.calc.statements.StatementSet`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.statements import StatementSet
from aer.calc.units import (
    CalculationError,
    Quantity,
    Unit,
    UnitMismatchError,
    UnsourcedValueError,
)

__all__ = [
    "DAYS_IN_YEAR",
    "RATIO_DEFINITIONS",
    "RatioDefinition",
    "RatioFamily",
    "RatioResult",
    "asset_turnover",
    "compute_ratios",
    "current_ratio",
    "days_outstanding",
    "debt_to_equity",
    "ebitda",
    "gross_margin",
    "interest_cover",
    "invested_capital",
    "net_debt",
    "net_debt_to_ebitda",
    "net_margin",
    "nopat",
    "operating_margin",
    "quick_ratio",
    "return_on_assets",
    "return_on_equity",
    "return_on_invested_capital",
    "working_capital",
]


class RatioFamily(StrEnum):
    """What question a ratio answers. Drives presentation order and nothing else."""

    MARGIN = "margin"
    RETURN = "return"
    LIQUIDITY = "liquidity"
    LEVERAGE = "leverage"
    COVERAGE = "coverage"
    EFFICIENCY = "efficiency"


# The denominator that turns a balance-sheet-over-flow ratio into a number of days.
#
# 365, not 360 and not 365.25. Filings state annual periods and readers compare days-sales
# figures against other readers' days-sales figures; the convention matters more than the
# astronomy, and 360 is a money-market convention that has no business here.
DAYS_IN_YEAR: Final = Decimal(365)

_DAY: Final = Unit.base("day")

# Errors that mean the code is wrong rather than the ratio is undefined.
#
# `compute_ratios` turns a refusal into an absent row, which is right for "equity is
# negative so there is no return on it" and catastrophic for "these two lines are in
# different currencies". The second is a mapping error that would then be invisible in
# exactly the place somebody is looking for problems.
_NEVER_SWALLOWED: Final = (UnitMismatchError, UnsourcedValueError)

# What every balance-sheet-derived ratio in this module is assuming, recorded on each one.
_PERIOD_END: Final = (
    "Balance-sheet figures are the period-end balance, not the average over the period."
)


@dataclass(frozen=True, slots=True)
class RatioResult:
    """One ratio: its value, or why there isn't one."""

    key: str
    label: str
    family: RatioFamily
    quantity: Quantity | None

    # Empty when the ratio computed. Otherwise the reason in words, either naming the
    # concepts the filing did not report or repeating what the guard refused and why.
    absent_because: str = ""

    # The canonical concepts this ratio needed and the filing did not have. Separate from
    # `absent_because` because "which concepts are we missing across the whole suite?" is a
    # question worth answering by aggregation rather than by reading prose.
    missing: tuple[str, ...] = ()

    @property
    def present(self) -> bool:
        return self.quantity is not None

    @property
    def value(self) -> Decimal | None:
        return self.quantity.value if self.quantity is not None else None


@dataclass(frozen=True, slots=True)
class RatioDefinition:
    """A ratio, the concepts it needs, and how to compute it.

    A table rather than a function per caller, so "what can this platform compute, and from
    what?" is a value that can be inspected, counted and tested — and so adding a ratio is a
    row rather than an edit to a chain of conditionals.
    """

    key: str
    label: str
    family: RatioFamily
    needs: tuple[str, ...]
    compute: Callable[[CalculationContext, Mapping[str, Quantity]], Quantity]

    # What the figure means, and any convention it commits to. Shown beside the number,
    # because "ROIC" without a definition of invested capital is not a comparable figure.
    note: str = ""


# -- Aggregates the ratios are built from, each traced in its own right -----------------------
#
# These are calculations, not conveniences. `ebitda` appears as an input to two ratios and as
# its own ledger entry, so a reader asking "what EBITDA did this use?" gets one answer with
# one provenance rather than two coincidentally equal numbers.


@traced(
    name="ebitda",
    formula="EBITDA = operating income + depreciation and amortisation",
    assumptions=(
        "Operating income is after the filer's own operating expenses and before interest and tax.",
        "No adjustment is made for exceptional or non-recurring items: this is EBITDA as "
        "the filing supports it, not an adjusted measure.",
    ),
)
def ebitda(
    _context: CalculationContext, *, operating_income: Quantity, depreciation: Quantity
) -> Quantity:
    """Earnings before interest, tax, depreciation and amortisation."""
    return operating_income + depreciation


@traced(
    name="net_debt",
    formula="net debt = total debt - cash and equivalents",
    assumptions=(
        "Cash is treated as fully available to repay debt, which overstates flexibility for "
        "a group holding cash in subsidiaries it cannot freely upstream.",
        _PERIOD_END,
    ),
)
def net_debt(_context: CalculationContext, *, total_debt: Quantity, cash: Quantity) -> Quantity:
    """Borrowings net of cash."""
    return total_debt - cash


@traced(
    name="working_capital",
    formula="working capital = current assets - current liabilities",
    assumptions=(
        "Every current asset is as realisable as every current liability is payable. A "
        "business carrying slow inventory or a large receivable from one customer has less "
        "usable working capital than this figure says.",
        _PERIOD_END,
    ),
)
def working_capital(
    _context: CalculationContext, *, current_assets: Quantity, current_liabilities: Quantity
) -> Quantity:
    """Net working capital: the level, not the movement.

    Derived rather than read off a line, because no filer reports it as a tagged concept —
    it is a subtraction everybody does and nobody files. Negative is normal for a business
    paid before it pays, and is returned as it stands rather than floored at zero.
    """
    return current_assets - current_liabilities


@traced(
    name="invested_capital",
    formula="invested capital = total debt + equity - cash and equivalents",
    assumptions=(
        "The financing definition of invested capital: what the providers of capital have "
        "put in, less the cash not yet put to work. An operating definition — net working "
        "capital plus net fixed assets — reaches a similar figure by a different route and "
        "is not what this uses.",
        _PERIOD_END,
    ),
)
def invested_capital(
    _context: CalculationContext, *, total_debt: Quantity, equity: Quantity, cash: Quantity
) -> Quantity:
    """The capital the business is being asked to earn a return on."""
    return total_debt + equity - cash


@traced(
    name="nopat",
    formula="NOPAT = operating income * (1 - income tax expense / pre-tax income)",
    assumptions=(
        "The effective rate on pre-tax profit is applied to operating profit. This is the "
        "conventional approximation; the true marginal rate on operating income differs "
        "wherever tax on non-operating items is material.",
    ),
)
def nopat(
    _context: CalculationContext,
    *,
    operating_income: Quantity,
    income_tax_expense: Quantity,
    pre_tax_income: Quantity,
) -> Quantity:
    """Net operating profit after tax.

    Raises:
        CalculationError: If pre-tax income is not positive, which makes the effective rate
            meaningless — a loss-making year's tax charge says nothing about the rate a
            profitable operation would pay.
    """
    if pre_tax_income.value <= 0:
        message = (
            f"Pre-tax income is {pre_tax_income.value}, so there is no meaningful effective "
            "tax rate to apply to operating profit. NOPAT is not defined here."
        )
        raise CalculationError(message, context={"pre_tax_income": str(pre_tax_income.value)})

    one = Quantity.of(Decimal(1), source=operating_income.source)
    return operating_income * (one - income_tax_expense / pre_tax_income)


@traced(
    name="days_outstanding",
    formula="days = balance / annual flow * 365",
    assumptions=(
        "The flow covers a full year. A ratio computed from a half-year figure understates "
        "the days by half and looks entirely plausible.",
        _PERIOD_END,
    ),
)
def days_outstanding(
    _context: CalculationContext, *, balance: Quantity, flow: Quantity
) -> Quantity:
    """A balance expressed as the number of days of a flow it represents.

    Raises:
        CalculationError: If the flow is not positive. Days of sales on no sales is not a
            large number, it is a question with no answer.
    """
    if flow.value <= 0:
        message = (
            f"The annual flow is {flow.value}, so a days figure against it is undefined "
            "rather than large."
        )
        raise CalculationError(message, context={"flow": str(flow.value)})

    year = Quantity.of(DAYS_IN_YEAR, _DAY, source=balance.source)
    # Multiplied before divided. `balance / flow * year` rounds the intermediate quotient to
    # 34 digits and then multiplies the error back up: 100 over a flow of 365 comes back as
    # 99.999999999999999999999999999999 days. Reordering keeps the division last, so a
    # balance that is a whole number of days' flow is a whole number of days.
    return balance * year / flow


# -- The ratios ------------------------------------------------------------------------------
#
# One traced function each, rather than a shared `ratio()` used seventeen times. The ledger
# has to say *which* ratio it recorded: seventeen entries all named "ratio" is a ledger that
# answers no question anybody asks of it. Each also carries its own formula and assumptions,
# and those are the documentation rather than boilerplate around it.


def _margin(part: Quantity, whole: Quantity, *, of: str) -> Quantity:
    """A margin over revenue, refusing a base that makes it meaningless."""
    if part.unit != whole.unit:
        message = (
            f"A {of} of {part.unit.symbol} over {whole.unit.symbol} is not a margin. Both "
            "figures must measure the same thing in the same unit."
        )
        raise UnitMismatchError(
            message, context={"part": part.unit.symbol, "whole": whole.unit.symbol}
        )
    if whole.value <= 0:
        message = (
            f"Revenue is {whole.value}, so a {of} against it is undefined. A margin on zero "
            "or negative revenue is not a percentage of anything."
        )
        raise CalculationError(message, context={"revenue": str(whole.value)})
    return part / whole


@traced(name="gross_margin", formula="gross margin = gross profit / revenue")
def gross_margin(
    _context: CalculationContext, *, gross_profit: Quantity, revenue: Quantity
) -> Quantity:
    """What is left of a pound of sales after the direct cost of delivering it."""
    return _margin(gross_profit, revenue, of="gross margin")


@traced(name="operating_margin", formula="operating margin = operating income / revenue")
def operating_margin(
    _context: CalculationContext, *, operating_income: Quantity, revenue: Quantity
) -> Quantity:
    """What is left after every operating cost, before interest and tax."""
    return _margin(operating_income, revenue, of="operating margin")


@traced(name="net_margin", formula="net margin = net income / revenue")
def net_margin(
    _context: CalculationContext, *, net_income: Quantity, revenue: Quantity
) -> Quantity:
    """What reaches the owners, per pound of sales."""
    return _margin(net_income, revenue, of="net margin")


@traced(name="ebitda_margin", formula="EBITDA margin = EBITDA / revenue")
def ebitda_margin(
    _context: CalculationContext, *, ebitda_value: Quantity, revenue: Quantity
) -> Quantity:
    """EBITDA per pound of sales. Not a cash-flow measure, whatever it is used as."""
    return _margin(ebitda_value, revenue, of="EBITDA margin")


@traced(
    name="return_on_equity",
    formula="ROE = net income / equity",
    assumptions=(_PERIOD_END,),
)
def return_on_equity(
    _context: CalculationContext, *, net_income: Quantity, equity: Quantity
) -> Quantity:
    """What the owners earned on what they have in.

    Raises:
        CalculationError: If equity is not positive. A company with negative book equity
            produces a *positive* ROE from a loss and a negative one from a profit, which is
            the most misleading number in this suite if it is allowed out.
    """
    if equity.value <= 0:
        message = (
            f"Equity is {equity.value}. Return on equity at zero or negative book equity is "
            "not a return — the sign inverts and the figure means the opposite of what it "
            "appears to."
        )
        raise CalculationError(message, context={"equity": str(equity.value)})
    return net_income / equity


@traced(
    name="return_on_assets",
    formula="ROA = net income / total assets",
    assumptions=(_PERIOD_END,),
)
def return_on_assets(
    _context: CalculationContext, *, net_income: Quantity, assets: Quantity
) -> Quantity:
    """What the business earned on everything it controls, however it was financed."""
    if assets.value <= 0:
        message = f"Total assets are {assets.value}, so a return on them is undefined."
        raise CalculationError(message, context={"assets": str(assets.value)})
    return net_income / assets


@traced(
    name="return_on_invested_capital",
    formula="ROIC = NOPAT / invested capital",
    assumptions=(_PERIOD_END,),
)
def return_on_invested_capital(
    _context: CalculationContext, *, nopat_value: Quantity, capital: Quantity
) -> Quantity:
    """The return the operations earn on the capital funding them.

    Raises:
        CalculationError: If invested capital is not positive. A net-cash company financed
            entirely by retained profit can have invested capital at or below zero, and the
            answer there is "undefined", not an enormous or negative percentage.
    """
    if capital.value <= 0:
        message = (
            f"Invested capital is {capital.value}. A return on zero or negative invested "
            "capital is undefined rather than infinite; a company holding more cash than "
            "debt and equity combined needs a different measure."
        )
        raise CalculationError(message, context={"invested_capital": str(capital.value)})
    return nopat_value / capital


@traced(
    name="current_ratio",
    formula="current ratio = current assets / current liabilities",
    assumptions=(_PERIOD_END,),
)
def current_ratio(
    _context: CalculationContext, *, current_assets: Quantity, current_liabilities: Quantity
) -> Quantity:
    """Short-term assets against short-term obligations."""
    if current_liabilities.value <= 0:
        message = (
            f"Current liabilities are {current_liabilities.value}, so a current ratio "
            "against them is undefined."
        )
        raise CalculationError(
            message, context={"current_liabilities": str(current_liabilities.value)}
        )
    return current_assets / current_liabilities


@traced(
    name="quick_ratio",
    formula="quick ratio = (cash + short-term investments + receivables) / current liabilities",
    assumptions=(
        "The additive acid test, not current assets less inventory. The subtractive form "
        "leaves prepayments and other current assets in the numerator, and neither can be "
        "used to settle a creditor.",
        _PERIOD_END,
    ),
)
def quick_ratio(
    _context: CalculationContext,
    *,
    cash: Quantity,
    short_term_investments: Quantity,
    accounts_receivable: Quantity,
    current_liabilities: Quantity,
) -> Quantity:
    """Obligations against the assets that could actually meet them this month."""
    if current_liabilities.value <= 0:
        message = (
            f"Current liabilities are {current_liabilities.value}, so a quick ratio against "
            "them is undefined."
        )
        raise CalculationError(
            message, context={"current_liabilities": str(current_liabilities.value)}
        )
    return (cash + short_term_investments + accounts_receivable) / current_liabilities


@traced(
    name="debt_to_equity",
    formula="debt to equity = total debt / equity",
    assumptions=(_PERIOD_END,),
)
def debt_to_equity(
    _context: CalculationContext, *, total_debt: Quantity, equity: Quantity
) -> Quantity:
    """Borrowings against the owners' stake."""
    if equity.value <= 0:
        message = (
            f"Equity is {equity.value}. Debt to equity at zero or negative book equity "
            "inverts its sign and reads as low leverage on the most leveraged balance "
            "sheets there are."
        )
        raise CalculationError(message, context={"equity": str(equity.value)})
    return total_debt / equity


@traced(
    name="net_debt_to_ebitda",
    formula="net debt to EBITDA = net debt / EBITDA",
    assumptions=(
        "EBITDA is the period's, unadjusted. A covenant measured on adjusted EBITDA will "
        "differ, sometimes by a great deal.",
        _PERIOD_END,
    ),
)
def net_debt_to_ebitda(
    _context: CalculationContext, *, net_debt_value: Quantity, ebitda_value: Quantity
) -> Quantity:
    """How many years of earnings the net borrowings represent.

    Raises:
        CalculationError: If EBITDA is not positive. A leverage multiple on negative EBITDA
            is negative, and reads as no leverage at all on a company that cannot service
            any.
    """
    if ebitda_value.value <= 0:
        message = (
            f"EBITDA is {ebitda_value.value}. A leverage multiple against it is negative and "
            "would read as low leverage on a company whose earnings cannot service any debt."
        )
        raise CalculationError(message, context={"ebitda": str(ebitda_value.value)})
    return net_debt_value / ebitda_value


@traced(
    name="interest_cover",
    formula="interest cover = operating income / interest expense",
)
def interest_cover(
    _context: CalculationContext, *, operating_income: Quantity, interest_expense: Quantity
) -> Quantity:
    """How many times over the operating profit covers the interest bill.

    Raises:
        CalculationError: If interest expense is not positive. Either the company has no
            interest cost, in which case cover is not the right question, or the line was
            tagged with the opposite sign — and this refuses rather than guessing which.
    """
    if interest_expense.value <= 0:
        message = (
            f"Interest expense is {interest_expense.value}. Either there is no interest cost "
            "to cover, or the line carries the opposite sign from the one this expects; "
            "either way a cover ratio here would be meaningless."
        )
        raise CalculationError(message, context={"interest_expense": str(interest_expense.value)})
    return operating_income / interest_expense


@traced(
    name="asset_turnover",
    formula="asset turnover = revenue / total assets",
    assumptions=(_PERIOD_END,),
)
def asset_turnover(
    _context: CalculationContext, *, revenue: Quantity, assets: Quantity
) -> Quantity:
    """How much revenue the asset base produces per pound of itself."""
    if assets.value <= 0:
        message = f"Total assets are {assets.value}, so asset turnover is undefined."
        raise CalculationError(message, context={"assets": str(assets.value)})
    return revenue / assets


@traced(
    name="cash_conversion_cycle",
    formula="CCC = days sales outstanding + days inventory outstanding - days payable outstanding",
    assumptions=(
        "All three components are computed on the same period-end balances and the same "
        "365-day year.",
    ),
)
def cash_conversion_cycle(
    _context: CalculationContext, *, dso: Quantity, dio: Quantity, dpo: Quantity
) -> Quantity:
    """Days between paying for stock and being paid for it.

    Negative is not an error: a retailer that sells for cash and pays suppliers in ninety
    days is funded by its suppliers, which is a real and enviable position.
    """
    return dso + dio - dpo


# -- The table -------------------------------------------------------------------------------

RATIO_DEFINITIONS: Final[tuple[RatioDefinition, ...]] = (
    RatioDefinition(
        key="gross_margin",
        label="Gross margin",
        family=RatioFamily.MARGIN,
        needs=("gross_profit", "revenue"),
        compute=lambda ctx, v: gross_margin(
            ctx, gross_profit=v["gross_profit"], revenue=v["revenue"]
        ),
        note="Gross profit over revenue. Comparable only between filers who draw the line "
        "between cost of sales and operating expense in the same place.",
    ),
    RatioDefinition(
        key="operating_margin",
        label="Operating margin",
        family=RatioFamily.MARGIN,
        needs=("operating_income", "revenue"),
        compute=lambda ctx, v: operating_margin(
            ctx, operating_income=v["operating_income"], revenue=v["revenue"]
        ),
        note="Operating income over revenue.",
    ),
    RatioDefinition(
        key="net_margin",
        label="Net margin",
        family=RatioFamily.MARGIN,
        needs=("net_income", "revenue"),
        compute=lambda ctx, v: net_margin(ctx, net_income=v["net_income"], revenue=v["revenue"]),
        note="Net income over revenue, after everything including tax and interest.",
    ),
    RatioDefinition(
        key="ebitda_margin",
        label="EBITDA margin",
        family=RatioFamily.MARGIN,
        needs=("operating_income", "depreciation_and_amortisation", "revenue"),
        compute=lambda ctx, v: ebitda_margin(
            ctx,
            ebitda_value=ebitda(
                ctx,
                operating_income=v["operating_income"],
                depreciation=v["depreciation_and_amortisation"],
            ),
            revenue=v["revenue"],
        ),
        note="Unadjusted EBITDA over revenue. Not a proxy for cash generation.",
    ),
    RatioDefinition(
        key="return_on_equity",
        label="Return on equity",
        family=RatioFamily.RETURN,
        needs=("net_income", "equity"),
        compute=lambda ctx, v: return_on_equity(
            ctx, net_income=v["net_income"], equity=v["equity"]
        ),
        note="Net income over period-end equity. A vendor using average equity will differ.",
    ),
    RatioDefinition(
        key="return_on_assets",
        label="Return on assets",
        family=RatioFamily.RETURN,
        needs=("net_income", "assets"),
        compute=lambda ctx, v: return_on_assets(
            ctx, net_income=v["net_income"], assets=v["assets"]
        ),
        note="Net income over period-end total assets.",
    ),
    RatioDefinition(
        key="return_on_invested_capital",
        label="Return on invested capital",
        family=RatioFamily.RETURN,
        needs=(
            "operating_income",
            "income_tax_expense",
            "pre_tax_income",
            "total_debt",
            "equity",
            "cash_and_equivalents",
        ),
        compute=lambda ctx, v: return_on_invested_capital(
            ctx,
            nopat_value=nopat(
                ctx,
                operating_income=v["operating_income"],
                income_tax_expense=v["income_tax_expense"],
                pre_tax_income=v["pre_tax_income"],
            ),
            capital=invested_capital(
                ctx,
                total_debt=v["total_debt"],
                equity=v["equity"],
                cash=v["cash_and_equivalents"],
            ),
        ),
        note="NOPAT over debt plus equity less cash. The financing definition of invested "
        "capital; an operating definition reaches a similar figure differently.",
    ),
    RatioDefinition(
        key="current_ratio",
        label="Current ratio",
        family=RatioFamily.LIQUIDITY,
        needs=("current_assets", "current_liabilities"),
        compute=lambda ctx, v: current_ratio(
            ctx, current_assets=v["current_assets"], current_liabilities=v["current_liabilities"]
        ),
        note="Current assets over current liabilities.",
    ),
    RatioDefinition(
        key="quick_ratio",
        label="Quick ratio",
        family=RatioFamily.LIQUIDITY,
        needs=(
            "cash_and_equivalents",
            "short_term_investments",
            "accounts_receivable",
            "current_liabilities",
        ),
        compute=lambda ctx, v: quick_ratio(
            ctx,
            cash=v["cash_and_equivalents"],
            short_term_investments=v["short_term_investments"],
            accounts_receivable=v["accounts_receivable"],
            current_liabilities=v["current_liabilities"],
        ),
        note="The additive acid test: cash, short-term investments and receivables over "
        "current liabilities.",
    ),
    RatioDefinition(
        key="debt_to_equity",
        label="Debt to equity",
        family=RatioFamily.LEVERAGE,
        needs=("total_debt", "equity"),
        compute=lambda ctx, v: debt_to_equity(ctx, total_debt=v["total_debt"], equity=v["equity"]),
        note="Total borrowings over book equity.",
    ),
    RatioDefinition(
        key="net_debt_to_ebitda",
        label="Net debt to EBITDA",
        family=RatioFamily.LEVERAGE,
        needs=(
            "total_debt",
            "cash_and_equivalents",
            "operating_income",
            "depreciation_and_amortisation",
        ),
        compute=lambda ctx, v: net_debt_to_ebitda(
            ctx,
            net_debt_value=net_debt(
                ctx, total_debt=v["total_debt"], cash=v["cash_and_equivalents"]
            ),
            ebitda_value=ebitda(
                ctx,
                operating_income=v["operating_income"],
                depreciation=v["depreciation_and_amortisation"],
            ),
        ),
        note="Net borrowings over unadjusted EBITDA. A covenant measured on adjusted EBITDA "
        "will differ.",
    ),
    RatioDefinition(
        key="interest_cover",
        label="Interest cover",
        family=RatioFamily.COVERAGE,
        needs=("operating_income", "interest_expense"),
        compute=lambda ctx, v: interest_cover(
            ctx, operating_income=v["operating_income"], interest_expense=v["interest_expense"]
        ),
        note="Operating income over the interest charge.",
    ),
    RatioDefinition(
        key="asset_turnover",
        label="Asset turnover",
        family=RatioFamily.EFFICIENCY,
        needs=("revenue", "assets"),
        compute=lambda ctx, v: asset_turnover(ctx, revenue=v["revenue"], assets=v["assets"]),
        note="Revenue over period-end total assets.",
    ),
    RatioDefinition(
        key="days_sales_outstanding",
        label="Days sales outstanding",
        family=RatioFamily.EFFICIENCY,
        needs=("accounts_receivable", "revenue"),
        compute=lambda ctx, v: days_outstanding(
            ctx, balance=v["accounts_receivable"], flow=v["revenue"]
        ),
        note="Receivables expressed as days of revenue.",
    ),
    RatioDefinition(
        key="days_inventory_outstanding",
        label="Days inventory outstanding",
        family=RatioFamily.EFFICIENCY,
        needs=("inventory", "cost_of_revenue"),
        compute=lambda ctx, v: days_outstanding(
            ctx, balance=v["inventory"], flow=v["cost_of_revenue"]
        ),
        note="Inventory expressed as days of cost of sales.",
    ),
    RatioDefinition(
        key="days_payable_outstanding",
        label="Days payable outstanding",
        family=RatioFamily.EFFICIENCY,
        needs=("accounts_payable", "cost_of_revenue"),
        compute=lambda ctx, v: days_outstanding(
            ctx, balance=v["accounts_payable"], flow=v["cost_of_revenue"]
        ),
        note="Payables expressed as days of cost of sales.",
    ),
    RatioDefinition(
        key="cash_conversion_cycle",
        label="Cash conversion cycle",
        family=RatioFamily.EFFICIENCY,
        needs=(
            "accounts_receivable",
            "inventory",
            "accounts_payable",
            "revenue",
            "cost_of_revenue",
        ),
        compute=lambda ctx, v: cash_conversion_cycle(
            ctx,
            dso=days_outstanding(ctx, balance=v["accounts_receivable"], flow=v["revenue"]),
            dio=days_outstanding(ctx, balance=v["inventory"], flow=v["cost_of_revenue"]),
            dpo=days_outstanding(ctx, balance=v["accounts_payable"], flow=v["cost_of_revenue"]),
        ),
        note="Days sales plus days inventory less days payable. Negative means suppliers "
        "fund the working capital, which is a position rather than a problem.",
    ),
)


def compute_ratios(
    context: CalculationContext,
    statements: StatementSet,
    *,
    not_meaningful: Mapping[str, str] | None = None,
) -> tuple[RatioResult, ...]:
    """Every ratio in :data:`RATIO_DEFINITIONS`, computed or explained.

    The result has one row per definition, always. A suite that returned only what it could
    compute would make a filing with four ratios indistinguishable from a filing with four
    ratios and thirteen unanswered questions.

    Args:
        not_meaningful: Ratio keys this company's kind of business makes meaningless,
            mapped to why (gap A64). Such a ratio is **not computed**, and comes back
            absent carrying the caller's reason. The judgement is the caller's because
            this module knows arithmetic and not industries: a bank's debt to equity is
            perfectly correct division and a wrong answer to the question a reader is
            asking, and only something that knows what a bank is can say so.

    Raises:
        UnitMismatchError: If two lines a ratio needs are in different units. Not caught and
            not reported as an absent ratio — see :data:`_NEVER_SWALLOWED`.
        UnsourcedValueError: If a line reached here without provenance.
    """
    excluded = not_meaningful or {}
    return tuple(
        _result(context, definition, statements, not_meaningful=excluded.get(definition.key, ""))
        for definition in RATIO_DEFINITIONS
    )


def _result(
    context: CalculationContext,
    definition: RatioDefinition,
    statements: StatementSet,
    *,
    not_meaningful: str = "",
) -> RatioResult:
    # Ahead of the concept check, because a ratio that is meaningless here is meaningless
    # whether or not the filing happens to carry its inputs — and "this filing does not
    # report X" would be a true sentence answering a question nobody should be asking.
    if not_meaningful:
        return RatioResult(
            key=definition.key,
            label=definition.label,
            family=definition.family,
            quantity=None,
            absent_because=not_meaningful,
        )

    values: dict[str, Quantity] = {}
    missing: list[str] = []
    for concept in definition.needs:
        found = statements.get(concept)
        if found is None:
            missing.append(concept)
        else:
            values[concept] = found

    if missing:
        return RatioResult(
            key=definition.key,
            label=definition.label,
            family=definition.family,
            quantity=None,
            absent_because=(
                f"{definition.label} needs {', '.join(definition.needs)}, and this filing "
                f"does not report {', '.join(missing)}."
            ),
            missing=tuple(missing),
        )

    try:
        computed = definition.compute(context, values)
    except _NEVER_SWALLOWED:
        raise
    except CalculationError as refused:
        # The guard's own words. Rewriting them here would mean two descriptions of one
        # condition, and the one an operator reads would be the one further from the check.
        return RatioResult(
            key=definition.key,
            label=definition.label,
            family=definition.family,
            quantity=None,
            absent_because=refused.message,
        )

    return RatioResult(
        key=definition.key,
        label=definition.label,
        family=definition.family,
        quantity=computed,
    )
