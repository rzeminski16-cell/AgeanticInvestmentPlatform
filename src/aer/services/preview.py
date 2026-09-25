"""A base case struck over values nobody has recorded: shown once, then thrown away.

ADR 0132 §3. The assumptions gate asks a person to confirm a perpetual growth rate and an
exit multiple, and until this module it showed them as two rows with nothing to say that,
together, they could not both hold. The verdict round's MSFT run confirmed growth of 3% a
year for ever beside a 14x exit multiple that implies 6.5%; the report then printed two
per-share figures nearly twice apart, and its masthead called them a range. The figures
that say why were computed, recorded and printed — after the confirmation, deep in the
valuation section, where nobody deciding anything was reading.

**The same assembly and the same arithmetic.** The analysis, the mandate and the market
figures come from :func:`~aer.workflow.workflows.vertical_slice_v1.valuation_basis`, which
the value step itself calls; the inputs from
:func:`~aer.services.valuation_run.base_case_inputs`, which the value step and Ask's
recompute call; the answer from :func:`aer.calc.dcf.discounted_cash_flow`. Nothing here
does arithmetic of its own, so a figure the gate shows before the confirmation is the figure
the value step records after it, if nobody changes a row in between.

**The one place a proposed, unconfirmed value enters arithmetic, and why that is allowed.**
:func:`aer.services.assumptions.as_quantity` refuses an unconfirmed row, because a figure
that reaches a report must rest on numbers a person agreed to (invariant 3). The preview's
whole purpose is to inform that agreement before it is given, so it reads the rows as
proposed — and the refusal's reason holds structurally rather than by care: :func:`strike`
takes no session, so the ledger it writes to cannot be persisted by anything it calls, and
nothing it returns carries a calculation id a report could footnote. The only output is a
sentence on the page that asks for the confirmation.

**It refuses nothing.** Two terminal assumptions that contradict each other are a
judgement the operator may make knowingly, and the report prints the reason beside the two
figures either way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

import structlog

from aer.calc.dcf import METHOD_DISAGREEMENT, DcfInputs, DcfResult, discounted_cash_flow
from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.sectors import ModelNotPermittedError, ValuationModel
from aer.errors import AerError
from aer.render import display
from aer.services.assumption_gate import valuation_model
from aer.services.assumptions import assumptions_for_request
from aer.services.calculations import new_context
from aer.services.mandate import mandate_of
from aer.services.sectors import CLASSIFY_STEP
from aer.services.valuation_run import (
    ValuationNotPossibleError,
    base_case_inputs,
    latest_period,
    prior_period,
)
from aer.workflow.workflows.vertical_slice_v1 import (
    ASSUMPTIONS_STEP,
    FORECAST_YEARS,
    PRICES_STEP,
    ValuationBasis,
    step_output,
    valuation_basis,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.config import HouseStyle
    from aer.db.models import Assumption, Job

__all__ = [
    "PREVIEW_CASE",
    "Preview",
    "TerminalCheck",
    "basis_for_run",
    "strike",
    "terminal_check",
]

_log = structlog.get_logger("aer.services.preview")

# The case label the arithmetic carries. Never seen in a ledger, because nothing the preview
# strikes is persisted — but `aer.calc.dcf` records a case on every row it strikes, and a
# preview wearing "base" would be the one row that could be mistaken for the report's own.
PREVIEW_CASE: Final = "preview"

# The steps whose records the value step reads, besides the analysis it recomputes.
_ACQUIRE_STEP: Final = "acquire"
_READ_STEPS: Final = (ASSUMPTIONS_STEP, _ACQUIRE_STEP, CLASSIFY_STEP, PRICES_STEP)


@dataclass(frozen=True, slots=True)
class Preview:
    """One base case over the values given: what went in and what came out."""

    inputs: DcfInputs
    result: DcfResult


@dataclass(frozen=True, slots=True)
class TerminalCheck:
    """Two terminal assumptions that disagree, and the figures that say how far.

    Every figure is the preview's, over the values as proposed; none is recorded.
    """

    wacc: Decimal
    terminal_growth: Decimal
    exit_multiple: Decimal
    # What each method implies about the other's assumption, at the same discount rate.
    growth_the_multiple_implies: Decimal
    multiple_the_growth_implies: Decimal
    gordon_per_share: Decimal
    exit_multiple_per_share: Decimal
    currency: str
    # The higher per-share figure over the lower, less one: `aer.calc.dcf`'s own measure.
    disagreement: Decimal

    def sentences(self, *, style: HouseStyle) -> tuple[str, str]:
        """What the gate says, in the notation the report would print the same figures in."""
        rate = display.figure(self.wacc, unit="pure", label="discount rate", style=style)
        growth = display.figure(self.terminal_growth, unit="pure", label="growth", style=style)
        implied_growth = display.figure(
            self.growth_the_multiple_implies, unit="pure", label="growth", style=style
        )
        multiple = display.multiple(self.exit_multiple)
        implied_multiple = display.multiple(self.multiple_the_growth_implies)
        gordon = display.money(self.gordon_per_share, self.currency, style=style)
        exit_value = display.money(self.exit_multiple_per_share, self.currency, style=style)
        apart = display.figure(self.disagreement, unit="pure", label="percentage", style=style)
        return (
            f"At a discount rate of {rate}, an exit multiple of {multiple} implies perpetual "
            f"growth of {implied_growth} a year, and perpetual growth of {growth} implies an "
            f"exit multiple of {implied_multiple}.",
            f"Confirmed as they stand, the two methods give {gordon} (perpetuity growth) and "
            f"{exit_value} (exit multiple) a share, the higher {apart} above the lower, and "
            "the report prints both with these two figures beside them.",
        )


async def basis_for_run(session: AsyncSession, *, job: Job) -> ValuationBasis | None:
    """What this run's value step would value from, or ``None`` where it values nothing here.

    ``None`` for every state in which the value step would not strike a discounted cash
    flow over the same basis: the assumptions not yet proposed, a model other than the free
    cash flow's (a bank's residual income has one terminal method, not two), a sector that
    refuses the model, and a run whose price step has not finished — the value step waits
    for it, and a preview struck without the market capitalisation would weigh equity at
    book and show a discount rate the report will not use.
    """
    outputs = {key: await step_output(session, job_id=job.id, step_key=key) for key in _READ_STEPS}
    if not outputs[ASSUMPTIONS_STEP] or not outputs[_ACQUIRE_STEP] or not outputs[PRICES_STEP]:
        return None
    model = valuation_model(outputs[ASSUMPTIONS_STEP])
    if model is not ValuationModel.DCF_FCFF:
        return None
    request = await mandate_of(session, job)
    if request is None:  # pragma: no cover -- a run cannot exist without its request
        return None
    try:
        return await valuation_basis(session, request=request, outputs=outputs, model=model)
    except ModelNotPermittedError:
        return None


def strike(
    basis: ValuationBasis, values: Mapping[str, Quantity], *, years: int = FORECAST_YEARS
) -> Preview:
    """The base case over ``values``, on a ledger that is discarded when this returns.

    No session is taken, which is the guarantee rather than a convenience: whatever the
    arithmetic writes to its ledger stays in memory, and nothing reachable from here can
    persist it.

    Raises:
        MissingAssumptionError: An input none of ``values`` supplies.
        ValuationNotPossibleError: A figure the filings do not carry.
        CalculationError: Inputs the arithmetic refuses, such as growth at the discount rate.
    """
    latest = latest_period(basis.analysis)
    if latest is None:
        message = (
            "No annual period could be assembled from this company's filings, so there is no "
            "base year to forecast from."
        )
        raise ValuationNotPossibleError(message)
    ledger = new_context()
    _, inputs = base_case_inputs(
        ledger,
        dict(values),
        latest=latest,
        prior=prior_period(basis.analysis),
        years=years,
        market_capitalisation=basis.market_capitalisation,
    )
    result = discounted_cash_flow(ledger, inputs, mandate=basis.mandate, case=PREVIEW_CASE)
    return Preview(inputs=inputs, result=result)


async def terminal_check(
    session: AsyncSession, *, job: Job, rows: Sequence[Assumption] | None = None
) -> TerminalCheck | None:
    """The two terminal assumptions as proposed, when they put the methods too far apart.

    ``None`` unless the preview can be struck and its two per-share figures sit further
    apart than :data:`aer.calc.dcf.METHOD_DISAGREEMENT` — the band past which the valuation
    already says, in its caveats, that the methods disagree. Every refusal to strike — an
    input nobody has proposed yet, a figure the filings lack, growth at the discount rate —
    is also ``None``: the gate lists what is outstanding on its own, and a preview that
    could not be struck has nothing to add to that.

    ``rows`` are the request's assumptions, read here when the caller has not already.
    """
    basis = await basis_for_run(session, job=job)
    if basis is None:
        return None
    if rows is None:
        rows = await assumptions_for_request(session, job.work_order_id)

    try:
        preview = strike(basis, _as_proposed(rows))
    except AerError as refused:
        _log.info("preview.not_struck", job_id=str(job.id), reason=type(refused).__name__)
        return None

    result = preview.result
    gap = result.method_disagreement
    implied_growth = result.exit_multiple.implied_terminal_growth
    implied_multiple = result.gordon.implied_exit_multiple
    currencies = result.gordon.value_per_share.unit.currencies
    if gap is None or implied_growth is None or implied_multiple is None or not currencies:
        return None
    if gap.value <= METHOD_DISAGREEMENT:
        return None

    return TerminalCheck(
        wacc=preview.inputs.wacc.value,
        terminal_growth=preview.inputs.terminal_growth.value,
        exit_multiple=preview.inputs.exit_multiple.value,
        growth_the_multiple_implies=implied_growth.value,
        multiple_the_growth_implies=implied_multiple.value,
        gordon_per_share=result.gordon.value_per_share.value,
        exit_multiple_per_share=result.exit_multiple.value_per_share.value,
        currency=currencies[0],
        disagreement=gap.value,
    )


def _as_proposed(rows: Sequence[Assumption]) -> dict[str, Quantity]:
    """Every row as the arithmetic would take it once confirmed — confirmed or not.

    The only reader of unconfirmed rows as quantities, and private so it stays that way:
    :func:`aer.services.assumptions.as_quantity` is the reader for anything recorded. Each
    quantity still carries the row as its source, because the arithmetic refuses an
    unsourced input and the preview must take exactly the path the value step takes.
    """
    return {
        row.name: Quantity.of(
            row.value,
            Unit.parse(row.unit),
            source=SourceRef.assumption(row.id, label=row.name),
        )
        for row in rows
    }
