"""Valuation against the database: assumptions in, lineage and a stored grid out.

Task 27's acceptance criterion is that every output row traces to a fact or a confirmed
assumption. The tests here check it where it can actually break — across the service boundary,
where an assumption is a row somebody confirmed and a grid cell is a row pointing at a
calculation that has to exist.

The scenario test is the one worth the most. A bear case built by copying the base case passes
every test that never changes the base case afterwards, so this one changes it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text

from aer.calc.dcf import BridgeItem, GridAxis, GridMeasure, TerminalMethod
from aer.calc.units import Quantity, SourceKind, SourceRef, money
from aer.core.enums import UserRole
from aer.core.sectors import ValuationModel, unclassified_mandate
from aer.db.models import (
    Assumption,
    Calculation,
    ResearchRequest,
    SensitivityCell,
    User,
)
from aer.services import assumptions as assumption_service
from aer.services import scenarios as scenario_service
from aer.services import valuation as valuation_service
from aer.services.valuation import MissingAssumptionError, inputs_from
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

FACT = SourceRef.fact("balance-sheet-1")

# Microsoft is not a bank, an insurer, a REIT or a pre-revenue biotech, so the standard
# model applies. `test_sectors_service.py` covers the runs where it does not.
MANDATE = unclassified_mandate(ValuationModel.DCF_FCFF, subject="MSFT")

# The five drivers plus the three scalars, at values that produce a healthy forecast.
BASE_ASSUMPTIONS = {
    "revenue_growth": "0.05",
    "ebit_margin": "0.25",
    "capex_intensity": "0.06",
    "depreciation_intensity": "0.05",
    "working_capital_intensity": "0.10",
    "tax_rate": "0.25",
    "terminal_growth": "0.02",
    "exit_multiple": "10",
}


@pytest.fixture
async def scene(db_session: Any) -> dict[str, Any]:
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

    analyst = User(email="analyst@example.invalid", display_name="Analyst", role=UserRole.ANALYST)
    db_session.add(analyst)
    await db_session.flush()

    request = ResearchRequest(
        user_id=analyst.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    job = await seed_job(db_session, request=request)
    return {"request": request, "job": job, "analyst": analyst}


async def confirm_all(session: Any, scene: dict[str, Any], **overrides: str) -> None:
    """Propose and confirm the whole driver set, so a valuation has something to run on."""
    values = BASE_ASSUMPTIONS | overrides
    for name, value in values.items():
        assumption = await assumption_service.propose(
            session,
            request_id=scene["request"].id,
            name=name,
            value=Decimal(value),
            unit="pure",
            justification=f"Test value for {name}.",
            proposed_by="planner",
        )
        await assumption_service.confirm(session, assumption=assumption, actor=scene["analyst"])


def usd(value: str) -> Quantity:
    return money(value, "USD", source=FACT)


def facts() -> dict[str, Any]:
    """The balance-sheet figures a valuation needs alongside its assumptions."""
    return {
        "years": 5,
        "base_revenue": usd("1000"),
        "opening_working_capital": usd("100"),
        "wacc": Quantity.of(Decimal("0.10"), source=SourceRef.calculation("wacc-1", label="wacc")),
        "net_debt": usd("400"),
        "shares_outstanding": Quantity.of(Decimal("100"), "shares", source=FACT),
        "non_operating": (),
    }


# -- Building the input set from what somebody confirmed -------------------------------------


class TestInputsFromAssumptions:
    async def test_a_flat_driver_applies_to_every_year(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)

        inputs = inputs_from(values, **facts())

        assert inputs.years == 5
        assert all(v.value == Decimal("0.050000000000") for v in inputs.revenue_growth.values)

    async def test_per_year_drivers_describe_a_fade(self, db_session, scene):
        await confirm_all(db_session, scene)
        for year, value in enumerate(("0.12", "0.09", "0.07", "0.05", "0.03"), start=1):
            assumption = await assumption_service.propose(
                db_session,
                request_id=scene["request"].id,
                name=f"revenue_growth_y{year}",
                value=Decimal(value),
                unit="pure",
                justification="Fading to the terminal rate.",
                proposed_by="planner",
            )
            await assumption_service.confirm(
                db_session, assumption=assumption, actor=scene["analyst"]
            )
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)

        inputs = inputs_from(values, **facts())

        assert [v.value for v in inputs.revenue_growth.values] == [
            Decimal("0.120000000000"),
            Decimal("0.090000000000"),
            Decimal("0.070000000000"),
            Decimal("0.050000000000"),
            Decimal("0.030000000000"),
        ]

    async def test_a_path_with_a_hole_is_refused_rather_than_filled(self, db_session, scene):
        """Filling year three from the flat value would be a house number nobody wrote."""
        await confirm_all(db_session, scene)
        for year in (1, 2, 4, 5):
            assumption = await assumption_service.propose(
                db_session,
                request_id=scene["request"].id,
                name=f"ebit_margin_y{year}",
                value=Decimal("0.25"),
                unit="pure",
                justification="Partial path.",
                proposed_by="planner",
            )
            await assumption_service.confirm(
                db_session, assumption=assumption, actor=scene["analyst"]
            )
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)

        with pytest.raises(MissingAssumptionError, match="ebit_margin_y3"):
            inputs_from(values, **facts())

    async def test_a_missing_driver_refuses_and_says_what_it_looked_for(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = dict(await assumption_service.confirmed_values(db_session, scene["request"].id))
        del values["capex_intensity"]

        with pytest.raises(MissingAssumptionError, match="no default for any of them"):
            inputs_from(values, **facts())

    async def test_a_missing_scalar_refuses(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = dict(await assumption_service.confirmed_values(db_session, scene["request"].id))
        del values["terminal_growth"]

        with pytest.raises(MissingAssumptionError, match="terminal_growth"):
            inputs_from(values, **facts())

    async def test_an_unconfirmed_driver_never_reaches_the_forecast(self, db_session, scene):
        """`confirmed_values` filters it out, so this refuses as a missing driver."""
        await confirm_all(db_session, scene)
        assumption = await assumption_service.propose(
            db_session,
            request_id=scene["request"].id,
            name="ebit_margin",
            value=Decimal("0.40"),
            unit="pure",
            justification="Re-proposing un-confirms it.",
            proposed_by="planner",
        )
        assert not assumption.approved
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)

        with pytest.raises(MissingAssumptionError, match="ebit_margin"):
            inputs_from(values, **facts())

    async def test_every_driver_carries_its_assumption_source(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)

        inputs = inputs_from(values, **facts())

        for path in inputs.drivers:
            for value in path.values:
                assert value.source is not None
                assert value.source.kind is SourceKind.ASSUMPTION


# -- Running it ------------------------------------------------------------------------------


class TestRunningAValuation:
    async def test_every_calculation_is_persisted(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(values, **facts())

        result = await valuation_service.run_valuation(
            db_session, job_id=scene["job"].id, inputs=inputs, mandate=MANDATE
        )

        rows = list(
            await db_session.scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        )
        names = {row.name for row in rows}
        assert "wacc" not in names, "the discount rate is computed elsewhere and passed in"
        assert {"projected_revenue", "free_cash_flow", "value_per_share"} <= names
        assert result.gordon.value_per_share.value > 0

    async def test_the_per_share_figure_resolves_through_stored_rows(self, db_session, scene):
        """The acceptance criterion, against the database rather than an in-memory ledger."""
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(values, **facts())

        result = await valuation_service.run_valuation(
            db_session, job_id=scene["job"].id, inputs=inputs, mandate=MANDATE
        )

        source = result.gordon.value_per_share.source
        assert source is not None
        stored = await db_session.get(Calculation, uuid.UUID(source.identifier))
        assert stored is not None
        assert stored.name == "value_per_share"
        assert stored.formula.startswith("value per share =")

    async def test_both_terminal_methods_are_stored(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(values, **facts())

        await valuation_service.run_valuation(
            db_session, job_id=scene["job"].id, inputs=inputs, mandate=MANDATE
        )

        rows = list(
            await db_session.scalars(
                select(Calculation).where(
                    Calculation.job_id == scene["job"].id,
                    Calculation.name.in_(["gordon_terminal_value", "exit_multiple_terminal_value"]),
                )
            )
        )
        assert {row.name for row in rows} == {
            "gordon_terminal_value",
            "exit_multiple_terminal_value",
        }

    async def test_the_terminal_share_is_stored_on_every_run(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(values, **facts())

        await valuation_service.run_valuation(
            db_session, job_id=scene["job"].id, inputs=inputs, mandate=MANDATE
        )

        rows = list(
            await db_session.scalars(
                select(Calculation).where(
                    Calculation.job_id == scene["job"].id,
                    Calculation.name == "terminal_value_share",
                )
            )
        )
        assert len(rows) == 2


# -- Scenarios -------------------------------------------------------------------------------


class TestScenarios:
    async def test_a_bear_case_differs_only_where_it_says_it_does(self, db_session, scene):
        await confirm_all(db_session, scene)
        bear = await scenario_service.create_scenario(
            db_session,
            request_id=scene["request"].id,
            key="bear",
            label="Bear",
            description="Margin compression from a price war.",
        )
        await scenario_service.set_override(
            db_session,
            scenario=bear,
            assumption_name="ebit_margin",
            value=Decimal("0.18"),
            unit="pure",
            justification="Two competitors have said they will buy share.",
        )
        base = await scenario_service.create_scenario(
            db_session,
            request_id=scene["request"].id,
            key="base",
            label="Base",
            description="The confirmed assumptions as they stand.",
        )

        valuations = await valuation_service.run_scenarios(
            db_session, job_id=scene["job"].id, scenarios=[bear, base], **facts(), mandate=MANDATE
        )

        by_key = {v.key: v for v in valuations}
        assert by_key["bear"].overridden == ("ebit_margin",)
        assert by_key["base"].overridden == ()
        assert (
            by_key["bear"].result.gordon.value_per_share.value
            < by_key["base"].result.gordon.value_per_share.value
        )

        # Every outcome row is stamped with its case (task 47): the ledger can be read
        # back per scenario, which is what the scenario bridge exhibit draws from.
        rows = list(
            await db_session.scalars(
                select(Calculation).where(
                    Calculation.job_id == scene["job"].id,
                    Calculation.name == "value_per_share",
                )
            )
        )
        recorded_cases = {str(row.parameters.get("case")) for row in rows}
        assert recorded_cases == {"bear", "base"}

    async def test_correcting_the_base_case_moves_the_bear_case_too(self, db_session, scene):
        """The property a copied scenario would fail, and the reason scenarios are diffs."""
        await confirm_all(db_session, scene)
        bear = await scenario_service.create_scenario(
            db_session,
            request_id=scene["request"].id,
            key="bear",
            label="Bear",
            description="Margin compression.",
        )
        await scenario_service.set_override(
            db_session,
            scenario=bear,
            assumption_name="ebit_margin",
            value=Decimal("0.18"),
            unit="pure",
            justification="A price war.",
        )

        before = (
            await valuation_service.run_scenarios(
                db_session, job_id=scene["job"].id, scenarios=[bear], **facts(), mandate=MANDATE
            )
        )[0]

        # Somebody notices the tax rate was wrong. The bear case never argued about tax.
        tax = await db_session.scalar(
            select(Assumption).where(
                Assumption.request_id == scene["request"].id,
                Assumption.name == "tax_rate",
            )
        )
        amended = await assumption_service.amend(
            db_session,
            assumption=tax,
            value=Decimal("0.19"),
            justification="The statutory rate, corrected.",
            actor=scene["analyst"],
        )
        await assumption_service.confirm(db_session, assumption=amended, actor=scene["analyst"])

        after = (
            await valuation_service.run_scenarios(
                db_session, job_id=scene["job"].id, scenarios=[bear], **facts(), mandate=MANDATE
            )
        )[0]

        assert (
            after.result.gordon.value_per_share.value > before.result.gordon.value_per_share.value
        )
        assert after.overridden == ("ebit_margin",)

    async def test_a_scenario_missing_a_driver_stops_the_whole_run(self, db_session, scene):
        """One context for all the cases, so a failure leaves no half-written base case."""
        await confirm_all(db_session, scene)
        broken = await scenario_service.create_scenario(
            db_session,
            request_id=scene["request"].id,
            key="broken",
            label="Broken",
            description="Runs before its drivers are confirmed.",
        )
        capex = await db_session.scalar(
            select(Assumption).where(
                Assumption.request_id == scene["request"].id,
                Assumption.name == "capex_intensity",
            )
        )
        # Un-confirmed the way an amendment does it: the approval fields go together, and
        # the schema refuses `approved = false` with an approver still on the row.
        capex.approved = False
        capex.approved_at = None
        capex.approved_by = None
        await db_session.flush()

        with pytest.raises(MissingAssumptionError):
            await valuation_service.run_scenarios(
                db_session, job_id=scene["job"].id, scenarios=[broken], **facts(), mandate=MANDATE
            )

        rows = list(
            await db_session.scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        )
        assert rows == []


# -- The sensitivity grid --------------------------------------------------------------------


class TestTheStoredGrid:
    async def test_every_cell_points_at_a_calculation_that_exists(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(values, **facts())

        grid, stored = await valuation_service.run_sensitivity(
            db_session,
            request_id=scene["request"].id,
            job_id=scene["job"].id,
            inputs=inputs,
            rows=GridAxis(
                "wacc",
                (
                    Quantity.of(Decimal("0.09"), source=SourceRef.assumption("a")),
                    Quantity.of(Decimal("0.10"), source=SourceRef.assumption("a")),
                    Quantity.of(Decimal("0.11"), source=SourceRef.assumption("a")),
                ),
            ),
            columns=GridAxis(
                "terminal_growth",
                (
                    Quantity.of(Decimal("0.01"), source=SourceRef.assumption("a")),
                    Quantity.of(Decimal("0.02"), source=SourceRef.assumption("a")),
                ),
            ),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.VALUE_PER_SHARE,
            label="Value per share: discount rate against terminal growth",
            mandate=MANDATE,
        )

        cells = list(
            await db_session.scalars(
                select(SensitivityCell).where(SensitivityCell.sensitivity_id == stored.id)
            )
        )
        assert len(cells) == 6
        assert len(grid.cells) == 6
        assert len({cell.output_value for cell in cells}) == 6

        for cell in cells:
            calculation = await db_session.get(Calculation, cell.calculation_id)
            assert calculation is not None
            assert calculation.name == "value_per_share"

    async def test_the_grid_records_which_inputs_it_varied(self, db_session, scene):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(values, **facts())

        _, stored = await valuation_service.run_sensitivity(
            db_session,
            request_id=scene["request"].id,
            job_id=scene["job"].id,
            inputs=inputs,
            rows=GridAxis(
                "wacc",
                (
                    Quantity.of(Decimal("0.09"), source=SourceRef.assumption("a")),
                    Quantity.of(Decimal("0.11"), source=SourceRef.assumption("a")),
                ),
            ),
            columns=GridAxis(
                "exit_multiple",
                (
                    Quantity.of(Decimal("8"), source=SourceRef.assumption("a")),
                    Quantity.of(Decimal("12"), source=SourceRef.assumption("a")),
                ),
            ),
            method=TerminalMethod.EXIT_MULTIPLE,
            measure=GridMeasure.VALUE_PER_SHARE,
            label="Exit multiple grid",
            mandate=MANDATE,
        )

        assert stored.x_assumption == "wacc"
        assert stored.y_assumption == "exit_multiple"
        assert stored.output_name == "value_per_share_exit_multiple"
        assert stored.output_unit == "USD/shares"

    async def test_the_calculations_are_written_before_the_cells_reference_them(
        self, db_session, scene
    ):
        """`sensitivity_cells.calculation_id` is not nullable and restricts on delete.

        Writing the grid first would either fail on the foreign key or, worse, succeed
        against rows some other path had written and point the grid at somebody else's
        arithmetic.
        """
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(values, **facts())

        _, stored = await valuation_service.run_sensitivity(
            db_session,
            request_id=scene["request"].id,
            job_id=scene["job"].id,
            inputs=inputs,
            rows=GridAxis(
                "wacc",
                (
                    Quantity.of(Decimal("0.09"), source=SourceRef.assumption("a")),
                    Quantity.of(Decimal("0.11"), source=SourceRef.assumption("a")),
                ),
            ),
            columns=GridAxis(
                "terminal_growth",
                (
                    Quantity.of(Decimal("0.01"), source=SourceRef.assumption("a")),
                    Quantity.of(Decimal("0.02"), source=SourceRef.assumption("a")),
                ),
            ),
            method=TerminalMethod.GORDON_GROWTH,
            measure=GridMeasure.ENTERPRISE_VALUE,
            label="Enterprise value grid",
            mandate=MANDATE,
        )

        cells = list(
            await db_session.scalars(
                select(SensitivityCell).where(SensitivityCell.sensitivity_id == stored.id)
            )
        )
        # Four distinct valuations, so four distinct calculations *and* four distinct
        # figures. Distinct ids alone would still pass for a grid that ran the base case four
        # times, which is the interpolation failure a stored grid exists to rule out.
        assert len({cell.calculation_id for cell in cells}) == 4
        assert len({cell.output_value for cell in cells}) == 4


# -- The bridge, against real balance-sheet figures ------------------------------------------


class TestTheEquityBridge:
    async def test_non_operating_items_carry_their_labels_into_the_valuation(
        self, db_session, scene
    ):
        await confirm_all(db_session, scene)
        values = await assumption_service.confirmed_values(db_session, scene["request"].id)
        inputs = inputs_from(
            values,
            **(
                facts()
                | {
                    "non_operating": (
                        BridgeItem("Associates at carrying value", usd("120")),
                        BridgeItem("Pension deficit, net of tax", usd("-75")),
                    )
                }
            ),
        )

        result = await valuation_service.run_valuation(
            db_session, job_id=scene["job"].id, inputs=inputs, mandate=MANDATE
        )

        assert [item.label for item in inputs.non_operating] == [
            "Associates at carrying value",
            "Pension deficit, net of tax",
        ]
        bare = result.gordon.enterprise_value.value - Decimal("400")
        assert result.gordon.equity_value.value > bare
