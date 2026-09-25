"""The discounted cash flow, and the parts of it that are really about the assumptions.

A DCF is the most respectable-looking way to be wrong in finance. It produces a single
number to two decimal places from a chain of arithmetic nobody disputes, and the number is
almost entirely determined by things nobody can know: how fast revenue grows, what margin it
carries, and what the business is worth at the end. Everything in this module is arranged so
that a reader can see *which* of those is doing the work.

**Every driver is a confirmed assumption, per year.** Revenue growth, EBIT margin, capital
intensity, depreciation and working capital each arrive as a
:class:`DriverPath` — one value for each forecast year, each a
:class:`~aer.calc.units.Quantity` carrying an assumption's source. A flat path is a path
whose years happen to agree, and it is still recorded year by year, so "what growth did year
four use?" has an answer in the ledger rather than in somebody's memory of the inputs page.

**Both terminal values, always, side by side.** Terminal value is usually most of the answer.
Gordon growth and an exit multiple are two different guesses about the same unknowable thing,
and they routinely disagree by a third. Presenting one is presenting a choice as a fact, so
:func:`discounted_cash_flow` computes both and returns both — with each method's *implied*
version of the other's parameter, which is the cross-check an analyst actually runs. A Gordon
terminal value implying a 19x exit multiple on a business that trades at 8x is a statement
about the assumptions, and it is invisible unless somebody divides.

**And the distance between them is a figure, banded.** :func:`method_disagreement` records it
rather than leaving a reader to divide, because a distance the platform will not name is one
it is hoping nobody measures. Past :data:`METHOD_DISAGREEMENT` the result says the two are a
width; past :data:`METHOD_CONTRADICTION` it stops saying that, because a higher figure more
than twice the lower is not a range with an answer inside it. The September measurement round
published a pair 2.25x apart under the first sentence, and every judge who read it said so.

**The terminal share is an output, not a diagnostic.** A valuation whose terminal value is
85% of enterprise value is a forecast of the forecast period's irrelevance. It appears on
every result.

**What refuses.** A terminal growth rate at or above the discount rate makes the perpetuity
denominator zero or negative and the value infinite or nonsensical; it raises. So does a
Gordon terminal value on a negative final cash flow, an exit multiple on negative EBITDA, and
a per-share figure with no shares. None of these produces a large number with a footnote.

**What growth does not do.** Enterprise value is *not* monotonically increasing in revenue
growth, and a test asserting it were would be asserting something false. When capital
intensity exceeds the operating margin, each extra pound of revenue consumes more cash than
it produces and growth destroys value — which is the correct answer, and one of the more
useful things a DCF says. The invariants tested here are the ones that actually hold: value
falls as the discount rate rises, rises with margin, rises with terminal growth, and scales
linearly with the level of the cash flows.

**A mandate is required, and that is the sector block.** :func:`project` and
:func:`discounted_cash_flow` take a :class:`~aer.core.sectors.ValuationMandate`, which cannot
be constructed for a sector whose profile blocks free cash flow to the firm. A bank therefore
does not produce a DCF that is then suppressed at the page — it produces a `TypeError` at the
call site or a refusal at construction, whichever comes first, and there is no route through
this module that skips it. See `docs/adr/0029`.

Pure and side-effect free, like everything in :mod:`aer.calc`. It is *given* a discount rate
from :mod:`aer.calc.wacc` and a set of drivers; it fetches nothing and reads no clock.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from aer.calc.engine import CalculationContext, traced
from aer.calc.units import (
    CalculationError,
    Quantity,
    Unit,
    UnitMismatchError,
)
from aer.calc.wacc import MAX_RATE, MIN_RATE
from aer.core.sectors import ModelNotPermittedError, ValuationMandate, ValuationModel

__all__ = [
    "ARGUED_CASE",
    "DRIVER_NAMES",
    "HIGH_TERMINAL_SHARE",
    "MAX_AXIS_POINTS",
    "MAX_FORECAST_YEARS",
    "METHOD_CONTRADICTION",
    "METHOD_DISAGREEMENT",
    "MIN_AXIS_POINTS",
    "MIN_TERMINAL_SPREAD",
    "PERTURBATION_CASES",
    "SENSITIVITY_CASE",
    "BridgeItem",
    "DcfInputs",
    "DcfResult",
    "DriverPath",
    "ForecastYear",
    "GridAxis",
    "GridCell",
    "GridMeasure",
    "SensitivityGrid",
    "TerminalMethod",
    "TerminalOutcome",
    "change_in_working_capital",
    "discount_factor",
    "discounted_cash_flow",
    "enterprise_value",
    "equity_value",
    "exit_multiple_terminal_value",
    "forecast_ebitda",
    "free_cash_flow",
    "gordon_terminal_value",
    "implied_exit_multiple",
    "implied_terminal_growth",
    "method_disagreement",
    "nopat_from_ebit",
    "present_value",
    "project",
    "projected_capex",
    "projected_depreciation",
    "projected_ebit",
    "projected_revenue",
    "projected_working_capital",
    "sensitivity_grid",
    "terminal_value_share",
    "value_per_share",
]


class TerminalMethod(StrEnum):
    """How the value beyond the forecast period was estimated."""

    GORDON_GROWTH = "gordon_growth"
    """A perpetuity growing at a constant rate. Sensitive to a rate nobody can observe."""

    EXIT_MULTIPLE = "exit_multiple"
    """A multiple of terminal EBITDA. Imports today's market mood into a decade's time."""

    @property
    def spoken(self) -> str:
        """The method as a reader meets it, never ``gordon_growth``.

        Three surfaces had their own copy of this pair — the valuation page, the report's
        method table, and nearly a fourth on the front page — which is one rule written
        three times waiting for one copy to rot (roadmap §3.19, the recurring class). It
        belongs on the enum for the same reason :attr:`aer.core.escalation.TriggerKind.spoken`
        does: the value is the record's and this is the sentence's.
        """
        return _SPOKEN_METHODS[self]


_SPOKEN_METHODS: Final[dict[TerminalMethod, str]] = {
    TerminalMethod.GORDON_GROWTH: "Gordon growth",
    TerminalMethod.EXIT_MULTIPLE: "Exit multiple",
}


class GridMeasure(StrEnum):
    """Which figure a sensitivity grid reports in each cell."""

    VALUE_PER_SHARE = "value_per_share"
    EQUITY_VALUE = "equity_value"
    ENTERPRISE_VALUE = "enterprise_value"


DRIVER_NAMES: Final[tuple[str, ...]] = (
    "revenue_growth",
    "ebit_margin",
    "capex_intensity",
    "depreciation_intensity",
    "working_capital_intensity",
)
"""The five drivers a forecast needs, as assumption names.

Named here rather than in the service layer because the *model* decides what it needs. A
driver missing from a request's confirmed assumptions is a refusal with this list in the
message, not a zero.
"""

MAX_FORECAST_YEARS: Final = 15
"""The longest explicit forecast this will build.

Beyond about ten years an explicit forecast is a terminal value written out longhand: the
drivers are all converging on the same fade, and the extra rows lend precision the inputs do
not have. Fifteen is generous. Refused rather than truncated, because truncating would drop
years the caller asked for and say nothing.
"""

MIN_TERMINAL_SPREAD: Final = Decimal("0.01")
"""How close terminal growth may come to the discount rate before the result is caveated.

Not a refusal — a spread of 80 basis points is arithmetically fine and occasionally
defensible. It is flagged because the perpetuity denominator is the spread itself, so at
100 basis points a ten-basis-point change in either input moves terminal value by a tenth.
"""

HIGH_TERMINAL_SHARE: Final = Decimal("0.75")
"""The terminal share above which the result says so in words.

Three quarters of the value beyond the forecast period means the forecast period is
decoration. That is often genuinely true for a durable business; it is never something a
reader should have to work out for themselves.
"""

METHOD_DISAGREEMENT: Final = Decimal("0.25")
"""How far the two terminal methods may diverge before the result says so."""

METHOD_CONTRADICTION: Final = Decimal("1.00")
"""How far they may diverge before the result stops calling the distance a width.

At a gap of one the lower figure is less than half the higher: no single view of the
business holds both, so the pair is not a range with an answer somewhere inside it. Below
this the two are a width and are described as one. Above it they are two answers, and a
caveat that reassures a reader about two answers is the platform arguing for its own
output — which is exactly what the September round's panel objected to, in the same words,
on both subjects (roadmap §3.19 item 38).
"""

MIN_AXIS_POINTS: Final = 2
"""Fewer values than this is a list, not a sensitivity."""

MAX_AXIS_POINTS: Final = 9
"""The most values one axis of a sensitivity grid may take.

Nine by nine is eighty-one complete discounted cash flows, each with its full calculation
lineage recorded — several thousand rows for one grid. That is the price of a grid whose
every cell can be taken apart, and it is the right price; but it is a price, so the grid is
bounded rather than open-ended.
"""

# Growth beyond this is a driver entered as a percentage rather than a fraction. A real
# company can double revenue in a year; none of them grows 2,500%.
MAX_REVENUE_GROWTH: Final = Decimal(3)

# An EV/EBITDA multiple beyond this is a typo or a company for which EBITDA is meaningless.
MAX_EXIT_MULTIPLE: Final = Decimal(50)

_ONE: Final = Decimal(1)
_SHARES: Final = Unit.base("shares")

_TERMINAL_ASSUMPTION: Final = (
    "The terminal value is the whole of the business's worth beyond the forecast period, "
    "which for most companies is the majority of the answer. It rests on a single parameter "
    "nobody can observe."
)

NARROW_SPREAD_CAVEAT: Final = (
    "Terminal growth is within one percentage point of the discount rate. The perpetuity "
    "divides by the difference between them, so at this spread a small change in either "
    "input moves the terminal value by a large fraction of itself. The valuation is a "
    "statement about those two numbers rather than about the business."
)

HIGH_TERMINAL_SHARE_CAVEAT: Final = (
    "More than three quarters of the enterprise value is terminal value. The explicit "
    "forecast is contributing little, so the answer rests almost entirely on the terminal "
    "assumption rather than on the projected years a reader can check."
)

METHOD_DISAGREEMENT_CAVEAT: Final = (
    "The two terminal methods disagree by more than a quarter. That is information, not an "
    "error: they are two different guesses about the same unknowable quantity, and the "
    "distance between them is the honest width of the answer."
)

METHOD_CONTRADICTION_CAVEAT: Final = (
    "The two terminal methods do not bracket a range — they contradict each other. The "
    "higher figure is more than twice the lower, so no single view of this business holds "
    "both, and what is stated below is two answers rather than one answer with a width. "
    "The distance between them is recorded as a figure of its own, as is the perpetual "
    "growth rate the exit multiple implies; that rate set against the one the perpetuity "
    "was given is what separates the two, and it is the thing to argue about before either "
    "figure is used."
)

NEGATIVE_EQUITY_CAVEAT: Final = (
    "The equity value is negative: the enterprise value does not cover net debt. The "
    "per-share figure below zero is arithmetic, not a price — equity is a claim with a floor "
    "at nil, and a company in this position is being valued as an option on recovery rather "
    "than as a going concern."
)


# -- The forecast ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriverPath:
    """One driver's value in each forecast year.

    A path rather than a scalar because a forecast whose growth rate never changes is a
    forecast nobody argued about. Even a flat path is stored year by year, so the ledger says
    what year four used rather than leaving it to be inferred.
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
        """The same value in every year, still recorded once per year."""
        if years < 1:
            message = f"A forecast of {years} years is not a forecast."
            raise CalculationError(message, context={"driver": name, "years": str(years)})
        return cls(name=name, values=(value,) * years)

    @property
    def years(self) -> int:
        return len(self.values)

    def at(self, year: int) -> Quantity:
        """The value for a one-based forecast year."""
        return self.values[year - 1]


@dataclass(frozen=True, slots=True)
class BridgeItem:
    """One non-operating item between enterprise value and equity value.

    Signed: an associate holding is positive, a pension deficit or a minority interest is
    negative. The label travels into the report, because "other adjustments: -412" is a
    number a reader has to take on trust.
    """

    label: str
    amount: Quantity


@dataclass(frozen=True, slots=True)
class DcfInputs:
    """Everything one discounted cash flow needs, and nothing it can do without.

    Flat rather than nested so that :func:`sensitivity_grid` can vary one field with
    :func:`dataclasses.replace` and produce an input set that is complete by construction.

    **No field has a default.** ``non_operating`` must be passed even when empty, because "no
    non-operating items" is a claim about a balance sheet rather than an absence of input.
    """

    base_revenue: Quantity
    revenue_growth: DriverPath
    ebit_margin: DriverPath
    capex_intensity: DriverPath
    depreciation_intensity: DriverPath
    working_capital_intensity: DriverPath
    opening_working_capital: Quantity
    tax_rate: Quantity
    wacc: Quantity
    terminal_growth: Quantity
    exit_multiple: Quantity
    net_debt: Quantity
    shares_outstanding: Quantity
    non_operating: tuple[BridgeItem, ...]

    @property
    def drivers(self) -> tuple[DriverPath, ...]:
        return (
            self.revenue_growth,
            self.ebit_margin,
            self.capex_intensity,
            self.depreciation_intensity,
            self.working_capital_intensity,
        )

    @property
    def years(self) -> int:
        return self.revenue_growth.years

    def validate(self) -> None:
        """Refuse an input set that cannot produce a coherent forecast.

        Raises:
            CalculationError: If the drivers disagree about how many years there are, or if
                the forecast is empty or longer than :data:`MAX_FORECAST_YEARS`.
        """
        lengths = {driver.name: driver.years for driver in self.drivers}
        if len(set(lengths.values())) != 1:
            message = (
                "The drivers cover different numbers of years: "
                f"{', '.join(f'{name} {years}' for name, years in sorted(lengths.items()))}. "
                "A forecast in which one driver runs out before the others would silently "
                "reuse or drop a year."
            )
            raise CalculationError(message, context=dict.fromkeys(lengths, "") | {})

        if not 1 <= self.years <= MAX_FORECAST_YEARS:
            message = (
                f"A {self.years}-year explicit forecast is outside the 1 to "
                f"{MAX_FORECAST_YEARS} this builds. Beyond that the drivers have converged "
                "and the extra rows lend a precision the assumptions do not have."
            )
            raise CalculationError(message, context={"years": str(self.years)})


@dataclass(frozen=True, slots=True)
class ForecastYear:
    """One projected year, every line of it a recorded calculation."""

    year: int
    revenue: Quantity
    ebit: Quantity
    nopat: Quantity
    depreciation: Quantity
    capex: Quantity
    working_capital: Quantity
    change_in_working_capital: Quantity
    ebitda: Quantity
    free_cash_flow: Quantity
    discount_factor: Quantity
    present_value: Quantity


@dataclass(frozen=True, slots=True)
class TerminalOutcome:
    """One terminal method carried all the way through to a per-share figure."""

    method: TerminalMethod
    terminal_value: Quantity
    discounted_terminal_value: Quantity
    enterprise_value: Quantity
    terminal_share: Quantity
    equity_value: Quantity
    value_per_share: Quantity

    # The cross-check: each method's implied version of the *other* method's parameter. A
    # Gordon terminal value has an implied exit multiple, and an exit multiple has an implied
    # perpetual growth rate. Each is `None` on the method that took it as an input, where it
    # would only restate the assumption.
    implied_exit_multiple: Quantity | None
    implied_terminal_growth: Quantity | None


@dataclass(frozen=True, slots=True)
class DcfResult:
    """A valuation, both ways, with what a reader has to be told about it."""

    years: tuple[ForecastYear, ...]
    gordon: TerminalOutcome
    exit_multiple: TerminalOutcome
    caveats: tuple[str, ...]

    # How far the two methods finished apart, as a fraction of the lower. `None` only where
    # the ratio is not defined — a per-share figure at or below zero, which carries its own
    # caveat. Recorded rather than derived at the page, so the report can state the distance
    # with a calculation behind it.
    method_disagreement: Quantity | None = None

    @property
    def methods_contradict(self) -> bool:
        """Whether the pair is two answers rather than one answer with a width.

        The predicate the caveat branches on, exposed so a caller deciding what to *do*
        about it — record a disagreement, refuse to compose a view — reads the same
        threshold rather than restating it. One rule stated twice is one rule with a copy
        that rots (roadmap §3.19, five instances in one day).
        """
        return (
            self.method_disagreement is not None
            and self.method_disagreement.value > METHOD_CONTRADICTION
        )

    @property
    def outcomes(self) -> tuple[TerminalOutcome, TerminalOutcome]:
        return (self.gordon, self.exit_multiple)

    def outcome(self, method: TerminalMethod) -> TerminalOutcome:
        return self.gordon if method is TerminalMethod.GORDON_GROWTH else self.exit_multiple


# -- The projection --------------------------------------------------------------------------


@traced(
    name="projected_revenue",
    formula="revenue_t = revenue_(t-1) * (1 + growth_t)",
    assumptions=(
        "Growth is organic and constant within each year. Acquisitions and disposals are not "
        "modelled, so a company that grows by buying things has a forecast this does not "
        "describe.",
    ),
)
def projected_revenue(
    _context: CalculationContext, *, prior_revenue: Quantity, growth: Quantity
) -> Quantity:
    """One year's revenue from the year before it.

    Raises:
        UnitMismatchError: If revenue is not a currency amount or growth is not dimensionless.
        CalculationError: If growth is below -100% or implausibly large, which is what a rate
            entered as a percentage rather than a fraction looks like.
    """
    _require_money(prior_revenue, name="prior_revenue")
    _require_dimensionless(growth, name="growth")

    if not -_ONE <= growth.value <= MAX_REVENUE_GROWTH:
        message = (
            f"Revenue growth of {growth.value} is outside -1 to {MAX_REVENUE_GROWTH}. Below "
            "-1 revenue would turn negative; above this it is a percentage that was never "
            "divided by a hundred."
        )
        raise CalculationError(message, context={"growth": str(growth.value)})

    return prior_revenue * (Quantity.of(_ONE) + growth)


@traced(
    name="projected_ebit",
    formula="EBIT_t = revenue_t * EBIT margin_t",
    assumptions=(
        "Operating margin is the single driver of operating cost. Fixed and variable costs "
        "are not separated, so operating leverage — margin widening as revenue grows — has "
        "to be expressed by the margin path rather than falling out of the model.",
    ),
)
def projected_ebit(
    _context: CalculationContext, *, revenue: Quantity, margin: Quantity
) -> Quantity:
    """Operating profit from revenue and a margin.

    Raises:
        CalculationError: If the margin is outside -100% to 100%. A margin above one is
            impossible; below minus one the company spends more than twice its revenue and
            has no meaningful discounted cash flow.
    """
    _require_money(revenue, name="revenue")
    _require_dimensionless(margin, name="margin")

    if not -_ONE <= margin.value <= _ONE:
        message = (
            f"An EBIT margin of {margin.value} is outside -1 to 1. Above one is arithmetically "
            "impossible; below minus one is a company whose costs exceed twice its revenue, "
            "which a discounted cash flow cannot say anything useful about."
        )
        raise CalculationError(message, context={"margin": str(margin.value)})

    return revenue * margin


@traced(
    name="nopat_from_ebit",
    formula="NOPAT_t = EBIT_t * (1 - tax rate)",
    assumptions=(
        "One tax rate for every forecast year. Rate changes, expiring incentives and the "
        "unwinding of deferred balances are not modelled.",
        "A loss is taxed at the same rate as a profit, which assumes it shelters taxable "
        "income elsewhere in the group in the year it arises. A standalone loss-maker "
        "carries it forward, and a shield deferred is worth less than one taken.",
    ),
)
def nopat_from_ebit(
    _context: CalculationContext, *, ebit: Quantity, tax_rate: Quantity
) -> Quantity:
    """Operating profit after tax, before financing.

    Named for the input it takes rather than sharing :func:`aer.calc.ratios.nopat`'s ledger
    entry: that one derives an effective rate from a filing, this one applies a forecast rate
    to a forecast profit, and two different formulas under one name make the ledger useless
    for the question it exists to answer.
    """
    _require_money(ebit, name="ebit")
    _require_rate(tax_rate, name="tax_rate", floor=Decimal(0))

    return ebit * (Quantity.of(_ONE) - tax_rate)


@traced(
    name="projected_capex",
    formula="capex_t = revenue_t * capital intensity_t",
    assumptions=(
        "Capital expenditure scales with revenue. A step change — a new plant, a platform "
        "rebuild — has to be expressed in the intensity path for the year it falls in.",
    ),
)
def projected_capex(
    _context: CalculationContext, *, revenue: Quantity, intensity: Quantity
) -> Quantity:
    """Capital expenditure as a share of revenue.

    Raises:
        CalculationError: If intensity is negative or above 100% of revenue. Negative capex
            is a disposal and belongs in the non-operating bridge, not in the operating
            forecast.
    """
    _require_money(revenue, name="revenue")
    _require_dimensionless(intensity, name="intensity")

    if not Decimal(0) <= intensity.value <= _ONE:
        message = (
            f"Capital intensity of {intensity.value} is outside 0 to 1. Negative capex is a "
            "disposal, which belongs in the non-operating bridge rather than in the operating "
            "forecast; above one the company spends more on assets than it sells."
        )
        raise CalculationError(message, context={"intensity": str(intensity.value)})

    return revenue * intensity


@traced(
    name="projected_depreciation",
    formula="depreciation_t = revenue_t * depreciation intensity_t",
    assumptions=(
        "Depreciation scales with revenue rather than being rolled forward from the asset "
        "base. Over a forecast in which capital intensity is broadly stable the two converge; "
        "for a company mid-way through a large investment programme they do not.",
    ),
)
def projected_depreciation(
    _context: CalculationContext, *, revenue: Quantity, intensity: Quantity
) -> Quantity:
    """Depreciation and amortisation as a share of revenue."""
    _require_money(revenue, name="revenue")
    _require_dimensionless(intensity, name="intensity")

    if not Decimal(0) <= intensity.value <= _ONE:
        message = (
            f"Depreciation intensity of {intensity.value} is outside 0 to 1. Depreciation is "
            "a charge, not a credit."
        )
        raise CalculationError(message, context={"intensity": str(intensity.value)})

    return revenue * intensity


@traced(
    name="projected_working_capital",
    formula="working capital_t = revenue_t * working-capital intensity_t",
    assumptions=(
        "Working capital scales with revenue. Negative intensity is normal and not an error: "
        "a retailer collecting cash at the till and paying suppliers in sixty days runs "
        "structurally negative working capital, and growth releases cash rather than "
        "consuming it.",
    ),
)
def projected_working_capital(
    _context: CalculationContext, *, revenue: Quantity, intensity: Quantity
) -> Quantity:
    """The working capital the business carries at a given level of revenue."""
    _require_money(revenue, name="revenue")
    _require_dimensionless(intensity, name="intensity")

    if not -_ONE <= intensity.value <= _ONE:
        message = (
            f"Working-capital intensity of {intensity.value} is outside -1 to 1. A business "
            "holding more than a year of revenue in working capital, in either direction, is "
            "not one this model describes."
        )
        raise CalculationError(message, context={"intensity": str(intensity.value)})

    return revenue * intensity


@traced(
    name="change_in_working_capital",
    formula="change in working capital_t = working capital_t - working capital_(t-1)",
    assumptions=(
        "The change is the cash effect. An increase consumes cash and reduces free cash flow; "
        "a decrease releases it.",
    ),
)
def change_in_working_capital(
    _context: CalculationContext, *, opening: Quantity, closing: Quantity
) -> Quantity:
    """The cash absorbed by working capital over a year."""
    _require_money(opening, name="opening")
    _require_money(closing, name="closing")
    return closing - opening


@traced(
    name="forecast_ebitda",
    formula="EBITDA_t = EBIT_t + depreciation_t",
    assumptions=(
        "A forecast measure, built from the model's own drivers. It is not the filing's "
        "EBITDA and does not carry the filing's exceptional items; it exists so an exit "
        "multiple has something to multiply.",
    ),
)
def forecast_ebitda(
    _context: CalculationContext, *, ebit: Quantity, depreciation: Quantity
) -> Quantity:
    """Projected EBITDA, for the exit-multiple terminal value."""
    return ebit + depreciation


@traced(
    name="forecast_free_cash_flow",
    formula="FCFF_t = NOPAT_t + depreciation_t - capex_t - change in working capital_t",
    assumptions=(
        "Free cash flow to the firm: before interest and before debt repayment, because the "
        "cost of debt is in the discount rate rather than in the cash flow. Deducting "
        "interest here as well would charge for the debt twice.",
        "Share-based payment is not added back. It is a real cost of employing people, and "
        "adding it back is the single most common way a discounted cash flow flatters a "
        "company that pays in equity.",
    ),
)
def free_cash_flow(
    _context: CalculationContext,
    *,
    nopat: Quantity,
    depreciation: Quantity,
    capex: Quantity,
    working_capital_change: Quantity,
) -> Quantity:
    """The cash the business generates for all providers of capital."""
    return nopat + depreciation - capex - working_capital_change


# -- Discounting -----------------------------------------------------------------------------


@traced(
    name="discount_factor",
    formula="factor_t = 1 / (1 + WACC) ^ t",
    assumptions=(
        "Cash arrives at the end of each year. A mid-year convention would raise every "
        "discounted figure by roughly half a year of the discount rate; end-of-year is the "
        "more conservative of the two and is stated rather than chosen silently.",
        "One discount rate for every year, so the capital structure is assumed constant.",
    ),
)
def discount_factor(_context: CalculationContext, *, wacc: Quantity, year: int) -> Quantity:
    """What a pound in year ``t`` is worth today.

    Raises:
        CalculationError: If the year is not a positive whole number, or the rate is outside
            the range a rate expressed as a fraction can take.
    """
    _require_rate(wacc, name="wacc", floor=MIN_RATE)
    _require_year(year)

    return (Quantity.of(_ONE) + wacc).power(-year)


@traced(name="present_value", formula="PV = amount * discount factor")
def present_value(_context: CalculationContext, *, amount: Quantity, factor: Quantity) -> Quantity:
    """A future amount in today's money."""
    _require_dimensionless(factor, name="factor")
    return amount * factor


# -- Terminal value --------------------------------------------------------------------------


@traced(
    name="gordon_terminal_value",
    formula="TV = final FCFF * (1 + g) / (WACC - g)",
    assumptions=(
        _TERMINAL_ASSUMPTION,
        "The business grows at a constant rate for ever and earns its cost of capital on the "
        "growth. A perpetual rate above long-run nominal economic growth implies the company "
        "eventually becomes the economy.",
        "The final forecast year is representative. A year containing an unusual capital "
        "programme or working-capital swing is capitalised into perpetuity by this formula.",
    ),
)
def gordon_terminal_value(
    _context: CalculationContext,
    *,
    final_cash_flow: Quantity,
    wacc: Quantity,
    terminal_growth: Quantity,
) -> Quantity:
    """The value beyond the forecast, as a growing perpetuity.

    Raises:
        CalculationError: If terminal growth is at or above the discount rate — the
            denominator is then nil or negative and the value infinite or, worse, a large
            negative number that looks like an answer. Also if the final cash flow is not
            positive, because a perpetuity of a negative cash flow values the business at
            less than nothing on the strength of one forecast year.
    """
    _require_money(final_cash_flow, name="final_cash_flow")
    _require_rate(wacc, name="wacc", floor=MIN_RATE)
    _require_rate(terminal_growth, name="terminal_growth", floor=MIN_RATE)

    spread = wacc.value - terminal_growth.value
    if spread <= 0:
        message = (
            f"Terminal growth of {terminal_growth.value} is not below the discount rate of "
            f"{wacc.value}, so the perpetuity denominator is {spread}. A business growing for "
            "ever at or above its cost of capital is worth an unbounded amount, which is a "
            "statement about the assumptions rather than a valuation."
        )
        raise CalculationError(
            message,
            context={
                "wacc": str(wacc.value),
                "terminal_growth": str(terminal_growth.value),
                "spread": str(spread),
            },
        )

    if final_cash_flow.value <= 0:
        message = (
            f"The final forecast year's free cash flow is {final_cash_flow.value}. Growing a "
            "negative cash flow in perpetuity values the business below nothing on the "
            "strength of one year; extend the forecast until the business generates cash, or "
            "say that it does not."
        )
        raise CalculationError(message, context={"final_cash_flow": str(final_cash_flow.value)})

    grown = final_cash_flow * (Quantity.of(_ONE) + terminal_growth)
    return grown / (wacc - terminal_growth)


@traced(
    name="exit_multiple_terminal_value",
    formula="TV = terminal EBITDA * EV/EBITDA multiple",
    assumptions=(
        _TERMINAL_ASSUMPTION,
        "The multiple the business commands at the end of the forecast is knowable today. It "
        "imports the current market's rating of the sector into a decade's time, which is the "
        "opposite failure to Gordon growth's and is why both are shown.",
        "EBITDA is a proxy for the cash a buyer would pay for. It ignores the capital "
        "intensity that the rest of this model spends five years projecting.",
    ),
)
def exit_multiple_terminal_value(
    _context: CalculationContext, *, terminal_ebitda: Quantity, multiple: Quantity
) -> Quantity:
    """The value beyond the forecast, as a multiple of the final year's EBITDA.

    Raises:
        CalculationError: If EBITDA is not positive, or the multiple is not a plausible
            positive number. A multiple applied to negative EBITDA produces a negative
            terminal value that is arithmetic rather than analysis.
    """
    _require_money(terminal_ebitda, name="terminal_ebitda")
    _require_dimensionless(multiple, name="multiple")

    if terminal_ebitda.value <= 0:
        message = (
            f"Terminal EBITDA is {terminal_ebitda.value}. An exit multiple on a negative "
            "figure produces a negative terminal value, which is multiplication rather than "
            "valuation — a buyer does not pay a negative price eight times over."
        )
        raise CalculationError(message, context={"terminal_ebitda": str(terminal_ebitda.value)})

    if not Decimal(0) < multiple.value <= MAX_EXIT_MULTIPLE:
        message = (
            f"An exit multiple of {multiple.value} is outside 0 to {MAX_EXIT_MULTIPLE}. Zero "
            "or negative is not a multiple; beyond this it is a typo or a company for which "
            "EBITDA means nothing."
        )
        raise CalculationError(message, context={"multiple": str(multiple.value)})

    return terminal_ebitda * multiple


@traced(
    name="implied_exit_multiple",
    formula="implied EV/EBITDA = terminal value / terminal EBITDA",
)
def implied_exit_multiple(
    _context: CalculationContext,
    *,
    terminal_value: Quantity,
    terminal_ebitda: Quantity,
    case: str = "base",
) -> Quantity:
    """What multiple of final-year EBITDA a terminal value amounts to.

    The cross-check on Gordon growth. A perpetual growth rate is hard to argue with because
    nobody has an intuition for it; the multiple it implies is a number the same reader
    compares against the sector every day.
    """
    _require_case(case)
    if terminal_ebitda.value <= 0:
        message = (
            f"Terminal EBITDA is {terminal_ebitda.value}, so the implied multiple is not a "
            "multiple of anything."
        )
        raise CalculationError(message, context={"terminal_ebitda": str(terminal_ebitda.value)})
    return terminal_value / terminal_ebitda


@traced(
    name="implied_terminal_growth",
    formula="implied g = (TV * WACC - final FCFF) / (TV + final FCFF)",
    assumptions=(
        "Solves the Gordon formula backwards for the growth rate that reproduces this "
        "terminal value.",
    ),
)
def implied_terminal_growth(
    _context: CalculationContext,
    *,
    terminal_value: Quantity,
    final_cash_flow: Quantity,
    wacc: Quantity,
    case: str = "base",
) -> Quantity:
    """The perpetual growth rate an exit multiple amounts to.

    The cross-check on the exit multiple. A multiple of 12x sounds ordinary; the 4.5% real
    perpetual growth it implies does not, and one of the two is easier to disagree with.
    """
    _require_case(case)
    _require_money(terminal_value, name="terminal_value")
    _require_money(final_cash_flow, name="final_cash_flow")
    _require_rate(wacc, name="wacc", floor=MIN_RATE)

    denominator = terminal_value + final_cash_flow
    if denominator.value == 0:
        message = (
            "The terminal value and the final cash flow cancel, so no growth rate reproduces "
            "this terminal value."
        )
        raise CalculationError(message, context={"terminal_value": str(terminal_value.value)})

    return (terminal_value * wacc - final_cash_flow) / denominator


@traced(
    name="method_disagreement",
    formula="disagreement = (higher value per share - lower value per share) / lower",
    assumptions=(
        "Measured against the lower of the two, so the figure reads as how much more the "
        "higher method says the business is worth.",
    ),
)
def method_disagreement(
    _context: CalculationContext,
    *,
    gordon_per_share: Quantity,
    exit_multiple_per_share: Quantity,
    case: str = "base",
) -> Quantity:
    """How far apart the two terminal methods finish, as a fraction of the lower.

    **Recorded rather than left for a reader to divide.** The September measurement round
    produced a pair 2.25x apart and said of it only that the methods *"disagree by more than
    a quarter"* — which was true, and was the threshold the caveat had been written against
    rather than the distance the run had reached. All three judges reading that document
    worked the ratio out for themselves and held it against it. A distance the platform will
    not name is a distance it is hoping nobody measures, and naming it needs a record like
    any other figure (invariant 3).

    ``case`` is recorded for the reason :func:`enterprise_value` records it: a sensitivity
    grid strikes this once per cell, and without it the ledger holds many rows of one name
    with nothing saying which is the valuation the report describes.

    Raises:
        CalculationError: If the lower figure is not positive. Equity below zero has its own
            caveat, and a ratio struck against it would be arithmetic dressed as a
            comparison.
    """
    _require_case(case)
    if gordon_per_share.unit != exit_multiple_per_share.unit:
        message = (
            f"The two terminal methods produced {gordon_per_share.unit.symbol} and "
            f"{exit_multiple_per_share.unit.symbol}, which is not a disagreement about one "
            "number."
        )
        raise UnitMismatchError(
            message,
            context={
                "gordon": gordon_per_share.unit.symbol,
                "exit_multiple": exit_multiple_per_share.unit.symbol,
            },
        )

    lower, higher = sorted(
        (gordon_per_share, exit_multiple_per_share), key=lambda figure: figure.value
    )
    if lower.value <= 0:
        message = (
            f"The lower of the two per-share figures is {lower.value}, so the distance "
            "between the methods is not a fraction of anything."
        )
        raise CalculationError(message, context={"lower": str(lower.value)})

    return (higher - lower) / lower


# -- Enterprise and equity value -------------------------------------------------------------


@traced(
    name="enterprise_value",
    formula="EV = sum of discounted forecast cash flows + discounted terminal value",
)
def enterprise_value(
    _context: CalculationContext,
    *,
    discounted_flows: Sequence[Quantity],
    discounted_terminal_value: Quantity,
    method: TerminalMethod,
    case: str = "base",
) -> Quantity:
    """The value of the operating business to all providers of capital.

    Each discounted year is recorded as its own input, so the ledger says which year
    contributed what rather than storing a total nobody can decompose.

    ``method`` does not enter the arithmetic. It is recorded because **this calculation runs
    twice per valuation, once per terminal method, and without it the ledger holds two rows
    with the same name and different answers and nothing saying why.** A reader looking at
    the calculations table would have to infer it from the order they were written in.

    ``case`` is the same argument one level up: a run that values its scenarios executes
    this calculation once per case, and before task 47 recorded them indistinguishably —
    which made a scenario chart unreadable from the ledger. Recorded, never computed with.
    """
    _require_method(method)
    _require_case(case)
    if not discounted_flows:
        message = (
            "An enterprise value with no discounted forecast years is a terminal value "
            "wearing a different name."
        )
        raise CalculationError(message, context={"years": "0"})

    total = discounted_terminal_value
    for flow in discounted_flows:
        total = total + flow
    return total


@traced(
    name="terminal_value_share",
    formula="terminal share = discounted terminal value / enterprise value",
    assumptions=(
        "Reported on every valuation, because a discounted cash flow whose terminal value is "
        "most of the answer is a statement about the terminal assumption rather than about "
        "the projected years a reader can check.",
    ),
)
def terminal_value_share(
    _context: CalculationContext,
    *,
    discounted_terminal_value: Quantity,
    enterprise_value: Quantity,
    method: TerminalMethod,
    case: str = "base",
) -> Quantity:
    """How much of the valuation lies beyond the forecast period.

    ``method`` is recorded rather than used, for the reason :func:`enterprise_value` gives.
    """
    _require_method(method)
    _require_case(case)
    if enterprise_value.value <= 0:
        message = (
            f"The enterprise value is {enterprise_value.value}, so a terminal share of it is "
            "not a share of anything."
        )
        raise CalculationError(message, context={"enterprise_value": str(enterprise_value.value)})
    return discounted_terminal_value / enterprise_value


@traced(
    name="equity_value",
    formula="equity value = enterprise value - net debt + non-operating items",
    assumptions=(
        "Net debt is the figure at the valuation date, not an average and not a forecast. A "
        "company that repays debt during the forecast has already had that cash counted in "
        "free cash flow; deducting the repaid balance as well would count it twice.",
        "Non-operating items are taken at their stated carrying or market value. Associates, "
        "investments, pension deficits and minority interests are each a valuation in their "
        "own right, and none of them is performed here.",
    ),
)
def equity_value(
    _context: CalculationContext,
    *,
    enterprise_value: Quantity,
    net_debt: Quantity,
    adjustments: Sequence[Quantity],
    method: TerminalMethod,
    case: str = "base",
) -> Quantity:
    """What is left for the ordinary shareholders.

    ``method`` is recorded rather than used, for the reason :func:`enterprise_value` gives.
    """
    _require_method(method)
    _require_case(case)
    total = enterprise_value - net_debt
    for adjustment in adjustments:
        total = total + adjustment
    return total


@traced(
    name="value_per_share",
    formula="value per share = equity value / shares outstanding",
    assumptions=(
        "Shares outstanding at the valuation date, undiluted unless a diluted count was "
        "supplied. Options and convertibles in the money would raise the count and lower this "
        "figure.",
    ),
)
def value_per_share(
    _context: CalculationContext,
    *,
    equity_value: Quantity,
    shares: Quantity,
    method: TerminalMethod,
    case: str = "base",
) -> Quantity:
    """The valuation, per share.

    ``method`` is recorded rather than used, for the reason :func:`enterprise_value` gives —
    and it matters most here, because this is the figure a reader quotes.

    Raises:
        UnitMismatchError: If the share count is not in shares.
        CalculationError: If the share count is not positive, or the method is not a
            :class:`TerminalMethod`.
    """
    _require_method(method)
    _require_case(case)
    if shares.unit != _SHARES:
        message = (
            f"The share count is in {shares.unit.symbol}, not shares. A per-share figure "
            "divided by the wrong thing carries the right currency and the wrong meaning."
        )
        raise UnitMismatchError(message, context={"unit": shares.unit.symbol})

    if shares.value <= 0:
        message = f"The share count is {shares.value}. A per-share value needs shares."
        raise CalculationError(message, context={"shares": str(shares.value)})

    return equity_value / shares


# -- The valuation ---------------------------------------------------------------------------


def project(
    context: CalculationContext, inputs: DcfInputs, *, mandate: ValuationMandate
) -> tuple[ForecastYear, ...]:
    """Build the explicit forecast, one recorded calculation per line per year.

    Not traced itself — it returns a structure rather than a quantity — but every line of
    every year is, so ``context.records`` afterwards contains the whole forecast and a reader
    can ask what any cell was made of.

    Takes the mandate as well as :func:`discounted_cash_flow` does, because a forecast plus a
    terminal value computed by hand is a discounted cash flow by another name, and "by any
    route" in the acceptance criterion means this one too.

    **Accepts a free-cash-flow mandate of either kind.** A projection is not itself a
    valuation: the same forecast underlies free cash flow to the firm and to equity, and they
    differ in what is done with it rather than in how it is built. A sector that blocks one
    and permits the other — and a company blocked from FCFF because enterprise value is
    meaningless for it may well be valued on FCFE — should not be blocked from forecasting.

    Raises:
        ModelNotPermittedError: If the mandate is for something that is not a discounted cash
            flow at all.
    """
    _require_cash_flow_mandate(mandate)
    inputs.validate()

    years: list[ForecastYear] = []
    revenue = inputs.base_revenue
    working_capital = inputs.opening_working_capital

    for year in range(1, inputs.years + 1):
        revenue = projected_revenue(
            context, prior_revenue=revenue, growth=inputs.revenue_growth.at(year)
        )
        ebit = projected_ebit(context, revenue=revenue, margin=inputs.ebit_margin.at(year))
        nopat = nopat_from_ebit(context, ebit=ebit, tax_rate=inputs.tax_rate)
        depreciation = projected_depreciation(
            context, revenue=revenue, intensity=inputs.depreciation_intensity.at(year)
        )
        capex = projected_capex(context, revenue=revenue, intensity=inputs.capex_intensity.at(year))
        closing_working_capital = projected_working_capital(
            context, revenue=revenue, intensity=inputs.working_capital_intensity.at(year)
        )
        movement = change_in_working_capital(
            context, opening=working_capital, closing=closing_working_capital
        )
        ebitda = forecast_ebitda(context, ebit=ebit, depreciation=depreciation)
        flow = free_cash_flow(
            context,
            nopat=nopat,
            depreciation=depreciation,
            capex=capex,
            working_capital_change=movement,
        )
        factor = discount_factor(context, wacc=inputs.wacc, year=year)
        discounted = present_value(context, amount=flow, factor=factor)

        years.append(
            ForecastYear(
                year=year,
                revenue=revenue,
                ebit=ebit,
                nopat=nopat,
                depreciation=depreciation,
                capex=capex,
                working_capital=closing_working_capital,
                change_in_working_capital=movement,
                ebitda=ebitda,
                free_cash_flow=flow,
                discount_factor=factor,
                present_value=discounted,
            )
        )
        working_capital = closing_working_capital

    return tuple(years)


# What a sensitivity cell is recorded as, so it is never mistaken for a scenario. The bank
# model uses the same word for the same reason.
SENSITIVITY_CASE: Final = "sensitivity"

# What a lever one of the report's two cases turns on is recorded as (ADR 0135): the base
# case with one input moved to a value the record already holds, struck so the case can print
# what it would give.
ARGUED_CASE: Final = "argued"

# The cases that perturb the base case rather than state it. Neither is an answer the report
# gives nor a scenario it compares against, so every reader of the base case or of a scenario
# passes both by; the rows beneath them are set aside by lineage
# (:func:`aer.services.calculations.perturbation_only`).
PERTURBATION_CASES: Final[frozenset[str]] = frozenset({SENSITIVITY_CASE, ARGUED_CASE})


def discounted_cash_flow(
    context: CalculationContext, inputs: DcfInputs, *, mandate: ValuationMandate, case: str = "base"
) -> DcfResult:
    """The whole valuation, both terminal methods, with every step recorded.

    ``case`` names the scenario this valuation prices — ``"base"`` unless a scenario run
    says otherwise — and is stamped on the outcome calculations exactly as ``method`` is,
    so the ledger can be read back per case. See :func:`enterprise_value`.

    ``mandate`` is the sector block, and it is a required argument rather than a check
    performed inside. A :class:`~aer.core.sectors.ValuationMandate` for
    ``DCF_FCFF`` cannot be constructed for a company classified as a bank, an insurer, a REIT
    or a pre-revenue biotech, so there is no value a caller could pass that would get a
    discounted cash flow out of this function for one of them.

    Raises:
        ModelNotPermittedError: If the mandate is for a different model.
        CalculationError: From any of the guards above. A discounted cash flow that cannot be
            computed correctly is not computed at all.
    """
    _require_fcff_mandate(mandate)
    years = project(context, inputs, mandate=mandate)
    final = years[-1]

    gordon_value = gordon_terminal_value(
        context,
        final_cash_flow=final.free_cash_flow,
        wacc=inputs.wacc,
        terminal_growth=inputs.terminal_growth,
    )
    exit_value = exit_multiple_terminal_value(
        context, terminal_ebitda=final.ebitda, multiple=inputs.exit_multiple
    )

    gordon = _outcome(
        context,
        inputs,
        years=years,
        method=TerminalMethod.GORDON_GROWTH,
        terminal=gordon_value,
        final=final,
        case=case,
    )
    exit_outcome = _outcome(
        context,
        inputs,
        years=years,
        method=TerminalMethod.EXIT_MULTIPLE,
        terminal=exit_value,
        final=final,
        case=case,
    )

    # Struck before the caveats because they branch on it, and struck through the ledger
    # because the report states it. A per-share figure at or below zero leaves the pair with
    # no ratio — that case carries the negative-equity caveat and nothing from here — and it
    # is skipped by asking rather than by catching: `UnitMismatchError` is a
    # `CalculationError`, so a `suppress` around this call would silently decline the one
    # comparison invariant 5 says must raise.
    lower = min(gordon.value_per_share.value, exit_outcome.value_per_share.value)
    gap = (
        method_disagreement(
            context,
            gordon_per_share=gordon.value_per_share,
            exit_multiple_per_share=exit_outcome.value_per_share,
            case=case,
        )
        if lower > 0
        else None
    )

    return DcfResult(
        years=years,
        gordon=gordon,
        exit_multiple=exit_outcome,
        caveats=_caveats(inputs, gordon=gordon, exit_outcome=exit_outcome, gap=gap),
        method_disagreement=gap,
    )


def _outcome(
    context: CalculationContext,
    inputs: DcfInputs,
    *,
    years: tuple[ForecastYear, ...],
    method: TerminalMethod,
    terminal: Quantity,
    final: ForecastYear,
    case: str,
) -> TerminalOutcome:
    """Carry one terminal value through to a per-share figure."""
    discounted_terminal = present_value(context, amount=terminal, factor=final.discount_factor)
    total = enterprise_value(
        context,
        discounted_flows=[year.present_value for year in years],
        discounted_terminal_value=discounted_terminal,
        method=method,
        case=case,
    )
    share = terminal_value_share(
        context,
        discounted_terminal_value=discounted_terminal,
        enterprise_value=total,
        method=method,
        case=case,
    )
    equity = equity_value(
        context,
        enterprise_value=total,
        net_debt=inputs.net_debt,
        adjustments=[item.amount for item in inputs.non_operating],
        method=method,
        case=case,
    )
    per_share = value_per_share(
        context, equity_value=equity, shares=inputs.shares_outstanding, method=method, case=case
    )

    # Each method reports the *other* one's parameter. Reporting its own would restate an
    # input as though it were a finding.
    #
    # Both carry `case` for the reason `enterprise_value` does: a grid strikes them once per
    # cell, and until the implied growth rate became a row a reader sees it was only the
    # ledger's order that said which of fifty was the base case.
    multiple = (
        implied_exit_multiple(
            context, terminal_value=terminal, terminal_ebitda=final.ebitda, case=case
        )
        if method is TerminalMethod.GORDON_GROWTH
        else None
    )
    growth = (
        implied_terminal_growth(
            context,
            terminal_value=terminal,
            final_cash_flow=final.free_cash_flow,
            wacc=inputs.wacc,
            case=case,
        )
        if method is TerminalMethod.EXIT_MULTIPLE
        else None
    )

    return TerminalOutcome(
        method=method,
        terminal_value=terminal,
        discounted_terminal_value=discounted_terminal,
        enterprise_value=total,
        terminal_share=share,
        equity_value=equity,
        value_per_share=per_share,
        implied_exit_multiple=multiple,
        implied_terminal_growth=growth,
    )


def _caveats(
    inputs: DcfInputs,
    *,
    gordon: TerminalOutcome,
    exit_outcome: TerminalOutcome,
    gap: Quantity | None,
) -> tuple[str, ...]:
    """What a reader has to be told about this particular valuation.

    **The method gap is banded rather than thresholded**, and that is the correction the
    September round forced. One threshold meant a pair 26% apart and a pair 226% apart were
    told to the reader in the same sentence — a sentence which says the distance between
    them is "the honest width of the answer". At 26% that is true and worth saying. At 226%
    it is the platform reassuring a reader about a contradiction, which is the one thing a
    caveat must never do.
    """
    caveats: list[str] = []

    if inputs.wacc.value - inputs.terminal_growth.value < MIN_TERMINAL_SPREAD:
        caveats.append(NARROW_SPREAD_CAVEAT)

    if any(
        outcome.terminal_share.value > HIGH_TERMINAL_SHARE for outcome in (gordon, exit_outcome)
    ):
        caveats.append(HIGH_TERMINAL_SHARE_CAVEAT)

    if gap is not None and gap.value > METHOD_CONTRADICTION:
        caveats.append(METHOD_CONTRADICTION_CAVEAT)
    elif gap is not None and gap.value > METHOD_DISAGREEMENT:
        caveats.append(METHOD_DISAGREEMENT_CAVEAT)

    if any(outcome.equity_value.value < 0 for outcome in (gordon, exit_outcome)):
        caveats.append(NEGATIVE_EQUITY_CAVEAT)

    return tuple(caveats)


# -- The sensitivity grid --------------------------------------------------------------------


VARIABLE_FIELDS: Final[frozenset[str]] = frozenset({"wacc", "terminal_growth", "exit_multiple"})
"""Which inputs a grid may vary.

The three scalars, and deliberately not the driver paths. A grid axis has to be one number
with an ordering; varying "revenue growth" means varying five numbers at once, and a reader
looking at the axis label would have no way to know which.
"""


@dataclass(frozen=True, slots=True)
class GridAxis:
    """One axis of a sensitivity grid: which input varies, and over what values."""

    field: str
    values: tuple[Quantity, ...]

    def __post_init__(self) -> None:
        if self.field not in VARIABLE_FIELDS:
            message = (
                f"{self.field!r} is not an input a sensitivity grid may vary. Available: "
                f"{', '.join(sorted(VARIABLE_FIELDS))}. A driver path is five numbers, and an "
                "axis labelled with one of them would be a label for something else."
            )
            raise CalculationError(message, context={"field": self.field})

        if not MIN_AXIS_POINTS <= len(self.values) <= MAX_AXIS_POINTS:
            message = (
                f"A grid axis over {self.field} has {len(self.values)} values, outside "
                f"{MIN_AXIS_POINTS} to "
                f"{MAX_AXIS_POINTS}. One value is not a sensitivity; beyond the ceiling each "
                "extra column is a complete valuation with a complete lineage to store."
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

        Every cell has one: the result is a traced calculation's output, so the reference is
        read off the quantity rather than tracked alongside it. See
        :mod:`aer.db.models.sensitivity` on why a grid is the easiest thing in a valuation to
        fabricate.
        """
        if self.result.source is None:  # pragma: no cover - traced output always has one
            message = "A grid cell's result carries no source, so it cannot be recorded."
            raise CalculationError(message, context={"value": str(self.result.value)})
        return uuid.UUID(self.result.source.identifier)


@dataclass(frozen=True, slots=True)
class SensitivityGrid:
    """A rectangular grid of complete valuations."""

    row_axis: GridAxis
    column_axis: GridAxis
    method: TerminalMethod
    measure: GridMeasure
    cells: tuple[GridCell, ...]

    @property
    def output_name(self) -> str:
        return f"{self.measure.value}_{self.method.value}"

    @property
    def output_unit(self) -> str:
        return self.cells[0].result.unit.symbol


def sensitivity_grid(
    context: CalculationContext,
    inputs: DcfInputs,
    *,
    rows: GridAxis,
    columns: GridAxis,
    method: TerminalMethod,
    measure: GridMeasure,
    mandate: ValuationMandate,
) -> SensitivityGrid:
    """Run a complete valuation at every point of a two-dimensional grid.

    **Every cell is a whole discounted cash flow**, not an interpolation between the corners
    and not a linear approximation around the base case. A grid is the easiest figure in a
    valuation to fabricate — eighty-one numbers that look like eighty-one pieces of analysis —
    and nothing in the presentation distinguishes a computed grid from an invented one. So
    each cell computes, and each cell's figure carries the calculation that produced it.

    Raises:
        CalculationError: If both axes vary the same input, which would produce a grid whose
            only meaningful cells are on the diagonal.
    """
    if rows.field == columns.field:
        message = (
            f"Both axes vary {rows.field}. Only the diagonal of such a grid would mean "
            "anything, and every other cell would contradict it."
        )
        raise CalculationError(message, context={"field": rows.field})

    cells: list[GridCell] = []
    for row_value in rows.values:
        for column_value in columns.values:
            # Typed loosely because the field name is data. `replace` still refuses a
            # name `DcfInputs` does not have, so a typo is an error rather than a
            # silently ignored axis.
            overrides: dict[str, Any] = {
                rows.field: row_value,
                columns.field: column_value,
            }
            varied = replace(inputs, **overrides)
            # Not "base": the front page and the football field read the base band off
            # the latest row tagged base, and nine grid corners recorded under that tag
            # were what they found (readiness audit 2026-09).
            result = discounted_cash_flow(context, varied, mandate=mandate, case=SENSITIVITY_CASE)
            outcome = result.outcome(method)
            cells.append(
                GridCell(
                    row_value=row_value,
                    column_value=column_value,
                    result=_measure_of(outcome, measure),
                )
            )

    return SensitivityGrid(
        row_axis=rows,
        column_axis=columns,
        method=method,
        measure=measure,
        cells=tuple(cells),
    )


def _measure_of(outcome: TerminalOutcome, measure: GridMeasure) -> Quantity:
    if measure is GridMeasure.VALUE_PER_SHARE:
        return outcome.value_per_share
    if measure is GridMeasure.EQUITY_VALUE:
        return outcome.equity_value
    return outcome.enterprise_value


# -- Guards ----------------------------------------------------------------------------------


_CASH_FLOW_MODELS: Final = (ValuationModel.DCF_FCFF, ValuationModel.DCF_FCFE)


def _require_fcff_mandate(mandate: ValuationMandate) -> None:
    """Refuse a mandate granted for some other model.

    The sector rules are enforced when the mandate is *constructed*; this is the second half,
    and it is about identity rather than permission. A mandate for comparable multiples is a
    perfectly valid mandate — it is simply not permission to run this.

    Raises:
        ModelNotPermittedError: If the mandate is not for free cash flow to the firm.
    """
    if mandate.model is ValuationModel.DCF_FCFF:
        return
    message = (
        f"This is a discounted free cash flow to the firm, and the mandate is for "
        f"{mandate.model.value}. A mandate permits one model; running a second under it "
        "would make the permission mean whatever the caller wanted it to."
    )
    raise ModelNotPermittedError(
        message, context={"model": mandate.model.value, "subject": mandate.subject}
    )


def _require_cash_flow_mandate(mandate: ValuationMandate) -> None:
    """Refuse a mandate that is not for a discounted cash flow of any kind.

    Wider than :func:`_require_fcff_mandate` on purpose. A forecast is the raw material of
    both free-cash-flow models, so building one under an FCFE mandate is legitimate; building
    one under a comparable-multiples mandate is not, because comparables do not forecast.

    Raises:
        ModelNotPermittedError: If the mandate is for neither free-cash-flow model.
    """
    if mandate.model in _CASH_FLOW_MODELS:
        return
    message = (
        f"A forecast is the raw material of a discounted cash flow, and the mandate is for "
        f"{mandate.model.value}, which does not forecast. A mandate permits one model; "
        "building the inputs to a second under it would make the permission mean whatever "
        "the caller wanted it to."
    )
    raise ModelNotPermittedError(
        message, context={"model": mandate.model.value, "subject": mandate.subject}
    )


def _require_case(case: str) -> None:
    """Refuse a blank case label.

    A blank case is a row nobody can attribute to a scenario, which is the exact gap the
    parameter exists to close. Validated rather than defaulted here: the default lives on
    the signature, so an explicit empty string is a caller error, not a base case.
    """
    if not case.strip():
        message = (
            "The case label is blank; the ledger could not say which scenario this valuation "
            "prices."
        )
        raise CalculationError(message, context={"case": case})


def _require_method(value: object) -> None:
    """Refuse anything but a :class:`TerminalMethod`.

    The annotation covers every caller mypy checks; this catches the ones it does not. A
    free-text method would be recorded verbatim as the terminal approach a valuation used,
    which reads as a specification and is a string.
    """
    if isinstance(value, TerminalMethod):
        return

    message = (
        f"method is {value!r}, which is not a TerminalMethod. This calculation runs once per "
        "terminal method, so the record has to say which in a form code can read back."
    )
    raise CalculationError(message, context={"method": repr(value)})


def _require_money(value: Quantity, *, name: str) -> None:
    if value.unit.currencies:
        return
    message = (
        f"{name} is in {value.unit.symbol}, which is not a currency amount. A cash flow "
        "without a currency is a number somebody will add to a different one."
    )
    raise UnitMismatchError(message, context={"input": name, "unit": value.unit.symbol})


def _require_dimensionless(value: Quantity, *, name: str) -> None:
    if value.unit.is_dimensionless:
        return
    message = (
        f"{name} is in {value.unit.symbol}. A driver is a pure number — a margin denominated "
        "in dollars is a category error rather than a large margin."
    )
    raise UnitMismatchError(message, context={"input": name, "unit": value.unit.symbol})


def _require_rate(value: Quantity, *, name: str, floor: Decimal) -> None:
    """The same range check :mod:`aer.calc.wacc` applies, for the same reason.

    A discount rate of 8 rather than 0.08 discounts the first year to nothing and the answer
    to a rounding error, and both are dimensionless so no unit catches it.
    """
    _require_dimensionless(value, name=name)

    if floor <= value.value <= MAX_RATE:
        return

    hint = ""
    if value.value > MAX_RATE:
        hint = (
            " A figure this size is usually a percentage that was never divided by 100 — see "
            "aer.calc.wacc.rate_from_percent."
        )
    message = (
        f"{name} is {value.value}, outside the range {floor} to {MAX_RATE} that a rate "
        f"expressed as a fraction can take.{hint}"
    )
    raise CalculationError(
        message, context={"input": name, "value": str(value.value), "floor": str(floor)}
    )


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
            f"Year {year} is not a forecast period. Discounting starts at year one; year "
            "zero is today and needs no factor."
        )
        raise CalculationError(message, context={"year": str(year)})
