"""The levers a report's two cases may turn on, and the strikes that price them (ADR 0135).

**The list is the record's.** Each lever moves one input of the report's discounted cash flow
to a value the run already holds: a driver's lowest, highest or latest observation in the
filings, as the same ratio the confirmed assumption averages; perpetual growth at the rate the
exit multiple implies, or at the risk-free rate the gate confirmed; the exit multiple the
perpetuity method implies. A point names a lever by its key and writes no figure for it, so
the one number an argument needs is never the argument's own.

**The same model, struck again.** The analysis, the mandate and the market figures are the
value step's own (:func:`aer.services.preview.basis_for_run`), the inputs are assembled by
:func:`aer.services.valuation_run.base_case_inputs`, and the answer is
:func:`aer.calc.dcf.discounted_cash_flow`. A lever is the base case with one input moved, in
every forecast year for a driver, exactly as Ask's recompute moves one (ADR 0130 §3).

**Recorded as a perturbation.** Every strike is recorded under
:data:`aer.calc.dcf.ARGUED_CASE`, which every reader of the base case or a scenario passes by,
and the rows beneath it are set aside by lineage. The strikes run once every section is
drafted — and again when a revision or a redraft rewrites the section — so no section's
evidence moves while the draft is being written.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy import select

from aer.calc.basic import growth_rate
from aer.calc.bridge import margin_of
from aer.calc.dcf import ARGUED_CASE, DcfResult
from aer.calc.engine import CalculationContext
from aer.calc.ratios import operating_margin, working_capital
from aer.calc.units import Quantity, SourceKind, SourceRef, Unit
from aer.calc.wacc import effective_tax_rate
from aer.config import HouseStyle
from aer.core.assumption_scales import PLAUSIBLE_RANGE
from aer.core.cases import (
    DRIVERS,
    PRICED_FOR_FIELD,
    Anchor,
    Direction,
    LeverKey,
    Side,
    levers_named,
)
from aer.db.models import Calculation
from aer.errors import AerError, ValidationError
from aer.render import display
from aer.services.assumptions import confirmed_values
from aer.services.calculations import new_context, persist_context
from aer.services.mandate import mandate_of

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Job, ReportSection
    from aer.services.analysis import PeriodAnalysis
    from aer.services.preview import Preview
    from aer.workflow.workflows.vertical_slice_v1 import ValuationBasis

__all__ = [
    "LEVERS_BLOCK",
    "LeverList",
    "LeverOption",
    "lever_list",
    "price_the_cases",
]

_log = structlog.get_logger("aer.services.cases")

# Where the writer's block carries the list: a private key, handed to the section's check
# and its note and never stored (:func:`aer.sections.deterministic.stored_fields`).
LEVERS_BLOCK: Final = "_levers"

# A confirmed input and a lever closer than this move nothing a reader could see.
_SAME: Final = Decimal("1e-9")

# A confirmed driver entered year by year, as the valuation reads it (`_path_for`).
_PER_YEAR: Final = re.compile(r"^(?P<name>[a-z][a-z0-9_]*?)_y(?P<year>[1-9][0-9]*)$")

_RISK_FREE: Final = "risk_free_rate"

# How each driver's history reads a lever, in the words a table cell and the writer's list
# both use. Every one keeps a word `aer.render.display` reads as a percentage, and the exit
# multiple's avoids "growth", which would print 6.4 as 640%.
_DRIVER_WORDS: Final[dict[str, tuple[str, str, str]]] = {
    "revenue_growth": (
        "revenue growth at its slowest in the record",
        "revenue growth at its fastest in the record",
        "revenue growth as in the latest year",
    ),
    "ebit_margin": (
        "the operating margin at its lowest in the record",
        "the operating margin at its highest in the record",
        "the operating margin as in the latest year",
    ),
    "capex_intensity": (
        "capital expenditure at its lowest share of revenue in the record",
        "capital expenditure at its highest share of revenue in the record",
        "capital expenditure at its share of revenue in the latest year",
    ),
    "depreciation_intensity": (
        "depreciation at its lowest share of revenue in the record",
        "depreciation at its highest share of revenue in the record",
        "depreciation at its share of revenue in the latest year",
    ),
    "working_capital_intensity": (
        "net working capital at its lowest share of revenue in the record",
        "net working capital at its highest share of revenue in the record",
        "net working capital at its share of revenue in the latest year",
    ),
    "tax_rate": (
        "the effective tax rate at its lowest in the record",
        "the effective tax rate at its highest in the record",
        "the effective tax rate as in the latest year",
    ),
}

_TERMINAL_WORDS: Final[dict[Anchor, str]] = {
    Anchor.EXIT_MULTIPLE_IMPLIES: "perpetual growth at the rate the exit multiple implies",
    Anchor.RISK_FREE_RATE: "perpetual growth at the risk-free rate the assumptions gate confirmed",
    Anchor.GROWTH_IMPLIES: "the exit multiple the perpetuity method implies",
}

# The recorded base-case figure a terminal lever takes its value from.
_IMPLIED_ROW: Final[dict[Anchor, str]] = {
    Anchor.EXIT_MULTIPLE_IMPLIES: "implied_terminal_growth",
    Anchor.GROWTH_IMPLIES: "implied_exit_multiple",
}

# What a driver's observation is recorded as, when the ratio suite may already hold it: the
# operating margin is struck every period by the analysis, and one figure has one id.
_RECORDED_AS: Final[dict[str, tuple[str, dict[str, str]]]] = {
    "ebit_margin": ("operating_margin", {}),
}


@dataclass(frozen=True, slots=True)
class LeverOption:
    """One lever this run's record supplies."""

    key: str
    input: str
    words: str
    value: Decimal
    # The fiscal year a driver's observation belongs to; empty for a terminal lever.
    period: str = ""
    # Which way code's strike of it moved the value per share. ``None`` only in a list
    # carried in a block written before directions were struck.
    direction: Direction | None = None

    @property
    def label(self) -> str:
        """The lever as a priced table's row reads it."""
        where = f", {self.period}" if self.period else ""
        every = ", in every forecast year" if self.input in DRIVERS else ""
        return f"{self.words[0].upper()}{self.words[1:]}{where}{every}"

    def shown(self, *, style: HouseStyle) -> str:
        return display.figure(self.value, unit="pure", label=self.words, style=style)


@dataclass(frozen=True, slots=True)
class LeverList:
    """The levers a run offers, or why it offers none."""

    options: tuple[LeverOption, ...] = ()
    reason: str = ""

    def offered(self) -> dict[str, str]:
        return {option.key: option.words for option in self.options}

    def directions(self) -> dict[str, Direction]:
        return {
            option.key: option.direction for option in self.options if option.direction is not None
        }

    def as_block(self) -> dict[str, Any]:
        """The list as the writer's block carries it: JSON, with nothing it cannot hold."""
        return {
            "reason": self.reason,
            "options": [
                {
                    "key": option.key,
                    "input": option.input,
                    "words": option.words,
                    "value": str(option.value),
                    "period": option.period,
                    "direction": option.direction.value if option.direction else "",
                }
                for option in self.options
            ],
        }

    @classmethod
    def from_block(cls, block: Any) -> LeverList:
        if not isinstance(block, Mapping):
            return cls(reason="The run's levers were not listed.")
        options = tuple(
            LeverOption(
                key=str(item["key"]),
                input=str(item["input"]),
                words=str(item["words"]),
                value=Decimal(str(item["value"])),
                period=str(item.get("period") or ""),
                direction=Direction(item["direction"]) if item.get("direction") else None,
            )
            for item in block.get("options") or []
            if isinstance(item, Mapping)
        )
        return cls(options=options, reason=str(block.get("reason") or ""))

    def note(self, *, style: HouseStyle | None = None) -> str:
        """What the writer is told: the levers it may name, and that it writes no figure."""
        if not self.options:
            return (
                "No point may name a lever: "
                f"{self.reason or 'this run has none to strike.'} Make every point in words."
            )
        active = style if style is not None else HouseStyle()
        listed = "\n".join(
            f"- {option.key}: {option.label} ({option.shown(style=active)})"
            + (f"; struck, it {option.direction.spoken}" if option.direction else "")
            for option in self.options
        )
        return (
            "A point may name one lever from this list in its `lever` field, by its key "
            "exactly as written. The platform strikes the report's discounted cash flow with "
            "that one input moved and prints what it gives beside the case, so write no "
            "figure for the lever yourself. Name a lever only where the point's argument is "
            "about that input; a point may name none. Each lever says which way the "
            "platform's strike of it moved the value: the case for may not name one that "
            "lowers it, nor the case against one that raises it.\n" + listed
        )


async def lever_list(session: AsyncSession, *, job: Job) -> LeverList:
    """The levers this run's record supplies to the two cases, or why it supplies none."""
    basis, reason = await _basis(session, job=job)
    if basis is None:
        return LeverList(reason=reason)
    request = await mandate_of(session, job)
    if request is None:  # pragma: no cover -- a run cannot exist without its request
        return LeverList(reason="The run has no request.")
    held = await confirmed_values(session, request.id)
    try:
        base = _preview(basis, held)
    except AerError as refused:
        return LeverList(
            reason=f"the report's discounted cash flow cannot be struck again: {refused}"
        )
    return LeverList(options=_options(basis, held, base))


async def price_the_cases(
    session: AsyncSession, *, job: Job, section: ReportSection, force: bool = False
) -> bool:
    """Strike every lever the drafted cases name, and fill the tables beside them.

    Returns whether anything was struck. Nothing is when the tables were already struck for
    exactly these levers, which is what makes a second pass over an unchanged draft free —
    unless ``force``, which a refresh passes: a carried argument keeps its words, and its
    figures are struck again on the refresh's own base case.
    """
    content = dict(section.content or {})
    named = levers_named(content)
    wanted = [f"{side.value}:{number}:{key}" for side, number, _, key in named]
    if not force and content.get(PRICED_FOR_FIELD) == wanted and _tables_present(content):
        return False

    tables: dict[Side, list[dict[str, Any]]] = {side: [] for side in Side}
    struck = 0
    if named:
        struck = await _strike_named(session, job=job, named=named, tables=tables)

    for side in Side:
        content[side.priced_field] = tables[side]
    content[PRICED_FOR_FIELD] = wanted
    section.content = content
    await session.flush()
    _log.info("cases.priced", job_id=str(job.id), levers=len(named), struck=struck)
    return struck > 0


# -- The list --------------------------------------------------------------------------------


async def _basis(session: AsyncSession, *, job: Job) -> tuple[ValuationBasis | None, str]:
    # A cycle at import time and none at call time: the preview reads the workflow's own
    # assembly, and the workflow reaches this module through the section registry.
    from aer.services.preview import basis_for_run  # noqa: PLC0415

    basis = await basis_for_run(session, job=job)
    if basis is None:
        return None, (
            "the report's valuation is not a discounted cash flow this build can strike again "
            "— a bank's residual income, or a run that reached no valuation."
        )
    return basis, ""


def _preview(basis: ValuationBasis, values: Mapping[str, Quantity]) -> Preview:
    from aer.services.preview import strike  # noqa: PLC0415 -- see `_basis`

    return strike(basis, values)


def _options(
    basis: ValuationBasis, held: Mapping[str, Quantity], base: Preview
) -> tuple[LeverOption, ...]:
    """Every lever the record supplies and the gate would accept, drivers first."""
    ledger = new_context()
    periods = _oldest_first(basis)
    wacc = base.inputs.wacc.value
    options: list[LeverOption] = []
    for driver in DRIVERS:
        observed = _observations(ledger, periods, driver)
        if not observed:
            continue
        for anchor, words, chosen in zip(
            (Anchor.LOWEST, Anchor.HIGHEST, Anchor.LATEST),
            _DRIVER_WORDS[driver],
            _chosen(observed),
            strict=True,
        ):
            option = LeverOption(
                key=LeverKey(driver, anchor).key,
                input=driver,
                words=words,
                value=chosen.quantity.value,
                period=chosen.label,
            )
            if _admissible(option, held, wacc=wacc):
                options.extend(_directed(option, chosen.quantity, basis, held, base))

    implied_growth = base.result.exit_multiple.implied_terminal_growth
    implied_multiple = base.result.gordon.implied_exit_multiple
    for anchor, name, figure in (
        (Anchor.EXIT_MULTIPLE_IMPLIES, "terminal_growth", implied_growth),
        (Anchor.RISK_FREE_RATE, "terminal_growth", held.get(_RISK_FREE)),
        (Anchor.GROWTH_IMPLIES, "exit_multiple", implied_multiple),
    ):
        if figure is None:
            continue
        option = LeverOption(
            key=LeverKey(name, anchor).key,
            input=name,
            words=_TERMINAL_WORDS[anchor],
            value=figure.value,
        )
        if _admissible(option, held, wacc=wacc):
            options.extend(_directed(option, figure, basis, held, base))
    return tuple(options)


def _directed(
    option: LeverOption,
    quantity: Quantity,
    basis: ValuationBasis,
    held: Mapping[str, Quantity],
    base: Preview,
) -> tuple[LeverOption, ...]:
    """The option with the way its strike moves the value, or nothing if it cannot be struck.

    Struck on a ledger that is thrown away, the same arithmetic the priced table's strike
    will record, so the direction the writer is told is the one the table will print. A
    lever the arithmetic refuses is not offered: it would price nothing beside its point.
    """
    try:
        struck = _preview(basis, _moved(held, option.input, quantity))
    except AerError:
        return ()
    direction = Direction.between(_per_share(base.result), _per_share(struck.result))
    return (replace(option, direction=direction),)


def _moved(held: Mapping[str, Quantity], name: str, quantity: Quantity) -> dict[str, Quantity]:
    """The confirmed values with one input moved, in every year it is confirmed for.

    As Ask's recompute moves one (ADR 0130 §3): a per-year path the operator entered would
    otherwise outrank the flat value moved here.
    """
    moved = dict(held)
    for key in _held_keys(held, name) or [name]:
        moved[key] = quantity
    return moved


def _per_share(result: DcfResult) -> tuple[Decimal, Decimal]:
    return result.gordon.value_per_share.value, result.exit_multiple.value_per_share.value


def _admissible(option: LeverOption, held: Mapping[str, Quantity], *, wacc: Decimal) -> bool:
    """A lever the gate would accept, that moves something, and that the arithmetic can take."""
    bounds = PLAUSIBLE_RANGE.get(option.input)
    if bounds is not None and not bounds[0] <= option.value <= bounds[1]:
        return False
    confirmed = [held[key] for key in _held_keys(held, option.input)]
    if confirmed and all(abs(value.value - option.value) <= _SAME for value in confirmed):
        return False
    if option.input == "terminal_growth" and option.value >= wacc:
        return False
    return not (option.input == "exit_multiple" and option.value <= 0)


def _held_keys(held: Mapping[str, Quantity], name: str) -> list[str]:
    """Every confirmed row an input is read from: its flat row, or each year's."""
    return [
        key
        for key in held
        if key == name or ((per_year := _PER_YEAR.match(key)) and per_year["name"] == name)
    ]


@dataclass(frozen=True, slots=True)
class _Observation:
    label: str
    period_end: date
    quantity: Quantity


def _oldest_first(basis: ValuationBasis) -> tuple[PeriodAnalysis, ...]:
    return tuple(reversed(basis.analysis.periods))


def _period_label(period: PeriodAnalysis) -> str:
    """The analysis's own label for a period, so an observation reads as its ratios do."""
    return f"FY{period.fiscal_year}" if period.fiscal_year else period.period_end.isoformat()


def _observations(
    ledger: CalculationContext, periods: Sequence[PeriodAnalysis], driver: str
) -> list[_Observation]:
    found: list[_Observation] = []
    for index, period in enumerate(periods):
        prior = periods[index - 1] if index else None
        label = _period_label(period)
        with ledger.period(label, end=period.period_end):
            try:
                quantity = _observe(ledger, driver, period, prior)
            except AerError:
                # A year the ratio is undefined in — a loss before tax, a zero base — is a
                # year with no observation, not a run with no levers.
                continue
        if quantity is not None:
            found.append(_Observation(label, period.period_end, quantity))
    return found


def _observe(
    ledger: CalculationContext,
    driver: str,
    period: PeriodAnalysis,
    prior: PeriodAnalysis | None,
) -> Quantity | None:
    """One period's observation of a driver, as the ratio the proposals average."""
    revenue = period.statements.get("revenue")
    if driver == "revenue_growth":
        start = prior.statements.get("revenue") if prior is not None else None
        if start is None or revenue is None or start.value <= 0:
            return None
        return growth_rate(ledger, start=start, end=revenue)
    if revenue is None or revenue.value <= 0:
        return None
    observe = _SHARES_OF_REVENUE.get(driver)
    return observe(ledger, period.statements.get, revenue) if observe is not None else None


_Line = Callable[[str], Quantity | None]


def _operating_margin(
    ledger: CalculationContext, line: _Line, revenue: Quantity
) -> Quantity | None:
    income = line("operating_income")
    return operating_margin(ledger, operating_income=income, revenue=revenue) if income else None


def _share_of(concept: str) -> Callable[[CalculationContext, _Line, Quantity], Quantity | None]:
    def observe(ledger: CalculationContext, line: _Line, revenue: Quantity) -> Quantity | None:
        part = line(concept)
        return margin_of(ledger, line=part, revenue=revenue, concept=concept) if part else None

    return observe


def _net_working_capital(
    ledger: CalculationContext, line: _Line, revenue: Quantity
) -> Quantity | None:
    """Current assets less current liabilities over revenue: the proposals' measure.

    Not the ratio suite's `working_capital_intensity`, which is *trade* working capital —
    receivables and inventory less payables. The driver the valuation reads is the net
    figure, so a lever taken from the trade one would move the forecast to a different
    measure of the same words.
    """
    assets, liabilities = line("current_assets"), line("current_liabilities")
    if assets is None or liabilities is None:
        return None
    capital = working_capital(ledger, current_assets=assets, current_liabilities=liabilities)
    return margin_of(ledger, line=capital, revenue=revenue, concept="net_working_capital")


def _tax_rate(ledger: CalculationContext, line: _Line, _revenue: Quantity) -> Quantity | None:
    tax, pre_tax = line("income_tax_expense"), line("pre_tax_income")
    if tax is None or pre_tax is None:
        return None
    return effective_tax_rate(ledger, income_tax_expense=tax, pre_tax_income=pre_tax)


# Every driver but growth is a share of the period's revenue, or read beside it.
_SHARES_OF_REVENUE: Final[
    dict[str, Callable[[CalculationContext, _Line, Quantity], Quantity | None]]
] = {
    "ebit_margin": _operating_margin,
    "capex_intensity": _share_of("capital_expenditure"),
    "depreciation_intensity": _share_of("depreciation_and_amortisation"),
    "working_capital_intensity": _net_working_capital,
    "tax_rate": _tax_rate,
}


def _chosen(observed: Sequence[_Observation]) -> tuple[_Observation, _Observation, _Observation]:
    """The lowest, the highest and the latest. A tie goes to the later year."""
    lowest = min(observed, key=lambda item: (item.quantity.value, -item.period_end.toordinal()))
    highest = max(observed, key=lambda item: (item.quantity.value, item.period_end))
    latest = max(observed, key=lambda item: item.period_end)
    return lowest, highest, latest


# -- The strikes -----------------------------------------------------------------------------


async def _strike_named(
    session: AsyncSession,
    *,
    job: Job,
    named: Sequence[tuple[Side, int, str, str]],
    tables: dict[Side, list[dict[str, Any]]],
) -> int:
    basis, reason = await _basis(session, job=job)
    request = await mandate_of(session, job)
    if basis is None or request is None:
        _log.warning("cases.not_priced", job_id=str(job.id), reason=reason)
        return 0
    held = await confirmed_values(session, request.id)
    try:
        base = _preview(basis, held)
    except AerError as refused:
        _log.warning("cases.not_priced", job_id=str(job.id), reason=str(refused))
        return 0
    offered = {option.key: option for option in _options(basis, held, base)}
    recorded = list(await session.scalars(select(Calculation).where(Calculation.job_id == job.id)))

    ledger = new_context()
    results: dict[str, tuple[Quantity, DcfResult]] = {}
    for side, number, lead_in, key in named:
        option = offered.get(key)
        if option is None:
            # Refused at the draft; reachable from a refresh whose record no longer supplies
            # it. The point stands in words.
            _log.info("cases.lever_not_offered", job_id=str(job.id), key=key, point=number)
            continue
        if option.direction is not None and side.contradicted_by(option.direction):
            # Refused at the draft too; reachable from a refresh whose own base case moves
            # the lever the other way. The point stands in words rather than beside figures
            # that argue the other case.
            _log.info("cases.lever_contradicts", job_id=str(job.id), key=key, point=number)
            continue
        if key not in results:
            try:
                results[key] = _strike(ledger, basis, held, option, recorded=recorded)
            except AerError as refused:
                _log.warning(
                    "cases.lever_refused", job_id=str(job.id), key=key, reason=str(refused)
                )
                continue
        anchor, result = results[key]
        tables[side].extend(_rows(lead_in or f"Point {number}", option, anchor, result))

    if ledger.records:
        await persist_context(session, ledger, job_id=job.id)
    return len(results)


def _strike(
    ledger: CalculationContext,
    basis: ValuationBasis,
    held: Mapping[str, Quantity],
    option: LeverOption,
    *,
    recorded: Sequence[Calculation],
) -> tuple[Quantity, DcfResult]:
    """The base case with one input moved to the lever's value, struck into ``ledger``."""
    from aer.services.preview import strike_into  # noqa: PLC0415 -- see `_basis`

    anchor = _anchor(ledger, basis, held, option, recorded=recorded)
    moved = _moved(held, option.input, anchor)
    return anchor, strike_into(ledger, basis, moved, case=ARGUED_CASE).result


def _anchor(
    ledger: CalculationContext,
    basis: ValuationBasis,
    held: Mapping[str, Quantity],
    option: LeverOption,
    *,
    recorded: Sequence[Calculation],
) -> Quantity:
    """The lever's value as a sourced quantity: a recorded row, or one struck here."""
    anchor = LeverKey.parse(option.key)
    if anchor is None:  # pragma: no cover -- every offered key parses
        message = f"The lever {option.key!r} is not in the vocabulary."
        raise ValidationError(message, context={"lever": option.key})
    if anchor.anchor is Anchor.RISK_FREE_RATE:
        return held[_RISK_FREE]
    if anchor.anchor in _IMPLIED_ROW:
        return _base_row(recorded, _IMPLIED_ROW[anchor.anchor])

    twin = _recorded_twin(recorded, option)
    if twin is not None:
        return twin
    periods = _oldest_first(basis)
    for index, period in enumerate(periods):
        if _period_label(period) != option.period:
            continue
        prior = periods[index - 1] if index else None
        with ledger.period(option.period, end=period.period_end):
            quantity = _observe(ledger, option.input, period, prior)
        if quantity is not None:
            return quantity
    message = f"The {option.period} observation behind {option.key!r} is no longer on the record."
    raise ValidationError(message, context={"lever": option.key})


def _base_row(recorded: Sequence[Calculation], name: str) -> Quantity:
    """The base case's own recorded figure of this name — the report's, not a preview's."""
    rows = [
        row for row in recorded if row.name == name and (row.parameters or {}).get("case") == "base"
    ]
    if not rows:
        message = f"The base case recorded no {name.replace('_', ' ')} to take a lever from."
        raise ValidationError(message, context={"calculation": name})
    row = max(rows, key=lambda item: item.sequence)
    return _from_row(row)


def _recorded_twin(recorded: Sequence[Calculation], option: LeverOption) -> Quantity | None:
    """The ratio suite's own row for this observation, where it holds one.

    Matched on the value too, not only the name and the year: a row of the same name that
    came out differently is a different derivation, and the lever must move the input to
    exactly what the list said.
    """
    shape = _RECORDED_AS.get(option.input)
    if shape is None:
        return None
    name, parameters = shape
    for row in recorded:
        if (
            row.name == name
            and row.period_label == option.period
            and (row.parameters or {}) == parameters
            and abs(row.output_value - option.value) <= _SAME
        ):
            return _from_row(row)
    return None


def _from_row(row: Calculation) -> Quantity:
    return Quantity.of(
        row.output_value,
        Unit.parse(row.output_unit),
        source=SourceRef.calculation(row.id, label=row.name.replace("_", " ")),
    )


def _rows(
    point: str, option: LeverOption, anchor: Quantity, result: DcfResult
) -> list[dict[str, Any]]:
    """A lever's three rows: its value, and the value per share by each terminal method."""
    per_share = result.gordon.value_per_share.unit.symbol
    return [
        _row(point, option.label, anchor, unit="pure"),
        _row(
            "",
            "Value per share by perpetuity growth",
            result.gordon.value_per_share,
            unit=per_share,
        ),
        _row(
            "",
            "Value per share by the exit multiple",
            result.exit_multiple.value_per_share,
            unit=per_share,
        ),
    ]


def _row(point: str, label: str, figure: Quantity, *, unit: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "point": point,
        "label": label,
        "value": str(figure.value),
        "unit": unit,
    }
    source = figure.source
    if source is not None and source.kind is SourceKind.CALCULATION:
        row["calculation_id"] = source.identifier
    return row


def _tables_present(content: Mapping[str, Any]) -> bool:
    return all(isinstance(content.get(side.priced_field), list) for side in Side)
