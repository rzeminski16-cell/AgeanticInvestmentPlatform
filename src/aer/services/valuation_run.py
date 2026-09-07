"""The run's valuation: confirmed assumptions and filed facts in, stored grids out.

Gap B2c, the second half. :mod:`aer.services.valuation` already knew how to assemble a
:class:`~aer.calc.dcf.DcfInputs` from confirmed assumptions and how to store what came out;
what it never had was a caller. This module is that caller — it sources the balance-sheet
side from the run's own analysis, decomposes the discount rate, and drives the base case,
the scenarios and the sensitivity grids in one transaction.

**The discount rate is decomposed, never supplied.** ADR 0046 and
:data:`aer.services.valuation.SCALAR_NAMES` both refuse a bare `wacc` assumption, because
one unexplained number would then stand in for the whole cost-of-capital chain. So the WACC
is built by :func:`aer.calc.wacc.cost_of_capital` from a confirmed risk-free rate, beta and
premium — each a row somebody agreed to — and every intermediate step lands in the ledger.

**The cost of debt is derived where it can be, and confirmed where it cannot.** Interest
expense over average debt is arithmetic on two filed lines, so where the filings carry
both it belongs with the six derived drivers rather than on the assumptions page — and the
derivation wins even when an assumption also exists, because a filed line outranks an
opinion about it. But some filers tag no interest expense at all (the live CHRW run —
report-quality R13), and for them the rate is a confirmed ``cost_of_debt`` assumption the
gate demanded up front. What this module still never does is *invent* one: no filed line
and no confirmed row is a refusal that names both.

**Book equity weights, and the caveat says so.** Nothing in this workflow acquires a price,
so the equity side of the capital structure is shareholders' funds.
:class:`~aer.calc.wacc.EquityBasis` records which measure was used and
:func:`~aer.calc.wacc.cost_of_capital` attaches the caveat, because book weights understate
the equity weight and produce a WACC that is too low — which raises every valuation
computed from it.

**Every refusal names what was missing.** A valuation that cannot run is an ordinary
outcome for a company whose filings are thin, and the report has to say which line was
absent rather than showing an empty page.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import (
    DcfInputs,
    DcfResult,
    GridAxis,
    GridMeasure,
    SensitivityGrid,
    TerminalMethod,
)
from aer.calc.engine import CalculationContext
from aer.calc.ratios import net_debt, working_capital
from aer.calc.units import Quantity
from aer.calc.wacc import (
    CapitalStructure,
    CostOfCapital,
    EquityBasis,
    average_debt,
    cost_of_capital,
    cost_of_debt,
)
from aer.core.sectors import ValuationMandate
from aer.db.models import ResearchRequest
from aer.errors import AerError
from aer.services.analysis import AnalysisOutcome, PeriodAnalysis
from aer.services.assumption_gate import (
    COST_OF_DEBT_ASSUMPTION,
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    RISK_FREE_ASSUMPTION,
)
from aer.services.assumptions import confirmed_values
from aer.services.calculations import new_context, persist_context
from aer.services.prices import BETA_ASSUMPTION
from aer.services.scenarios import scenarios_for_request
from aer.services.valuation import (
    MissingAssumptionError,
    ScenarioValuation,
    axis_around,
    inputs_from,
    run_scenarios,
    run_sensitivity,
    run_valuation,
)

__all__ = [
    "ValuationNotPossibleError",
    "ValuationOutcome",
    "latest_period",
    "required_line",
    "share_count",
    "value_the_business",
]

_log = structlog.get_logger("aer.services.valuation_run")

# The step between grid points, per axis. Absolute rather than proportional: a discount rate
# moving by half a point is the comparison an analyst makes, and ±10% of 8.4% is a step
# nobody would have chosen. How *many* points, and how they are laid out around the base
# case, is `aer.services.valuation.axis_around`'s — both models ask the same question of it.
_WACC_STEP: Final = Decimal("0.005")
_GROWTH_STEP: Final = Decimal("0.0025")


class ValuationNotPossibleError(AerError):
    """The run holds no forecast, and the reason is a missing figure rather than a defect.

    Distinct from :class:`~aer.services.valuation.MissingAssumptionError`, which is about a
    number nobody has chosen. This one is about a number nobody *filed*: a balance sheet
    with no debt line, an income statement with no interest expense against borrowings that
    exist. Both stop the valuation; only one is fixed by an operator typing something.
    """

    code = "valuation_not_possible"
    http_status = 409


@dataclass(frozen=True, slots=True)
class ValuationOutcome:
    """What the valuation came to, or why it did not run."""

    ran: bool
    reason: str = ""
    cost_of_capital: CostOfCapital | None = None
    base: DcfResult | None = None
    scenarios: tuple[ScenarioValuation, ...] = ()
    grids: tuple[SensitivityGrid, ...] = ()
    caveats: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        if not self.ran or self.base is None or self.cost_of_capital is None:
            return {"valued": False, "reason": self.reason}
        return {
            "valued": True,
            "wacc": str(self.cost_of_capital.wacc.value),
            "equity_basis": self.cost_of_capital.basis.value,
            "gordon_per_share": str(self.base.gordon.value_per_share.value),
            "exit_multiple_per_share": str(self.base.exit_multiple.value_per_share.value),
            "terminal_share": str(self.base.gordon.terminal_share.value),
            "years": len(self.base.years),
            "scenarios": [
                {"key": item.key, "label": item.label, "overridden": list(item.overridden)}
                for item in self.scenarios
            ],
            "grids": [
                {
                    "rows": grid.row_axis.field,
                    "columns": grid.column_axis.field,
                    "cells": len(grid.cells),
                }
                for grid in self.grids
            ],
            "caveats": list(self.caveats),
        }


async def value_the_business(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    analysis: AnalysisOutcome,
    mandate: ValuationMandate,
    years: int,
) -> ValuationOutcome:
    """Run the base case, the scenarios and the grids, and store every calculation.

    Returns a :class:`ValuationOutcome` rather than raising when the run simply cannot
    produce a forecast: a company with one filed year or an unconfirmed assumption is an
    ordinary state the report has to describe, not an error the workflow should die on. A
    genuine defect — a unit mismatch, a broken ledger — still raises.
    """
    latest = latest_period(analysis)
    prior = _prior_period(analysis)
    if latest is None:
        return ValuationOutcome(
            ran=False,
            reason=(
                "No annual period could be assembled from this company's filings, so there "
                "is no base year to forecast from."
            ),
        )

    values = await confirmed_values(session, request.id)

    ledger = new_context()
    try:
        capital = _cost_of_capital(ledger, values, latest=latest, prior=prior)
        inputs = inputs_from(
            values,
            years=years,
            base_revenue=required_line(latest, "revenue"),
            opening_working_capital=_working_capital(ledger, latest),
            wacc=capital.wacc,
            net_debt=_net_debt(ledger, latest),
            shares_outstanding=share_count(latest),
            # Empty, and stated rather than defaulted: this build reads no associate
            # holdings, minority interests or pension deficits off a filing, so the bridge
            # from enterprise to equity value is debt and cash alone. A reader is told.
            non_operating=(),
        )
    except (MissingAssumptionError, ValuationNotPossibleError) as refusal:
        return ValuationOutcome(ran=False, reason=str(refusal))

    base = await run_valuation(
        session, job_id=job_id, inputs=inputs, mandate=mandate, context=ledger, case="base"
    )
    await persist_context(session, ledger, job_id=job_id)

    scenarios = await _scenarios(
        session, request=request, job_id=job_id, inputs=inputs, mandate=mandate, years=years
    )
    grids = await _grids(session, request=request, job_id=job_id, inputs=inputs, mandate=mandate)

    caveats = (*capital.caveats, *base.caveats, _BRIDGE_CAVEAT)
    _log.info(
        "valuation.run_completed",
        job_id=str(job_id),
        wacc=str(capital.wacc.value),
        basis=capital.basis.value,
        scenarios=len(scenarios),
        grids=len(grids),
    )
    return ValuationOutcome(
        ran=True,
        cost_of_capital=capital,
        base=base,
        scenarios=tuple(scenarios),
        grids=tuple(grids),
        caveats=caveats,
    )


_BRIDGE_CAVEAT: Final = (
    "The bridge from enterprise value to equity value is net debt alone. Associate "
    "holdings, minority interests and pension deficits are not read off the filings by this "
    "build, so a business carrying material non-operating items is valued as though it does "
    "not."
)


# -- The discount rate ---------------------------------------------------------------------


def _cost_of_capital(
    ledger: CalculationContext,
    values: dict[str, Quantity],
    *,
    latest: PeriodAnalysis,
    prior: PeriodAnalysis | None,
) -> CostOfCapital:
    """The WACC, decomposed from confirmed assumptions and the filed balance sheet.

    Raises:
        MissingAssumptionError: If the risk-free rate, the beta or the premium is not
            confirmed. Named individually, because "the cost of capital is missing" does
            not tell an operator which row to go and agree to.
        ValuationNotPossibleError: If the company carries debt and the filings show no
            interest expense to price it with.
    """
    missing = [
        name
        for name in (RISK_FREE_ASSUMPTION, BETA_ASSUMPTION, EQUITY_RISK_PREMIUM_ASSUMPTION)
        if name not in values
    ]
    if missing:
        message = (
            f"The discount rate needs {', '.join(missing)}, and no confirmed assumption of "
            "that name exists on this request. The rate is decomposed rather than taken as "
            "one number, so each part has to be agreed on its own terms."
        )
        raise MissingAssumptionError(message, context={"missing": ",".join(missing)})

    equity_value = required_line(latest, "equity")
    debt_value = _line(latest, "total_debt", required=False)
    if debt_value is None:
        # Sourced to the equity line it sits beside: a nil with no provenance is still a
        # number the ledger cannot explain.
        debt_value = Quantity.of(Decimal(0), equity_value.unit, source=equity_value.source)

    structure = CapitalStructure(
        equity_value=equity_value,
        debt_value=debt_value,
        # Book, because nothing here acquires a price. `cost_of_capital` attaches the
        # caveat; the enum is what makes the substitution visible rather than assumed.
        basis=EquityBasis.BOOK,
    )

    debt_rate: Quantity | None = None
    tax_rate: Quantity | None = None
    if structure.has_debt:
        debt_rate = _cost_of_debt(
            ledger, values, latest=latest, prior=prior, closing_debt=debt_value
        )
        tax_rate = values.get("tax_rate")
        if tax_rate is None:
            message = (
                "The company carries debt, so the after-tax cost of it needs a confirmed "
                "tax rate, and none exists on this request."
            )
            raise MissingAssumptionError(message, context={"missing": "tax_rate"})

    return cost_of_capital(
        ledger,
        risk_free=values[RISK_FREE_ASSUMPTION],
        beta=values[BETA_ASSUMPTION],
        equity_risk_premium=values[EQUITY_RISK_PREMIUM_ASSUMPTION],
        cost_of_debt_pre_tax=debt_rate,
        tax_rate=tax_rate,
        structure=structure,
    )


def _cost_of_debt(
    ledger: CalculationContext,
    values: dict[str, Quantity],
    *,
    latest: PeriodAnalysis,
    prior: PeriodAnalysis | None,
    closing_debt: Quantity,
) -> Quantity:
    """Interest expense over average debt, or the confirmed rate where nothing was filed.

    Derived first, and the derivation wins even when a confirmed ``cost_of_debt`` row also
    exists: both inputs are filed lines, and a filed line outranks an opinion about it.
    Only a filer that tags no interest expense at all falls back to the confirmed
    assumption — the case the gate demanded the row for (report-quality R13), where the
    CHRW run's operator confirmed everything asked of them and still watched the valuation
    refuse over a line no surface had ever mentioned.

    Averaged against the prior year's closing debt where there is one. Closing debt alone
    understates the rate for a company that borrowed during the year — a full year of
    interest divided by a balance that existed for a month.

    Raises:
        ValuationNotPossibleError: If there is no interest expense to divide *and* no
            confirmed rate to stand in. A rate this platform invented would be weighted
            into the discount rate and would look in the output exactly like one somebody
            sourced — so the refusal names the remedy instead.
    """
    interest = _line(latest, "interest_expense", required=False)
    if interest is None:
        supplied = values.get(COST_OF_DEBT_ASSUMPTION)
        if supplied is not None:
            return supplied
        message = (
            "The balance sheet carries debt and the income statement shows no interest "
            "expense, so the cost of that debt cannot be derived. Nothing here will invent "
            "a rate: it would be weighted into the discount rate and would be "
            "indistinguishable in the report from one somebody sourced. Supply and confirm "
            "a cost_of_debt assumption — the pre-tax rate the borrowings cost, with its "
            "source stated — and the valuation will use that."
        )
        raise ValuationNotPossibleError(message, context={"concept": "interest_expense"})

    # The *prior year's* closing balance, from the analysis. There is no
    # "opening debt" concept in any taxonomy — it is last year's closing figure, and
    # reaching for it as though it were a filed line would silently never average at all.
    opening = _line(prior, "total_debt", required=False) if prior is not None else None
    debt = (
        average_debt(ledger, opening=opening, closing=closing_debt)
        if opening is not None
        else closing_debt
    )
    return cost_of_debt(ledger, interest_expense=interest, debt=debt)


# -- The balance-sheet side ------------------------------------------------------------------


def latest_period(analysis: AnalysisOutcome) -> PeriodAnalysis | None:
    """The most recent analysed year. `AnalysisOutcome.periods` is newest first."""
    return analysis.periods[0] if analysis.periods else None


def _prior_period(analysis: AnalysisOutcome) -> PeriodAnalysis | None:
    """The year before the latest, when the run assembled one.

    Only used to average the debt the year's interest was charged on. A single-period run
    prices the cost of debt off the closing balance and says so in the calculation's own
    recorded assumptions.
    """
    return analysis.periods[1] if len(analysis.periods) > 1 else None


def _line(period: PeriodAnalysis, concept: str, *, required: bool = True) -> Quantity | None:
    """One filed line as a quantity.

    Raises:
        ValuationNotPossibleError: If a required line is absent. Named, so the report can
            say which figure the company did not file rather than showing an empty page.
    """
    found = period.statements.get(concept)
    if found is not None or not required:
        return found

    message = (
        f"The valuation needs {concept.replace('_', ' ')} for the year ending "
        f"{period.period_end.isoformat()}, and this company's filings do not report it under "
        "any recognised concept."
    )
    raise ValuationNotPossibleError(
        message, context={"concept": concept, "period": period.period_end.isoformat()}
    )


def required_line(period: PeriodAnalysis, concept: str) -> Quantity:
    """A filed line that must be there, narrowed to a quantity.

    `_line` answers `Quantity | None` because most callers want the optional form; this is
    the assertion-free narrowing for the ones that do not, so the type flows rather than
    being cast at four call sites.
    """
    found = _line(period, concept)
    assert found is not None
    return found


def _working_capital(ledger: CalculationContext, period: PeriodAnalysis) -> Quantity:
    """Current assets less current liabilities, as a recorded calculation.

    **Through the traced function rather than by subtracting two quantities**, and the unit
    system is what insists: bare arithmetic produces a value with no source, and
    `aer.calc.dcf` refuses an unsourced input because a figure that traces to nothing cannot
    be defended. The refusal caught this on the first run of this module.
    """
    return working_capital(
        ledger,
        current_assets=required_line(period, "current_assets"),
        current_liabilities=required_line(period, "current_liabilities"),
    )


def _net_debt(ledger: CalculationContext, period: PeriodAnalysis) -> Quantity:
    """Total debt less cash, as a recorded calculation.

    A company with no debt line has none, which is a real balance sheet rather than a
    missing figure — so the debt side is nil and the cash side is not. The nil is sourced
    to the same filing the cash came from, because an unsourced zero is still unsourced.
    """
    cash = required_line(period, "cash_and_equivalents")
    debt = _line(period, "total_debt", required=False)
    if debt is None:
        debt = Quantity.of(Decimal(0), cash.unit, source=cash.source)
    return net_debt(ledger, total_debt=debt, cash=cash)


def share_count(period: PeriodAnalysis) -> Quantity:
    """The share count the per-share figures divide by.

    Diluted first: a per-share value that ignores options in issue flatters itself, and the
    dilution is the more conservative of the two readings.
    """
    for concept in ("diluted_shares_outstanding", "shares_outstanding", "basic_shares_outstanding"):
        found = period.statements.get(concept)
        if found is not None:
            return found

    message = (
        f"The valuation needs a share count for the year ending "
        f"{period.period_end.isoformat()} and the filings carry none — neither diluted, "
        "basic nor a plain outstanding figure. Without one there is no per-share value to "
        "report, and an enterprise value on its own is not a recommendation."
    )
    raise ValuationNotPossibleError(
        message, context={"concept": "shares_outstanding", "period": period.period_end.isoformat()}
    )


# -- Scenarios and grids ---------------------------------------------------------------------


async def _scenarios(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    inputs: DcfInputs,
    mandate: ValuationMandate,
    years: int,
) -> list[ScenarioValuation]:
    """Every case the request carries, each resolved against the current base.

    An empty list when the request has no scenarios, which is the ordinary state: scenarios
    are authored, not generated, and a run without them has a base case and nothing to
    compare it to.
    """
    cases = await scenarios_for_request(session, request.id)
    if not cases:
        return []

    return await run_scenarios(
        session,
        job_id=job_id,
        scenarios=cases,
        mandate=mandate,
        years=years,
        base_revenue=inputs.base_revenue,
        opening_working_capital=inputs.opening_working_capital,
        wacc=inputs.wacc,
        net_debt=inputs.net_debt,
        shares_outstanding=inputs.shares_outstanding,
        non_operating=inputs.non_operating,
    )


async def _grids(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job_id: uuid.UUID,
    inputs: DcfInputs,
    mandate: ValuationMandate,
) -> list[SensitivityGrid]:
    """The two grids a discounted cash flow is always read with.

    Discount rate against terminal growth for the Gordon case, and discount rate against
    the exit multiple for the other — the pairs that decide most of the answer, each
    reported per share because that is the figure a reader compares to a price.
    """
    grids: list[SensitivityGrid] = []
    wacc_axis = GridAxis(field="wacc", values=axis_around(inputs.wacc, step=_WACC_STEP))

    for method, field, step in (
        (TerminalMethod.GORDON_GROWTH, "terminal_growth", _GROWTH_STEP),
        (TerminalMethod.EXIT_MULTIPLE, "exit_multiple", Decimal(1)),
    ):
        anchor = inputs.terminal_growth if field == "terminal_growth" else inputs.exit_multiple
        grid, _ = await run_sensitivity(
            session,
            request_id=request.id,
            job_id=job_id,
            inputs=inputs,
            rows=wacc_axis,
            columns=GridAxis(field=field, values=axis_around(anchor, step=step)),
            method=method,
            measure=GridMeasure.VALUE_PER_SHARE,
            mandate=mandate,
            label=f"WACC against {field.replace('_', ' ')}",
        )
        grids.append(grid)

    return grids
