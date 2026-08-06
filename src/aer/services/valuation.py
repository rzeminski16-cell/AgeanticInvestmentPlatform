"""Turning confirmed assumptions into a valuation, and storing what it was made of.

:mod:`aer.calc.dcf` is pure: it takes a :class:`~aer.calc.dcf.DcfInputs` and produces a
result. This module is the layer that *builds* that input set — from a request's confirmed
assumptions, or a scenario's overrides on top of them — and persists what came out.

**The drivers are looked up, never defaulted.** A forecast needs five driver paths, a tax
rate, a terminal growth rate and an exit multiple. Every one of them is a confirmed
assumption, found by name; a name that is missing raises :class:`MissingAssumptionError`
listing what it looked for. There is no house value for revenue growth, and a valuation that
proceeded on one would be this platform's own opinion wearing the operator's name.

**A driver may be flat or per year, and the difference is explicit.** ``revenue_growth_y1``
through ``revenue_growth_y5`` describe a fade; a single ``revenue_growth`` applies to every
year. Looking for the per-year names first is a lookup convention rather than a fallback
value: a driver with *some* of its years present is refused, because a fade missing its third
year is a mistake, and filling the gap from the flat name would be a house value entering
through the side door.

**Every scenario is priced from the base case, not from a copy of it.** Scenarios resolve
through :func:`aer.services.scenarios.resolve`, so a corrected base assumption reaches every
case that did not explicitly disagree with it.

**The mandate travels with the request, not with the code path.** Every entry point here
takes a :class:`~aer.core.sectors.ValuationMandate` and hands it to :mod:`aer.calc.dcf`, which
requires one. A bank cannot reach a discounted cash flow through this module for the same
reason it cannot reach one directly: the permission does not exist to be passed.

**A grid cell is a whole valuation, and its calculations are stored.** ``sensitivity_cells``
has a non-null ``calculation_id`` for exactly this reason. Running the grid writes every
calculation in it before the cells reference them, so no cell ever points at a row that is
not there.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import (
    DRIVER_NAMES,
    BridgeItem,
    DcfInputs,
    DcfResult,
    DriverPath,
    GridAxis,
    GridMeasure,
    SensitivityGrid,
    TerminalMethod,
    discounted_cash_flow,
    sensitivity_grid,
)
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity
from aer.core.sectors import ValuationMandate
from aer.db.models import Scenario, Sensitivity
from aer.errors import AerError
from aer.services.calculations import new_context, persist_context
from aer.services.scenarios import CellInput, record_sensitivity, resolve

__all__ = [
    "SCALAR_NAMES",
    "MissingAssumptionError",
    "ScenarioValuation",
    "inputs_from",
    "run_scenarios",
    "run_sensitivity",
    "run_valuation",
]

_log = structlog.get_logger("aer.services.valuation")

SCALAR_NAMES: tuple[str, ...] = ("tax_rate", "terminal_growth", "exit_multiple")
"""The single-valued assumptions a discounted cash flow needs, alongside the driver paths.

The discount rate is deliberately absent: it is the output of :mod:`aer.calc.wacc`, built
from a vintage and its own confirmed assumptions, and taking it as a bare assumption here
would let a number nobody decomposed stand in for the whole cost-of-capital chain.
"""


class MissingAssumptionError(AerError):
    """A valuation was attempted without a number somebody has to have chosen.

    Its own class rather than a `ValidationError`, because what the caller does about it is
    different: a malformed request is fixed by correcting the request, and this is fixed by
    somebody proposing and confirming an assumption.
    """

    code = "missing_assumption"
    http_status = 409


@dataclass(frozen=True, slots=True)
class ScenarioValuation:
    """One case, its valuation, and which assumptions it argued about."""

    key: str
    label: str
    overridden: tuple[str, ...]
    result: DcfResult


def inputs_from(
    values: Mapping[str, Quantity],
    *,
    years: int,
    base_revenue: Quantity,
    opening_working_capital: Quantity,
    wacc: Quantity,
    net_debt: Quantity,
    shares_outstanding: Quantity,
    non_operating: Sequence[BridgeItem],
) -> DcfInputs:
    """Assemble a forecast's inputs from confirmed assumptions and balance-sheet facts.

    Args:
        values: Confirmed assumptions by name, from
            :func:`aer.services.assumptions.confirmed_values` or a resolved scenario. Each
            already carries an assumption source, so everything computed from one traces
            back to the row and the justification on it.
        years: How long the explicit forecast runs. Drives which per-year assumption names
            are looked for.
        wacc: The discount rate, from :mod:`aer.calc.wacc` rather than from ``values``. See
            :data:`SCALAR_NAMES`.

    Raises:
        MissingAssumptionError: If any driver or scalar has no confirmed assumption, or if a
            per-year driver is missing one of its years.
    """
    if years < 1:
        message = f"A {years}-year forecast is not a forecast."
        raise MissingAssumptionError(message, context={"years": str(years)})

    missing = [name for name in SCALAR_NAMES if name not in values]
    if missing:
        message = (
            f"The valuation needs {', '.join(missing)}, and no confirmed assumption of that "
            "name exists on this request. There is no house value for any of them: a "
            "terminal growth rate this platform chose would be its opinion presented as the "
            "operator's."
        )
        raise MissingAssumptionError(message, context={"missing": ",".join(missing)})

    paths = {name: _path_for(values, name, years=years) for name in DRIVER_NAMES}

    return DcfInputs(
        base_revenue=base_revenue,
        revenue_growth=paths["revenue_growth"],
        ebit_margin=paths["ebit_margin"],
        capex_intensity=paths["capex_intensity"],
        depreciation_intensity=paths["depreciation_intensity"],
        working_capital_intensity=paths["working_capital_intensity"],
        opening_working_capital=opening_working_capital,
        tax_rate=values["tax_rate"],
        wacc=wacc,
        terminal_growth=values["terminal_growth"],
        exit_multiple=values["exit_multiple"],
        net_debt=net_debt,
        shares_outstanding=shares_outstanding,
        non_operating=tuple(non_operating),
    )


def _path_for(values: Mapping[str, Quantity], name: str, *, years: int) -> DriverPath:
    """One driver's path: its per-year assumptions, or its flat one applied to every year.

    Raises:
        MissingAssumptionError: If neither form is confirmed, or if the per-year form is
            partially present. A fade missing its third year is a mistake, and quietly
            filling the gap from the flat value would hide it behind a plausible number.
    """
    per_year = [f"{name}_y{year}" for year in range(1, years + 1)]
    present = [key for key in per_year if key in values]

    if present and len(present) != years:
        absent = [key for key in per_year if key not in values]
        message = (
            f"The driver {name!r} is confirmed for some years and not others: "
            f"{', '.join(absent)} missing. A path with a hole in it is a mistake somebody "
            "made, and filling it from the flat value would produce a forecast nobody wrote."
        )
        raise MissingAssumptionError(message, context={"driver": name, "missing": ",".join(absent)})

    if present:
        return DriverPath(name=name, values=tuple(values[key] for key in per_year))

    if name in values:
        return DriverPath.flat(name, values[name], years=years)

    message = (
        f"The driver {name!r} has no confirmed assumption. Confirm either {name!r} for every "
        f"year, or {', '.join(per_year)} for a path that changes. Every driver is a number "
        "somebody chose and justified; there is no default for any of them."
    )
    raise MissingAssumptionError(
        message, context={"driver": name, "looked_for": ",".join([name, *per_year])}
    )


async def run_valuation(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    inputs: DcfInputs,
    mandate: ValuationMandate,
    context: CalculationContext | None = None,
    case: str = "base",
) -> DcfResult:
    """Value the business and store every calculation that produced the answer.

    Args:
        context: An existing ledger to add to. Supplied when several valuations belong to one
            run — the scenarios, say — so they persist together rather than in fragments a
            failure could leave half-written.

    Returns:
        The valuation. Persisted only when this call owns the context; a caller that supplied
        one is persisting it itself.
    """
    owned = context is None
    ledger = context if context is not None else new_context()

    result = discounted_cash_flow(ledger, inputs, mandate=mandate, case=case)

    if owned:
        await persist_context(session, ledger, job_id=job_id)

    _log.info(
        "valuation.computed",
        job_id=str(job_id),
        years=len(result.years),
        subject=mandate.subject,
        sector=mandate.sector_key or "unclassified",
        gordon_per_share=str(result.gordon.value_per_share.value),
        exit_per_share=str(result.exit_multiple.value_per_share.value),
        terminal_share=str(result.gordon.terminal_share.value),
        caveats=len(result.caveats),
    )
    return result


async def run_scenarios(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    scenarios: Sequence[Scenario],
    mandate: ValuationMandate,
    years: int,
    base_revenue: Quantity,
    opening_working_capital: Quantity,
    wacc: Quantity,
    net_debt: Quantity,
    shares_outstanding: Quantity,
    non_operating: Sequence[BridgeItem],
) -> list[ScenarioValuation]:
    """Value every case, each resolved against the current base rather than a stored copy.

    One calculation context for all of them, persisted once. A bear case that fails on a
    missing assumption therefore leaves no half-written base case behind it.
    """
    ledger = new_context()
    valuations: list[ScenarioValuation] = []

    for scenario in scenarios:
        resolved = await resolve(session, scenario=scenario)
        inputs = inputs_from(
            resolved.values,
            years=years,
            base_revenue=base_revenue,
            opening_working_capital=opening_working_capital,
            wacc=wacc,
            net_debt=net_debt,
            shares_outstanding=shares_outstanding,
            non_operating=non_operating,
        )
        result = await run_valuation(
            session,
            job_id=job_id,
            inputs=inputs,
            mandate=mandate,
            context=ledger,
            case=scenario.key,
        )
        valuations.append(
            ScenarioValuation(
                key=scenario.key,
                label=scenario.label,
                overridden=resolved.overridden,
                result=result,
            )
        )

    await persist_context(session, ledger, job_id=job_id)
    return valuations


async def run_sensitivity(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    job_id: uuid.UUID,
    inputs: DcfInputs,
    rows: GridAxis,
    columns: GridAxis,
    method: TerminalMethod,
    measure: GridMeasure,
    mandate: ValuationMandate,
    label: str,
    scenario_id: uuid.UUID | None = None,
) -> tuple[SensitivityGrid, Sensitivity]:
    """Run a grid and store it, calculations first.

    The order matters and is the whole reason this is one function rather than two. Every
    cell references the calculation that produced it, and ``sensitivity_cells.calculation_id``
    is not nullable with ``ON DELETE RESTRICT``; writing the cells before the calculations
    would either fail on the foreign key or, worse, succeed against rows written by some
    other path and point the grid at somebody else's arithmetic.
    """
    ledger = new_context()
    grid = sensitivity_grid(
        ledger,
        inputs,
        rows=rows,
        columns=columns,
        method=method,
        measure=measure,
        mandate=mandate,
    )

    await persist_context(session, ledger, job_id=job_id)

    stored = await record_sensitivity(
        session,
        request_id=request_id,
        job_id=job_id,
        scenario_id=scenario_id,
        label=label,
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
        "valuation.sensitivity_recorded",
        job_id=str(job_id),
        label=label,
        x=rows.field,
        y=columns.field,
        cells=len(grid.cells),
        calculations=len(ledger),
    )
    return grid, stored
