"""What the valuation page shows, assembled from what the run recorded.

**Read back, never recomputed.** The obvious implementation of this page re-runs the
valuation and renders the answer, and it is wrong in a way that would take months to notice:
an assumption amended after the run, a corrected fact, a change to the arithmetic itself —
each would silently give the page a different figure from the report the run produced. A
valuation surface whose numbers drift away from the report they describe is worse than no
surface, because both look authoritative and only one of them is what happened.

So everything here comes from `calculations`, `sensitivities` and `approvals` rows written
during the run. Where a figure is absent, the page says the run did not produce it rather
than producing it now.

**Every figure carries the id of the calculation that produced it.** That is what makes the
two-click standard reachable: a figure links to its calculation, and the calculation's inputs
link to the facts and assumptions underneath. `docs/PLAN.md` set that standard for evidence in
Phase 2; this applies it to arithmetic.

Two calculations per valuation share each name — one per terminal method — and they are told
apart by the ``method`` parameter recorded on them. That parameter exists because this module
needed it: before it, the ledger held two `value_per_share` rows with different answers and
nothing saying which was which.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.calc.comps import Audience, CompsTable, WithheldComps
from aer.calc.dcf import HIGH_TERMINAL_SHARE, TerminalMethod
from aer.core.sectors import ValuationModel
from aer.db.models import Calculation, Job, Sensitivity
from aer.services.sectors import confirmed_classification

__all__ = [
    "OUTCOME_NAMES",
    "Figure",
    "GridView",
    "SectorBanner",
    "TerminalView",
    "ValuationView",
    "valuation_view",
]

_log = structlog.get_logger("aer.services.valuation_view")

# The calculations that exist once per terminal method. Read back by name *and* by the method
# recorded on them; either alone would be ambiguous.
OUTCOME_NAMES: Final[tuple[str, ...]] = (
    "enterprise_value",
    "equity_value",
    "terminal_value_share",
    "value_per_share",
)


@dataclass(frozen=True, slots=True)
class Figure:
    """One number on the page, and the calculation it came from.

    ``calculation_id`` is not optional in spirit: a figure with no calculation behind it has
    no business on this page, and :func:`valuation_view` produces no such figure. It is typed
    as a string because the template writes it into a URL.
    """

    label: str
    value: Decimal
    unit: str
    calculation_id: str
    formula: str = ""

    @property
    def href(self) -> str:
        return f"/calculations/{self.calculation_id}"


@dataclass(frozen=True, slots=True)
class TerminalView:
    """One terminal method, carried through to a per-share figure."""

    method: TerminalMethod
    label: str
    enterprise_value: Figure | None
    equity_value: Figure | None
    terminal_share: Figure | None
    value_per_share: Figure | None

    @property
    def is_complete(self) -> bool:
        return all(
            item is not None
            for item in (
                self.enterprise_value,
                self.equity_value,
                self.terminal_share,
                self.value_per_share,
            )
        )

    @property
    def terminal_share_is_high(self) -> bool:
        """Whether most of the answer lies beyond the forecast.

        Shown beside the figure rather than left for a reader to work out. A discounted cash
        flow whose terminal value is three-quarters of the answer is a statement about one
        assumption, not about the years anybody can check.
        """
        return self.terminal_share is not None and self.terminal_share.value > HIGH_TERMINAL_SHARE


@dataclass(frozen=True, slots=True)
class GridView:
    """A stored sensitivity grid, laid out for a table.

    ``rows`` is a list of ``(y_value, [(x_value, output, calculation_id), ...])``. Assembled
    here rather than in the template, because a template that pivoted a flat cell list would
    be doing arithmetic in Jinja, and a grid transposed by mistake is a grid that looks fine.
    """

    label: str
    x_assumption: str
    y_assumption: str
    output_name: str
    output_unit: str
    x_values: tuple[Decimal, ...]
    rows: tuple[tuple[Decimal, tuple[tuple[Decimal, Decimal, str], ...]], ...]

    @property
    def cell_count(self) -> int:
        return sum(len(cells) for _, cells in self.rows)


@dataclass(frozen=True, slots=True)
class SectorBanner:
    """What a specialist classification obliges this page to say, before any figure.

    Assembled from the *confirmed* classification. A run whose specialist proposal was never
    agreed has already been stopped by the gate, so reaching here means somebody said yes.
    """

    label: str
    blocked_models: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def blocks_the_dcf(self) -> bool:
        """Whether the model this page renders was blocked for this sector.

        The page shows the banner **and no valuation** when this is true — not a valuation
        with the banner above it. A number a reader has seen is a number they remember.

        Compared against the enum's own members rather than by looking for "DCF" in the
        string. The values are lowercase, so a substring test would have matched nothing and
        the banner would have appeared above a full valuation — which is the exact failure
        this property exists to prevent.
        """
        return any(model in _DCF_MODELS for model in self.blocked_models)


@dataclass(frozen=True, slots=True)
class ValuationView:
    """Everything the valuation page renders, and nothing it computes."""

    job_id: uuid.UUID
    sector: SectorBanner | None
    gordon: TerminalView
    exit_multiple: TerminalView
    grids: tuple[GridView, ...] = ()
    comps: CompsTable | WithheldComps | None = None
    caveats: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_valuation(self) -> bool:
        return self.gordon.is_complete or self.exit_multiple.is_complete

    @property
    def methods_disagree(self) -> Decimal | None:
        """How far apart the two methods are, as a fraction of the lower one.

        ``None`` when either is missing or the lower is not positive. Computed from two
        recorded figures rather than recomputed from the inputs, so it describes the run.
        """
        if self.gordon.value_per_share is None or self.exit_multiple.value_per_share is None:
            return None

        low = min(self.gordon.value_per_share.value, self.exit_multiple.value_per_share.value)
        high = max(self.gordon.value_per_share.value, self.exit_multiple.value_per_share.value)
        if low <= 0:
            return None
        return (high - low) / low


async def valuation_view(
    session: AsyncSession,
    job: Job,
    *,
    comps: CompsTable | None = None,
    audience: Audience = Audience.INTERNAL,
) -> ValuationView:
    """Assemble the page from the run's own record.

    Args:
        comps: The comparables table, when one was built. Rendered at ``audience`` — which
            defaults to ``INTERNAL`` because **this page is not exported**. The Markdown
            report is the shareable artefact and takes a `WithheldComps`; see ADR 0034.
    """
    calculations = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job.id)
            .order_by(Calculation.created_at, Calculation.sequence)
        )
    )

    profile, _ = await confirmed_classification(session, job)
    banner = (
        SectorBanner(
            label=profile.label,
            blocked_models=tuple(model.value for model in profile.blocked_models),
            warnings=profile.warnings,
        )
        if profile is not None
        else None
    )

    view = ValuationView(
        job_id=job.id,
        sector=banner,
        gordon=_terminal_view(calculations, method=TerminalMethod.GORDON_GROWTH),
        exit_multiple=_terminal_view(calculations, method=TerminalMethod.EXIT_MULTIPLE),
        grids=await _grids_for(session, job.id),
        comps=comps.for_audience(audience) if comps is not None else None,
        caveats=(),
    )

    _log.info(
        "valuation_view.assembled",
        job_id=str(job.id),
        calculations=len(calculations),
        grids=len(view.grids),
        has_valuation=view.has_valuation,
        sector=banner.label if banner else "unclassified",
    )
    return view


# -- Reading the ledger back ------------------------------------------------------------------


def _terminal_view(calculations: Sequence[Calculation], *, method: TerminalMethod) -> TerminalView:
    """One method's figures, picked out of the ledger by name and recorded method."""
    found = {
        name: _figure(_latest(calculations, name=name, method=method), label=_LABELS[name])
        for name in OUTCOME_NAMES
    }
    return TerminalView(
        method=method,
        label=_METHOD_LABELS[method],
        enterprise_value=found["enterprise_value"],
        equity_value=found["equity_value"],
        terminal_share=found["terminal_value_share"],
        value_per_share=found["value_per_share"],
    )


def _latest(
    calculations: Sequence[Calculation], *, name: str, method: TerminalMethod
) -> Calculation | None:
    """The most recent calculation of this name run under this terminal method.

    **The most recent, not the first.** A run that was resumed, or one whose scenarios each
    valued the business, writes several; the page describes the last state the run reached,
    which is the one the report was written from.

    "Most recent" is by ``created_at`` and then by ``sequence`` within the ledger, and the
    order of those two matters. Timestamp alone cannot do it — Postgres `now()` is
    transaction-start time, so every calculation one context persists carries the same one and
    the tie-break was a random UUID. Sequence alone cannot either: it restarts at zero for
    each context, so two contexts would interleave.

    **Two valuations persisted in a single transaction remain genuinely ambiguous**, and that
    is honest rather than a gap: they carry the same timestamp and overlapping sequences, and
    nothing recorded says which came second. A resumed run is a later transaction, which is
    the case this ordering exists for. Migration 0019 exists because of this paragraph.
    """
    matching = [
        row
        for row in calculations
        if row.name == name and str(row.parameters.get("method", "")) == method.value
    ]
    return matching[-1] if matching else None


def _figure(calculation: Calculation | None, *, label: str) -> Figure | None:
    if calculation is None:
        return None
    return Figure(
        label=label,
        value=calculation.output_value,
        unit=calculation.output_unit,
        calculation_id=str(calculation.id),
        formula=calculation.formula,
    )


async def _grids_for(session: AsyncSession, job_id: uuid.UUID) -> tuple[GridView, ...]:
    """Every sensitivity grid this run stored, pivoted for a table."""
    stored = list(
        await session.scalars(
            select(Sensitivity)
            .where(Sensitivity.job_id == job_id)
            .options(selectinload(Sensitivity.cells))
            .order_by(Sensitivity.created_at)
        )
    )

    grids: list[GridView] = []
    for grid in stored:
        x_values = sorted({cell.x_value for cell in grid.cells})
        y_values = sorted({cell.y_value for cell in grid.cells})
        by_point = {
            (cell.x_value, cell.y_value): (cell.output_value, str(cell.calculation_id))
            for cell in grid.cells
        }

        rows: list[tuple[Decimal, tuple[tuple[Decimal, Decimal, str], ...]]] = []
        for y in y_values:
            cells: list[tuple[Decimal, Decimal, str]] = []
            for x in x_values:
                point = by_point.get((x, y))
                if point is not None:
                    cells.append((x, point[0], point[1]))
            rows.append((y, tuple(cells)))

        grids.append(
            GridView(
                label=grid.label,
                x_assumption=grid.x_assumption,
                y_assumption=grid.y_assumption,
                output_name=grid.output_name,
                output_unit=grid.output_unit,
                x_values=tuple(x_values),
                rows=tuple(rows),
            )
        )

    return tuple(grids)


# The models this page renders. A sector that blocks one of them gets the banner and no
# valuation.
_DCF_MODELS: Final[frozenset[str]] = frozenset(
    {ValuationModel.DCF_FCFF.value, ValuationModel.DCF_FCFE.value}
)

_LABELS: Final[dict[str, str]] = {
    "enterprise_value": "Enterprise value",
    "equity_value": "Equity value",
    "terminal_value_share": "Terminal value share",
    "value_per_share": "Value per share",
}

_METHOD_LABELS: Final[dict[TerminalMethod, str]] = {
    TerminalMethod.GORDON_GROWTH: "Gordon growth",
    TerminalMethod.EXIT_MULTIPLE: "Exit multiple",
}


def lineage_rows(tree: Any) -> list[dict[str, Any]]:
    """A lineage tree flattened for a table, with a depth per row.

    The calculation page renders a tree, and a nested Jinja macro recursing over it is both
    hard to read and hard to test. Flattening here means the template is a loop.
    """
    rows: list[dict[str, Any]] = []

    def walk(node: Any, depth: int) -> None:
        rows.append(
            {
                "kind": node.kind,
                "id": node.identifier,
                "label": node.label,
                "value": node.value,
                "unit": node.unit,
                "detail": node.detail,
                "depth": depth,
                "is_leaf": node.is_leaf,
                "is_resolved": node.is_resolved,
            }
        )
        for child in node.inputs:
            walk(child, depth + 1)

    walk(tree, 0)
    return rows
