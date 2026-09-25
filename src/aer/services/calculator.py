"""The model calculator: the report's own discounted cash flow, struck over the operator's numbers.

ADR 0133. The operator asked for a page per model "where the user can manually change the
values and see how that affects the results". The valuation page cannot be that page: it
reads the ledger and never recomputes, because a page that re-ran the valuation would show
today's answer beside yesterday's report with both looking authoritative. This page
recomputes and says so in its first sentence — and records nothing, so nothing it shows can
be mistaken for the report.

**The report's own model, proved before it is used.** The arithmetic is
:func:`aer.services.preview.strike`, which is the value step's assembly and the value step's
discounted cash flow. Before the operator's numbers are struck, the confirmed values are, and
the answer is held against the report's recorded base case at the ledger's stored precision.
When the two agree the page says the calculator reproduces the report; when they do not —
a later run acquired filings the report predates, and the base year moved — it says that,
and shows both.

**Only what the model reads is offered.** The boxes are the confirmed rows the discounted
cash flow takes. The cost of debt is left out where the filings carry an interest expense,
because the valuation then derives the rate and a typed one would move nothing. An entry
outside the gate's plausible range is refused with the gate's own sentence; one the
arithmetic refuses — growth at the discount rate — is refused with the arithmetic's.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy import select

from aer.calc import basic
from aer.calc.dcf import DRIVER_NAMES, TerminalMethod
from aer.calc.units import Quantity
from aer.core.assumption_scales import assumption_words, scale_complaint
from aer.db.models import Calculation
from aer.errors import AerError
from aer.render import display
from aer.services.assumption_gate import (
    COST_OF_DEBT_ASSUMPTION,
    REQUIRED_NAMES,
    cost_of_debt_required,
)
from aer.services.assumptions import as_quantity, assumptions_for_request
from aer.services.calculations import new_context
from aer.services.preview import Preview, basis_for_run, strike

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.config import HouseStyle
    from aer.db.models import Assumption, Job
    from aer.workflow.workflows.vertical_slice_v1 import ValuationBasis

__all__ = ["CalculatorView", "Figures", "Input", "calculator_view", "result_rows"]

_log = structlog.get_logger("aer.services.calculator")

# The ledger's stored scale: `calculations.output_value` is Numeric(38, 12). A struck figure
# is held against a recorded one at the precision the recorded one was kept at.
_STORED: Final = Decimal("1e-12")

_PER_YEAR_DRIVER: Final = re.compile(
    rf"^(?:{'|'.join(re.escape(name) for name in DRIVER_NAMES)})_y[1-9][0-9]*$"
)


@dataclass(frozen=True, slots=True)
class Input:
    """One confirmed assumption, as the form offers it."""

    name: str
    words: str
    confirmed: Decimal
    # What the box holds: the operator's entry as typed, or the confirmed value.
    entered: str
    changed: bool
    problem: str = ""


@dataclass(frozen=True, slots=True)
class Figures:
    """One base case, reduced to what the page compares. ``None`` where it was not struck."""

    gordon_per_share: Decimal | None
    exit_multiple_per_share: Decimal | None
    disagreement: Decimal | None
    growth_the_multiple_implies: Decimal | None
    multiple_the_growth_implies: Decimal | None
    wacc: Decimal | None
    terminal_share: Decimal | None
    gordon_upside: Decimal | None
    exit_multiple_upside: Decimal | None


@dataclass(frozen=True, slots=True)
class CalculatorView:
    """Everything the page shows, or why it shows nothing."""

    currency: str
    inputs: tuple[Input, ...]
    recorded: Figures
    as_confirmed: Figures | None
    # Whether the confirmed values, struck today, give the report's own base case.
    reproduces: bool
    yours: Figures | None
    refusal: str = ""

    @property
    def any_changed(self) -> bool:
        return any(item.changed for item in self.inputs)


async def calculator_view(
    session: AsyncSession, *, job: Job, entries: Mapping[str, str]
) -> CalculatorView | None:
    """The page for ``job``, with ``entries`` — the operator's numbers by name — applied.

    ``None`` when the run has no discounted cash flow to recalculate: no valuation, a bank's
    residual income, or a run stopped before its assumptions were proposed. The page says
    which rather than showing an empty form.
    """
    basis = await basis_for_run(session, job=job)
    calculations = list(
        await session.scalars(
            select(Calculation).where(Calculation.job_id == job.id).order_by(Calculation.sequence)
        )
    )
    recorded = _recorded(calculations)
    if basis is None or recorded.gordon_per_share is None:
        return None

    rows = [
        row for row in await assumptions_for_request(session, job.work_order_id) if row.approved
    ]
    offered = _offered(rows, basis=basis)
    confirmed = {row.name: as_quantity(row) for row in rows}
    inputs = tuple(_input(row, entries.get(row.name)) for row in offered)
    currency = _currency(calculations)

    as_confirmed, _ = _struck(basis, confirmed)
    reproduces = as_confirmed is not None and _agrees(as_confirmed, recorded)

    yours: Figures | None = None
    refusal = ""
    changes = {item.name: item for item in inputs if item.changed}
    if changes:
        moved = dict(confirmed)
        for item in changes.values():
            held = confirmed[item.name]
            moved[item.name] = Quantity.of(Decimal(item.entered), held.unit, source=held.source)
        yours, refusal = _struck(basis, moved)

    _log.info(
        "calculator.struck",
        job_id=str(job.id),
        changed=len(changes),
        reproduces=reproduces,
        refused=bool(refusal),
    )
    return CalculatorView(
        currency=currency,
        inputs=inputs,
        recorded=recorded,
        as_confirmed=as_confirmed,
        reproduces=reproduces,
        yours=yours,
        refusal=refusal,
    )


def _offered(rows: Sequence[Assumption], *, basis: ValuationBasis) -> list[Assumption]:
    """The confirmed rows the discounted cash flow reads, in the gate's order."""
    wanted = set(REQUIRED_NAMES)
    if cost_of_debt_required(basis.analysis):
        wanted.add(COST_OF_DEBT_ASSUMPTION)
    return [row for row in rows if row.name in wanted or _PER_YEAR_DRIVER.match(row.name)]


def _input(row: Assumption, entry: str | None) -> Input:
    """One box: the confirmed value, or the operator's entry and whether it stands."""
    words = assumption_words(row.name) or row.name.replace("_", " ")
    confirmed = row.value
    if entry is None or not entry.strip():
        return Input(row.name, words, confirmed, _plain(confirmed), changed=False)

    text = entry.strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        return Input(
            row.name,
            words,
            confirmed,
            text,
            changed=False,
            problem=f"{text!r} is not a number. Enter the {words} as a decimal, as it is stored.",
        )
    # The assumptions form's range and sentence, without the form's box to tick: a what-if
    # that reads as a typing mistake is typed again, not overridden.
    complaint = scale_complaint(row.name, value, remedy="")
    if complaint is not None:
        return Input(row.name, words, confirmed, text, changed=False, problem=complaint)
    return Input(row.name, words, confirmed, text, changed=value != confirmed)


def _struck(basis: ValuationBasis, values: Mapping[str, Quantity]) -> tuple[Figures | None, str]:
    """The base case over ``values`` reduced to figures, or the arithmetic's own refusal."""
    try:
        preview = strike(basis, values)
    except AerError as refused:
        return None, str(refused)
    return _figures(preview, price=basis.price_per_share), ""


def _figures(preview: Preview, *, price: Quantity | None) -> Figures:
    result = preview.result
    gordon, exit_multiple = result.gordon, result.exit_multiple
    upside = {
        method: _upside(outcome.value_per_share, price, method=method)
        for method, outcome in (
            (TerminalMethod.GORDON_GROWTH, gordon),
            (TerminalMethod.EXIT_MULTIPLE, exit_multiple),
        )
    }
    return Figures(
        gordon_per_share=gordon.value_per_share.value,
        exit_multiple_per_share=exit_multiple.value_per_share.value,
        disagreement=_value(result.method_disagreement),
        growth_the_multiple_implies=_value(exit_multiple.implied_terminal_growth),
        multiple_the_growth_implies=_value(gordon.implied_exit_multiple),
        wacc=preview.inputs.wacc.value,
        terminal_share=gordon.terminal_share.value,
        gordon_upside=upside[TerminalMethod.GORDON_GROWTH],
        exit_multiple_upside=upside[TerminalMethod.EXIT_MULTIPLE],
    )


def _upside(value: Quantity, price: Quantity | None, *, method: TerminalMethod) -> Decimal | None:
    """The distance from the recorded price, struck by the report's own function."""
    if price is None:
        return None
    try:
        figure = basic.implied_upside(
            new_context(), value_per_share=value, price_per_share=price, measure=method.value
        )
    except AerError:
        return None
    return figure.value


def _recorded(calculations: Sequence[Calculation]) -> Figures:
    """The report's own base case, read off the ledger rather than struck again."""

    def last(name: str, **parameters: str) -> Decimal | None:
        for row in reversed(calculations):
            found = row.parameters or {}
            if row.name == name and all(found.get(k) == v for k, v in parameters.items()):
                return row.output_value
        return None

    base = {"case": "base"}
    return Figures(
        gordon_per_share=last("value_per_share", method=TerminalMethod.GORDON_GROWTH.value, **base),
        exit_multiple_per_share=last(
            "value_per_share", method=TerminalMethod.EXIT_MULTIPLE.value, **base
        ),
        disagreement=last("method_disagreement", **base),
        growth_the_multiple_implies=last("implied_terminal_growth", **base),
        multiple_the_growth_implies=last("implied_exit_multiple", **base),
        wacc=last("wacc") or last("wacc_all_equity"),
        terminal_share=last(
            "terminal_value_share", method=TerminalMethod.GORDON_GROWTH.value, **base
        ),
        gordon_upside=last("implied_upside", measure=TerminalMethod.GORDON_GROWTH.value),
        exit_multiple_upside=last("implied_upside", measure=TerminalMethod.EXIT_MULTIPLE.value),
    )


def _agrees(struck: Figures, recorded: Figures) -> bool:
    """Whether the per-share answers match the ledger's at its stored precision."""
    pairs = (
        (struck.gordon_per_share, recorded.gordon_per_share),
        (struck.exit_multiple_per_share, recorded.exit_multiple_per_share),
    )
    return all(now is not None and then is not None and _stored(now) == then for now, then in pairs)


def _stored(value: Decimal) -> Decimal:
    return value.quantize(_STORED, rounding=ROUND_HALF_UP)


def _currency(calculations: Sequence[Calculation]) -> str:
    """The per-share figures' currency, read off the report's own rows."""
    for row in reversed(calculations):
        if row.name == "value_per_share":
            return row.output_unit.split("/")[0]
    return ""


def _value(figure: Quantity | None) -> Decimal | None:
    return None if figure is None else figure.value


def _plain(value: Decimal) -> str:
    """A stored decimal without the column's trailing zeros, as the box shows it."""
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# What the results table prints, in order: the words a reader meets, which figure, and how it
# reads. The two per-share rows lead, as they lead the report; the upside rows are dropped on
# a run with no price rather than printed empty.
_NOT_STRUCK: Final = "\N{EM DASH}"


def result_rows(view: CalculatorView, *, style: HouseStyle) -> list[dict[str, str]]:
    """The results table, each figure in the notation the report prints it in.

    The operator's column is the confirmed values struck today until something is changed,
    which is what it is; a change the arithmetic refused leaves it empty beside the reason.
    """
    yours = view.yours if view.any_changed else view.as_confirmed

    def money(value: Decimal | None) -> str:
        return _NOT_STRUCK if value is None else display.money(value, view.currency, style=style)

    def share(value: Decimal | None, label: str) -> str:
        if value is None:
            return _NOT_STRUCK
        return display.figure(value, unit="pure", label=label, style=style)

    def times(value: Decimal | None) -> str:
        return _NOT_STRUCK if value is None else display.multiple(value)

    specs: list[tuple[str, str, Any]] = [
        ("Value per share, perpetuity growth", "gordon_per_share", money),
        ("Value per share, exit multiple", "exit_multiple_per_share", money),
        (
            "How far apart the two finish, as a share of the lower",
            "disagreement",
            lambda value: share(value, "percentage"),
        ),
        (
            "Perpetual growth the exit multiple implies",
            "growth_the_multiple_implies",
            lambda value: share(value, "growth"),
        ),
        ("Exit multiple the perpetuity method implies", "multiple_the_growth_implies", times),
        ("Discount rate", "wacc", lambda value: share(value, "discount rate")),
        (
            "Terminal value's share of the perpetuity value",
            "terminal_share",
            lambda value: share(value, "value share"),
        ),
    ]
    priced = any(
        figures is not None and figures.gordon_upside is not None
        for figures in (view.recorded, view.as_confirmed, yours)
    )
    if priced:
        specs += [
            (
                "Distance from the price, perpetuity growth",
                "gordon_upside",
                lambda value: share(value, "upside"),
            ),
            (
                "Distance from the price, exit multiple",
                "exit_multiple_upside",
                lambda value: share(value, "upside"),
            ),
        ]

    rows = []
    for label, field, shown in specs:
        rows.append(
            {
                "label": label,
                "recorded": shown(getattr(view.recorded, field)),
                "as_confirmed": (
                    shown(getattr(view.as_confirmed, field)) if view.as_confirmed else _NOT_STRUCK
                ),
                "yours": shown(getattr(yours, field)) if yours is not None else _NOT_STRUCK,
            }
        )
    return rows
