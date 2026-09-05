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

**Scenarios and two grids, on the bank model's own axes** (ADR 0101). Every case runs both
treatments, because ADR 0070's reasoning — that choosing between them is a judgement about
banking rather than arithmetic — does not weaken for a bear case. The grids are the cost of
equity against the terminal growth rate under the perpetuity, and against the return on
equity under the fade; each sits under the treatment its second axis means something in, and
the second one is the one a bank's reader actually wants, because the two treatments already
bracket the terminal question and say nothing at all about the spread.

**A grid that cannot be computed is absent whole, and says why.** The perpetuity refuses a
terminal growth at or above the cost of equity and a final year earning below its charge, and
a grid corner can reach either. A partly filled grid would be worse than none — a hole is a
cell a reader interprets — so the refusal takes the grid and leaves a sentence, in the same
shape :attr:`BankValuationOutcome.perpetual_refusal` already uses for the base case.

**Every refusal names what was missing.** A valuation that cannot run is an ordinary outcome
for a company whose filings are thin, and the report has to say which line was absent rather
than showing an empty page.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.engine import CalculationContext
from aer.calc.residual_income import (
    DRIVER_NAMES,
    DriverPath,
    GridAxis,
    GridMeasure,
    ResidualIncomeInputs,
    ResidualIncomeResult,
    SensitivityGrid,
    TerminalTreatment,
    residual_income_value,
    sensitivity_grid,
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
from aer.services.scenarios import CellInput, record_sensitivity, resolve, scenarios_for_request
from aer.services.valuation import MissingAssumptionError, axis_around, driver_values
from aer.services.valuation_run import (
    ValuationNotPossibleError,
    latest_period,
    required_line,
    share_count,
)

__all__ = ["BankScenarioValuation", "BankValuationOutcome", "value_the_bank"]

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
    "This request carries no authored scenarios, so the valuation is the base case and the "
    "two grids beside it. Scenarios are written, not generated: nothing here invents a bear "
    "case the operator did not argue for."
)

# The step between grid points, per axis. Absolute rather than proportional, for the reason
# `valuation_run` gives: half a point on a discount rate is the comparison an analyst makes.
# The return on equity moves in the same steps as the rate it is compared against, so a
# reader can read the spread off the two axes without converting between them.
_RATE_STEP: Final = Decimal("0.005")
_GROWTH_STEP: Final = Decimal("0.0025")

_COST_OF_EQUITY_FIELD: Final = "cost_of_equity"


@dataclass(frozen=True, slots=True)
class TreatmentPair:
    """One set of assumptions, valued both ways.

    The unit every case is reported in, base and scenario alike. ADR 0070 holds that choosing
    between the treatments is a judgement about banking rather than arithmetic; that does not
    weaken for a bear case, so a scenario reports the same pair the base case does and may
    have its own perpetuity refused on its own terms.
    """

    faded: ResidualIncomeResult
    perpetual: ResidualIncomeResult | None = None
    # Why the perpetuity is absent, when it is. Empty whenever `perpetual` is present.
    perpetual_refusal: str = ""


@dataclass(frozen=True, slots=True)
class BankScenarioValuation:
    """One authored case, valued both ways, and which assumptions it argued about."""

    key: str
    label: str
    overridden: tuple[str, ...]
    valued: TreatmentPair


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
    scenarios: tuple[BankScenarioValuation, ...] = ()
    grids: tuple[SensitivityGrid, ...] = ()
    # Why a grid is absent, when it is: one sentence per refused grid, in the order the two
    # would have been built. A grid dies whole (ADR 0101), and a reader is owed the corner.
    grid_refusals: tuple[str, ...] = ()
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

        produced["scenarios"] = [
            {"key": item.key, "label": item.label, "overridden": list(item.overridden)}
            for item in self.scenarios
        ]
        produced["grids"] = [
            {
                "rows": grid.row_axis.field,
                "columns": grid.column_axis.field,
                "treatment": grid.treatment.value,
                "cells": len(grid.cells),
            }
            for grid in self.grids
        ]
        if self.grid_refusals:
            produced["grids_refused"] = list(self.grid_refusals)
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

    base = _both_treatments(ledger, inputs, mandate=mandate, case="base")
    await persist_context(session, ledger, job_id=job_id)

    scenarios = await _scenarios(
        session,
        request=request,
        job_id=job_id,
        inputs=inputs,
        mandate=mandate,
        years=years,
    )
    grids, refusals = await _grids(
        session, request=request, job_id=job_id, inputs=inputs, mandate=mandate
    )

    caveats = (*base.faded.caveats, _DISAGREEMENT_CAVEAT)
    if base.perpetual is not None:
        caveats = (*caveats, *(item for item in base.perpetual.caveats if item not in caveats))
    if not scenarios:
        caveats = (*caveats, _NO_SCENARIOS_CAVEAT)

    _log.info(
        "valuation.bank_completed",
        job_id=str(job_id),
        cost_of_equity=str(equity_rate.value),
        fade_per_share=str(base.faded.value_per_share.value),
        perpetual_refused=bool(base.perpetual_refusal),
        scenarios=len(scenarios),
        grids=len(grids),
        grids_refused=len(refusals),
    )
    return BankValuationOutcome(
        ran=True,
        cost_of_equity=equity_rate,
        faded=base.faded,
        perpetual=base.perpetual,
        caveats=caveats,
        perpetual_refusal=base.perpetual_refusal,
        scenarios=tuple(scenarios),
        grids=tuple(grids),
        grid_refusals=tuple(refusals),
    )


def _both_treatments(
    ledger: CalculationContext,
    inputs: ResidualIncomeInputs,
    *,
    mandate: ValuationMandate,
    case: str,
) -> TreatmentPair:
    """One set of assumptions, valued fading and growing, into one ledger.

    The fade always runs; the perpetuity may refuse a final year earning below the cost of
    equity or a growth rate at or above it. Both refusals are statements about this bank
    rather than failures, and the fade result stands on its own — so the case keeps its
    valuation and records why the second treatment is absent.
    """
    faded = residual_income_value(
        ledger,
        _with_treatment(inputs, TerminalTreatment.FADE_TO_NOTHING),
        mandate=mandate,
        case=case,
    )
    try:
        perpetual = residual_income_value(
            ledger,
            _with_treatment(inputs, TerminalTreatment.PERPETUAL_GROWTH),
            mandate=mandate,
            case=case,
        )
    except CalculationError as refused:
        return TreatmentPair(faded=faded, perpetual_refusal=str(refused))
    return TreatmentPair(faded=faded, perpetual=perpetual)


# -- Scenarios and grids ---------------------------------------------------------------------


async def _scenarios(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    inputs: ResidualIncomeInputs,
    mandate: ValuationMandate,
    years: int,
) -> list[BankScenarioValuation]:
    """Every case the request carries, each resolved against the current base.

    One calculation context for all of them, persisted once, for the reason
    :func:`aer.services.valuation.run_scenarios` gives: a bear case that fails on a missing
    assumption leaves no half-written bull case behind it. What a case may and may not
    disagree with the base about is :func:`_rebuilt`'s.

    An empty list when the request has no scenarios, which is the ordinary state.
    """
    cases = await scenarios_for_request(session, request.id)
    if not cases:
        return []

    ledger = new_context()
    valued: list[BankScenarioValuation] = []
    for case in cases:
        resolved = await resolve(session, scenario=case)
        try:
            case_inputs = _rebuilt(ledger, inputs, dict(resolved.values), years=years)
        except MissingAssumptionError:
            # A case whose override left one of the model's names unconfirmed prices nothing.
            # It is skipped rather than raised on: the base case is a real valuation and this
            # is one scenario missing, which the report describes by its absence from the list.
            continue
        valued.append(
            BankScenarioValuation(
                key=case.key,
                label=case.label,
                overridden=resolved.overridden,
                valued=_both_treatments(ledger, case_inputs, mandate=mandate, case=case.key),
            )
        )

    await persist_context(session, ledger, job_id=job_id)
    return valued


def _rebuilt(
    ledger: CalculationContext,
    base: ResidualIncomeInputs,
    values: dict[str, Quantity],
    *,
    years: int,
) -> ResidualIncomeInputs:
    """The base inputs with everything an assumption decides taken from ``values`` instead.

    Four of the seven fields: the two drivers, the terminal growth rate, and the cost of
    equity — which is decomposed from this case's own risk-free rate, beta and premium rather
    than carried over, so a scenario that argues about the discount rate argues about the
    three numbers it is built from.

    The opening book value and the share count are deliberately not among them. Both come off
    the filed balance sheet, and a scenario that moved either would be arguing with the filing
    rather than with a forecast.

    Raises:
        MissingAssumptionError: If the case leaves one of the model's names unconfirmed.
    """
    return replace(
        base,
        return_on_equity=_path(values, DRIVER_NAMES[0], years=years),
        payout_ratio=_path(values, DRIVER_NAMES[1], years=years),
        cost_of_equity=_cost_of_equity(ledger, values),
        terminal_growth=_scalar(values, _TERMINAL_GROWTH),
    )


async def _grids(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    inputs: ResidualIncomeInputs,
    mandate: ValuationMandate,
) -> tuple[list[SensitivityGrid], list[str]]:
    """The two grids a residual-income valuation is read with, and why either is missing.

    Cost of equity against terminal growth under the perpetuity, and against return on equity
    under the fade — each under the treatment its second axis means something in (ADR 0101).
    Both are reported per share, because that is the figure a reader compares to a price.
    """
    rate_axis = GridAxis(
        field=_COST_OF_EQUITY_FIELD, values=axis_around(inputs.cost_of_equity, step=_RATE_STEP)
    )
    plan = (
        (
            TerminalTreatment.PERPETUAL_GROWTH,
            _TERMINAL_GROWTH,
            inputs.terminal_growth,
            _GROWTH_STEP,
        ),
        (
            TerminalTreatment.FADE_TO_NOTHING,
            DRIVER_NAMES[0],
            inputs.return_on_equity.flat_value,
            _RATE_STEP,
        ),
    )

    grids: list[SensitivityGrid] = []
    refusals: list[str] = []
    for treatment, field, anchor, step in plan:
        if anchor is None:
            # Only reachable for the driver axis, and only when its confirmed path fades.
            # `sensitivity_grid` would refuse the same case; this says so without building an
            # axis around a value that does not exist.
            refusals.append(
                f"No grid over {field.replace('_', ' ')} was built: the confirmed path moves "
                "from year to year, so it is several numbers rather than one and an axis over "
                "it would be labelled for a quantity it does not vary."
            )
            continue
        try:
            grid = await _run_grid(
                session,
                request=request,
                job_id=job_id,
                inputs=inputs,
                mandate=mandate,
                rows=rate_axis,
                columns=GridAxis(field=field, values=axis_around(anchor, step=step)),
                treatment=treatment,
            )
        except CalculationError as refused:
            # A corner the perpetuity will not price takes the grid whole, because a hole in
            # a grid is a cell a reader interprets (ADR 0101).
            refusals.append(
                f"No grid of cost of equity against {field.replace('_', ' ')} was built under "
                f"the {treatment.value.replace('_', ' ')} treatment: {refused}"
            )
            continue
        grids.append(grid)

    return grids, refusals


async def _run_grid(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    inputs: ResidualIncomeInputs,
    mandate: ValuationMandate,
    rows: GridAxis,
    columns: GridAxis,
    treatment: TerminalTreatment,
) -> SensitivityGrid:
    """Run one grid and store it, calculations first.

    The order is the one :func:`aer.services.valuation.run_sensitivity` fixes for the same
    reason: ``sensitivity_cells.calculation_id`` is not nullable with ``ON DELETE RESTRICT``,
    so writing the cells before the calculations would either fail on the foreign key or —
    worse — succeed against rows written by some other path and point the grid at somebody
    else's arithmetic.
    """
    ledger = new_context()
    grid = sensitivity_grid(
        ledger,
        inputs,
        rows=rows,
        columns=columns,
        treatment=treatment,
        measure=GridMeasure.VALUE_PER_SHARE,
        mandate=mandate,
    )

    await persist_context(session, ledger, job_id=job_id)

    await record_sensitivity(
        session,
        request_id=request.id,
        job_id=job_id,
        label=(
            f"Cost of equity against {columns.field.replace('_', ' ')} "
            f"({treatment.value.replace('_', ' ')})"
        ),
        x_assumption=rows.field,
        y_assumption=columns.field,
        output_name=grid.output_name,
        output_unit=grid.output_unit,
        cells=[
            CellInput(
                x_value=cell.row_value.value,
                y_value=cell.column_value.value,
                output_value=cell.result.value,
                calculation_id=cell.calculation_id,
            )
            for cell in grid.cells
        ],
    )

    _log.info(
        "valuation.bank_sensitivity_recorded",
        job_id=str(job_id),
        treatment=treatment.value,
        columns=columns.field,
        cells=len(grid.cells),
        calculations=len(ledger),
    )
    return grid


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
