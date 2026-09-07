"""Assumptions: who proposed, who confirmed, and what a rejected proposal leaves behind.

The three properties `docs/archive/phase-3-plan.md` task 24 asks for, each tested against the
database rather than against a stub:

- an unconfirmed assumption cannot enter a calculation;
- an amended assumption keeps the original proposal on the record;
- a scenario is a diff, so a base-case change propagates.

The third is the one worth the most: a scenario built by copying the base case passes every
test that does not change the base case afterwards, which is why the test here changes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from aer.calc.units import Unit
from aer.core.enums import UserRole
from aer.db.models import (
    AssumptionProposal,
    ResearchRequest,
    ScenarioOverride,
    SensitivityCell,
    User,
)
from aer.errors import ValidationError
from aer.services import assumptions as assumption_service
from aer.services import scenarios as scenario_service
from aer.services.assumptions import UnconfirmedAssumptionError
from aer.services.scenarios import CellInput
from tests.request_fixtures import research_request
from tests.workflow_fixtures import AS_OF_DATE, seed_job

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

DISCOUNT_RATE = "discount_rate"


@pytest.fixture
async def scene(db_session: Any) -> dict[str, Any]:
    """A request, an analyst and a second person, on a clean slate."""
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))

    analyst = User(email="analyst@example.invalid", display_name="Analyst", role=UserRole.ANALYST)
    reviewer = User(email="reviewer@example.invalid", display_name="Reviewer", role=UserRole.OWNER)
    db_session.add_all([analyst, reviewer])
    await db_session.flush()

    request = research_request(
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

    return {"request": request, "job": job, "analyst": analyst, "reviewer": reviewer}


async def propose_rate(session: Any, request: ResearchRequest, value: str = "0.09", **kw: Any):
    return await assumption_service.propose(
        session,
        request_id=request.id,
        name=DISCOUNT_RATE,
        value=Decimal(value),
        unit=kw.pop("unit", "pure"),
        justification=kw.pop(
            "justification", "CAPM with a 4.2% equity risk premium and a beta of 1.1."
        ),
        proposed_by=kw.pop("proposed_by", "valuation_interpretation"),
        **kw,
    )


class TestAModelMayProposeOnlyAPersonMayConfirm:
    async def test_a_proposal_is_not_confirmed_however_confident(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"], confidence=0.99)
        assert assumption.approved is False
        assert assumption.approved_by is None

    async def test_a_person_typing_a_value_has_still_only_proposed_it(self, db_session, scene):
        """A reviewer who scrolls past a row on the assumptions page has not agreed to it."""
        assumption = await propose_rate(
            db_session, scene["request"], proposed_by=scene["analyst"].email, by_human=True
        )
        assert assumption.approved is False

    async def test_confirming_records_who_and_when(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"])
        confirmed = await assumption_service.confirm(
            db_session, assumption=assumption, actor=scene["reviewer"]
        )

        assert confirmed.approved is True
        assert confirmed.approved_by == "reviewer@example.invalid"
        assert confirmed.approved_at is not None

    async def test_confirming_twice_is_refused(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"])
        await assumption_service.confirm(db_session, assumption=assumption, actor=scene["reviewer"])

        with pytest.raises(ValidationError, match="already confirmed"):
            await assumption_service.confirm(
                db_session, assumption=assumption, actor=scene["reviewer"]
            )


class TestAnUnconfirmedAssumptionCannotEnterACalculation:
    async def test_as_quantity_refuses_it(self, db_session, scene):
        """The acceptance criterion, at the point the number would be used."""
        assumption = await propose_rate(db_session, scene["request"])

        with pytest.raises(UnconfirmedAssumptionError, match="only a person may confirm"):
            assumption_service.as_quantity(assumption)

    async def test_a_confirmed_one_becomes_a_sourced_quantity(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"])
        await assumption_service.confirm(db_session, assumption=assumption, actor=scene["reviewer"])

        quantity = assumption_service.as_quantity(assumption)
        assert quantity.value == Decimal("0.09")
        assert quantity.unit == Unit.parse("pure")
        assert quantity.source.kind == "assumption"
        assert quantity.source.identifier == str(assumption.id)

    async def test_the_base_case_contains_only_confirmed_values(self, db_session, scene):
        await propose_rate(db_session, scene["request"])
        terminal = await assumption_service.propose(
            db_session,
            request_id=scene["request"].id,
            name="terminal_growth",
            value=Decimal("0.02"),
            unit="pure",
            justification="Long-run nominal GDP growth for the group's main markets.",
            proposed_by="valuation_interpretation",
        )
        await assumption_service.confirm(db_session, assumption=terminal, actor=scene["reviewer"])

        base = await assumption_service.confirmed_values(db_session, scene["request"].id)
        assert set(base) == {"terminal_growth"}

    async def test_the_unconfirmed_ones_are_listed_so_they_can_be_chased(self, db_session, scene):
        await propose_rate(db_session, scene["request"])
        outstanding = await assumption_service.unconfirmed_for_request(
            db_session, scene["request"].id
        )
        assert [a.name for a in outstanding] == [DISCOUNT_RATE]


class TestAnAmendmentKeepsTheOriginalOnTheRecord:
    async def test_the_first_proposal_survives(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        await assumption_service.amend(
            db_session,
            assumption=assumption,
            value=Decimal("0.11"),
            justification="The model's beta ignores the pending disposal of the US division.",
            actor=scene["reviewer"],
        )

        history = await assumption_service.history_of(db_session, assumption.id)
        assert [p.value for p in history] == [Decimal("0.09"), Decimal("0.11")]

    async def test_the_current_value_is_the_amendment(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        amended = await assumption_service.amend(
            db_session,
            assumption=assumption,
            value=Decimal("0.11"),
            justification="Beta reflects a business the company is about to sell.",
            actor=scene["reviewer"],
        )
        assert amended.value == Decimal("0.11")

    async def test_the_history_says_who_said_what(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        await assumption_service.amend(
            db_session,
            assumption=assumption,
            value=Decimal("0.11"),
            justification="Beta reflects a business the company is about to sell.",
            actor=scene["reviewer"],
        )

        history = await assumption_service.history_of(db_session, assumption.id)
        assert [p.proposed_by for p in history] == [
            "valuation_interpretation",
            "reviewer@example.invalid",
        ]
        assert [p.by_human for p in history] == [False, True]

    async def test_the_original_justification_is_not_overwritten(self, db_session, scene):
        """The most useful question about a valuation is what a number was chosen over."""
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        await assumption_service.amend(
            db_session,
            assumption=assumption,
            value=Decimal("0.11"),
            justification="Beta reflects a business the company is about to sell.",
            actor=scene["reviewer"],
        )

        history = await assumption_service.history_of(db_session, assumption.id)
        assert "equity risk premium" in history[0].justification
        assert "about to sell" in history[1].justification

    async def test_each_proposal_points_at_the_one_it_replaced(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        await assumption_service.amend(
            db_session,
            assumption=assumption,
            value=Decimal("0.11"),
            justification="Beta reflects a business the company is about to sell.",
            actor=scene["reviewer"],
        )

        history = await assumption_service.history_of(db_session, assumption.id)
        assert history[0].supersedes_id is None
        assert history[1].supersedes_id == history[0].id

    async def test_three_proposals_in_one_transaction_share_a_timestamp(self, db_session, scene):
        """Which is why `sequence` exists. Postgres `now()` is transaction-start time."""
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        for value in ("0.10", "0.11"):
            await assumption_service.amend(
                db_session,
                assumption=assumption,
                value=Decimal(value),
                justification=f"Revised to {value} after reviewing the disposal timetable.",
                actor=scene["reviewer"],
            )

        history = await assumption_service.history_of(db_session, assumption.id)
        assert [p.sequence for p in history] == [1, 2, 3]
        assert len({p.created_at for p in history}) == 1

    async def test_the_history_is_ordered_by_sequence_not_by_timestamp(self, db_session, scene):
        """The timestamps are set to *contradict* the sequence, so the two orderings differ.

        Without this the test cannot tell them apart: rows written in one transaction share a
        `created_at`, and Postgres happens to return them in insertion order, so ordering on
        the timestamp passes by luck. It would stop passing the first time a row was updated
        and moved in the heap — silently, on the page a reviewer reads to see what a number
        was chosen over.
        """
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        for value in ("0.10", "0.11"):
            await assumption_service.amend(
                db_session,
                assumption=assumption,
                value=Decimal(value),
                justification=f"Revised to {value} after reviewing the disposal timetable.",
                actor=scene["reviewer"],
            )

        written = await assumption_service.history_of(db_session, assumption.id)
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for proposal, offset in zip(written, (2, 0, 1), strict=True):
            proposal.created_at = base + timedelta(hours=offset)
        await db_session.flush()

        history = await assumption_service.history_of(db_session, assumption.id)
        assert [p.sequence for p in history] == [1, 2, 3]
        assert [p.value for p in history] == [
            Decimal("0.09"),
            Decimal("0.10"),
            Decimal("0.11"),
        ]
        # Ordering on the timestamp would have produced a different reading entirely.
        by_time = sorted(history, key=lambda p: p.created_at)
        assert [p.sequence for p in by_time] == [2, 3, 1]

    async def test_amending_a_confirmed_assumption_un_confirms_it(self, db_session, scene):
        """ "Approved" must not come to mean "approved at some value, possibly not this one"."""
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        await assumption_service.confirm(db_session, assumption=assumption, actor=scene["reviewer"])

        amended = await assumption_service.amend(
            db_session,
            assumption=assumption,
            value=Decimal("0.11"),
            justification="Beta reflects a business the company is about to sell.",
            actor=scene["reviewer"],
        )

        assert amended.approved is False
        assert amended.approved_by is None
        with pytest.raises(UnconfirmedAssumptionError):
            assumption_service.as_quantity(amended)

    async def test_an_amendment_needs_a_reason_of_its_own(self, db_session, scene):
        assumption = await propose_rate(db_session, scene["request"])

        with pytest.raises(ValidationError, match="no justification"):
            await assumption_service.amend(
                db_session,
                assumption=assumption,
                value=Decimal("0.11"),
                justification="   ",
                actor=scene["reviewer"],
            )

    async def test_a_rejected_proposal_is_still_on_the_record(self, db_session, scene):
        """Nobody confirmed the 9%, and the record still says it was put forward."""
        assumption = await propose_rate(db_session, scene["request"], value="0.09")
        await assumption_service.amend(
            db_session,
            assumption=assumption,
            value=Decimal("0.11"),
            justification="Beta reflects a business the company is about to sell.",
            actor=scene["reviewer"],
        )

        rows = list(
            await db_session.scalars(
                select(AssumptionProposal).where(
                    AssumptionProposal.assumption_id == assumption.id,
                    AssumptionProposal.value == Decimal("0.09"),
                )
            )
        )
        assert len(rows) == 1


class TestWhatIsRefusedAtWriteTime:
    async def test_a_proposal_with_no_justification(self, db_session, scene):
        with pytest.raises(ValidationError, match="guess wearing a label"):
            await propose_rate(db_session, scene["request"], justification="  ")

    async def test_a_unit_this_platform_cannot_parse(self, db_session, scene):
        """A typo in a unit sits in the database looking correct until a valuation fails."""
        with pytest.raises(ValidationError, match="not a unit"):
            await propose_rate(db_session, scene["request"], unit="percentt")

    async def test_the_database_refuses_a_blank_justification_too(self, db_session, scene):
        """The service checks first; the constraint is what holds when something else writes."""
        assumption = await propose_rate(db_session, scene["request"])
        db_session.add(
            AssumptionProposal(
                assumption_id=assumption.id,
                value=Decimal("0.11"),
                unit="pure",
                justification="",
                proposed_by="someone",
                by_human=True,
                sequence=99,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestAScenarioIsADiffRatherThanACopy:
    async def test_a_base_case_change_propagates_to_a_scenario_that_did_not_override_it(
        self, db_session, scene
    ):
        """The property. A copied scenario passes every test that never moves the base case."""
        request = scene["request"]
        tax = await assumption_service.propose(
            db_session,
            request_id=request.id,
            name="tax_rate",
            value=Decimal("0.25"),
            unit="pure",
            justification="The statutory rate in the group's main jurisdiction.",
            proposed_by="valuation_interpretation",
        )
        await assumption_service.confirm(db_session, assumption=tax, actor=scene["reviewer"])

        growth = await assumption_service.propose(
            db_session,
            request_id=request.id,
            name="revenue_growth",
            value=Decimal("0.06"),
            unit="pure",
            justification="Three-year historical CAGR.",
            proposed_by="valuation_interpretation",
        )
        await assumption_service.confirm(db_session, assumption=growth, actor=scene["reviewer"])

        bear = await scenario_service.create_scenario(
            db_session,
            request_id=request.id,
            key="bear",
            label="Bear case",
            description="The two largest contracts expire and neither renewal is signed.",
        )
        await scenario_service.set_override(
            db_session,
            scenario=bear,
            assumption_name="revenue_growth",
            value=Decimal("0.02"),
            unit="pure",
            justification="Both renewals are unsigned at the as-of date.",
        )

        before = await scenario_service.resolve(db_session, scenario=bear)
        assert before.values["tax_rate"].value == Decimal("0.25")
        assert before.values["revenue_growth"].value == Decimal("0.02")

        # The base case is corrected. The bear case never argued about the tax rate, so it
        # must inherit the correction rather than keep the old figure.
        await assumption_service.amend(
            db_session,
            assumption=tax,
            value=Decimal("0.19"),
            justification=(
                "The statutory rate falls under legislation enacted before the as-of date."
            ),
            actor=scene["reviewer"],
        )
        await assumption_service.confirm(db_session, assumption=tax, actor=scene["reviewer"])

        after = await scenario_service.resolve(db_session, scenario=bear)
        assert after.values["tax_rate"].value == Decimal("0.19")
        assert after.values["revenue_growth"].value == Decimal("0.02")

    async def test_it_says_which_assumptions_it_overrode(self, db_session, scene):
        request = scene["request"]
        for name, value in (("tax_rate", "0.25"), ("revenue_growth", "0.06")):
            assumption = await assumption_service.propose(
                db_session,
                request_id=request.id,
                name=name,
                value=Decimal(value),
                unit="pure",
                justification="Base case.",
                proposed_by="valuation_interpretation",
            )
            await assumption_service.confirm(
                db_session, assumption=assumption, actor=scene["reviewer"]
            )

        bull = await scenario_service.create_scenario(
            db_session,
            request_id=request.id,
            key="bull",
            label="Bull case",
            description="Both renewals sign and the new plant reaches capacity a year early.",
        )
        await scenario_service.set_override(
            db_session,
            scenario=bull,
            assumption_name="revenue_growth",
            value=Decimal("0.11"),
            unit="pure",
            justification="Signed renewals plus the capacity uplift.",
        )

        resolved = await scenario_service.resolve(db_session, scenario=bull)
        assert resolved.overridden == ("revenue_growth",)

    async def test_an_overridden_value_traces_to_the_override_not_the_base(self, db_session, scene):
        """A figure computed in the bear case must trace to the bear case's own reasoning."""
        request = scene["request"]
        growth = await assumption_service.propose(
            db_session,
            request_id=request.id,
            name="revenue_growth",
            value=Decimal("0.06"),
            unit="pure",
            justification="Three-year historical CAGR.",
            proposed_by="valuation_interpretation",
        )
        await assumption_service.confirm(db_session, assumption=growth, actor=scene["reviewer"])

        bear = await scenario_service.create_scenario(
            db_session,
            request_id=request.id,
            key="bear",
            label="Bear case",
            description="Contracts expire unsigned.",
        )
        override = await scenario_service.set_override(
            db_session,
            scenario=bear,
            assumption_name="revenue_growth",
            value=Decimal("0.02"),
            unit="pure",
            justification="Both renewals unsigned at the as-of date.",
        )

        resolved = await scenario_service.resolve(db_session, scenario=bear)
        source = resolved.values["revenue_growth"].source
        assert source.identifier == str(override.id)
        assert source.identifier != str(growth.id)

    async def test_a_scenario_with_no_overrides_is_the_base_case(self, db_session, scene):
        request = scene["request"]
        tax = await assumption_service.propose(
            db_session,
            request_id=request.id,
            name="tax_rate",
            value=Decimal("0.25"),
            unit="pure",
            justification="Statutory rate.",
            proposed_by="valuation_interpretation",
        )
        await assumption_service.confirm(db_session, assumption=tax, actor=scene["reviewer"])

        base = await scenario_service.create_scenario(
            db_session,
            request_id=request.id,
            key="base",
            label="Base case",
            description="The company executes its stated plan without surprises.",
        )
        resolved = await scenario_service.resolve(db_session, scenario=base)

        assert resolved.overridden == ()
        assert resolved.values["tax_rate"].value == Decimal("0.25")

    async def test_an_override_of_an_unknown_assumption_is_refused(self, db_session, scene):
        """A scenario may argue about a number the base case has, not introduce one."""
        bear = await scenario_service.create_scenario(
            db_session,
            request_id=scene["request"].id,
            key="bear",
            label="Bear case",
            description="Contracts expire unsigned.",
        )

        with pytest.raises(ValidationError, match="second model nobody reviewed"):
            await scenario_service.set_override(
                db_session,
                scenario=bear,
                assumption_name="synergies",
                value=Decimal("0"),
                unit="pure",
                justification="There are none.",
            )

    async def test_an_override_of_an_unconfirmed_assumption_is_refused(self, db_session, scene):
        await propose_rate(db_session, scene["request"])
        bear = await scenario_service.create_scenario(
            db_session,
            request_id=scene["request"].id,
            key="bear",
            label="Bear case",
            description="Contracts expire unsigned.",
        )

        with pytest.raises(ValidationError):
            await scenario_service.set_override(
                db_session,
                scenario=bear,
                assumption_name=DISCOUNT_RATE,
                value=Decimal("0.12"),
                unit="pure",
                justification="Higher risk in the bear case.",
            )

    async def test_a_scenario_is_not_a_way_to_smuggle_an_unconfirmed_number_in(
        self, db_session, scene
    ):
        """The base assumption is un-confirmed *after* the override was legitimately set."""
        request = scene["request"]
        growth = await assumption_service.propose(
            db_session,
            request_id=request.id,
            name="revenue_growth",
            value=Decimal("0.06"),
            unit="pure",
            justification="Three-year historical CAGR.",
            proposed_by="valuation_interpretation",
        )
        await assumption_service.confirm(db_session, assumption=growth, actor=scene["reviewer"])

        bear = await scenario_service.create_scenario(
            db_session,
            request_id=request.id,
            key="bear",
            label="Bear case",
            description="Contracts expire unsigned.",
        )
        await scenario_service.set_override(
            db_session,
            scenario=bear,
            assumption_name="revenue_growth",
            value=Decimal("0.02"),
            unit="pure",
            justification="Both renewals unsigned.",
        )

        # An amendment leaves the base assumption unconfirmed again.
        await assumption_service.amend(
            db_session,
            assumption=growth,
            value=Decimal("0.05"),
            justification="Corrected for the disposal.",
            actor=scene["reviewer"],
        )

        resolved = await scenario_service.resolve(db_session, scenario=bear)
        assert "revenue_growth" not in resolved.values
        assert resolved.overridden == ()

    async def test_setting_an_override_twice_replaces_it(self, db_session, scene):
        request = scene["request"]
        growth = await assumption_service.propose(
            db_session,
            request_id=request.id,
            name="revenue_growth",
            value=Decimal("0.06"),
            unit="pure",
            justification="Three-year historical CAGR.",
            proposed_by="valuation_interpretation",
        )
        await assumption_service.confirm(db_session, assumption=growth, actor=scene["reviewer"])

        bear = await scenario_service.create_scenario(
            db_session,
            request_id=request.id,
            key="bear",
            label="Bear case",
            description="Contracts expire unsigned.",
        )
        for value in ("0.02", "0.01"):
            await scenario_service.set_override(
                db_session,
                scenario=bear,
                assumption_name="revenue_growth",
                value=Decimal(value),
                unit="pure",
                justification=f"Revised to {value}.",
            )

        rows = list(
            await db_session.scalars(
                select(ScenarioOverride).where(ScenarioOverride.scenario_id == bear.id)
            )
        )
        assert len(rows) == 1
        assert rows[0].value == Decimal("0.01")

    async def test_a_scenario_needs_a_stated_premise(self, db_session, scene):
        with pytest.raises(ValidationError, match="column of numbers"):
            await scenario_service.create_scenario(
                db_session,
                request_id=scene["request"].id,
                key="bear",
                label="Bear case",
                description="   ",
            )


class TestASensitivityCellNamesItsCalculation:
    async def test_a_grid_stores_every_cell(self, db_session, scene):
        calculation_id = await _a_calculation(db_session, scene)
        cells = [
            CellInput(
                x_value=Decimal(x),
                y_value=Decimal(y),
                output_value=Decimal(x) * Decimal(y),
                calculation_id=calculation_id,
            )
            for x in ("0.08", "0.09")
            for y in ("0.01", "0.02")
        ]

        sensitivity = await scenario_service.record_sensitivity(
            db_session,
            request_id=scene["request"].id,
            label="Equity value per share",
            x_assumption=DISCOUNT_RATE,
            y_assumption="terminal_growth",
            output_name="equity_value_per_share",
            output_unit="USD",
            cells=cells,
        )
        # Queried rather than read off the relationship: a lazy load in an async session
        # raises, and the point of the test is what reached the database anyway.
        stored = list(
            await db_session.scalars(
                select(SensitivityCell).where(SensitivityCell.sensitivity_id == sensitivity.id)
            )
        )
        assert len(stored) == 4
        assert all(cell.calculation_id == calculation_id for cell in stored)

    async def test_a_grid_of_one_assumption_against_itself_is_refused(self, db_session, scene):
        calculation_id = await _a_calculation(db_session, scene)
        with pytest.raises(ValidationError, match="line drawn twice"):
            await scenario_service.record_sensitivity(
                db_session,
                request_id=scene["request"].id,
                label="Nonsense",
                x_assumption=DISCOUNT_RATE,
                y_assumption=DISCOUNT_RATE,
                output_name="equity_value",
                output_unit="USD",
                cells=[
                    CellInput(
                        x_value=Decimal("0.08"),
                        y_value=Decimal("0.09"),
                        output_value=Decimal(1),
                        calculation_id=calculation_id,
                    )
                ],
            )

    async def test_an_empty_grid_is_refused(self, db_session, scene):
        with pytest.raises(ValidationError, match="heading promising analysis"):
            await scenario_service.record_sensitivity(
                db_session,
                request_id=scene["request"].id,
                label="Empty",
                x_assumption=DISCOUNT_RATE,
                y_assumption="terminal_growth",
                output_name="equity_value",
                output_unit="USD",
                cells=[],
            )

    async def test_a_grid_with_a_hole_in_it_is_refused(self, db_session, scene):
        """A hole renders as a failure at that point rather than as a point nobody computed."""
        calculation_id = await _a_calculation(db_session, scene)
        cells = [
            CellInput(
                x_value=Decimal("0.08"),
                y_value=Decimal("0.01"),
                output_value=Decimal(1),
                calculation_id=calculation_id,
            ),
            CellInput(
                x_value=Decimal("0.09"),
                y_value=Decimal("0.02"),
                output_value=Decimal(2),
                calculation_id=calculation_id,
            ),
        ]

        with pytest.raises(ValidationError, match="grid with a hole"):
            await scenario_service.record_sensitivity(
                db_session,
                request_id=scene["request"].id,
                label="Ragged",
                x_assumption=DISCOUNT_RATE,
                y_assumption="terminal_growth",
                output_name="equity_value",
                output_unit="USD",
                cells=cells,
            )

    async def test_the_same_point_cannot_appear_twice(self, db_session, scene):
        """A grid with two rows for one coordinate renders in read order."""
        calculation_id = await _a_calculation(db_session, scene)
        sensitivity = await scenario_service.record_sensitivity(
            db_session,
            request_id=scene["request"].id,
            label="Grid",
            x_assumption=DISCOUNT_RATE,
            y_assumption="terminal_growth",
            output_name="equity_value",
            output_unit="USD",
            cells=[
                CellInput(
                    x_value=Decimal("0.08"),
                    y_value=Decimal("0.01"),
                    output_value=Decimal(1),
                    calculation_id=calculation_id,
                )
            ],
        )

        db_session.add(
            SensitivityCell(
                sensitivity_id=sensitivity.id,
                x_value=Decimal("0.08"),
                y_value=Decimal("0.01"),
                output_value=Decimal(2),
                calculation_id=calculation_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_a_cell_cannot_be_written_without_a_calculation(self, db_session, scene):
        """`calculation_id` is NOT NULL, and that is the point of the table."""
        calculation_id = await _a_calculation(db_session, scene)
        sensitivity = await scenario_service.record_sensitivity(
            db_session,
            request_id=scene["request"].id,
            label="Grid",
            x_assumption=DISCOUNT_RATE,
            y_assumption="terminal_growth",
            output_name="equity_value",
            output_unit="USD",
            cells=[
                CellInput(
                    x_value=Decimal("0.08"),
                    y_value=Decimal("0.01"),
                    output_value=Decimal(1),
                    calculation_id=calculation_id,
                )
            ],
        )

        db_session.add(
            SensitivityCell(
                sensitivity_id=sensitivity.id,
                x_value=Decimal("0.09"),
                y_value=Decimal("0.02"),
                output_value=Decimal(2),
                calculation_id=None,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


async def _a_calculation(session: Any, scene: dict[str, Any]) -> uuid.UUID:
    """A minimal `calculations` row, so a cell has something real to point at."""
    from aer.db.models import Calculation  # noqa: PLC0415 -- only these tests need it

    calculation = Calculation(
        job_id=scene["job"].id,
        name="equity_value",
        formula="value = a * b",
        function_ref="tests._a_calculation",
        code_version="testsha",
        inputs=[],
        output_value=Decimal(1),
        output_unit="USD",
    )
    session.add(calculation)
    await session.flush()
    return calculation.id
