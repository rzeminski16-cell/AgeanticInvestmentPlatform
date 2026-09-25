"""The evidence index offers the base case's rows, never a perturbation's beneath its answer.

Found in the verdict round's MSFT report (roadmap §3.19 item 75). Its thesis quoted discount
factors of 0.6655 in year four and 0.6011 in year five — a 10.72% discount rate, the top row
of the grid around the report's 9.72%. A valuation stamps its case on its answer rows only;
the discount factors, present values and terminal values beneath a grid cell's answer carry
the base case's own parameters, and by sequence the last cell's are the newest. The index
kept the newest row per name and parameters, so it offered every writer, and the red team,
a corner of the grid as the discount rate the report used.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import SENSITIVITY_CASE
from aer.db.models import Calculation
from aer.services.calculations import indexed_calculations, perturbation_only
from tests.valued_run_fixtures import valued_run

# The rows beneath a valuation's answer that carry no case of their own, and which a grid
# over the discount rate or a terminal assumption strikes again under the same parameters.
_BENEATH_THE_ANSWER = (
    "discount_factor",
    "present_value",
    "gordon_terminal_value",
    "exit_multiple_terminal_value",
)


def _row(name: str, *, reads: tuple[Calculation, ...] = (), case: str | None = None) -> Calculation:
    """A recorded row with its inputs pointing at ``reads``, as the ledger writes them."""
    return Calculation(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        sequence=0,
        name=name,
        formula="f",
        function_ref="tests",
        code_version="testsha",
        inputs=[
            {
                "name": parent.name,
                "unit": "pure",
                "value": "1",
                "source": {"id": str(parent.id), "kind": "calculation", "table": "calculations"},
            }
            for parent in reads
        ],
        parameters={"case": case} if case else {},
        assumptions=[],
        output_value=Decimal(1),
        output_unit="pure",
    )


class TestWhatARowBelongsTo:
    """The rule, on rows built by hand: lineage decides, and labels are left alone."""

    def test_a_row_only_a_grid_cell_reads_is_set_aside(self) -> None:
        factor = _row("discount_factor")
        cell = _row("value_per_share", reads=(factor,), case=SENSITIVITY_CASE)

        assert perturbation_only([factor, cell]) == {factor.id}

    def test_a_row_both_read_is_the_base_cases(self) -> None:
        """The engine strikes one derivation once, so a projection the grid shares with the
        base case is the base case's projection."""
        shared = _row("projected_revenue")
        base = _row("value_per_share", reads=(shared,), case="base")
        cell = _row("value_per_share", reads=(shared,), case=SENSITIVITY_CASE)

        assert perturbation_only([shared, base, cell]) == frozenset()

    def test_the_walk_goes_all_the_way_down(self) -> None:
        deep = _row("discount_factor")
        middle = _row("present_value", reads=(deep,))
        cell = _row("enterprise_value", reads=(middle,), case=SENSITIVITY_CASE)

        assert perturbation_only([deep, middle, cell]) == {deep.id, middle.id}

    def test_a_row_no_answer_reads_is_kept(self) -> None:
        """Most of a run: its ratios, its history, its cost of capital."""
        margin = _row("operating_margin")
        cell = _row("value_per_share", case=SENSITIVITY_CASE)

        assert margin.id not in perturbation_only([margin, cell])

    def test_a_scenarios_answer_stays_a_scenarios(self) -> None:
        """The rows beneath a scenario's answer are set aside like a cell's; the answer
        itself carries its case, and the index offers it as that case."""
        projection = _row("projected_ebit")
        answer = _row("value_per_share", reads=(projection,), case="bear")

        excluded = perturbation_only([projection, answer])
        assert projection.id in excluded
        assert answer.id not in excluded

    def test_a_row_derived_from_one_set_aside_is_set_aside(self) -> None:
        """A forecast's EBITDA before its last year feeds no answer at all, so reading
        upwards alone would keep a scenario's year-two EBITDA as the report's."""
        projection = _row("projected_ebit")
        answer = _row("value_per_share", reads=(projection,), case="bear")
        early = _row("forecast_ebitda", reads=(projection,))

        assert perturbation_only([projection, answer, early]) == {projection.id, early.id}

    def test_a_row_derived_from_the_base_case_alone_is_kept(self) -> None:
        projection = _row("projected_ebit")
        base = _row("value_per_share", reads=(projection,), case="base")
        early = _row("forecast_ebitda", reads=(projection,))
        cell = _row("value_per_share", case=SENSITIVITY_CASE)

        assert perturbation_only([projection, base, early, cell]) == frozenset()

    def test_a_run_with_no_perturbation_sets_nothing_aside(self) -> None:
        factor = _row("discount_factor")
        base = _row("value_per_share", reads=(factor,), case="base")

        assert perturbation_only([factor, base]) == frozenset()


@pytest.fixture
async def valued(db_session: AsyncSession) -> dict[str, Any]:
    return await valued_run(db_session)


async def _base_lineage(session: AsyncSession, job_id: uuid.UUID) -> set[uuid.UUID]:
    """Every row the base case's answers read, walked here independently of the rule."""
    rows = list(await session.scalars(select(Calculation).where(Calculation.job_id == job_id)))
    by_id = {str(row.id): row for row in rows}
    frontier = [row for row in rows if (row.parameters or {}).get("case") == "base"]
    found: set[uuid.UUID] = {row.id for row in frontier}
    while frontier:
        row = frontier.pop()
        for raw in row.inputs or []:
            source = raw.get("source") or {}
            parent = by_id.get(str(source.get("id", "")))
            if (
                source.get("kind") == "calculation"
                and parent is not None
                and parent.id not in found
            ):
                found.add(parent.id)
                frontier.append(parent)
    return found


@pytest.mark.integration
class TestTheIndexOffersTheBaseCase:
    async def test_the_grid_struck_rows_the_base_case_could_be_mistaken_for(
        self, valued: dict[str, Any]
    ) -> None:
        """The premise: without it the test below would pass on a run with nothing to hide."""
        session, job = valued["session"], valued["job"]
        base = await _base_lineage(session, job.id)
        factors = list(
            await session.scalars(
                select(Calculation).where(
                    Calculation.job_id == job.id, Calculation.name == "discount_factor"
                )
            )
        )

        outside = [row for row in factors if row.id not in base]
        assert outside, "the grids struck no discount factors of their own"
        assert all(not (row.parameters or {}).get("case") for row in outside), (
            "a grid cell's discount factor carries a case after all, and the defect is gone"
        )

    async def test_every_row_beneath_the_answer_it_offers_is_the_base_cases(
        self, valued: dict[str, Any]
    ) -> None:
        session, job = valued["session"], valued["job"]
        base = await _base_lineage(session, job.id)

        offered = await indexed_calculations(session, job_id=job.id, limit=10_000)

        beneath = [row for row in offered if row.name in _BENEATH_THE_ANSWER]
        assert {row.name for row in beneath} == set(_BENEATH_THE_ANSWER), (
            "the base case's own rows beneath its answer went missing with the grid's"
        )
        strays = [
            f"{row.name} {row.parameters} = {row.output_value}"
            for row in beneath
            if row.id not in base
        ]
        assert not strays, f"the index offered a grid cell's rows as the base case's: {strays}"

        # A forecast year's EBITDA before the last is read by no answer, so it is judged by
        # what it was derived from: the base case's own projection, never a grid cell's.
        for row in offered:
            if row.name == "forecast_ebitda":
                parents = {
                    uuid.UUID(str(raw["source"]["id"]))
                    for raw in row.inputs
                    if (raw.get("source") or {}).get("kind") == "calculation"
                }
                assert parents <= base, "the index offered a grid cell's forecast EBITDA"

    async def test_a_discount_factor_offered_is_struck_at_the_reports_discount_rate(
        self, valued: dict[str, Any]
    ) -> None:
        """The same failure read the way the MSFT thesis read it: the rate behind a factor.

        Compared with the rate the base case's own factors read rather than with the `wacc`
        row's output, which the ledger stores to twelve places where an input keeps every
        digit the arithmetic used.
        """
        session, job = valued["session"], valued["job"]
        base = await _base_lineage(session, job.id)
        factors = list(
            await session.scalars(
                select(Calculation).where(
                    Calculation.job_id == job.id, Calculation.name == "discount_factor"
                )
            )
        )
        reports_rate = {_rate_read_by(row) for row in factors if row.id in base}
        assert len(reports_rate) == 1, reports_rate

        offered = await indexed_calculations(session, job_id=job.id, limit=10_000)

        for row in offered:
            if row.name == "discount_factor":
                assert {_rate_read_by(row)} == reports_rate, (
                    f"year {row.parameters.get('year')} was offered at a discount rate of "
                    f"{_rate_read_by(row)}, where the report's is {reports_rate.pop()}"
                )


def _rate_read_by(factor: Calculation) -> Decimal:
    return next(Decimal(str(raw["value"])) for raw in factor.inputs if raw.get("name") == "wacc")
