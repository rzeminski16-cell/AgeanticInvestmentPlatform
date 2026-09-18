"""The composed half of the report's view: a range, a spread, a distance, and the levers.

ADR 0117. `reports.rating` is assigned ``None`` in one place and written nowhere, so every
report this platform has ever produced printed *"no view reached"* — not because no view
was reached but because nothing was wired to reach one. Nine blind comparisons went against
it and the most-cited reason was that the console's answer said what it thought and this
one did not.

**Code composes one half; the operator authors the other; the model writes neither.** This
module is the first half, and it ships and is judged alone, because AstraZeneca's second run
stated a view, argued it, and moved no judge — so stating a conclusion is necessary and
demonstrably not sufficient, and two halves landing together could not be told apart.

**Composed means assembled, never computed.** Every figure here is a row the run already
struck: the base case and the scenarios are `value_per_share` calculations tagged by case,
the distance from the price is an `implied_upside` calculation, and a lever is a grid cell
beside its axis value. Nothing in this file does arithmetic — a renderer that subtracted a
price from a value would produce a figure no ledger row accounts for and no footnote could
point at, which is the platform's founding rule read backwards.

**No adjectives.** Not "attractive", not "compelling", not "cautious". A range, the method
that produced it, a distance from the market and the levers that move it — every one a
recorded calculation with a marker a reader can follow. What the figures *mean* is the
operator's half, and it is not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.db.models import Calculation, Job, Sensitivity

__all__ = ["VIEW_CONTRACT", "VIEW_TITLE", "ComposedView", "base_range", "view_content"]

_log = structlog.get_logger("aer.render.view")

VIEW_TITLE: Final = "The view"

# The walk's contract: field order is display order, and each row names the calculation it
# came from so the walk footnotes it exactly as it footnotes a section's figures.
VIEW_CONTRACT: Final[dict[str, Any]] = {
    "type": "object",
    "title": VIEW_TITLE,
    "properties": {
        "base": {
            "type": "array",
            "title": "Base case, by method",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                },
            },
        },
        "against_the_price": {
            "type": "array",
            "title": "Against the market price",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                },
            },
        },
        "scenarios": {
            "type": "array",
            "title": "Scenarios",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                },
            },
        },
        "levers": {
            "type": "array",
            "title": "What would change it",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                },
            },
        },
    },
}

# The terminal methods, in display order, with the words a reader meets. The keys are the
# ones `aer.calc.dcf.TerminalMethod` records on every row it strikes.
_METHODS: Final[tuple[tuple[str, str], ...]] = (
    ("gordon_growth", "Perpetuity growth"),
    ("exit_multiple", "Exit multiple"),
)

# The calculation whose case-tagged rows are the base case and each scenario.
_PER_SHARE: Final = "value_per_share"

# The distance from the market, one row per terminal method, tagged by `measure`.
_UPSIDE: Final = "implied_upside"

# The case a sensitivity cell is tagged with. Read off the stored corpus rather than
# assumed: 180 of the 186 per-share rows on the audited runs carry it.
_SENSITIVITY: Final = "sensitivity"

# The assumption names as a reader meets them. A lever printed as `terminal_growth` is a
# code identifier on a page, which Phase 1.4's ratchet exists to keep out of one.
_LEVER_WORDS: Final[dict[str, str]] = {
    "wacc": "the discount rate",
    "terminal_growth": "the terminal growth rate",
    "exit_multiple": "the exit multiple",
    "revenue_growth": "the revenue growth rate",
    "ebit_margin": "the operating margin",
}


@dataclass(frozen=True, slots=True)
class ComposedView:
    """The block's content, or nothing where the run reached no valuation.

    ``None`` is an ordinary outcome and the honest one: a run that produced no discounted
    cash flow has no range to state, and *"no view reached"* is true of it.
    """

    content: dict[str, Any] | None


async def view_content(session: AsyncSession, *, job: Job) -> ComposedView:
    """Assemble the composed half from the run's own ledger.

    Reads calculations rather than the valuation step's record, for the reason the
    valuation page gives: the step's record keeps per-share summaries and the ledger keeps
    the rows, and a figure printed with a footnote has to come from the thing the footnote
    resolves to.
    """
    calculations = list(
        await session.scalars(
            select(Calculation).where(Calculation.job_id == job.id).order_by(Calculation.sequence)
        )
    )
    by_case = _per_share_by_case(calculations)

    content: dict[str, Any] = {}
    base = _base_rows(by_case)
    if base:
        content["base"] = base
    against = _upside_rows(calculations)
    if against:
        content["against_the_price"] = against
    scenarios = _scenario_rows(by_case)
    if scenarios:
        content["scenarios"] = scenarios
    levers = await _lever_rows(session, job=job)
    if levers:
        content["levers"] = levers

    if not content:
        return ComposedView(content=None)

    _log.info(
        "view.composed",
        job_id=str(job.id),
        methods=len(content.get("base", ())),
        priced=len(content.get("against_the_price", ())),
        scenarios=len(content.get("scenarios", ())),
        levers=len(content.get("levers", ())),
    )
    return ComposedView(content=content)


def _per_share_by_case(calculations: list[Calculation]) -> dict[str, list[Calculation]]:
    """Every per-share valuation the run struck, grouped by the case it belongs to.

    A ``value_per_share`` row belongs to the base case, a scenario or a sensitivity cell,
    and the case is a recorded parameter rather than something inferred from order.

    **Grid cells are excluded, and the corpus is what says how.** They are tagged
    ``case: "sensitivity"`` — not left untagged, which the first draft of this assumed —
    so 180 of the 186 per-share rows on the stored runs are grid points. Reading them as
    conclusions would have printed ninety "scenarios" per method, which is a working
    paper. The levers block shows the grid, once, as a grid.
    """
    grouped: dict[str, list[Calculation]] = {}
    for row in calculations:
        if row.name != _PER_SHARE:
            continue
        case = (row.parameters or {}).get("case")
        if not isinstance(case, str) or case == _SENSITIVITY:
            continue
        grouped.setdefault(case, []).append(row)
    return grouped


def _base_rows(by_case: dict[str, list[Calculation]]) -> list[dict[str, str]]:
    """The base case, one row per terminal method, in the order a reader meets them."""
    return _method_rows(by_case.get("base", ()))


def _method_rows(rows: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for key, label in _METHODS:
        row = next(
            (item for item in reversed(list(rows)) if (item.parameters or {}).get("method") == key),
            None,
        )
        if row is not None:
            found.append(_figure(label, row))
    return found


def _upside_rows(calculations: list[Calculation]) -> list[dict[str, str]]:
    """The distance from the market, one row per method.

    Keyed on the recorded ``measure`` rather than on ledger order: a run strikes one of
    these per terminal method, and two rows differing only in an input are two rows
    nothing can label by reading them (roadmap §3.19.4).
    """
    found: list[dict[str, str]] = []
    for key, label in _METHODS:
        row = next(
            (
                item
                for item in reversed(calculations)
                if item.name == _UPSIDE and (item.parameters or {}).get("measure") == key
            ),
            None,
        )
        if row is not None:
            found.append(_figure(f"Upside on the {label.lower()} value", row))
    return found


def _scenario_rows(by_case: dict[str, list[Calculation]]) -> list[dict[str, str]]:
    """Each scenario's per-share answer, base excluded — it has its own block above.

    One row per case rather than per method: the spread is what the scenarios are for, and
    a reader comparing four cases across two methods is reading a grid, which is what the
    valuation page is for.

    **Empty on every run so far, and that is a fact about the runs.** A scenario is the
    operator's to define and none has defined one — the `scenarios` table is empty across
    the whole stored corpus — so ADR 0117's "bear and bull, as figures" describes a
    category the composed half supports and no run has yet filled. The block omits it
    rather than apologising for it, exactly as the front page omits an empty category.
    """
    found: list[dict[str, str]] = []
    for case, rows in by_case.items():
        if case == "base":
            continue
        row = rows[-1]
        found.append(_figure(_spoken_case(case), row))
    return found


def _spoken_case(case: str) -> str:
    """A scenario key as a reader meets it: "bear" is a word, "downside_2" is not."""
    return case.replace("_", " ").strip().capitalize()


async def _lever_rows(session: AsyncSession, *, job: Job) -> list[dict[str, str]]:
    """What the run's own sensitivity says the answer turns on.

    One pair of rows per grid — the lowest and the highest per-share answer it reached,
    with the two assumptions it varied named in the label. Both are stored cells carrying
    their own calculation, so both footnote like any other figure.

    **Not one row per axis.** The first draft did that, and the extreme of a grid is the
    same cell whichever axis you name it after: three levers came out reading $32.47,
    $32.47 and $32.25 under three different headings, which is a label describing nothing.
    The swing belongs to the grid, and the grid is what the pair says.

    **Selected, never computed.** Choosing which stored cells to print is the same kind of
    act as the front page choosing which calculations are headlines; the figures are the
    cells themselves.
    """
    grids = list(
        await session.scalars(
            select(Sensitivity)
            .where(Sensitivity.job_id == job.id)
            .options(selectinload(Sensitivity.cells))
            .order_by(Sensitivity.created_at)
        )
    )

    found: list[dict[str, str]] = []
    for grid in grids:
        if not grid.cells:
            continue
        varied = " and ".join(
            _LEVER_WORDS.get(axis, axis) for axis in (grid.y_assumption, grid.x_assumption)
        )
        low = min(grid.cells, key=lambda cell: cell.output_value)
        high = max(grid.cells, key=lambda cell: cell.output_value)
        found.extend(
            {
                "label": f"{edge} across {varied}",
                "value": str(cell.output_value),
                "unit": grid.output_unit,
                "calculation_id": str(cell.calculation_id),
            }
            for edge, cell in (("Lowest", low), ("Highest", high))
        )
    return found


def _figure(label: str, row: Calculation) -> dict[str, str]:
    """One recorded calculation as a row the walk can footnote."""
    return {
        "label": label,
        "value": str(row.output_value),
        "unit": row.output_unit,
        "calculation_id": str(row.id),
    }


def base_range(content: dict[str, Any] | None) -> tuple[Decimal, Decimal, str] | None:
    """The low and high of the base case, for the header line, or ``None``.

    The header says *"no view reached"* only where that is true — a run with no valuation
    — rather than on every run, which is what it did until ADR 0117 (F13). Read off the
    same rows the block prints, so the two cannot disagree.
    """
    rows = (content or {}).get("base") or []
    values = [Decimal(row["value"]) for row in rows if row.get("value")]
    if not values:
        return None
    unit = str(rows[0].get("unit", ""))
    return min(values), max(values), unit
