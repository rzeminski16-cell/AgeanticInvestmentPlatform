"""Residual income: what a bank's equity is worth beyond the book value it already has.

**Why this model and not a discounted cash flow.** For a bank, deposits and borrowings are
raw material rather than financing, so enterprise value means nothing and free cash flow to
the firm cannot be separated from the business of lending. `aer.core.sectors` blocks
``DCF_FCFF`` for depositories for exactly that reason. What a bank does have is a book value
that is close to economically real — assets marked at amortised cost or fair value, funded
by liabilities of known amount — and a return on that book that can be forecast from
observable drivers.

Residual income takes the book value as given and values only the part a reader should
argue about: the excess of what the equity earns over what the equity costs.

    equity value = opening book value + Σ PV(residual income) + PV(terminal residual income)

    residual income_t = (ROE_t - cost of equity) * book value_(t-1)

**The identity that makes this exact.** Under clean surplus — every gain and loss passes
through profit, so book value moves only by earnings less dividends — the residual-income
value and the dividend-discount value are the same number. They differ only in where the
uncertainty sits: a dividend discount puts almost everything into a terminal value nobody
can observe, while residual income anchors on a balance sheet the filer published and
values the *spread*. For a bank, where book value is the most reliable figure in the
accounts, that is the better place to put the weight.

**Clean surplus is an assumption, and for a bank a consequential one.** Available-for-sale
securities move through other comprehensive income, and a bank whose bond book is
underwater has a book value this model treats as fully earning. Every result carries the
caveat rather than burying it.

**The discount rate is the cost of equity, not WACC.** This values the equity directly, so
discounting at a blended rate that includes the cost of deposits would charge the funding
twice — once in net interest income, once in the discount rate.

**A mandate is required, and that is the sector block.**
:func:`residual_income_value` takes a :class:`~aer.core.sectors.ValuationMandate`, on the
same terms :mod:`aer.calc.dcf` takes one and for the same reason (`docs/adr/0029`): there is
no route through this module that runs a valuation nobody was permitted to run. Permitting
a model is not the same as exempting it from the gate, and the gate is what makes the
permission mean something.

Pure and ``mypy --strict``: quantities in, quantities out, every step recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from aer.calc.dcf import MAX_AXIS_POINTS, MIN_AXIS_POINTS, present_value
from aer.calc.engine import CalculationContext, traced
from aer.calc.units import CalculationError, Quantity, Unit
from aer.calc.wacc import MAX_RATE, MIN_RATE
from aer.core.sectors import ModelNotPermittedError, ValuationMandate, ValuationModel

__all__ = [
    "CLEAN_SURPLUS_CAVEAT",
    "DRIVER_NAMES",
    "MAX_FORECAST_YEARS",
    "SENSITIVITY_CASE",
    "VARIABLE_FIELDS",
    "GridAxis",
    "GridCell",
    "GridMeasure",
    "ResidualIncomeInputs",
    "ResidualIncomeResult",
    "ResidualIncomeYear",
    "SensitivityGrid",
    "TerminalTreatment",
    "book_value_roll_forward",
    "equity_charge",
    "equity_discount_factor",
    "equity_value",
    "explicit_residual_value",
    "net_income_from_roe",
    "perpetual_residual_value",
    "premium_to_book",
    "residual_income",
    "residual_income_value",
    "sensitivity_grid",
    "value_per_share",
]

_ONE: Final = Decimal(1)
_SHARES: Final = Unit.base("shares")

DRIVER_NAMES: Final[tuple[str, ...]] = ("return_on_equity", "payout_ratio")
"""The two drivers a residual-income forecast needs, as assumption names.

Named here rather than in the service layer because the *model* decides what it needs, for
the reason :data:`aer.calc.dcf.DRIVER_NAMES` gives. Two rather than five, and the omissions
are the point: this model forecasts a return on book value rather than a profit and loss, so
there is no revenue path and no margin to argue about — which is most of why it suits a
bank, whose revenue line is an accounting choice about how to present interest.
"""

# The same ceiling the discounted cash flow applies, for the same reason: beyond about ten
# years an explicit forecast is a terminal value written out longhand.
MAX_FORECAST_YEARS: Final = 15

CLEAN_SURPLUS_CAVEAT: Final = (
    "Residual income equals the dividend-discount value only under clean surplus — that "
    "every gain and loss passes through profit, so book value moves only by earnings less "
    "dividends. A bank's available-for-sale securities move through other comprehensive "
    "income instead, so a book value carrying unrealised losses is treated here as fully "
    "earning."
)

_SPREAD_ASSUMPTION: Final = (
    "The forecast return on equity is sustainable at the forecast leverage. A return raised "
    "by thinner capital is not the same claim as one raised by better lending, and this "
    "model cannot tell them apart."
)

_TERMINAL_ASSUMPTION: Final = (
    "Everything beyond the explicit forecast rests on one unobservable parameter. For a "
    "residual-income model that parameter decides whether the bank keeps earning above its "
    "cost of equity for ever, which is a strong claim about competition."
)


class TerminalTreatment(StrEnum):
    """What happens to the excess return after the explicit forecast.

    Stated as a choice with no default, because the two say opposite things about the
    business and the difference is most of the answer.
    """

    FADE_TO_NOTHING = "fade_to_nothing"
    """Competition removes the excess return at the end of the forecast: no terminal value.

    The conservative reading, and the one banking history mostly supports — an excess return
    on regulated, commoditised lending is competed away. It values the bank at book value
    plus the excess it earns while the forecast runs, and nothing for what comes after.
    """

    PERPETUAL_GROWTH = "perpetual_growth"
    """The final year's residual income grows at a constant rate for ever.

    A strong claim: a bank earning above its cost of equity in perpetuity is one whose
    advantage no competitor erodes. Available because it is the standard treatment and
    because refusing it would push an operator into doing the arithmetic elsewhere, where
    nothing records the assumption.
    """


@dataclass(frozen=True, slots=True)
class DriverPath:
    """One driver's value in each forecast year.

    Mirrors :class:`aer.calc.dcf.DriverPath` deliberately — a forecast whose return on
    equity never changes is a forecast nobody argued about, and even a flat path is recorded
    year by year so the ledger says what year four used.
    """

    name: str
    values: tuple[Quantity, ...]

    def __post_init__(self) -> None:
        if not self.values:
            message = (
                f"The driver {self.name!r} has no values. A forecast needs one for every year "
                "it projects."
            )
            raise CalculationError(message, context={"driver": self.name})

    @classmethod
    def flat(cls, name: str, value: Quantity, *, years: int) -> DriverPath:
        if years < 1:
            message = f"A forecast of {years} years is not a forecast."
            raise CalculationError(message, context={"driver": name, "years": str(years)})
        return cls(name=name, values=(value,) * years)

    @property
    def years(self) -> int:
        return len(self.values)

    @property
    def flat_value(self) -> Quantity | None:
        """The one value this path repeats, or ``None`` when it moves year to year.

        What decides whether a sensitivity axis may vary this driver (ADR 0101). A flat path
        *is* one number with an ordering, so an axis over it means what its label says; a
        fading path is several, and an axis labelled with one of them would be a label for
        something else — which is the objection :data:`aer.calc.dcf.VARIABLE_FIELDS` raises
        against driver axes in general, and it only holds against the fading case.

        Compared on value and unit rather than on the whole quantity: two years of a flat
        path carry the same source, but a caller that built one by hand may not have, and
        the source is not what makes a path flat.
        """
        first = self.values[0]
        if any(value.value != first.value or value.unit != first.unit for value in self.values):
            return None
        return first

    def at(self, year: int) -> Quantity:
        return self.values[year - 1]


@dataclass(frozen=True, slots=True)
class ResidualIncomeInputs:
    """Everything one residual-income valuation needs, and nothing it can do without.

    **No field has a default.** The terminal treatment in particular must be chosen: the
    two readings differ by most of the answer, and a default would make the more optimistic
    one the silent case.
    """

    opening_book_value: Quantity
    return_on_equity: DriverPath
    payout_ratio: DriverPath
    cost_of_equity: Quantity
    terminal_treatment: TerminalTreatment
    terminal_growth: Quantity
    shares_outstanding: Quantity

    @property
    def years(self) -> int:
        return self.return_on_equity.years

    def validate(self) -> None:
        """Refuse an input set that cannot produce an answer worth reading.

        Raises:
            CalculationError: If the drivers disagree about how many years there are, or the
                forecast is empty or longer than :data:`MAX_FORECAST_YEARS`.
        """
        lengths = {
            driver.name: driver.years for driver in (self.return_on_equity, self.payout_ratio)
        }
        if len(set(lengths.values())) != 1:
            message = (
                "The drivers cover different numbers of years: "
                f"{', '.join(f'{name} {years}' for name, years in sorted(lengths.items()))}. "
                "A forecast needs every driver for every year it projects."
            )
            raise CalculationError(message, context=dict.fromkeys(lengths, ""))

        if not 1 <= self.years <= MAX_FORECAST_YEARS:
            message = (
                f"A {self.years}-year explicit forecast is outside the 1 to "
                f"{MAX_FORECAST_YEARS} range this model builds."
            )
            raise CalculationError(message, context={"years": str(self.years)})


@dataclass(frozen=True, slots=True)
class ResidualIncomeYear:
    """One forecast year, every figure traced to the drivers behind it."""

    year: int
    opening_book_value: Quantity
    net_income: Quantity
    equity_charge: Quantity
    residual_income: Quantity
    discount_factor: Quantity
    present_value: Quantity
    closing_book_value: Quantity


@dataclass(frozen=True, slots=True)
class ResidualIncomeResult:
    """A finished valuation: the book value, the excess, and what it comes to per share.

    ``terminal_value`` and ``terminal_present_value`` are ``None`` under
    :attr:`TerminalTreatment.FADE_TO_NOTHING` rather than nil. The two are not the same
    statement — a nil terminal value is an arithmetic result, and no terminal value is a
    refusal to make the claim — and a reader who sees 0.00 in a valuation table is entitled
    to ask which formula produced it.
    """

    years: tuple[ResidualIncomeYear, ...]
    opening_book_value: Quantity
    explicit_present_value: Quantity
    terminal_value: Quantity | None
    terminal_present_value: Quantity | None
    equity_value: Quantity
    premium_to_book: Quantity
    value_per_share: Quantity
    caveats: tuple[str, ...]


# -- The steps -------------------------------------------------------------------------------


@traced(
    name="net_income_from_roe",
    formula="NI_t = ROE_t * opening book value_t",
    assumptions=(_SPREAD_ASSUMPTION,),
)
def net_income_from_roe(
    _context: CalculationContext, *, opening_book_value: Quantity, return_on_equity: Quantity
) -> Quantity:
    """What the equity earns in one year, at the forecast return."""
    _require_money(opening_book_value, name="opening_book_value")
    _require_dimensionless(return_on_equity, name="return_on_equity")
    return opening_book_value * return_on_equity


@traced(
    name="equity_charge",
    formula="charge_t = cost of equity * opening book value_t",
    assumptions=(
        "The cost of equity is constant across the forecast, so the bank's risk is assumed "
        "not to change as its balance sheet does.",
    ),
)
def equity_charge(
    _context: CalculationContext, *, opening_book_value: Quantity, cost_of_equity: Quantity
) -> Quantity:
    """What the equity costs in one year — the hurdle the earnings must clear."""
    _require_money(opening_book_value, name="opening_book_value")
    _require_dimensionless(cost_of_equity, name="cost_of_equity")
    return opening_book_value * cost_of_equity


@traced(
    name="residual_income",
    formula="RI_t = net income_t - equity charge_t",
    assumptions=(
        "A negative residual income is a real answer, not a failure: a bank earning below "
        "its cost of equity is worth less than its book value, and this model says so "
        "rather than flooring at zero.",
    ),
)
def residual_income(
    _context: CalculationContext, *, net_income: Quantity, charge: Quantity
) -> Quantity:
    """What the year earned over and above what the equity cost."""
    _require_money(net_income, name="net_income")
    _require_money(charge, name="charge")
    return net_income - charge


@traced(
    name="closing_book_value",
    formula="BV_t = BV_(t-1) + net income_t * (1 - payout_t)",
    assumptions=(
        CLEAN_SURPLUS_CAVEAT,
        "Retained earnings are the only thing that moves book value: no issuance, no "
        "buy-back, no revaluation. A bank raising capital mid-forecast breaks this roll.",
    ),
)
def book_value_roll_forward(
    _context: CalculationContext,
    *,
    opening_book_value: Quantity,
    net_income: Quantity,
    payout_ratio: Quantity,
) -> Quantity:
    """Next year's book value, from this year's retained earnings.

    Raises:
        CalculationError: If the payout ratio is outside nil to one. A negative payout is a
            capital raise wearing a dividend's name, and a payout above one is a
            distribution out of capital — both are real events and neither is what this
            parameter means.
    """
    _require_money(opening_book_value, name="opening_book_value")
    _require_money(net_income, name="net_income")
    _require_dimensionless(payout_ratio, name="payout_ratio")

    if not 0 <= payout_ratio.value <= _ONE:
        message = (
            f"A payout ratio of {payout_ratio.value} is outside nil to one. Below nil is a "
            "capital raise and above one is a distribution out of capital; both are real "
            "events, and neither is what this parameter means."
        )
        raise CalculationError(message, context={"payout_ratio": str(payout_ratio.value)})

    retained = net_income * (Quantity.of(_ONE) - payout_ratio)
    return opening_book_value + retained


@traced(
    name="equity_discount_factor",
    formula="DF_t = 1 / (1 + cost of equity)^t",
    assumptions=(
        "Cash arrives at the end of each year. Mid-year arrival would raise every present "
        "value by roughly half a year's discounting.",
    ),
)
def equity_discount_factor(
    _context: CalculationContext, *, cost_of_equity: Quantity, year: int
) -> Quantity:
    """What a pound of residual income in year ``t`` is worth today.

    :func:`aer.calc.dcf.discount_factor` does the same arithmetic, and is deliberately not
    reused: it records its rate under the name ``wacc``, and a ledger that calls a cost of
    equity a weighted average cost of capital is a ledger a reader cannot check. The number
    would have been right and the audit trail wrong, which is the failure this platform is
    built to make impossible.

    Raises:
        CalculationError: If the year is not a positive whole number, or the rate is outside
            the range a rate expressed as a fraction can take.
    """
    _require_rate(cost_of_equity, name="cost_of_equity")
    _require_year(year)

    return (Quantity.of(_ONE) + cost_of_equity).power(-year)


@traced(
    name="perpetual_residual_value",
    formula="TV = final RI * (1 + g) / (cost of equity - g)",
    assumptions=(
        _TERMINAL_ASSUMPTION,
        "The bank earns above its cost of equity for ever. Competition in deposit-taking and "
        "lending has historically removed such a spread, which is why the fade-to-nothing "
        "treatment exists beside this one.",
    ),
)
def perpetual_residual_value(
    _context: CalculationContext,
    *,
    final_residual_income: Quantity,
    cost_of_equity: Quantity,
    terminal_growth: Quantity,
) -> Quantity:
    """The excess return beyond the forecast, as a growing perpetuity.

    Raises:
        CalculationError: If terminal growth is at or above the cost of equity — the
            denominator is then nil or negative, and the result is either unbounded or a
            large negative number that looks like an answer. Also if the final year's
            residual income is negative, because growing a shortfall in perpetuity subtracts
            an unbounded amount from book value on the strength of one forecast year.
    """
    _require_money(final_residual_income, name="final_residual_income")
    _require_dimensionless(cost_of_equity, name="cost_of_equity")
    _require_dimensionless(terminal_growth, name="terminal_growth")

    spread = cost_of_equity.value - terminal_growth.value
    if spread <= 0:
        message = (
            f"Terminal growth of {terminal_growth.value} is not below the cost of equity of "
            f"{cost_of_equity.value}, so the perpetuity denominator is {spread}. A bank "
            "growing its excess return for ever at or above its cost of equity is worth an "
            "unbounded amount, which is a statement about the assumptions rather than a "
            "valuation."
        )
        raise CalculationError(
            message,
            context={
                "cost_of_equity": str(cost_of_equity.value),
                "terminal_growth": str(terminal_growth.value),
                "spread": str(spread),
            },
        )

    if final_residual_income.value < 0:
        message = (
            f"The final forecast year's residual income is {final_residual_income.value}. "
            "Growing a shortfall in perpetuity subtracts an unbounded amount from book value "
            "on the strength of one year; extend the forecast until the bank earns its cost "
            "of equity, or choose the fade-to-nothing treatment and say that it does not."
        )
        raise CalculationError(
            message, context={"final_residual_income": str(final_residual_income.value)}
        )

    grown = final_residual_income * (Quantity.of(_ONE) + terminal_growth)
    return grown / (cost_of_equity - terminal_growth)


@traced(
    name="explicit_residual_value",
    formula="explicit value = sum of PV(RI_t) for t in 1..N",
)
def explicit_residual_value(
    _context: CalculationContext, *, discounted_residual_income: Sequence[Quantity]
) -> Quantity:
    """The forecast's whole contribution, in today's money.

    Each discounted year is recorded as its own input, so the ledger says which year
    contributed what rather than storing a total nobody can decompose.

    Raises:
        CalculationError: If there are no discounted years.
    """
    if not discounted_residual_income:
        message = (
            "A residual-income valuation with no forecast years is a book value wearing a "
            "valuation's name."
        )
        raise CalculationError(message, context={"years": "0"})

    total = discounted_residual_income[0]
    for discounted in discounted_residual_income[1:]:
        total = total + discounted
    return total


@traced(
    name="residual_income_equity_value",
    formula="equity value = opening book value + explicit value + PV(terminal residual income)",
    assumptions=(
        "The opening book value is taken as economically real. That is the assumption this "
        "model exists to make, and it is the one worth arguing with first: a bank whose "
        "loan book is under-provisioned starts from a number that is already too high.",
    ),
)
def equity_value(
    _context: CalculationContext,
    *,
    opening_book_value: Quantity,
    explicit_value: Quantity,
    discounted_terminal_value: Quantity | None,
    treatment: TerminalTreatment,
    case: str = "base",
) -> Quantity:
    """Book value plus everything the excess return is worth.

    ``treatment`` does not enter the arithmetic. It is recorded because the two treatments
    produce different answers from identical drivers, and without it the ledger holds a total
    with nothing saying which claim about competition produced it. ``None`` for the terminal
    value is the fade treatment saying there is nothing beyond the forecast — recorded as an
    absence rather than as a nil that looks like a computed figure.

    ``case`` is the same argument one level up, and it is recorded for the reason
    :func:`aer.calc.dcf.enterprise_value` records it: a run that values its scenarios executes
    this once per case, and without the label a scenario chart cannot be read off the ledger.
    A grid cell carries :data:`SENSITIVITY_CASE` rather than a scenario's key, so twenty-five
    perturbations can never be mistaken for the valuation they perturb (ADR 0101).
    """
    _require_treatment(treatment)
    _require_case(case)
    _require_money(opening_book_value, name="opening_book_value")
    _require_money(explicit_value, name="explicit_value")

    total = opening_book_value + explicit_value
    if discounted_terminal_value is None:
        return total
    _require_money(discounted_terminal_value, name="discounted_terminal_value")
    return total + discounted_terminal_value


@traced(
    name="premium_to_book",
    formula="premium = equity value - opening book value",
    assumptions=(
        "Reported on every valuation, because a residual-income answer that is almost all "
        "book value is a statement about the accounts, and one that is half premium is a "
        "statement about the next decade's competition.",
    ),
)
def premium_to_book(
    _context: CalculationContext,
    *,
    equity_value: Quantity,
    opening_book_value: Quantity,
    treatment: TerminalTreatment,
    case: str = "base",
) -> Quantity:
    """How much of the answer is the excess return rather than the balance sheet.

    ``treatment`` and ``case`` do not enter the arithmetic. They are recorded for the reason
    :func:`equity_value` records them: this runs once per treatment and once per case, and
    rows of the same name with different answers and nothing saying why is a ledger a reader
    cannot use.
    """
    _require_treatment(treatment)
    _require_case(case)
    _require_money(equity_value, name="equity_value")
    _require_money(opening_book_value, name="opening_book_value")
    return equity_value - opening_book_value


@traced(
    name="residual_income_per_share",
    formula="value per share = equity value / shares outstanding",
    assumptions=(
        "The share count is today's. Dilution from options and from any issuance the "
        "forecast implies is not modelled.",
    ),
)
def value_per_share(
    _context: CalculationContext,
    *,
    equity_value: Quantity,
    shares: Quantity,
    treatment: TerminalTreatment,
    case: str = "base",
) -> Quantity:
    """The equity value spread over the shares in issue.

    Recorded as ``residual_income_per_share`` rather than ``value_per_share``, which
    :mod:`aer.calc.dcf` already claims. The ledger stores the name, so two functions sharing
    one would make every stored row of it ambiguous — and this is the figure a reader quotes,
    which is the worst place for that. The prefix is not decoration: a per-share number from
    this model and one from a discounted cash flow are different claims, and a report showing
    both should not have to guess which row is which.

    ``treatment`` and ``case`` are recorded and never computed with, for the same reason as
    above. This is the figure a reader quotes, so it is the row that most needs to say which
    claim about competition produced it and which set of assumptions it was priced on.

    Raises:
        CalculationError: If the share count is not positive.
    """
    _require_treatment(treatment)
    _require_case(case)
    _require_money(equity_value, name="equity_value")
    if shares.unit != _SHARES:
        message = (
            f"shares is in {shares.unit.symbol}, not shares. Dividing by a currency amount "
            "would produce a ratio wearing a per-share label."
        )
        raise CalculationError(message, context={"unit": shares.unit.symbol})
    if shares.value <= 0:
        message = (
            f"A share count of {shares.value} cannot divide an equity value. A company with "
            "no shares has no per-share anything."
        )
        raise CalculationError(message, context={"shares": str(shares.value)})
    return equity_value / shares


# -- The valuation ---------------------------------------------------------------------------


def residual_income_value(
    context: CalculationContext,
    inputs: ResidualIncomeInputs,
    *,
    mandate: ValuationMandate,
    case: str = "base",
) -> ResidualIncomeResult:
    """Value a bank's equity as its book value plus the excess return it earns on it.

    Every year's figures are recorded in ``context`` as traced calculations, so the final
    per-share number resolves back through the terminal value, the discounting, the residual
    income, the equity charge and the drivers to the filed book value it started from.

    ``mandate`` is the sector block, and it is a required argument rather than a check
    somewhere upstream, for the reason :mod:`aer.calc.dcf` gives.

    ``case`` labels which set of assumptions this valuation priced. It reaches the three
    outcome calculations and nothing else, because those are the rows a reader quotes and a
    chart reads (ADR 0101).

    Raises:
        ModelNotPermittedError: If the mandate is for a different model.
        CalculationError: From :meth:`ResidualIncomeInputs.validate` and from the steps —
            a payout outside nil to one, a terminal growth at or above the cost of equity, a
            share count of nil.
    """
    _require_residual_income_mandate(mandate)
    _require_case(case)
    inputs.validate()

    book = inputs.opening_book_value
    years: list[ResidualIncomeYear] = []

    for year in range(1, inputs.years + 1):
        opening = book
        earned = net_income_from_roe(
            context,
            opening_book_value=opening,
            return_on_equity=inputs.return_on_equity.at(year),
        )
        charge = equity_charge(
            context, opening_book_value=opening, cost_of_equity=inputs.cost_of_equity
        )
        excess = residual_income(context, net_income=earned, charge=charge)
        factor = equity_discount_factor(context, cost_of_equity=inputs.cost_of_equity, year=year)
        discounted = present_value(context, amount=excess, factor=factor)
        book = book_value_roll_forward(
            context,
            opening_book_value=opening,
            net_income=earned,
            payout_ratio=inputs.payout_ratio.at(year),
        )
        years.append(
            ResidualIncomeYear(
                year=year,
                opening_book_value=opening,
                net_income=earned,
                equity_charge=charge,
                residual_income=excess,
                discount_factor=factor,
                present_value=discounted,
                closing_book_value=book,
            )
        )

    explicit = explicit_residual_value(
        context, discounted_residual_income=[year.present_value for year in years]
    )

    terminal: Quantity | None = None
    terminal_pv: Quantity | None = None
    caveats = [CLEAN_SURPLUS_CAVEAT]

    if inputs.terminal_treatment is TerminalTreatment.PERPETUAL_GROWTH:
        terminal = perpetual_residual_value(
            context,
            final_residual_income=years[-1].residual_income,
            cost_of_equity=inputs.cost_of_equity,
            terminal_growth=inputs.terminal_growth,
        )
        terminal_factor = equity_discount_factor(
            context, cost_of_equity=inputs.cost_of_equity, year=inputs.years
        )
        terminal_pv = present_value(context, amount=terminal, factor=terminal_factor)
        caveats.append(
            "The valuation assumes the bank earns above its cost of equity in perpetuity. "
            "Competition has historically removed such a spread."
        )
    else:
        caveats.append(
            "No value is placed on anything beyond the explicit forecast: the excess return "
            "is assumed competed away at the end of it."
        )

    value = equity_value(
        context,
        opening_book_value=inputs.opening_book_value,
        explicit_value=explicit,
        discounted_terminal_value=terminal_pv,
        treatment=inputs.terminal_treatment,
        case=case,
    )
    premium = premium_to_book(
        context,
        equity_value=value,
        opening_book_value=inputs.opening_book_value,
        treatment=inputs.terminal_treatment,
        case=case,
    )
    per_share = value_per_share(
        context,
        equity_value=value,
        shares=inputs.shares_outstanding,
        treatment=inputs.terminal_treatment,
        case=case,
    )

    return ResidualIncomeResult(
        years=tuple(years),
        opening_book_value=inputs.opening_book_value,
        explicit_present_value=explicit,
        terminal_value=terminal,
        terminal_present_value=terminal_pv,
        equity_value=value,
        premium_to_book=premium,
        value_per_share=per_share,
        caveats=tuple(caveats),
    )


# -- Sensitivity -----------------------------------------------------------------------------


SENSITIVITY_CASE: Final = "sensitivity"
"""The case label every grid cell carries.

Not ``"base"``, which is what :mod:`aer.calc.dcf`'s grid cells inherit. A grid writes one
complete valuation per cell, and a scenario chart reads the *most recent* row for a case
(:func:`aer.services.exhibits._latest_for_case`) — so cells labelled ``base`` are twenty-five
later rows under the base case's own label, and a scenario keyed ``base`` would draw its bar
from whichever corner happened to be written last. ADR 0101 makes that unrepresentable here.
"""


class GridMeasure(StrEnum):
    """Which figure a grid reports in each cell.

    Three, where the discounted cash flow has three of its own, and the third is the one this
    model adds: a premium to book says how much of the answer is the excess return rather than
    the balance sheet, which is the question a bank's grid is drawn to answer.
    """

    VALUE_PER_SHARE = "residual_income_per_share"
    EQUITY_VALUE = "residual_income_equity_value"
    PREMIUM_TO_BOOK = "premium_to_book"


VARIABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"cost_of_equity", "terminal_growth", "return_on_equity", "payout_ratio"}
)
"""Which inputs a grid may vary.

Two scalars and two drivers, where :data:`aer.calc.dcf.VARIABLE_FIELDS` admits scalars alone.
The difference is not a relaxation: a driver axis is permitted here only when the confirmed
path is *flat*, checked in :func:`sensitivity_grid` against the inputs it is given, because
that is the case in which the cash-flow model's objection does not hold. A flat return on
equity is one number with an ordering, and an axis over it means exactly what its label says.

The opening book value and the share count are absent and stay absent. Both are filed
figures; varying them would be varying the filing, which is not a sensitivity but a different
company.
"""

_DRIVER_FIELDS: Final[frozenset[str]] = frozenset(DRIVER_NAMES)

_TERMINAL_GROWTH_FIELD: Final = "terminal_growth"


@dataclass(frozen=True, slots=True)
class GridAxis:
    """One axis of a sensitivity grid: which input varies, and over what values."""

    field: str
    values: tuple[Quantity, ...]

    def __post_init__(self) -> None:
        if self.field not in VARIABLE_FIELDS:
            message = (
                f"{self.field!r} is not an input a residual-income grid may vary. Available: "
                f"{', '.join(sorted(VARIABLE_FIELDS))}. The opening book value and the share "
                "count are filed figures, and a grid over one of those is a different company "
                "rather than a sensitivity."
            )
            raise CalculationError(message, context={"field": self.field})

        if not MIN_AXIS_POINTS <= len(self.values) <= MAX_AXIS_POINTS:
            message = (
                f"A grid axis over {self.field} has {len(self.values)} values, outside "
                f"{MIN_AXIS_POINTS} to {MAX_AXIS_POINTS}. One value is not a sensitivity; "
                "beyond the ceiling each extra column is a complete valuation with a complete "
                "lineage to store."
            )
            raise CalculationError(
                message, context={"field": self.field, "values": str(len(self.values))}
            )


@dataclass(frozen=True, slots=True)
class GridCell:
    """One point of a grid, and the calculation that produced it."""

    row_value: Quantity
    column_value: Quantity
    result: Quantity

    @property
    def calculation_id(self) -> uuid.UUID:
        """The calculation this cell's figure came from.

        Read off the quantity rather than tracked beside it, exactly as
        :class:`aer.calc.dcf.GridCell` reads it: the result is a traced calculation's output,
        so a cell cannot exist without the arithmetic that produced it.
        """
        if self.result.source is None:  # pragma: no cover - traced output always has one
            message = "A grid cell's result carries no source, so it cannot be recorded."
            raise CalculationError(message, context={"value": str(self.result.value)})
        return uuid.UUID(self.result.source.identifier)


@dataclass(frozen=True, slots=True)
class SensitivityGrid:
    """A rectangular grid of complete residual-income valuations."""

    row_axis: GridAxis
    column_axis: GridAxis
    treatment: TerminalTreatment
    measure: GridMeasure
    cells: tuple[GridCell, ...]

    @property
    def output_name(self) -> str:
        return f"{self.measure.value}_{self.treatment.value}"

    @property
    def output_unit(self) -> str:
        return self.cells[0].result.unit.symbol


def sensitivity_grid(
    context: CalculationContext,
    inputs: ResidualIncomeInputs,
    *,
    rows: GridAxis,
    columns: GridAxis,
    treatment: TerminalTreatment,
    measure: GridMeasure,
    mandate: ValuationMandate,
) -> SensitivityGrid:
    """Run a complete valuation at every point of a two-dimensional grid.

    **Every cell is a whole residual-income valuation** on ADR 0028's terms — not an
    interpolation between the corners, not a first-order approximation around the base case.
    The surface is no flatter here than it is for a discounted cash flow: the perpetuity
    denominator is ``cost of equity - g``, and the equity charge moves the explicit years in
    the opposite direction from the discounting, so a linear reading of it would understate
    exactly the corner the grid was drawn for.

    ``treatment`` applies to every cell, and it is a required argument rather than read off
    ``inputs``: a grid says what happens beyond the forecast once, for all twenty-five
    valuations, and letting it come in through the input set would allow a grid whose cells
    disagreed about the claim they were testing.

    Raises:
        CalculationError: If both axes vary the same input; if terminal growth is an axis
            under the fade treatment, which does not read it; or if a driver axis names a
            path that is not flat. Also from the valuation itself — a perpetuity refused in
            any corner takes the grid, because a hole in a grid is a cell a reader
            interprets (ADR 0101).
    """
    _require_distinct(rows, columns)
    _require_treatment(treatment)
    for axis in (rows, columns):
        _require_axis_is_read(axis, treatment)
        _require_flat_driver(axis, inputs)

    cells: list[GridCell] = []
    for row_value in rows.values:
        for column_value in columns.values:
            varied = _varied(inputs, {rows.field: row_value, columns.field: column_value})
            result = residual_income_value(
                context,
                replace(varied, terminal_treatment=treatment),
                mandate=mandate,
                case=SENSITIVITY_CASE,
            )
            cells.append(
                GridCell(
                    row_value=row_value,
                    column_value=column_value,
                    result=_measure_of(result, measure),
                )
            )

    return SensitivityGrid(
        row_axis=rows,
        column_axis=columns,
        treatment=treatment,
        measure=measure,
        cells=tuple(cells),
    )


def _varied(inputs: ResidualIncomeInputs, overrides: dict[str, Quantity]) -> ResidualIncomeInputs:
    """One perturbed input set.

    A driver's axis value becomes a whole flat path of that length, which is what varying a
    flat driver means: every year moves together, because every year was the same number to
    begin with.
    """
    # Typed loosely because the field name is data. `replace` still refuses a name
    # `ResidualIncomeInputs` does not have, so a typo is an error rather than a silently
    # ignored axis.
    applied: dict[str, Any] = {}
    for field, value in overrides.items():
        if field in _DRIVER_FIELDS:
            existing: DriverPath = getattr(inputs, field)
            applied[field] = DriverPath.flat(field, value, years=existing.years)
        else:
            applied[field] = value
    return replace(inputs, **applied)


def _measure_of(result: ResidualIncomeResult, measure: GridMeasure) -> Quantity:
    if measure is GridMeasure.VALUE_PER_SHARE:
        return result.value_per_share
    if measure is GridMeasure.EQUITY_VALUE:
        return result.equity_value
    return result.premium_to_book


def _require_distinct(rows: GridAxis, columns: GridAxis) -> None:
    if rows.field != columns.field:
        return
    message = (
        f"Both axes vary {rows.field}. Only the diagonal of such a grid would mean anything, "
        "and every other cell would contradict it."
    )
    raise CalculationError(message, context={"field": rows.field})


def _require_axis_is_read(axis: GridAxis, treatment: TerminalTreatment) -> None:
    """Refuse an axis over an input this treatment never reads.

    Only one such pairing exists: the terminal growth rate is read by the perpetuity and by
    nothing else, so a grid over it under the fade treatment renders identical columns. That
    is worse than no grid, because a flat surface labelled as a sensitivity reads as a finding
    — "the answer does not depend on this" — when what it means is that the grid asked a
    question this valuation was not answering.
    """
    if axis.field != _TERMINAL_GROWTH_FIELD or treatment is TerminalTreatment.PERPETUAL_GROWTH:
        return
    message = (
        "The fade-to-nothing treatment places no value beyond the forecast, so it never reads "
        "the terminal growth rate. A grid over it would render identical columns and read as "
        "a finding about the bank rather than about the axis."
    )
    raise CalculationError(message, context={"field": axis.field, "treatment": treatment.value})


def _require_flat_driver(axis: GridAxis, inputs: ResidualIncomeInputs) -> None:
    """Refuse a driver axis whose confirmed path moves year to year.

    ADR 0101's rule, and the sentence names what to do about it. A fading return on equity is
    several numbers, and an axis labelled with one of them would be a label for something
    else; shifting the whole path instead would put the *shift* on the axis, where a reader
    scanning a bank's grid takes ``-0.5%`` for a return on equity of minus a half per cent.
    """
    if axis.field not in _DRIVER_FIELDS:
        return
    path: DriverPath = getattr(inputs, axis.field)
    if path.flat_value is not None:
        return
    message = (
        f"The confirmed {axis.field} moves from year to year, so it is several numbers rather "
        "than one and cannot be an axis. Confirm a single flat "
        f"{axis.field} if you want this grid; a grid over one year of a fade would be "
        "labelled for a quantity it does not vary."
    )
    raise CalculationError(message, context={"field": axis.field, "years": str(path.years)})


def _require_residual_income_mandate(mandate: ValuationMandate) -> None:
    """Refuse a mandate granted for some other model.

    The sector rules are enforced when the mandate is *constructed*; this is the second half,
    and it is about identity rather than permission. A dividend-discount mandate is a valid
    mandate, and this model is the same arithmetic rearranged — which is exactly why the two
    must not be interchangeable here. A reader told the valuation is a dividend discount
    should be reading dividends, not a book value roll-forward.

    Raises:
        ModelNotPermittedError: If the mandate is not for residual income.
    """
    if mandate.model is ValuationModel.RESIDUAL_INCOME:
        return
    message = (
        f"This is a residual-income valuation, and the mandate is for "
        f"{mandate.model.value}. A mandate permits one model; running a second under it "
        "would make the permission mean whatever the caller wanted it to."
    )
    raise ModelNotPermittedError(
        message, context={"model": mandate.model.value, "subject": mandate.subject}
    )


def _require_money(value: Quantity, *, name: str) -> None:
    if value.unit.currencies:
        return
    message = (
        f"{name} is in {value.unit.symbol}, which is not a currency amount. A book value "
        "without a currency is a number somebody will add to a different one."
    )
    raise CalculationError(message, context={"name": name, "unit": value.unit.symbol})


def _require_case(case: str) -> None:
    """Refuse a blank case label.

    A blank case is a row nobody can attribute to a scenario, which is the exact gap the
    parameter exists to close. Validated rather than defaulted here: the default lives on the
    signature, so an explicit empty string is a caller error and not a base case.
    """
    if case.strip():
        return
    message = (
        "The case label is blank; the ledger could not say which set of assumptions this "
        "valuation prices."
    )
    raise CalculationError(message, context={"case": case})


def _require_treatment(value: object) -> None:
    """Refuse anything but a :class:`TerminalTreatment`.

    The annotation covers every caller mypy checks; this catches the ones it does not. A
    free-text treatment would be recorded verbatim as the claim a valuation made about
    competition, which reads as a specification and is a string.
    """
    if isinstance(value, TerminalTreatment):
        return

    message = (
        f"treatment is {value!r}, which is not a TerminalTreatment. The two treatments give "
        "different answers from identical drivers, so the record has to say which in a form "
        "code can read back."
    )
    raise CalculationError(message, context={"treatment": repr(value)})


def _require_rate(value: Quantity, *, name: str) -> None:
    """The same range check :mod:`aer.calc.wacc` applies, for the same reason.

    A cost of equity of 10 rather than 0.10 discounts the first year to nothing and the
    answer to the book value, and both are dimensionless so no unit catches it.
    """
    _require_dimensionless(value, name=name)

    if MIN_RATE <= value.value <= MAX_RATE:
        return

    hint = ""
    if value.value > MAX_RATE:
        hint = (
            " A figure this size is usually a percentage that was never divided by 100 — see "
            "aer.calc.wacc.rate_from_percent."
        )
    message = (
        f"{name} is {value.value}, outside the range {MIN_RATE} to {MAX_RATE} that a rate "
        f"expressed as a fraction can take.{hint}"
    )
    raise CalculationError(message, context={"input": name, "value": str(value.value)})


def _require_year(year: object) -> None:
    """Refuse a discounting period that is not a positive whole number.

    Takes ``object`` so mypy cannot narrow the check away, for the same reason as
    :func:`aer.calc.units._require_exact_decimal`.
    """
    if isinstance(year, bool) or not isinstance(year, int):
        message = (
            f"A discounting period must be a whole number of years, not {type(year).__name__}."
        )
        raise CalculationError(message, context={"year": repr(year)})
    if year < 1:
        message = (
            f"Year {year} is not a forecast year. Residual income is discounted from year one; "
            "a period of nil would leave the first year undiscounted."
        )
        raise CalculationError(message, context={"year": str(year)})


def _require_dimensionless(value: Quantity, *, name: str) -> None:
    if value.unit.is_dimensionless:
        return
    message = (
        f"{name} is in {value.unit.symbol}. A rate is a pure number — a return on equity "
        "denominated in dollars is a category error rather than a large return."
    )
    raise CalculationError(message, context={"name": name, "unit": value.unit.symbol})
