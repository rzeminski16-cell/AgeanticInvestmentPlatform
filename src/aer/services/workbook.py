"""What the model workbook is written from, and when there is none to write (ADR 0134).

The builder (:mod:`aer.render.workbook`) is pure and writes whatever it is handed. This module
decides what it is handed, and three rules live here rather than there.

**The model is proved before it is written.** The workbook is struck from the run's confirmed
values through :func:`aer.services.preview.strike`, the value step's own assembly and
arithmetic. The result is held against the report's recorded base case, at the ledger's
stored precision, using the calculator's check (ADR 0133). A run whose filings have moved
since its report gets no workbook. A workbook that does not reproduce the report it ships with
would be a second model with the report's name on it.

**The vendor's price does not go in.** A figure computed from licensed data may be published
and the data itself may not (ADR 0030's amendment, ADR 0034). So the market capitalisation
that weights the discount rate is an input, with its derivation named, and the close it was
computed from is not. The distance from the price therefore stays in the report and is not
recomputed here.

**Every input says where it came from**, in the words the sources sheet prints: the filing
and accession for a filed line, the person, date and reason for a confirmed assumption, the
lines it was computed from for a calculation.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

import structlog
from sqlalchemy import select

from aer.calc.comps import CompsTable, MultipleResult, WithheldComps
from aer.calc.units import Quantity, SourceKind
from aer.calc.wacc import EquityBasis
from aer.core.assumption_scales import assumption_words
from aer.db.models import Calculation, FinancialFact, SourceDocument
from aer.errors import AerError
from aer.render.workbook import (
    CompsLine,
    CostOfDebt,
    Input,
    ModelWorkbook,
    RecordedComps,
    RecordedGrid,
)
from aer.services.assumption_gate import (
    COST_OF_DEBT_ASSUMPTION,
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    RISK_FREE_ASSUMPTION,
)
from aer.services.assumptions import assumptions_for_request, confirmed_values
from aer.services.calculator import figures_of, recorded_base, reproduces
from aer.services.mandate import mandate_of
from aer.services.preview import basis_for_run, strike
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation_run import latest_period, prior_period, required_line
from aer.services.valuation_view import valuation_view
from aer.workflow.workflows.vertical_slice_v1 import comps_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Assumption, Job, Report, ResearchRequest
    from aer.services.analysis import PeriodAnalysis

__all__ = ["NoWorkbook", "recorded_comps", "workbook_model"]

_log = structlog.get_logger("aer.services.workbook")

_FILED: Final = "Filed"
_CONFIRMED: Final = "Confirmed assumption"
_COMPUTED: Final = "Calculated"

# How a grid's axis reads, by the assumption it varies.
_AXIS_STYLES: Final[dict[str, str]] = {
    "wacc": "rate",
    "terminal_growth": "rate",
    "exit_multiple": "multiple",
}

# A grid's axes in the words the model sheet labels the same inputs with, so a reader holding
# a recorded grid can find the cell it varies. The discount rate is no assumption anybody
# confirmed, which is why the gate's vocabulary has no words for it.
_AXIS_WORDS: Final[dict[str, str]] = {
    "wacc": "Discount rate (WACC)",
    "terminal_growth": "Perpetual growth after the forecast",
    "exit_multiple": "Exit multiple of final-year EBITDA",
}


@dataclass(frozen=True, slots=True)
class NoWorkbook:
    """Why a report carries no workbook, in a sentence the render step records."""

    reason: str


async def workbook_model(
    session: AsyncSession, *, job: Job, report: Report
) -> ModelWorkbook | NoWorkbook:
    """The workbook for ``report``, or why there is none."""
    if report.approved_at is None:
        return NoWorkbook("A workbook is written only when a report is approved.")
    request = await mandate_of(session, job)
    basis = await basis_for_run(session, job=job)
    calculations = list(
        await session.scalars(
            select(Calculation).where(Calculation.job_id == job.id).order_by(Calculation.sequence)
        )
    )
    # The ledger, not the run's plan: a run that meant to strike a discounted cash flow and
    # stopped short of it recorded no base case, and has nothing to write out.
    recorded = recorded_base(calculations)
    if request is None or basis is None or recorded.gordon_per_share is None:
        return NoWorkbook(
            "This run valued the company another way, or not at all, so there is no "
            "discounted cash flow to write out."
        )

    values = await confirmed_values(session, request.id)
    try:
        preview = strike(basis, values)
    except AerError as refused:
        return NoWorkbook(f"The model could not be struck from the confirmed values: {refused}")
    # The sheet bridges enterprise to equity value by net debt alone, as every base case does
    # today. The reproduction check below strikes the model in Python and so cannot see the
    # sheet leave an item out: a run that ever carries one is refused rather than misstated.
    if preview.inputs.non_operating:
        return NoWorkbook(
            "The bridge from enterprise to equity value carries items beyond net debt, which "
            "the workbook does not write out."
        )
    if not reproduces(figures_of(preview, price=None), recorded):
        return NoWorkbook(
            "Struck again from the confirmed values, the model no longer gives the report's "
            "own figures — a later run has moved the filings it reads — so a workbook written "
            "now would be a different model from the report's."
        )

    latest = latest_period(basis.analysis)
    assert latest is not None, "strike refuses a run with no annual period"
    prior = prior_period(basis.analysis)
    inputs, capital = preview.inputs, preview.capital
    rows = {
        row.id: row for row in await assumptions_for_request(session, request.id) if row.approved
    }
    quoted = [
        inputs.base_revenue,
        inputs.shares_outstanding,
        *(
            line
            for line in (
                latest.statements.get("equity"),
                latest.statements.get("total_debt"),
                latest.statements.get("interest_expense"),
                prior.statements.get("total_debt") if prior is not None else None,
            )
            if line is not None
        ),
    ]
    facts = await _facts(session, _fact_ids(quoted))
    sources = _Sources(facts=facts, rows=rows)
    period = latest.period_end.strftime("%d %B %Y")

    equity_value = (
        sources.computed(
            "Equity value: market capitalisation",
            basis.market_capitalisation,
            "amount",
            "The price step's market capitalisation: the close times the filed share count. "
            "The close itself is the price vendor's and is not reproduced here.",
        )
        if capital.basis is EquityBasis.MARKET and basis.market_capitalisation is not None
        else sources.filed(
            "Equity value: book equity, for want of a usable market capitalisation",
            required_line(latest, "equity"),
            "amount",
        )
    )
    debt = latest.statements.get("total_debt")
    debt_value = (
        sources.filed("Total debt", debt, "amount", derived=_debt_sum(latest))
        if debt is not None
        else Input(
            label="Total debt",
            value=Decimal(0),
            style="amount",
            source="The filings state no debt line, so the debt side of the capital is nil.",
            kind=_FILED,
        )
    )

    model = ModelWorkbook(
        company=request.company_name,
        ticker=request.ticker or "",
        currency=_currency(inputs.base_revenue),
        as_of=request.work_order.as_of_date,
        run_id=str(job.id),
        report_id=str(report.id),
        content_hash=report.content_hash,
        code_version=job.code_version,
        approved_at=report.approved_at,
        base_revenue=sources.filed(f"Revenue, year ending {period}", inputs.base_revenue, "amount"),
        opening_working_capital=sources.computed(
            "Opening working capital",
            inputs.opening_working_capital,
            "amount",
            f"Current assets less current liabilities, year ending {period}.",
        ),
        net_debt=sources.computed(
            "Net debt",
            inputs.net_debt,
            "amount",
            f"Total debt less cash and equivalents, year ending {period}.",
        ),
        shares_outstanding=sources.filed("Shares outstanding", inputs.shares_outstanding, "count"),
        tax_rate=sources.confirmed("Tax rate", inputs.tax_rate, "rate"),
        terminal_growth=sources.confirmed(
            "Perpetual growth after the forecast", inputs.terminal_growth, "rate"
        ),
        exit_multiple=sources.confirmed(
            "Exit multiple of final-year EBITDA", inputs.exit_multiple, "multiple"
        ),
        risk_free_rate=sources.confirmed("Risk-free rate", values[RISK_FREE_ASSUMPTION], "rate"),
        beta=sources.confirmed("Beta", values[BETA_ASSUMPTION], "coefficient"),
        equity_risk_premium=sources.confirmed(
            "Equity risk premium", values[EQUITY_RISK_PREMIUM_ASSUMPTION], "rate"
        ),
        equity_value=equity_value,
        debt_value=debt_value,
        cost_of_debt=_cost_of_debt(
            sources, latest=latest, prior=prior, values=values, has_debt=debt_value.value > 0
        ),
        revenue_growth=sources.path("Revenue growth", inputs.revenue_growth.values),
        ebit_margin=sources.path("EBIT margin", inputs.ebit_margin.values),
        depreciation_intensity=sources.path(
            "Depreciation intensity", inputs.depreciation_intensity.values
        ),
        capex_intensity=sources.path(
            "Capital expenditure intensity", inputs.capex_intensity.values
        ),
        working_capital_intensity=sources.path(
            "Working capital intensity", inputs.working_capital_intensity.values
        ),
        recorded={
            key: figure
            for key, figure in (
                ("gordon_per_share", recorded.gordon_per_share),
                ("exit_multiple_per_share", recorded.exit_multiple_per_share),
            )
            if figure is not None
        },
        grids=await _grids(session, job=job),
        comps=await _comparison(session, job=job, request=request),
    )
    _log.info("workbook.assembled", job_id=str(job.id), years=model.years)
    return model


async def _comparison(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> RecordedComps | None:
    """The comparison the shareable report prints, through the report's own reader.

    `comps_for` asks the licence question at the shareable audience, which is this file's
    audience too: a workbook is mailed on.
    """
    try:
        found = await comps_for(session, job=job, request=request)
    except AerError as refused:
        return RecordedComps(
            heading="The comparison could not be read back",
            columns=(),
            lines=(),
            chosen=(),
            left_out=(),
            note=str(refused),
        )
    return recorded_comps(found)


def recorded_comps(found: CompsTable | WithheldComps | None) -> RecordedComps | None:
    """A comparison as the workbook carries it: the report's figures, or why there are none."""
    if found is None:
        return None
    chosen = tuple(
        (peer.name, peer.rationale or "No reason was recorded.") for peer in found.confirmed_peers()
    )
    if isinstance(found, WithheldComps):
        return RecordedComps(
            heading=f"The comparison, to {found.as_of:%d %B %Y}",
            columns=(),
            lines=(),
            chosen=chosen,
            left_out=found.exclusion_reasons,
            note=(
                "The multiples are not reproduced here: the licence behind the prices they "
                "were computed from does not permit it. " + (found.licence_note or "")
            ).strip(),
        )

    companies = (found.subject, *found.peers)
    labels: dict[str, str] = {}
    for company in companies:
        for multiple in company.multiples:
            labels.setdefault(multiple.key, multiple.label)
    return RecordedComps(
        heading=f"Multiples on {found.basis.spoken} basis, to {found.as_of:%d %B %Y}",
        columns=tuple(labels.values()),
        lines=tuple(
            CompsLine(
                name=(
                    f"{company.name} (the company researched)"
                    if company is found.subject
                    else company.name
                ),
                figures=tuple(_multiple(company.multiple(key)) for key in labels),
            )
            for company in companies
        ),
        chosen=chosen,
        left_out=tuple(f"{row.name}: {row.reason}" for row in found.excluded),
        note=(
            "The report's own figures, as values. Each multiple was computed from a market "
            "price; the prices themselves are not here. " + (found.licence_note or "")
        ).strip(),
    )


def _multiple(found: MultipleResult | None) -> Decimal | str:
    if found is None:
        return "Not computed"
    if found.quantity is None:
        return (
            f"Not meaningful: {found.absent_because}" if found.absent_because else "Not meaningful"
        )
    return found.quantity.value


def _cost_of_debt(
    sources: _Sources,
    *,
    latest: PeriodAnalysis,
    prior: PeriodAnalysis | None,
    values: Mapping[str, Quantity],
    has_debt: bool,
) -> CostOfDebt:
    """The debt rate's inputs, chosen the way `valuation_run._cost_of_debt` chooses them.

    No debt, no rate. Otherwise a filed interest expense wins, over the average of the prior
    and latest debt where there is a prior year; only a filer tagging no interest at all
    falls back to the confirmed rate.
    """
    if not has_debt:
        return CostOfDebt()
    interest = latest.statements.get("interest_expense")
    if interest is not None:
        opening = prior.statements.get("total_debt") if prior is not None else None
        return CostOfDebt(
            interest_expense=sources.filed("Interest expense", interest, "amount"),
            prior_debt=(
                sources.filed(
                    "Total debt, the year before", opening, "amount", derived=_debt_sum(prior)
                )
                if opening is not None and prior is not None
                else None
            ),
        )
    return CostOfDebt(
        confirmed_rate=sources.confirmed(
            "Cost of debt, before tax", values[COST_OF_DEBT_ASSUMPTION], "rate"
        )
    )


@dataclass(frozen=True, slots=True)
class _Sources:
    """Turns a quantity into an input, with the sentence that says where it came from."""

    facts: Mapping[uuid.UUID, tuple[FinancialFact, SourceDocument | None]]
    rows: Mapping[uuid.UUID, Assumption]

    def filed(self, label: str, quantity: Quantity, style: str, *, derived: str = "") -> Input:
        """A line off the filings, or the sum of two where the filer stated only the parts.

        Which of the two it is comes from the figure's own source rather than the caller's
        expectation: total debt is a stated fact for one filer and a traced sum for the next.
        """
        source = quantity.source
        if source is not None and source.kind is not SourceKind.FACT:
            return self.computed(
                label, quantity, style, derived or "Calculated from the filed lines it sums."
            )
        identifier = _uuid_of(quantity)
        found = self.facts.get(identifier) if identifier is not None else None
        if found is None:
            said = "A filed line."
        else:
            fact, document = found
            words = fact.concept.replace("_", " ")
            said = (
                f"{words.capitalize()} from the {fact.form or 'filing'} for the period ending "
                f"{fact.period_end:%d %B %Y}, filed {fact.filed_date:%d %B %Y}"
                + (f", accession {fact.accession}" if fact.accession else "")
                + (
                    f"; retrieved {document.retrieved_at:%d %B %Y} from {document.url}."
                    if document is not None
                    else "."
                )
            )
        return Input(label=label, value=quantity.value, style=style, source=said, kind=_FILED)

    def confirmed(self, label: str, quantity: Quantity, style: str) -> Input:
        identifier = _uuid_of(quantity)
        row = self.rows.get(identifier) if identifier is not None else None
        # When, and why, but never who: the confirmer is recorded by address, and this file
        # is mailed on. The report names nobody either.
        if row is None:
            said = "A confirmed assumption."
        else:
            when = f" on {row.approved_at:%d %B %Y}" if row.approved_at is not None else ""
            said = f"Confirmed{when}. {row.justification}"
        return Input(label=label, value=quantity.value, style=style, source=said, kind=_CONFIRMED)

    def computed(self, label: str, quantity: Quantity | None, style: str, said: str) -> Input:
        assert quantity is not None
        return Input(label=label, value=quantity.value, style=style, source=said, kind=_COMPUTED)

    def path(self, label: str, quantities: Sequence[Quantity]) -> tuple[Input, ...]:
        """A driver, year by year, each year sourced to the row that supplied it."""
        return tuple(
            self.confirmed(f"{label}, year {year}", quantity, "rate")
            for year, quantity in enumerate(quantities, start=1)
        )


def _uuid_of(quantity: Quantity) -> uuid.UUID | None:
    source = quantity.source
    if source is None:
        return None
    try:
        return uuid.UUID(source.identifier)
    except ValueError:
        return None


def _fact_ids(quantities: Sequence[Quantity]) -> set[uuid.UUID]:
    """The facts behind ``quantities``, so every one the workbook quotes is read in one query."""
    found: set[uuid.UUID] = set()
    for quantity in quantities:
        source = quantity.source
        identifier = _uuid_of(quantity)
        if source is not None and source.kind is SourceKind.FACT and identifier is not None:
            found.add(identifier)
    return found


async def _facts(
    session: AsyncSession, ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[FinancialFact, SourceDocument | None]]:
    """Each fact with the document it was read from, for the date and address it was fetched."""
    if not ids:
        return {}
    rows = await session.execute(
        select(FinancialFact, SourceDocument)
        .outerjoin(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
        .where(FinancialFact.id.in_(ids))
    )
    return {fact.id: (fact, document) for fact, document in rows.tuples()}


async def _grids(session: AsyncSession, *, job: Job) -> tuple[RecordedGrid, ...]:
    """The report's grids as the valuation page lays them out, values only."""
    view = await valuation_view(session, job)
    return tuple(
        RecordedGrid(
            title=grid.label,
            row_label=_axis_words(grid.y_assumption),
            column_label=_axis_words(grid.x_assumption),
            row_values=tuple(y for y, _ in grid.rows),
            column_values=grid.x_values,
            cells=tuple(tuple(output for _, output, _ in cells) for _, cells in grid.rows),
            row_style=_AXIS_STYLES.get(grid.y_assumption, "coefficient"),
            column_style=_AXIS_STYLES.get(grid.x_assumption, "coefficient"),
        )
        for grid in view.grids
    )


def _debt_sum(period: PeriodAnalysis) -> str:
    """What total debt is where the filer stated only its parts (`aer.calc.statements`)."""
    return (
        "Short-term debt plus long-term debt, as filed for the year ending "
        f"{period.period_end:%d %B %Y}."
    )


def _axis_words(name: str) -> str:
    words = _AXIS_WORDS.get(name) or assumption_words(name) or name.replace("_", " ")
    return words[:1].upper() + words[1:]


def _currency(quantity: Quantity) -> str:
    currencies = quantity.unit.currencies
    return currencies[0] if currencies else ""
