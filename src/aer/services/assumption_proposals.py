"""Where a forecast's starting numbers come from, when the filings can answer.

**The valuation never ran because nobody could face the form.** `inputs_from` refuses
without a confirmed assumption for every driver and scalar — correctly, since a terminal
growth rate this platform picked would be its opinion presented as the operator's — and
nothing proposed any, so the assumptions page showed an empty list and gap B2 stayed open
from the first live run onward.

Six of the eight a discounted cash flow needs are not opinions. Revenue growth, EBIT margin,
capex intensity, depreciation intensity, working-capital intensity and the effective tax
rate all have a history in the filings the run already acquired, and "the compound rate the
filings show" is arithmetic with a stated basis rather than a judgement. This module derives
those six. ADR 0046 covers the other two, which are.

**Every proposal carries its derivation in words, and that is not decoration.** A gate is a
control only if the operator can interrogate what they are approving, so each proposal says
which measure it used, over which periods, and what the inputs were. "Revenue growth 11.4%"
is a number to click past; "the compound annual rate from FY2021 to FY2024, over three
periods" is a claim somebody can disagree with.

**Flat, not per-year.** A trailing average *is* one number; emitting it as five identical
per-year rows would be noise dressed as detail. `aer.services.valuation._path_for` prefers a
per-year path when one is confirmed, so an operator wanting a fade enters the years they
want and the flat proposal steps aside without a fight.

**Nothing here is confirmed, and nothing here can be.** `propose` returns an unconfirmed
assumption whatever its caller says. This module puts numbers in front of a person; the
person decides.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import CALC_CONTEXT, Quantity
from aer.db.models import Assumption
from aer.services.analysis import AnalysisOutcome, PeriodAnalysis
from aer.services.assumptions import propose

__all__ = [
    "DERIVED_NAMES",
    "PROPOSED_BY",
    "DerivedAssumption",
    "ProposalOutcome",
    "derive_assumptions",
    "propose_derived",
]

_log = structlog.get_logger("aer.services.assumption_proposals")

# Recorded on every row this module creates, so "which of these did a person choose?" is a
# query rather than an inspection. `aer.services.prices` uses the same convention.
PROPOSED_BY: Final = "aer.services.assumption_proposals"

# Every driver and scalar this module can derive. `terminal_growth` and `exit_multiple` are
# deliberately absent: no series answers either, and ADR 0046 is about the role that does.
DERIVED_NAMES: Final[tuple[str, ...]] = (
    "revenue_growth",
    "ebit_margin",
    "capex_intensity",
    "depreciation_intensity",
    "working_capital_intensity",
    "tax_rate",
)

# A dimensionless assumption. Every one here is a ratio of two currency amounts, so the
# units cancel and the stored unit says so.
_RATIO: Final = "pure"

# The fewest periods a trailing average is worth calling one. Two, because one period is not
# an average and stating it as though it were would overclaim.
_MIN_PERIODS_FOR_AN_AVERAGE: Final = 2

# The fewest periods a growth rate needs. Two endpoints, so at least two.
_MIN_PERIODS_FOR_GROWTH: Final = 2


@dataclass(frozen=True, slots=True)
class DerivedAssumption:
    """One proposal, and everything a reviewer needs to disagree with it."""

    name: str
    value: Decimal
    unit: str
    justification: str

    # The period ends the derivation used, oldest first. Carried separately from the prose
    # so the gate payload can render them without parsing a sentence.
    periods: tuple[date, ...]

    @property
    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": str(self.value),
            "unit": self.unit,
            "justification": self.justification,
            "periods": [period.isoformat() for period in self.periods],
        }


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    """What could be derived, and what could not — with the reason.

    ``skipped`` is not an error list. A company with one filed year has no growth rate and
    no average; a filer that reports no capital expenditure line has no capex intensity.
    Both are ordinary, and both have to be *said*: an assumption missing from the page with
    no explanation looks like a defect, and the operator cannot tell whether to wait for it
    or type it.
    """

    derived: tuple[DerivedAssumption, ...] = ()
    skipped: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "derived": [item.as_dict for item in self.derived],
            "skipped": list(self.skipped),
        }


def derive_assumptions(analysis: AnalysisOutcome) -> ProposalOutcome:
    """Everything the run's own history supports, as proposals with their derivations.

    Pure: it reads an analysis and returns values. Persisting them is
    :func:`propose_derived`, so the arithmetic can be tested without a database and a
    caller cannot accidentally write rows by asking what the numbers would be.
    """
    periods = _usable(analysis)
    if len(periods) < _MIN_PERIODS_FOR_GROWTH:
        return ProposalOutcome(
            skipped=(
                f"Only {len(periods)} annual period(s) could be assembled, and a trend needs "
                "at least two. Every driver has to be entered by hand for this run.",
            ),
        )

    derived: list[DerivedAssumption] = []
    skipped: list[str] = []

    for outcome in (
        _revenue_growth(periods),
        _ratio_of("ebit_margin", periods, top="operating_income", bottom="revenue"),
        _ratio_of("capex_intensity", periods, top="capital_expenditure", bottom="revenue"),
        _ratio_of(
            "depreciation_intensity",
            periods,
            top="depreciation_and_amortisation",
            bottom="revenue",
        ),
        _working_capital_intensity(periods),
        _ratio_of("tax_rate", periods, top="income_tax_expense", bottom="pre_tax_income"),
    ):
        if isinstance(outcome, DerivedAssumption):
            derived.append(outcome)
        else:
            skipped.append(outcome)

    return ProposalOutcome(derived=tuple(derived), skipped=tuple(skipped))


async def propose_derived(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    analysis: AnalysisOutcome,
    job_id: uuid.UUID | None = None,
) -> tuple[ProposalOutcome, tuple[Assumption, ...]]:
    """Derive and persist. Returns what was derived and the rows written.

    Each row is unconfirmed, because :func:`aer.services.assumptions.propose` makes them so
    whatever a caller passes. An operator confirms them at the assumptions gate, and until
    they do, `as_quantity` refuses every one.
    """
    outcome = derive_assumptions(analysis)

    rows: list[Assumption] = []
    for item in outcome.derived:
        rows.append(
            await propose(
                session,
                request_id=request_id,
                name=item.name,
                value=item.value,
                unit=item.unit,
                justification=item.justification,
                proposed_by=PROPOSED_BY,
                job_id=job_id,
            )
        )

    _log.info(
        "assumptions.derived",
        request_id=str(request_id),
        derived=[item.name for item in outcome.derived],
        skipped=len(outcome.skipped),
    )
    return outcome, tuple(rows)


# -- The derivations -----------------------------------------------------------------------
#
# Each returns a `DerivedAssumption` or a sentence saying why it could not. A sentence rather
# than `None`: the caller has to put something on the page, and "capex intensity is missing"
# with no reason is the state this whole module exists to end.


def _usable(analysis: AnalysisOutcome) -> tuple[PeriodAnalysis, ...]:
    """The analysed periods, oldest first.

    `AnalysisOutcome.periods` is newest-first because that is what a reader wants; a growth
    rate wants the other order, and reversing it once here beats reversing it in each
    derivation and getting one of them backwards.
    """
    return tuple(reversed(analysis.periods))


def _revenue_growth(periods: Sequence[PeriodAnalysis]) -> DerivedAssumption | str:
    """The compound annual rate between the first and last period's revenue.

    Compound rather than the mean of the year-on-year rates: the mean of +50% and -40% is
    +5%, and the business is smaller than it started. A reader shown "average growth 5%"
    for a company that shrank would be reading a true number that says something false.
    """
    first, last = periods[0], periods[-1]
    start = _line(first, "revenue")
    end = _line(last, "revenue")

    if start is None or end is None:
        return (
            "Revenue growth could not be derived: revenue is missing from the earliest or "
            "the latest period assembled."
        )
    if start.value <= 0:
        return (
            f"Revenue growth could not be derived: revenue in the base period "
            f"({first.period_end.isoformat()}) is {start.value}, and a growth rate from a "
            "non-positive base is not a rate."
        )

    spans = len(periods) - 1
    try:
        with localcontext(CALC_CONTEXT):
            ratio = end.value / start.value
            rate = ratio ** (Decimal(1) / Decimal(spans)) - Decimal(1)
    except (InvalidOperation, ZeroDivisionError, ValueError):
        return "Revenue growth could not be derived: the compound rate is not computable."

    return DerivedAssumption(
        name="revenue_growth",
        value=_rounded(rate),
        unit=_RATIO,
        justification=(
            f"The compound annual growth rate of revenue from "
            f"{first.period_end.isoformat()} to {last.period_end.isoformat()}, over "
            f"{spans} year(s): revenue moved from {start.value:,.0f} to {end.value:,.0f}. "
            "Held flat across the forecast, because a trailing rate is one number and "
            "projecting a fade is a judgement this derivation does not make."
        ),
        periods=tuple(period.period_end for period in periods),
    )


def _ratio_of(
    name: str, periods: Sequence[PeriodAnalysis], *, top: str, bottom: str
) -> DerivedAssumption | str:
    """The mean of one line over another, across every period that has both.

    The mean of the ratios rather than the ratio of the sums. A company whose revenue
    doubled would otherwise have its recent margin weighted at twice the earlier one purely
    by size, which is a defensible choice and not the one somebody reading "average margin"
    expects.
    """
    observed: list[tuple[date, Decimal]] = []
    for period in periods:
        numerator = _line(period, top)
        denominator = _line(period, bottom)
        if numerator is None or denominator is None or denominator.value <= 0:
            continue
        with localcontext(CALC_CONTEXT):
            observed.append((period.period_end, numerator.value / denominator.value))

    if len(observed) < _MIN_PERIODS_FOR_AN_AVERAGE:
        return (
            f"{name.replace('_', ' ').capitalize()} could not be derived: "
            f"{top.replace('_', ' ')} over {bottom.replace('_', ' ')} is available for "
            f"{len(observed)} period(s), and an average needs at least "
            f"{_MIN_PERIODS_FOR_AN_AVERAGE}."
        )

    values = [value for _, value in observed]
    if any(value < 0 for value in values):
        # A negative intensity or margin is real and is not a starting point. Proposing one
        # would carry a loss-making year into a perpetuity forecast as though it were normal.
        return (
            f"{name.replace('_', ' ').capitalize()} could not be derived: at least one "
            "period is negative, so a trailing average would project a loss-making year "
            "forward as though it were the normal state. Enter it by hand."
        )

    with localcontext(CALC_CONTEXT):
        mean = sum(values, Decimal(0)) / Decimal(len(values))

    covered = ", ".join(period.isoformat() for period, _ in observed)
    return DerivedAssumption(
        name=name,
        value=_rounded(mean),
        unit=_RATIO,
        justification=(
            f"The mean of {top.replace('_', ' ')} over {bottom.replace('_', ' ')} across "
            f"{len(observed)} period(s) ending {covered}. The individual observations were "
            f"{', '.join(f'{value:.4f}' for _, value in observed)}."
        ),
        periods=tuple(period for period, _ in observed),
    )


def _working_capital_intensity(periods: Sequence[PeriodAnalysis]) -> DerivedAssumption | str:
    """Net working capital over revenue, averaged.

    Computed from current assets less current liabilities rather than read off a line,
    because no filer reports "working capital" as a tagged concept — it is a subtraction
    everybody does and nobody files.
    """
    observed: list[tuple[date, Decimal]] = []
    for period in periods:
        assets = _line(period, "current_assets")
        liabilities = _line(period, "current_liabilities")
        revenue = _line(period, "revenue")
        if assets is None or liabilities is None or revenue is None or revenue.value <= 0:
            continue
        with localcontext(CALC_CONTEXT):
            observed.append((period.period_end, (assets.value - liabilities.value) / revenue.value))

    if len(observed) < _MIN_PERIODS_FOR_AN_AVERAGE:
        return (
            "Working capital intensity could not be derived: current assets, current "
            f"liabilities and revenue are available together for {len(observed)} period(s), "
            f"and an average needs at least {_MIN_PERIODS_FOR_AN_AVERAGE}."
        )

    with localcontext(CALC_CONTEXT):
        mean = sum((value for _, value in observed), Decimal(0)) / Decimal(len(observed))

    covered = ", ".join(period.isoformat() for period, _ in observed)
    return DerivedAssumption(
        name="working_capital_intensity",
        value=_rounded(mean),
        unit=_RATIO,
        justification=(
            "The mean of net working capital — current assets less current liabilities — "
            f"over revenue, across {len(observed)} period(s) ending {covered}. A negative "
            "figure is normal for a business paid before it pays, and is proposed as it "
            "stands rather than floored at zero."
        ),
        periods=tuple(period for period, _ in observed),
    )


def _line(period: PeriodAnalysis, concept: str) -> Quantity | None:
    return period.statements.get(concept)


def _rounded(value: Decimal) -> Decimal:
    """Six decimal places.

    Enough for a rate quoted to four figures of a percent, and short of the thirty-odd the
    calculation context carries — a proposal shown to a person as 0.114237881922... invites
    them to believe a precision the underlying filings do not have.
    """
    return value.quantize(Decimal("0.000001"))
