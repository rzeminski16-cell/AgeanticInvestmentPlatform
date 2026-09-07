"""The valuation method block: rendered from the ledger, never written by a model.

ADR 0063. The first complete report's DCF section described beta regressions, bond
yields and market weights the run never touched, and every existing defence passed it.
These tests pin the replacement: the block matches the recorded calculations and the
confirmed assumptions — including who set each one — the recorded caveats (the terminal
spread among them) reach the reader, and the model's commentary is refused when it names
a method input the record does not hold.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.dcf import METHOD_DISAGREEMENT_CAVEAT
from aer.calc.wacc import CapitalStructure, EquityBasis, cost_of_capital
from aer.core.enums import JobStatus, UserRole
from aer.db.models import JobStep, User
from aer.sections.deterministic import AUGMENTERS, model_facing_contract
from aer.sections.valuation_method import (
    commentary_problems,
    component_note,
    method_only,
    valuation_method_block,
)
from aer.services import assumptions as assumption_service
from aer.services import valuation as valuation_service
from aer.services.calculations import new_context, persist_context
from tests.request_fixtures import research_request
from tests.test_valuation_surface import AS_OF_DATE, MANDATE, base_inputs, rate, usd
from tests.workflow_fixtures import seed_job

pytestmark = pytest.mark.anyio


async def seed_method_scene(session: AsyncSession) -> dict[str, Any]:
    """A run whose valuation was really computed, with its assumptions and value step.

    The ledger is written by the actual engine — :func:`cost_of_capital` decomposing the
    rate, then the discounted cash flow — so the block's figures are compared against a
    genuine record rather than rows shaped to please the assertions. The beta and the
    risk-free rate are typed by a person, exactly the state the first live run was in.

    Seeded from scratch rather than on :func:`seed_scene`, because that helper records a
    valuation of its own and the block reads the job's *first* base-case rows.
    """
    analyst = User(
        email="method-block@example.invalid", display_name="Analyst", role=UserRole.ANALYST
    )
    session.add(analyst)
    await session.flush()
    request = research_request(
        user_id=analyst.id,
        company_name="Testco plc",
        ticker="TEST",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()
    job = await seed_job(session, request=request)
    scene: dict[str, Any] = {"analyst": analyst, "request": request, "job": job}

    proposals = (
        ("risk_free_rate", "0.03", "Typed from the operator's own sources.", True),
        ("beta", "1.79", "Typed from the operator's own sources.", True),
        ("equity_risk_premium", "0.05", "The long-run premium the survey supports.", False),
        ("terminal_growth", "0.02", "Long-run nominal growth.", False),
        ("revenue_growth", "0.10", "Continuation of the reported trend.", False),
    )
    for name, value, justification, by_human in proposals:
        assumption = await assumption_service.propose(
            session,
            request_id=request.id,
            name=name,
            value=Decimal(value),
            unit="pure",
            justification=justification,
            proposed_by="operator@example.invalid" if by_human else "aer.agents.assumptions",
            by_human=by_human,
        )
        await assumption_service.confirm(session, assumption=assumption, actor=scene["analyst"])

    # The cost of capital, decomposed through the real calc into the same job's ledger.
    # All equity, so the block's debt rows are legitimately absent.
    ledger = new_context()
    capital = cost_of_capital(
        ledger,
        risk_free=rate("0.03"),
        beta=rate("1.79"),
        equity_risk_premium=rate("0.05"),
        cost_of_debt_pre_tax=None,
        tax_rate=None,
        structure=CapitalStructure(
            equity_value=usd("4000"),
            debt_value=usd("0"),
            basis=EquityBasis.BOOK,
        ),
    )
    result = await valuation_service.run_valuation(
        session,
        job_id=job.id,
        inputs=base_inputs(wacc=capital.wacc),
        mandate=MANDATE,
        context=ledger,
    )
    await persist_context(session, ledger, job_id=job.id)

    session.add(
        JobStep(
            job_id=job.id,
            step_key="value",
            sequence=9,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{job.id}:value",
            input_hash="0" * 64,
            output_ref={
                "valued": True,
                "equity_basis": "book",
                "years": 3,
                "caveats": [str(caveat) for caveat in result.caveats],
            },
        )
    )
    await session.flush()

    scene["result"] = result
    scene["capital"] = capital
    return scene


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    await db_session.execute(
        text("TRUNCATE research_requests, audit_events, users, artefacts RESTART IDENTITY CASCADE")
    )
    return await seed_method_scene(db_session)


async def block_for(session: AsyncSession, scene: dict[str, Any]) -> dict[str, Any]:
    return await valuation_method_block(session, job_id=scene["job"].id, request=scene["request"])


def rows_by_label(block: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    return {row["label"]: row for row in block.get(field, [])}


class TestTheBlockIsTheLedgers:
    """The rendered method matches the recorded calculations, to the row."""

    async def test_each_component_states_its_value_and_who_set_it(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)
        capital_rows = rows_by_label(block, "cost_of_capital")

        typed = capital_rows["Risk-free rate"]
        assert Decimal(typed["value"]) == Decimal("0.03")
        assert typed["provenance"] == "set by the operator and confirmed at the assumptions gate"

        proposed = capital_rows["Equity risk premium"]
        assert "proposed by aer.agents.assumptions" in proposed["provenance"]
        assert "confirmed by the operator" in proposed["provenance"]

        computed = capital_rows["Cost of equity"]
        places = Decimal("0.000000000001")
        assert Decimal(computed["value"]) == scene["capital"].cost_of_equity.value.quantize(places)
        assert computed["provenance"].startswith("computed: ")
        assert computed["calculation_id"]

    async def test_a_typed_beta_is_typed_and_never_estimated(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The first live report's exact lie, made unwritable: 1.79 was typed."""
        block = await block_for(db_session, scene)
        beta = rows_by_label(block, "cost_of_capital")["Beta"]

        assert Decimal(beta["value"]) == Decimal("1.79")
        assert beta["provenance"] == "set by the operator and confirmed at the assumptions gate"
        rendered = str(block).lower()
        assert "estimat" not in rendered
        assert "regress" not in rendered

    async def test_the_weights_say_they_are_book_values(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)
        weight = rows_by_label(block, "cost_of_capital")["Equity weight"]

        assert "book values" in weight["provenance"]
        assert "book values" in block["method_note"]

    async def test_both_terminal_methods_reach_the_block_with_their_own_rows(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)
        terminal = rows_by_label(block, "terminal_valuations")
        result = scene["result"]

        places = Decimal("0.000000000001")
        gordon = terminal["Value per share \N{EM DASH} Gordon growth"]
        exit_row = terminal["Value per share \N{EM DASH} Exit multiple"]
        assert Decimal(gordon["value"]) == result.gordon.value_per_share.value.quantize(places)
        assert Decimal(exit_row["value"]) == result.exit_multiple.value_per_share.value.quantize(
            places
        )
        assert gordon["calculation_id"] != exit_row["calculation_id"]

    async def test_the_share_count_and_its_source_are_stated(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)
        shares = rows_by_label(block, "terminal_valuations")["Shares outstanding"]

        assert Decimal(shares["value"]) == Decimal("100")
        assert shares["unit"] == "shares"
        assert "filed count" in shares["provenance"]

    async def test_a_sensitivity_grid_does_not_shadow_the_base_case(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A grid's cells are whole DCFs recorded after the base run under the same case
        label; the block must render the valuation the report describes, not a corner."""
        from tests.test_valuation_surface import add_grid  # noqa: PLC0415

        await add_grid(db_session, scene)

        block = await block_for(db_session, scene)
        gordon = rows_by_label(block, "terminal_valuations")[
            "Value per share \N{EM DASH} Gordon growth"
        ]
        places = Decimal("0.000000000001")
        assert Decimal(gordon["value"]) == scene["result"].gordon.value_per_share.value.quantize(
            places
        )

    async def test_the_forecast_drivers_are_named_as_assumptions(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)
        drivers = rows_by_label(block, "forecast_drivers")

        growth = drivers["Revenue growth"]
        assert Decimal(growth["value"]) == Decimal("0.10")
        assert "confirmed" in growth["provenance"]
        assert "Terminal growth" in drivers

    async def test_a_run_that_did_not_value_gets_the_honest_state(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        step = await db_session.get(JobStep, next(iter(await _value_step_ids(db_session, scene))))
        assert step is not None
        step.output_ref = {"valued": False, "reason": "no annual period could be assembled"}
        await db_session.flush()

        block = await block_for(db_session, scene)

        assert set(block) == {"method_note"}
        assert "no annual period could be assembled" in block["method_note"]


async def _value_step_ids(session: AsyncSession, scene: dict[str, Any]) -> list[Any]:
    from sqlalchemy import select  # noqa: PLC0415

    return list(
        await session.scalars(
            select(JobStep.id).where(JobStep.job_id == scene["job"].id, JobStep.step_key == "value")
        )
    )


class TestTheSpreadReachesTheReader:
    """The caveat the first run computed and never printed renders with the section."""

    async def test_the_recorded_caveats_reach_the_block(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)
        recorded = scene["result"].caveats

        assert list(block.get("valuation_caveats", [])) == [str(item) for item in recorded]

    async def test_the_method_disagreement_appears_when_the_methods_diverge(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The stated factor is the calc module's own: more than a quarter apart."""
        result = scene["result"]
        low = min(result.gordon.value_per_share.value, result.exit_multiple.value_per_share.value)
        high = max(result.gordon.value_per_share.value, result.exit_multiple.value_per_share.value)
        diverged = low > 0 and (high - low) / low > Decimal("0.25")

        block = await block_for(db_session, scene)
        shown = METHOD_DISAGREEMENT_CAVEAT in block.get("valuation_caveats", [])

        assert shown == diverged


class TestTheCommentaryEdge:
    """A commentary naming a method input the record lacks is refused, by name."""

    async def test_naming_an_input_the_store_lacks_is_refused(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """All equity, so there is no cost of debt to talk about."""
        block = await block_for(db_session, scene)

        problems = commentary_problems(
            {"commentary": "The cost of debt reflects the company's funding mix."}, block
        )

        assert len(problems) == 1
        assert "'cost of debt'" in problems[0]
        assert "calculation store" in problems[0]

    async def test_prices_bonds_and_regressions_are_always_refused(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The first live report's claims, each one now a named refusal."""
        block = await block_for(db_session, scene)
        offending = (
            "The implied return is measured against the closing price on 18 August.",
            "Beta was estimated from five years of weekly returns.",
            "The cost of equity reflects traded yields on the company's notes.",
        )

        for commentary in offending:
            problems = commentary_problems({"commentary": commentary}, block)
            assert problems, commentary
            assert "holds no such input" in problems[0]

    async def test_an_interpreting_commentary_passes(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)

        problems = commentary_problems(
            {
                "commentary": (
                    "The WACC is demanding for the sector, and the answer turns almost "
                    "entirely on terminal growth; the beta carries the operator's own view."
                )
            },
            block,
        )

        assert problems == []

    async def test_an_empty_commentary_is_not_this_check_s_problem(self) -> None:
        assert commentary_problems({}, {}) == []


class TestTheWriterIsToldWhatItMayName:
    """The other half of the check: the writer cannot see the block, so it is told which
    components the block carries. A rule enforced against a list nobody was shown is a
    rule that can only be guessed at, and a live run paid for two attempts guessing."""

    async def test_the_note_names_the_blocks_components_and_nothing_else(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        block = await block_for(db_session, scene)

        note = component_note(block)

        assert "wacc" in note.lower()
        # All equity in this scene, so there is no cost of debt to name — and the check
        # would refuse a commentary that named one.
        assert "cost of debt" not in note.lower()
        assert commentary_problems({"commentary": "The cost of debt is high."}, block)

    async def test_every_component_the_note_names_is_one_the_check_admits(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """The note and the refusal are the same list read from the same block, so a
        commentary naming everything the note offers is refused for nothing."""
        block = await block_for(db_session, scene)
        note = component_note(block)
        named = note.split(": ", 1)[1].rsplit(".", 1)[0]

        assert commentary_problems({"commentary": f"The answer turns on {named}."}, block) == []

    async def test_a_block_with_no_components_says_to_name_none(self) -> None:
        note = component_note({})

        assert "no cost-of-capital" in note
        assert "Name none of them" in note

    async def test_the_note_carries_no_figure(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Labels travel; figures do not. A figure in the ask is a figure the numeral rule
        would then have to cover, on a section whose numbers are the platform's own."""
        note = component_note(await block_for(db_session, scene))

        assert not re.search(r"\d", note), note


class TestTheModelFacingContract:
    """The model is bound by the contract minus the platform's fields."""

    def test_platform_filled_fields_are_stripped(self) -> None:
        contract = {
            "type": "object",
            "required": ["commentary"],
            "properties": {
                "method_note": {"type": "string", "platform_filled": True},
                "commentary": {"type": "string"},
            },
        }

        narrowed = model_facing_contract(contract)

        assert list(narrowed["properties"]) == ["commentary"]
        assert narrowed["required"] == ["commentary"]

    def test_a_required_platform_field_leaves_the_model_s_required_list(self) -> None:
        contract = {
            "type": "object",
            "required": ["method_note", "commentary"],
            "properties": {
                "method_note": {"type": "string", "platform_filled": True},
                "commentary": {"type": "string"},
            },
        }

        assert model_facing_contract(contract)["required"] == ["commentary"]

    def test_a_contract_with_no_platform_fields_is_returned_unchanged(self) -> None:
        contract = {
            "type": "object",
            "required": ["commentary"],
            "properties": {"commentary": {"type": "string"}},
        }

        assert model_facing_contract(contract) is contract

    def test_the_valuation_section_has_an_augmenter_registered(self) -> None:
        """The wiring the whole mechanism rests on: the key is in the registry."""
        assert "valuation_dcf" in AUGMENTERS


class TestWhenTheBlockIsTheWholeSection:
    """Gap A51c: with no valuation there is nothing for a commentary to interpret, and
    the writer is not asked — `method_only` is the augmenter's answer before the model."""

    def test_a_block_with_no_figures_is_standalone_with_a_reason(self) -> None:
        """And the reason is in the report's register (gap R4): the sentence renders
        into a decision-maker's document, so it names what is absent and what that
        costs — never the writing model, the run or the platform."""
        reason = method_only({"method_note": "No discounted cash flow was produced."})

        assert "no valuation figures to interpret" in reason
        for process_noun in ("writing model", "this run", "the platform"):
            assert process_noun not in reason

    def test_a_block_with_cost_of_capital_rows_wants_its_commentary(self) -> None:
        assert (
            method_only({"method_note": "The figures…", "cost_of_capital": [{"label": "Beta"}]})
            == ""
        )

    def test_a_block_with_terminal_valuations_wants_its_commentary(self) -> None:
        assert (
            method_only(
                {
                    "method_note": "The figures…",
                    "terminal_valuations": [{"label": "Value per share — Gordon growth"}],
                }
            )
            == ""
        )

    async def test_a_valued_runs_block_is_never_standalone(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Pinned against the real block, not a hand-shaped one: a rendering change that
        emptied the rows would silently stop every commentary rather than fail here."""
        block = await block_for(db_session, scene)

        assert method_only(block) == ""

    async def test_the_live_runs_state_is_standalone(
        self, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        step = await db_session.get(JobStep, next(iter(await _value_step_ids(db_session, scene))))
        assert step is not None
        step.output_ref = {"valued": False, "reason": "the assumptions were never supplied"}
        await db_session.flush()

        block = await block_for(db_session, scene)

        assert method_only(block) != ""
