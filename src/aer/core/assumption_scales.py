"""What an assumption is measured in, and what a plausible one looks like — gap B14.

Every value a discounted cash flow rests on that no filing answers is typed in by a person,
and until this module existed the box taking it had no vocabulary and no scale. Two failures
followed from that, and the second is the dangerous one.

**A unit could be readable and still be wrong.**
:func:`aer.services.assumptions._require_unit` has always refused a string the algebra
cannot parse, so ``%`` never reached the database — but ``USD`` parses perfectly, and a tax
rate stated in dollars is nonsense that survives entry and fails much later, inside
arithmetic that cannot say which of its inputs was the mistake. Knowing the *right* unit per
name is what closes that, and it is also what lets the form default the field instead of
asking.

**The scale had no check at all.** Every rate and ratio here is a *decimal fraction*: a
4.5% risk-free rate is ``0.045``. Enter ``4.5`` and the unit is still ``pure``, the number
is still well formed, and the forecast discounts at 450% — a silently absurd answer with
nothing anywhere objecting. The ranges below are wide enough that a real figure passes and
narrow enough that a factor-of-a-hundred slip does not.

**Refused, not corrected.** The remedy for an implausible number is the operator looking at
it again, exactly as ADR 0046 refuses a model's out-of-band proposal rather than clamping
it: a value quietly moved into range is a number nobody chose standing where one somebody
chose should be. ``accepted_anyway`` exists for the case where the operator means it —
hyperinflation, a distressed year — because a check an operator cannot get past is the
"gate nobody can clear" failure in a smaller costume.

Kept in ``core`` and free of service imports on purpose: :mod:`aer.services.assumptions` is
low in the import graph and :mod:`aer.services.assumption_gate` imports *it*, so a table
living in the gate could not be reached from the place that needs it. The names are
therefore written out here and pinned to :data:`aer.services.assumption_gate.REQUIRED_NAMES`
by test, which is this repository's usual answer to a vocabulary that would otherwise drift.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

__all__ = [
    "EXPECTED_UNIT",
    "PLAUSIBLE_RANGE",
    "UNIT_CHOICES",
    "expected_unit",
    "scale_complaint",
    "unit_complaint",
]

# The unit vocabulary an assumption may be entered in. Deliberately smaller than
# `Unit.parse` accepts: every assumption a forecast needs is a rate, a ratio or a
# coefficient, so the only sensible answer is "pure", and offering an operator the whole
# algebra would be offering them ways to be wrong.
_PURE: Final = "pure"

UNIT_CHOICES: Final[tuple[str, ...]] = (_PURE, "USD", "GBP", "EUR", "shares", "USD/shares")
"""What the form offers, in the order it offers them.

``pure`` leads because every assumption a forecast needs is dimensionless. The rest are
here for a name this module does not know — an operator may store something the valuation
does not read — rather than as an invitation to state a tax rate in dollars, which
:func:`unit_complaint` refuses anyway. Every entry parses under
:meth:`aer.calc.units.Unit.parse`, and a test holds them to it.
"""

EXPECTED_UNIT: Final[dict[str, str]] = {
    "revenue_growth": _PURE,
    "ebit_margin": _PURE,
    "capex_intensity": _PURE,
    "depreciation_intensity": _PURE,
    "working_capital_intensity": _PURE,
    "tax_rate": _PURE,
    "terminal_growth": _PURE,
    "exit_multiple": _PURE,
    "risk_free_rate": _PURE,
    "beta": _PURE,
    "equity_risk_premium": _PURE,
    # Conditionally required rather than always: the gate demands it only when the filings
    # carry debt and no interest expense to derive the rate from (report-quality R13).
    "cost_of_debt": _PURE,
    # The residual-income drivers, for a bank or an insurer (ADR 0070). Dimensionless like
    # the rest: a return on equity is earnings over book and a payout is dividends over
    # earnings, so the currencies cancel in both.
    "return_on_equity": _PURE,
    "payout_ratio": _PURE,
}
"""The unit each assumption is measured in. All of them dimensionless, which is why the
form can default the field rather than asking."""

# Generous on purpose. These are not house views on what a good assumption looks like —
# ADR 0046's bounds are that, for the two the model proposes — but a floor and a ceiling
# outside which a *typing* mistake is far likelier than a real figure. A margin can be
# deeply negative and a growth rate can double; neither reaches these.
PLAUSIBLE_RANGE: Final[dict[str, tuple[Decimal, Decimal]]] = {
    "revenue_growth": (Decimal("-1"), Decimal("3")),
    "ebit_margin": (Decimal("-5"), Decimal("1")),
    "capex_intensity": (Decimal("-1"), Decimal("3")),
    "depreciation_intensity": (Decimal("-1"), Decimal("3")),
    "working_capital_intensity": (Decimal("-3"), Decimal("3")),
    "tax_rate": (Decimal("-1"), Decimal("1")),
    "terminal_growth": (Decimal("-0.5"), Decimal("0.5")),
    # The one that is not a fraction: an exit multiple of 12 means twelve times, and an
    # operator entering 0.12 has made the opposite of the usual mistake.
    "exit_multiple": (Decimal("0"), Decimal("100")),
    "risk_free_rate": (Decimal("-0.1"), Decimal("0.5")),
    "beta": (Decimal("-10"), Decimal("10")),
    "equity_risk_premium": (Decimal("-0.1"), Decimal("0.5")),
    # The same band as the risk-free rate: a pre-tax borrowing cost above 50% is distressed
    # beyond what a going-concern forecast should rest on, and 4.5 for 4.5% is the mistake
    # the band exists to catch.
    "cost_of_debt": (Decimal("-0.1"), Decimal("0.5")),
    # A bank can lose more than its book in a year, so the floor is generous; a *sustained*
    # return above book is a typing mistake rather than a business.
    "return_on_equity": (Decimal("-2"), Decimal("1")),
    # Nil to one is exactly what `aer.calc.residual_income.book_value_roll_forward` accepts,
    # and it refuses anything outside. Refused here too, at entry, so the operator learns at
    # the box rather than inside arithmetic that cannot say which input was wrong.
    "payout_ratio": (Decimal("0"), Decimal("1")),
}
"""The band inside which a value is taken at face value, by name."""

# Said in the refusal, because the mistake it catches is nearly always this one and naming
# it turns a bounds error into an instruction.
_FRACTION_RULE: Final = (
    "Rates and ratios are decimal fractions here, so 4.5% is 0.045 rather than 4.5."
)


def expected_unit(name: str) -> str | None:
    """The unit this assumption is measured in, or ``None`` for a name with no entry.

    An unknown name is not an error here. Per-year driver paths (``revenue_growth_y1``)
    and anything an operator adds beyond the forecast's vocabulary are stored the same
    way; what this module does is *know better* about the eleven it knows about.
    """
    return EXPECTED_UNIT.get(name)


def unit_complaint(name: str, unit: str) -> str | None:
    """Why this unit is wrong for this assumption, or ``None`` if it is right.

    Checked at entry rather than at use. The alternative — and what happened — is a
    valuation that dies on ``UnitMismatchError`` in a step that did not make the mistake.
    """
    wanted = expected_unit(name)
    if wanted is None or unit.strip() == wanted:
        return None
    return f"{name} is measured in {wanted!r}, not {unit.strip() or 'nothing'!r}. {_FRACTION_RULE}"


def scale_complaint(name: str, value: Decimal) -> str | None:
    """Why this value looks like a typing mistake, or ``None`` if it is plausible.

    Returns prose rather than raising, so the caller decides what a complaint is worth: the
    service refuses on it, and the same sentence is what the form shows.
    """
    bounds = PLAUSIBLE_RANGE.get(name)
    if bounds is None:
        return None
    low, high = bounds
    if low <= value <= high:
        return None
    return (
        f"{value} is outside the plausible range for {name} ({low} to {high}). "
        f"{_FRACTION_RULE} Submit it again with the box ticked if you mean this figure."
    )
