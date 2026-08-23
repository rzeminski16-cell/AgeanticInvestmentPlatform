"""The run's bank valuation: confirmed assumptions and a filed book value in, a spread out.

The residual-income counterpart to :mod:`aer.services.valuation_run`, and the caller
:mod:`aer.calc.residual_income` was written without. ADR 0070 chose the model; this is the
layer that assembles its inputs from the run's own analysis, decomposes the discount rate
and drives both terminal treatments in one transaction.

**The discount rate is a cost of equity, and it is decomposed rather than supplied.** The
same CAPM chain the discounted cash flow uses — a confirmed risk-free rate, beta and premium,
each a row somebody agreed to — but stopping at :func:`aer.calc.wacc.cost_of_equity` rather
than blending in a cost of debt. Blending would charge a bank's funding twice: once in net
interest income, and again in the rate that discounts it.

**Both terminal treatments are run, and neither is chosen.** This follows
:mod:`aer.services.valuation_run`, which reports Gordon growth and an exit multiple side by
side and lets their disagreement be the finding. Here the disagreement is sharper, because
the two treatments are not two ways of estimating one quantity — they are opposite claims
about whether competition removes a bank's excess return. Presenting one alone would be
presenting a claim about banking as though it were arithmetic.

**No scenarios and no sensitivity grids.** :mod:`aer.services.valuation_run` builds both;
this deliberately does not, and the omission is stated rather than quiet. The two treatments
already bracket the answer more widely than a grid over the discount rate would, and a grid
whose axes were the return on equity and the cost of equity would mostly re-describe the
spread the model is already reporting. It is a real gap for a later task, not a thing that
was forgotten.

**Every refusal names what was missing.** A valuation that cannot run is an ordinary outcome
for a company whose filings are thin, and the report has to say which line was absent rather
than showing an empty page.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.calc.residual_income import (
    DRIVER_NAMES,
    DriverPath,
    ResidualIncomeInputs,
    ResidualIncomeResult,
    TerminalTreatment,
    residual_income_value,
)
from aer.calc.units import CalculationError, Quantity
from aer.calc.wacc import cost_of_equity
from aer.core.sectors import ValuationMandate
from aer.db.models import ResearchRequest
from aer.services.analysis import AnalysisOutcome, PeriodAnalysis
from aer.services.assumption_gate import (
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    RISK_FREE_ASSUMPTION,
)
from aer.services.assumptions import confirmed_values
from aer.services.calculations import new_context, persist_context
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation import MissingAssumptionError, driver_values
from aer.services.valuation_run import (
    ValuationNotPossibleError,
    latest_period,
    required_line,
    share_count,
)

__all__ = ["BankValuationOutcome", "value_the_bank"]

_log = structlog.get_logger("aer.services.residual_income_run")

_TERMINAL_GROWTH: Final = "terminal_growth"

_DISAGREEMENT_CAVEAT: Final = (
    "The two terminal treatments are opposite claims about competition, not two estimates "
    "of one number. Fading to nothing says a bank's excess return is competed away at the "
    "end of the forecast; perpetual growth says it never is. Both are shown because "
    "choosing between them is a judgement about banking, and presenting either alone would "
    "present that judgement as arithmetic."
)

_NO_SCENARIOS_CAVEAT: Final = (
    "No scenarios or sensitivity grids were run for this valuation. The two terminal "
    "treatments bracket the answer, but nothing here varies the return on equity or the "
    "cost of equity a step at a time."
)


@dataclass(frozen=True, slots=True)
class BankValuationOutcome:
    """What the residual-income valuation came to, or why it did not run."""

    ran: bool
    reason: str = ""
    cost_of_equity: Quantity | None = None
    faded: ResidualIncomeResult | None = None
    perpetual: ResidualIncomeResult | None = None
    # Why the perpetuity is absent, when it is. Empty whenever `perpetual` is present.
    perpetual_refusal: str = ""
    caveats: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The step's recorded output.

        ``model`` is first and is always present, because every surface reading this has to
        branch on it before it reads anything else — a per-share figure means a different
        thing here than it does under a discounted cash flow.
        """
        if not self.ran or self.faded is None or self.cost_of_equity is None:
            return {"valued": False, "model": "residual_income", "reason": self.reason}

        produced: dict[str, Any] = {
            "valued": True,
            "model": "residual_income",
            "cost_of_equity": str(self.cost_of_equity.value),
            "opening_book_value": str(self.faded.opening_book_value.value),
            "currency": self.faded.opening_book_value.unit.symbol,
            "fade_per_share": str(self.faded.value_per_share.value),
            "fade_premium_to_book": str(self.faded.premium_to_book.value),
            "years": len(self.faded.years),
            "caveats": list(self.caveats),
        }
        if self.perpetual is not None:
            produced["perpetual_per_share"] = str(self.perpetual.value_per_share.value)
            produced["perpetual_premium_to_book"] = str(self.perpetual.premium_to_book.value)
        else:
            # Stated rather than absent. A perpetuity is refused when the final forecast year
            # earns below the cost of equity, and "we did not show one" is a finding about
            # the bank rather than a hole in the page.
            produced["perpetual_refused"] = self.perpetual_refusal
        return produced


async def value_the_bank(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    analysis: AnalysisOutcome,
    mandate: ValuationMandate,
    years: int,
) -> BankValuationOutcome:
    """Value the equity on both terminal treatments, and store every calculation.

    Returns a :class:`BankValuationOutcome` rather than raising when the run simply cannot
    produce a forecast: a bank with one filed year or an unconfirmed beta is an ordinary
    state the report has to describe, not an error the workflow should die on. A genuine
    defect — a unit mismatch, a broken ledger — still raises.
    """
    latest = latest_period(analysis)
    if latest is None:
        return BankValuationOutcome(
            ran=False,
            reason=(
                "No annual period could be assembled from this company's filings, so there "
                "is no book value to start from."
            ),
        )

    values = await confirmed_values(session, request.id)
    ledger = new_context()

    try:
        equity_rate = _cost_of_equity(ledger, values)
        inputs = _inputs_from(
            values,
            latest=latest,
            cost_of_equity_rate=equity_rate,
            years=years,
        )
    except (MissingAssumptionError, ValuationNotPossibleError) as refusal:
        return BankValuationOutcome(ran=False, reason=str(refusal))

    faded = residual_income_value(
        ledger,
        _with_treatment(inputs, TerminalTreatment.FADE_TO_NOTHING),
        mandate=mandate,
    )

    perpetual: ResidualIncomeResult | None = None
    perpetual_refusal = ""
    try:
        perpetual = residual_income_value(
            ledger,
            _with_treatment(inputs, TerminalTreatment.PERPETUAL_GROWTH),
            mandate=mandate,
        )
    except CalculationError as refused:
        # The perpetuity refuses a final year earning below the cost of equity, and a growth
        # rate at or above it. Both are statements about this bank rather than failures, and
        # the fade result stands on its own — so the run keeps its valuation and records why
        # the second treatment is absent.
        perpetual_refusal = str(refused)

    await persist_context(session, ledger, job_id=job_id)

    caveats = (*faded.caveats, _DISAGREEMENT_CAVEAT, _NO_SCENARIOS_CAVEAT)
    if perpetual is not None:
        caveats = (*caveats, *(item for item in perpetual.caveats if item not in caveats))

    _log.info(
        "valuation.bank_completed",
        job_id=str(job_id),
        cost_of_equity=str(equity_rate.value),
        fade_per_share=str(faded.value_per_share.value),
        perpetual_refused=bool(perpetual_refusal),
    )
    return BankValuationOutcome(
        ran=True,
        cost_of_equity=equity_rate,
        faded=faded,
        perpetual=perpetual,
        caveats=caveats,
        perpetual_refusal=perpetual_refusal,
    )


def _cost_of_equity(ledger: CalculationContext, values: dict[str, Quantity]) -> Quantity:
    """CAPM, from the same three confirmed rows the discounted cash flow decomposes.

    Raises:
        MissingAssumptionError: If any of the three is unconfirmed. Named individually,
            because "the discount rate is missing" does not tell an operator which row to go
            and agree to.
    """
    missing = [
        name
        for name in (RISK_FREE_ASSUMPTION, BETA_ASSUMPTION, EQUITY_RISK_PREMIUM_ASSUMPTION)
        if name not in values
    ]
    if missing:
        message = (
            f"The cost of equity needs {', '.join(missing)}, and no confirmed assumption of "
            "that name exists on this request. The rate is decomposed rather than taken as "
            "one number, so each part has to be agreed on its own terms."
        )
        raise MissingAssumptionError(message, context={"missing": ",".join(missing)})

    return cost_of_equity(
        ledger,
        risk_free=values[RISK_FREE_ASSUMPTION],
        beta=values[BETA_ASSUMPTION],
        equity_risk_premium=values[EQUITY_RISK_PREMIUM_ASSUMPTION],
    )


def _inputs_from(
    values: dict[str, Quantity],
    *,
    latest: PeriodAnalysis,
    cost_of_equity_rate: Quantity,
    years: int,
) -> ResidualIncomeInputs:
    """The model's inputs, from confirmed rows and the filed balance sheet.

    The book value and the share count come from the filings and are never assumed: this
    model's whole claim is that it starts from a number the filer published, and an opening
    book value somebody typed would make it a dividend discount wearing a balance sheet.
    """
    return ResidualIncomeInputs(
        opening_book_value=required_line(latest, "equity"),
        return_on_equity=_path(values, DRIVER_NAMES[0], years=years),
        payout_ratio=_path(values, DRIVER_NAMES[1], years=years),
        cost_of_equity=cost_of_equity_rate,
        # Replaced per treatment below. Named here because the dataclass has no default and
        # must not acquire one: which treatment ran is most of the answer (ADR 0070).
        terminal_treatment=TerminalTreatment.FADE_TO_NOTHING,
        terminal_growth=_scalar(values, _TERMINAL_GROWTH),
        shares_outstanding=share_count(latest),
    )


def _path(values: dict[str, Quantity], name: str, *, years: int) -> DriverPath:
    """One driver's path, under the same rule the discounted cash flow applies.

    Raises:
        MissingAssumptionError: If neither the flat nor the complete per-year form is
            confirmed. The rule lives in :func:`aer.services.valuation.driver_values`; only
            the type it is wrapped in differs.
    """
    return DriverPath(name=name, values=driver_values(values, name, years=years))


def _scalar(values: dict[str, Quantity], name: str) -> Quantity:
    """One confirmed single-valued assumption.

    Raises:
        MissingAssumptionError: If it is not confirmed. There is no default: a terminal
            growth rate this platform picked would be its opinion presented as the
            operator's, which is what ADR 0046 exists to prevent.
    """
    found = values.get(name)
    if found is None:
        message = (
            f"The valuation needs a confirmed {name!r}, and no such assumption exists on "
            "this request. It is a judgement somebody has to make and justify; there is no "
            "default for it."
        )
        raise MissingAssumptionError(message, context={"missing": name})
    return found


def _with_treatment(
    inputs: ResidualIncomeInputs, treatment: TerminalTreatment
) -> ResidualIncomeInputs:
    return replace(inputs, terminal_treatment=treatment)
