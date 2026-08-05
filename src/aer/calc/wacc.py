"""The discount rate, and where every number in it came from.

A discounted cash flow is arithmetic over two things: the cash flows and the rate they are
discounted at. The rate is the shorter half and does more damage. Move a WACC from 8% to 9%
and a perpetuity's value falls by an eighth; nothing in the output looks any different, and
"we used 9%" is not a defence unless somebody can say where the 9% came from.

So this module's job is not really the arithmetic — CAPM is one multiplication and one
addition. Its job is to make sure no part of that arithmetic can be performed on a number
with no origin. Every function here is :func:`~aer.calc.engine.traced`, which refuses an
unsourced quantity outright, and **no parameter anywhere in this module has a default**. A
missing risk-free rate is a ``TypeError`` at the call site, not an 8% that nobody chose.

**Where each input comes from.**

===========================  ===========================================================
Input                        Origin
===========================  ===========================================================
Risk-free rate               A macro vintage — a *fact*, at the vintage the as-of date had
Equity risk premium          A confirmed assumption
Beta                         A confirmed assumption
Cost of debt                 Interest expense over average debt, or a confirmed assumption
Tax rate                     The effective rate from the filing, or a confirmed assumption
Equity and debt values       Facts, from the market or from the balance sheet
===========================  ===========================================================

Two of those rows say "or", and neither is expressed as a flag. A confirmed assumption
arrives carrying ``SourceKind.ASSUMPTION`` and a computed rate arrives carrying
``SourceKind.CALCULATION``, so the recorded input already says which route was taken and a
separate ``used_override=True`` would only be a second, forgeable copy of the same fact.
:func:`cost_of_debt` is therefore the *computed* route and nothing else; overriding it means
passing a confirmed assumption in its place, which the ledger distinguishes on its own.

**Per cent is not a unit, and this is where that hurts.** A ten-year Treasury yield is
published as ``4.36`` meaning 4.36%. Beta times an equity risk premium is ``0.055`` meaning
5.5%. Both are dimensionless — genuinely, not by omission — so ``Quantity`` addition is
perfectly happy to produce ``4.415``, a cost of equity of 441.5% that will not raise anything
anywhere. :func:`rate_from_percent` is the one sanctioned conversion, the registry records
which series need it
(:attr:`~aer.sources.macro.series.MacroSeries.quoted_in_percent`), and the guards below
refuse a rate outside ±100% so a conversion that was skipped stops here rather than in a
report.

**No size premium, no country premium.** Both are commonly bolted onto CAPM as extra
additive terms. This module has neither, because a reviewer confronted with
``rf + beta*erp + size + country`` has to argue with four numbers, of which two were chosen
by convention and none carries a justification of its own. Where a premium is warranted it
belongs *in* the equity risk premium assumption, whose justification field then has to say
so. One number somebody defended beats four whose sum nobody stated.

**What the refusals are, and what they are not.** Several guards below compare against a
constant — a rate above 100%, a beta beyond ±5, weights that fail to sum to one. None of
these is a default: nothing is ever substituted for a missing input. They are the point at
which a number is implausible enough that the likeliest explanation is a mistake upstream,
and stopping there is cheaper than explaining it later.

Pure and side-effect free, like everything in :mod:`aer.calc`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import CalculationError, Quantity, UnitMismatchError

__all__ = [
    "MAX_BETA",
    "MAX_QUOTED_PERCENT",
    "MAX_RATE",
    "MIN_RATE",
    "WEIGHT_TOLERANCE",
    "CapitalStructure",
    "CostOfCapital",
    "EquityBasis",
    "after_tax_cost_of_debt",
    "average_debt",
    "cost_of_capital",
    "cost_of_debt",
    "cost_of_equity",
    "debt_weight",
    "effective_tax_rate",
    "equity_weight",
    "rate_from_percent",
    "wacc",
    "wacc_all_equity",
]


class EquityBasis(StrEnum):
    """What the equity side of the capital structure was measured with.

    Recorded as a parameter on the WACC calculation rather than inferred, because the two
    give materially different answers and the difference is invisible in the result. A
    profitable company's market capitalisation is usually a large multiple of its book
    equity, so book weights understate the equity weight, overweight the cheaper debt, and
    produce a WACC that is too low — which raises every valuation computed from it.
    """

    MARKET = "market"
    """Market capitalisation. What the shares are worth, and what the theory asks for."""

    BOOK = "book"
    """Shareholders' funds from the balance sheet. The substitution, when no price exists."""


_PERCENT: Final = Decimal(100)
_TWO: Final = Decimal(2)
_ONE: Final = Decimal(1)

MAX_QUOTED_PERCENT: Final = Decimal(100)
"""The largest figure :func:`rate_from_percent` will read as a percentage.

A published yield of 150 is not a 150% yield, it is an index or a basis-point quote that
has arrived in the wrong place.
"""

MAX_RATE: Final = Decimal(1)
"""Refusal threshold for anything that claims to be a rate, as a fraction.

100% a year. The number this catches in practice is not an extreme rate; it is a
percentage that was never converted, which is out by a factor of a hundred and would
otherwise flow into a discount factor and quietly annihilate a valuation.
"""

MIN_RATE: Final = -MAX_RATE
"""Rates may be negative — Bund and JGB yields have been for years — but not unboundedly."""

MAX_BETA: Final = Decimal(5)
"""Refusal threshold for beta, in both directions.

A listed equity's beta lives between about -0.5 and 3. Beyond ±5 the likeliest explanation
is a regression run on the wrong frequency or against the wrong index, and a beta of 40
would produce a cost of equity that reads as an error to a person and as a number to code.
"""

WEIGHT_TOLERANCE: Final = Decimal("0.000000001")
"""How far the capital weights may be from summing to one.

Tight enough that weights computed against two different totals — the error this catches —
cannot pass, and loose enough that 34-digit division never trips it.
"""

_CAPM: Final = (
    "The capital asset pricing model: the only risk the market pays for is covariance with "
    "the market itself, so beta is a complete description of the risk in this equity. It is "
    "not, and everybody knows it is not; it is used because the alternatives need inputs "
    "this platform cannot source."
)

_CONSTANT_BETA: Final = "Beta is constant over the whole forecast horizon."

_ONE_PERIOD: Final = (
    "One period's figures. A year containing a settlement, a disposal or a "
    "valuation-allowance release produces a rate that describes that year and not the next ten."
)

BOOK_WEIGHT_CAVEAT: Final = (
    "Book equity was used as the equity weight because no market capitalisation was "
    "available. Book value is what the shares were issued and retained for rather than what "
    "they are worth, and for a profitable company it is usually far lower — so the equity "
    "weight is understated, the debt weight overstated, and the resulting WACC too low. "
    "Every valuation discounted at it is correspondingly too high."
)

ALL_EQUITY_NOTE: Final = (
    "The capital structure carries no debt, so the WACC is the cost of equity and no cost "
    "of debt was used. A company that borrows next year has a lower WACC than this one, "
    "which is a statement about today's balance sheet rather than about the business."
)


@dataclass(frozen=True, slots=True)
class CapitalStructure:
    """The two claims on the business, and which measure the equity side used."""

    equity_value: Quantity
    debt_value: Quantity
    basis: EquityBasis

    @property
    def has_debt(self) -> bool:
        """Whether there is a debt side to weight at all.

        Zero is a real capital structure, not a missing input: plenty of listed companies
        have no borrowings, and they do not have a cost of debt of zero — they have no cost
        of debt. :func:`cost_of_capital` routes on this rather than weighting an invented
        rate at zero.
        """
        return self.debt_value.value > 0


@dataclass(frozen=True, slots=True)
class CostOfCapital:
    """A WACC and every component that produced it.

    Each field is a quantity sourced to its own calculation record, so the discount rate can
    be taken apart in the provenance viewer without re-deriving anything.
    """

    wacc: Quantity
    cost_of_equity: Quantity

    # ``None`` for an all-equity capital structure. Not zero: a company with no borrowings
    # has no cost of debt, and a nil rate in this field would be a number nobody chose.
    cost_of_debt_pre_tax: Quantity | None
    cost_of_debt_after_tax: Quantity | None

    equity_weight: Quantity
    debt_weight: Quantity
    basis: EquityBasis

    # What a reader has to be told about this particular rate, as distinct from the standing
    # assumptions recorded on each calculation. Empty when nothing was substituted.
    caveats: tuple[str, ...] = ()

    @property
    def value(self) -> Decimal:
        return self.wacc.value


# -- Conversions -----------------------------------------------------------------------------


@traced(
    name="rate_from_percent",
    formula="rate = quoted per cent / 100",
    assumptions=(
        "The quoted figure is a percentage as published, so 4.36 means 4.36% and not 436%.",
    ),
)
def rate_from_percent(_context: CalculationContext, *, quoted: Quantity) -> Quantity:
    """A published percentage as the fraction the arithmetic needs.

    The **only** sanctioned place this division happens. Doing it inline at a call site is
    how one of two callers ends up not doing it, and the resulting cost of equity is out by
    a factor of a hundred while remaining a perfectly ordinary-looking decimal.

    Raises:
        UnitMismatchError: If the quoted figure carries a dimension. A percentage of
            something is not a rate.
        CalculationError: If the figure is beyond :data:`MAX_QUOTED_PERCENT`, which means it
            was not a percentage.
    """
    _require_dimensionless(quoted, name="quoted")

    if abs(quoted.value) > MAX_QUOTED_PERCENT:
        message = (
            f"{quoted.value} is not a rate quoted in per cent — it is beyond "
            f"{MAX_QUOTED_PERCENT}%. Either this is an index rather than a rate, or it is "
            "quoted in basis points, or it has already been converted once."
        )
        raise CalculationError(message, context={"quoted": str(quoted.value)})

    return quoted / Quantity.of(_PERCENT)


# -- The cost of equity ----------------------------------------------------------------------


@traced(
    name="cost_of_equity",
    formula="Ke = risk-free rate + beta * equity risk premium",
    assumptions=(
        _CAPM,
        _CONSTANT_BETA,
        "No size or country premium is added. Where one is warranted it belongs inside the "
        "equity risk premium assumption, carrying its own justification.",
        "The risk-free rate is a long-dated government yield, matching an equity's holding "
        "period rather than a money-market one.",
    ),
)
def cost_of_equity(
    _context: CalculationContext,
    *,
    risk_free: Quantity,
    beta: Quantity,
    equity_risk_premium: Quantity,
) -> Quantity:
    """What the equity holders require, under CAPM.

    Raises:
        UnitMismatchError: If any of the three carries a dimension.
        CalculationError: If the risk-free rate or the premium is outside ±100%, if the
            premium is negative, or if beta is beyond :data:`MAX_BETA`.
    """
    _require_rate(risk_free, name="risk_free", floor=MIN_RATE)
    _require_rate(equity_risk_premium, name="equity_risk_premium", floor=Decimal(0))
    _require_beta(beta)

    return risk_free + beta * equity_risk_premium


# -- The cost of debt ------------------------------------------------------------------------


@traced(
    name="average_debt",
    formula="average debt = (opening debt + closing debt) / 2",
    assumptions=(
        "Debt moved smoothly between the two balance-sheet dates. An issuance or repayment "
        "part-way through the year makes this wrong in the direction of whichever end it "
        "happened nearer.",
    ),
)
def average_debt(_context: CalculationContext, *, opening: Quantity, closing: Quantity) -> Quantity:
    """The debt the year's interest was charged on.

    Closing debt alone understates the cost of debt for a company that borrowed during the
    year — a full year of interest divided by a balance that existed for a month.

    Raises:
        UnitMismatchError: If the two balances are in different currencies.
        CalculationError: If either balance is negative.
    """
    for name, balance in (("opening", opening), ("closing", closing)):
        if balance.value < 0:
            message = (
                f"The {name} debt balance is {balance.value}. A negative borrowing is a sign "
                "error in the statement mapping, not a debt to average."
            )
            raise CalculationError(message, context={"balance": name, "value": str(balance.value)})

    return (opening + closing) / Quantity.of(_TWO)


@traced(
    name="cost_of_debt",
    formula="Kd = interest expense / average debt",
    assumptions=(
        "The interest the company was actually charged approximates what it would pay to "
        "borrow now. For debt raised in a different rate environment this is the historical "
        "cost rather than the marginal one, which is what the confirmed-assumption route "
        "exists for.",
        "Interest expense is gross of any capitalised interest.",
    ),
)
def cost_of_debt(
    _context: CalculationContext, *, interest_expense: Quantity, debt: Quantity
) -> Quantity:
    """The rate the company's borrowings actually cost it, before tax.

    Raises:
        UnitMismatchError: If the interest and the debt are in different currencies. That
            division produces a rate in ``USD/GBP``, which is a currency-pair unit and not a
            rate at all — the unit system catches this one on its own, and the message here
            says what it means.
        CalculationError: If there is no debt, if the interest is negative, or if the
            resulting rate exceeds :data:`MAX_RATE`.
    """
    if interest_expense.unit != debt.unit:
        message = (
            f"Interest expense in {interest_expense.unit.symbol} over debt in "
            f"{debt.unit.symbol} is not a rate. Both must be the same currency; converting "
            "one needs a sourced rate, not a division."
        )
        raise UnitMismatchError(
            message,
            context={"interest": interest_expense.unit.symbol, "debt": debt.unit.symbol},
        )

    if debt.value <= 0:
        message = (
            f"Average debt is {debt.value}, so there is no cost of debt to compute. A "
            "company with no borrowings has an all-equity WACC; it does not have a cost of "
            "debt of zero, and treating it as though it did would give the debt term a "
            "weight it has not earned."
        )
        raise CalculationError(message, context={"debt": str(debt.value)})

    if interest_expense.value < 0:
        message = (
            f"Interest expense is {interest_expense.value}. Net interest income is not a "
            "negative cost of debt — it means interest receivable has been netted off, and "
            "the gross charge is the figure this needs."
        )
        raise CalculationError(message, context={"interest": str(interest_expense.value)})

    rate = interest_expense / debt
    _require_rate(rate, name="cost_of_debt", floor=Decimal(0))
    return rate


@traced(
    name="after_tax_cost_of_debt",
    formula="Kd after tax = Kd * (1 - tax rate)",
    assumptions=(
        "Interest is fully deductible against taxable profit. It is not where an interest "
        "restriction bites — the UK's corporate interest restriction and the US section "
        "163(j) limit both cap the deduction — so a heavily levered borrower's shield is "
        "smaller than this makes it.",
        "The company is profitable enough to use the deduction in the year it arises. A "
        "loss-making company carries it forward, and a shield deferred is worth less than "
        "one taken.",
    ),
)
def after_tax_cost_of_debt(
    _context: CalculationContext, *, cost_of_debt: Quantity, tax_rate: Quantity
) -> Quantity:
    """The cost of debt net of the interest tax shield.

    Raises:
        UnitMismatchError: If either argument carries a dimension.
        CalculationError: If the cost of debt or the tax rate is outside 0-100%.
    """
    _require_rate(cost_of_debt, name="cost_of_debt", floor=Decimal(0))
    _require_rate(tax_rate, name="tax_rate", floor=Decimal(0))

    return cost_of_debt * (Quantity.of(_ONE) - tax_rate)


# -- Tax -------------------------------------------------------------------------------------


@traced(
    name="effective_tax_rate",
    formula="effective tax rate = income tax expense / pre-tax income",
    assumptions=(
        "The rate the filer actually bore, which differs from the statutory rate wherever "
        "profits are earned in more than one jurisdiction, or where prior-year adjustments, "
        "deferred tax movements or unrecognised losses run through the charge.",
        _ONE_PERIOD,
    ),
)
def effective_tax_rate(
    _context: CalculationContext, *, income_tax_expense: Quantity, pre_tax_income: Quantity
) -> Quantity:
    """The rate this year's profit was actually taxed at.

    Deliberately the same arithmetic :func:`~aer.calc.ratios.nopat` applies inline, so a
    reader who checks one against the other finds them agreeing. A test asserts it.

    Raises:
        UnitMismatchError: If the two are in different currencies.
        CalculationError: If pre-tax income is not positive, or if the resulting rate falls
            outside 0-100%. Both are real situations and neither yields a usable rate: a
            credit against a loss, or a charge exceeding the profit, says what happened in
            one year rather than what a profitable operation pays. A confirmed statutory-rate
            assumption is the route for those.
    """
    if income_tax_expense.unit != pre_tax_income.unit:
        message = (
            f"A tax charge in {income_tax_expense.unit.symbol} over pre-tax income in "
            f"{pre_tax_income.unit.symbol} is not a rate. Both must be the same currency."
        )
        raise UnitMismatchError(
            message,
            context={
                "tax": income_tax_expense.unit.symbol,
                "pre_tax": pre_tax_income.unit.symbol,
            },
        )

    if pre_tax_income.value <= 0:
        message = (
            f"Pre-tax income is {pre_tax_income.value}, so there is no effective tax rate. A "
            "loss-making year's tax charge says nothing about the rate a profitable "
            "operation would pay; confirm a statutory rate as an assumption instead."
        )
        raise CalculationError(message, context={"pre_tax_income": str(pre_tax_income.value)})

    rate = income_tax_expense / pre_tax_income
    if not (Decimal(0) <= rate.value <= MAX_RATE):
        message = (
            f"The effective tax rate computes to {rate.value}, which is outside 0-100%. That "
            "happens for real — a credit, a valuation-allowance release, a charge larger than "
            "the profit — and none of those is a rate to discount at. Confirm a statutory "
            "rate as an assumption instead."
        )
        raise CalculationError(
            message,
            context={
                "rate": str(rate.value),
                "tax": str(income_tax_expense.value),
                "pre_tax_income": str(pre_tax_income.value),
            },
        )
    return rate


# -- Capital structure -----------------------------------------------------------------------


@traced(
    name="equity_weight",
    formula="E / (D + E) = equity value / (equity value + debt value)",
    assumptions=(
        "Ordinary equity and debt are the only claims weighted. Preference shares, "
        "convertibles and minority interests are not given a weight of their own, so a "
        "company with material amounts of any of them has a WACC this does not describe.",
    ),
)
def equity_weight(
    _context: CalculationContext, *, equity_value: Quantity, debt_value: Quantity
) -> Quantity:
    """The share of the capital base the equity holders provide."""
    return _weight(equity_value, equity_value=equity_value, debt_value=debt_value)


@traced(
    name="debt_weight",
    formula="D / (D + E) = debt value / (equity value + debt value)",
    assumptions=(
        "Debt is gross rather than net of cash. Netting cash off here and *also* deducting "
        "it in the equity bridge would count the same cash twice.",
    ),
)
def debt_weight(
    _context: CalculationContext, *, equity_value: Quantity, debt_value: Quantity
) -> Quantity:
    """The share of the capital base the lenders provide."""
    return _weight(debt_value, equity_value=equity_value, debt_value=debt_value)


# -- The weighted average --------------------------------------------------------------------


@traced(
    name="wacc",
    formula="WACC = Ke * E/(D+E) + Kd_after_tax * D/(D+E)",
    assumptions=(
        "The capital structure is constant over the forecast horizon: one rate discounts "
        "every year. A company deleveraging materially has a rising equity weight and "
        "therefore a rising WACC, which a single rate cannot express.",
        "The costs of equity and debt do not change as the weights change. Modigliani-Miller "
        "says they must — more leverage raises both — so this understates the cost of a "
        "capital structure materially different from today's.",
    ),
)
def wacc(
    _context: CalculationContext,
    *,
    cost_of_equity: Quantity,
    after_tax_cost_of_debt: Quantity,
    equity_weight: Quantity,
    debt_weight: Quantity,
    equity_basis: EquityBasis,
) -> Quantity:
    """The blended rate the whole capital base requires.

    ``equity_basis`` is not used in the arithmetic. It is here so that it is recorded as a
    parameter on this calculation, which is what the plan means by the book-value
    substitution being *stated on the calculation rather than hidden*: a reader who pulls up
    the WACC record sees which measure produced the weights without having to trace back
    through them.

    Raises:
        UnitMismatchError: If any of the four arguments carries a dimension.
        CalculationError: If a cost or weight is out of range, if the weights do not sum to
            one within :data:`WEIGHT_TOLERANCE`, or if ``equity_basis`` is not an
            :class:`EquityBasis`.
    """
    _require_equity_basis(equity_basis)
    _require_rate(cost_of_equity, name="cost_of_equity", floor=MIN_RATE)
    _require_rate(after_tax_cost_of_debt, name="after_tax_cost_of_debt", floor=Decimal(0))
    _require_weight(equity_weight, name="equity_weight")
    _require_weight(debt_weight, name="debt_weight")

    # Two weights computed against different totals each look entirely reasonable on their
    # own, and produce a rate that is not an average of anything.
    total = equity_weight.value + debt_weight.value
    if abs(total - _ONE) > WEIGHT_TOLERANCE:
        message = (
            f"The capital weights sum to {total}, not 1. They were computed against different "
            "totals, so the result would not be a weighted average of the two costs."
        )
        raise CalculationError(
            message,
            context={
                "equity_weight": str(equity_weight.value),
                "debt_weight": str(debt_weight.value),
                "total": str(total),
            },
        )

    return cost_of_equity * equity_weight + after_tax_cost_of_debt * debt_weight


@traced(
    name="wacc_all_equity",
    formula="WACC = Ke; the capital structure is all equity, so there is no debt term",
    assumptions=(
        "The company will not borrow over the forecast horizon. A first drawdown lowers the "
        "WACC through the tax shield, so this rate is a statement about the balance sheet as "
        "it stands rather than about the business's cost of capital in general.",
    ),
)
def wacc_all_equity(
    _context: CalculationContext,
    *,
    cost_of_equity: Quantity,
    equity_weight: Quantity,
    equity_basis: EquityBasis,
) -> Quantity:
    """The WACC of a company with no borrowings.

    Separate from :func:`wacc` rather than the same function with a nil debt term, because
    the two differ in what they demand of the caller. Weighting a cost of debt at zero
    requires a cost of debt, and a company with no debt has none — so the caller would have
    to supply a number nobody chose, in a module whose whole claim is that no such number
    exists in it.

    Raises:
        UnitMismatchError: If a rate or the weight carries a dimension.
        CalculationError: If the equity weight is not one, which means there *is* a debt side
            and :func:`wacc` is the function for it.
    """
    _require_equity_basis(equity_basis)
    _require_rate(cost_of_equity, name="cost_of_equity", floor=MIN_RATE)
    _require_weight(equity_weight, name="equity_weight")

    if abs(equity_weight.value - _ONE) > WEIGHT_TOLERANCE:
        message = (
            f"The equity weight is {equity_weight.value}, not 1, so this capital structure "
            "has a debt side. Use wacc() with a cost of debt rather than dropping the term."
        )
        raise CalculationError(message, context={"equity_weight": str(equity_weight.value)})

    return cost_of_equity * equity_weight


def cost_of_capital(
    context: CalculationContext,
    *,
    risk_free: Quantity,
    beta: Quantity,
    equity_risk_premium: Quantity,
    cost_of_debt_pre_tax: Quantity | None,
    tax_rate: Quantity | None,
    structure: CapitalStructure,
) -> CostOfCapital:
    """The whole discount rate, with every intermediate step recorded.

    Not traced itself — it returns a structure rather than a quantity — but every step it
    takes is, so ``context.records`` afterwards holds the cost of equity, the after-tax cost
    of debt, both weights and the WACC, each with its own inputs and formula.

    ``cost_of_debt_pre_tax`` and ``tax_rate`` are taken already resolved. Either may be the
    output of :func:`cost_of_debt` / :func:`effective_tax_rate`, or a confirmed assumption
    from :func:`aer.services.assumptions.as_quantity`; the ledger tells the two apart by the
    source kind on the recorded input, which is why there is no override flag.

    Both are ``Quantity | None`` and **neither has a default**: an all-equity company needs
    ``None`` typed at the call site, so "there is no cost of debt" is something the caller
    stated rather than something that happened when an argument was forgotten.

    Raises:
        CalculationError: If the capital structure and the debt arguments contradict each
            other in either direction.
    """
    if structure.has_debt and (cost_of_debt_pre_tax is None or tax_rate is None):
        missing = [
            name
            for name, value in (
                ("cost_of_debt_pre_tax", cost_of_debt_pre_tax),
                ("tax_rate", tax_rate),
            )
            if value is None
        ]
        message = (
            f"The capital structure carries debt of {structure.debt_value}, so "
            f"{' and '.join(missing)} must be supplied. There is no rate to fall back on: a "
            "cost of debt this platform invented would be weighted into the discount rate "
            "and would be indistinguishable in the output from one somebody sourced."
        )
        raise CalculationError(message, context={"missing": ",".join(missing)})

    if not structure.has_debt and (cost_of_debt_pre_tax is not None or tax_rate is not None):
        message = (
            "The capital structure carries no debt, but a cost of debt or a tax rate was "
            "supplied for it. One of the two is wrong, and guessing which would either "
            "discard a sourced rate or weight it against a balance sheet that does not "
            "support it."
        )
        raise CalculationError(message, context={"debt_value": str(structure.debt_value.value)})

    equity = equity_weight(
        context, equity_value=structure.equity_value, debt_value=structure.debt_value
    )
    debt = debt_weight(
        context, equity_value=structure.equity_value, debt_value=structure.debt_value
    )
    equity_cost = cost_of_equity(
        context, risk_free=risk_free, beta=beta, equity_risk_premium=equity_risk_premium
    )

    caveats: tuple[str, ...] = (BOOK_WEIGHT_CAVEAT,) if structure.basis is EquityBasis.BOOK else ()

    if cost_of_debt_pre_tax is None or tax_rate is None:
        return CostOfCapital(
            wacc=wacc_all_equity(
                context,
                cost_of_equity=equity_cost,
                equity_weight=equity,
                equity_basis=structure.basis,
            ),
            cost_of_equity=equity_cost,
            cost_of_debt_pre_tax=None,
            cost_of_debt_after_tax=None,
            equity_weight=equity,
            debt_weight=debt,
            basis=structure.basis,
            caveats=(*caveats, ALL_EQUITY_NOTE),
        )

    debt_cost_after_tax = after_tax_cost_of_debt(
        context, cost_of_debt=cost_of_debt_pre_tax, tax_rate=tax_rate
    )
    rate = wacc(
        context,
        cost_of_equity=equity_cost,
        after_tax_cost_of_debt=debt_cost_after_tax,
        equity_weight=equity,
        debt_weight=debt,
        equity_basis=structure.basis,
    )

    return CostOfCapital(
        wacc=rate,
        cost_of_equity=equity_cost,
        cost_of_debt_pre_tax=cost_of_debt_pre_tax,
        cost_of_debt_after_tax=debt_cost_after_tax,
        equity_weight=equity,
        debt_weight=debt,
        basis=structure.basis,
        caveats=caveats,
    )


# -- Guards ----------------------------------------------------------------------------------
#
# None of these substitutes a value. Every one of them stops.


def _weight(component: Quantity, *, equity_value: Quantity, debt_value: Quantity) -> Quantity:
    """One side's share of the capital base, refusing a structure that has no shares.

    Raises:
        UnitMismatchError: If either value is not a currency amount, or if the two are in
            different currencies.
        CalculationError: If equity is not positive or debt is negative.
    """
    for name, value in (("equity_value", equity_value), ("debt_value", debt_value)):
        if not value.unit.currencies:
            message = (
                f"{name} is in {value.unit.symbol}, which is not a currency amount. Capital "
                "weights are computed from values, not from weights supplied ready-made — "
                "otherwise the basis they were measured on never reaches the record."
            )
            raise UnitMismatchError(message, context={"input": name, "unit": value.unit.symbol})

    if equity_value.value <= 0:
        message = (
            f"The equity value is {equity_value.value}. A negative or nil equity weight makes "
            "the debt weight exceed one, and the WACC then lies outside the two costs it is "
            "meant to average — which is not a high discount rate, it is a meaningless one. "
            "Negative book equity is common and real; it is a reason to use market "
            "capitalisation, not a reason to weight with it."
        )
        raise CalculationError(message, context={"equity_value": str(equity_value.value)})

    if debt_value.value < 0:
        message = (
            f"The debt value is {debt_value.value}. Net debt goes negative for a cash-rich "
            "company, but capital weights take gross debt: the cash is deducted once, in the "
            "bridge from enterprise value to equity value."
        )
        raise CalculationError(message, context={"debt_value": str(debt_value.value)})

    # Raises on its own if the two are in different currencies, which is what should happen.
    return component / (equity_value + debt_value)


def _require_dimensionless(value: Quantity, *, name: str) -> None:
    if value.unit.is_dimensionless:
        return
    message = (
        f"{name} is in {value.unit.symbol}. A rate is a pure number — a cost of capital in "
        "dollars is a category error, not a large rate."
    )
    raise UnitMismatchError(message, context={"input": name, "unit": value.unit.symbol})


def _require_rate(value: Quantity, *, name: str, floor: Decimal) -> None:
    """Refuse a rate that is out of range, naming the percentage trap as the likely cause."""
    _require_dimensionless(value, name=name)

    if floor <= value.value <= MAX_RATE:
        return

    hint = ""
    if value.value > MAX_RATE:
        hint = (
            " A figure this size is usually a percentage that was never divided by 100 — see "
            "rate_from_percent."
        )
    message = (
        f"{name} is {value.value}, outside the range {floor} to {MAX_RATE} that a rate "
        f"expressed as a fraction can take.{hint}"
    )
    raise CalculationError(
        message, context={"input": name, "value": str(value.value), "floor": str(floor)}
    )


def _require_beta(value: Quantity) -> None:
    """Refuse an implausible beta. Negative betas are real; ±5 ones are regressions gone wrong."""
    _require_dimensionless(value, name="beta")

    if abs(value.value) > MAX_BETA:
        message = (
            f"Beta is {value.value}, beyond ±{MAX_BETA}. A listed equity's beta lives between "
            "roughly -0.5 and 3; a figure this far outside is a regression run at the wrong "
            "frequency or against the wrong index."
        )
        raise CalculationError(message, context={"beta": str(value.value)})


def _require_weight(value: Quantity, *, name: str) -> None:
    _require_dimensionless(value, name=name)

    if not (Decimal(0) <= value.value <= _ONE):
        message = (
            f"{name} is {value.value}, which is not a share of anything. Capital weights lie "
            "between 0 and 1."
        )
        raise CalculationError(message, context={"input": name, "value": str(value.value)})


def _require_equity_basis(value: object) -> None:
    """Refuse anything but an :class:`EquityBasis`.

    Takes ``object`` so mypy cannot narrow the check away, for the same reason as
    :func:`aer.calc.units._require_exact_decimal`: the annotation is a promise to typed
    callers, and this is what catches it being broken by an untyped one. A free-text basis
    would be recorded verbatim on the calculation and would then mean whatever the caller
    happened to type.
    """
    if isinstance(value, EquityBasis):
        return
    message = (
        f"equity_basis is {value!r}, which is not an EquityBasis. The measure behind the "
        "equity weight is recorded on the calculation, and a value nothing constrains is a "
        "record nobody can rely on."
    )
    raise CalculationError(message, context={"equity_basis": repr(value)})
