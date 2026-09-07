"""The first bank valuation this platform has ever produced.

ADR 0070 built `aer/calc/residual_income.py` with unit and property tests and nothing
called it, exactly as gap B2c described the discounted cash flow before it was wired. This
suite is the proof that the wiring closes: seed a bank, confirm the three numbers a
residual-income valuation needs, and get a per-share value on both terminal treatments with
every step of it recorded.

The refusals matter as much as the answer. A bank whose final forecast year earns below its
cost of equity gets no perpetuity — and keeps its fade valuation, with the refusal recorded
rather than the page going blank.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.agents.assumptions import AssumptionProposalDraft, OpinionProposal
from aer.calc.residual_income import CLEAN_SURPLUS_CAVEAT
from aer.config import Settings
from aer.core.enums import JobStatus, UserRole
from aer.core.sectors import ValuationMandate, ValuationModel, mandate_for, profile_for
from aer.db.models import Calculation, JobStep, Scenario, Sensitivity, User
from aer.providers.fake import FakeProvider
from aer.providers.router import Router
from aer.sections.valuation_method import commentary_problems, valuation_method_block
from aer.services.assumption_gate import (
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    RESIDUAL_INCOME_NAMES,
    RISK_FREE_ASSUMPTION,
)
from aer.services.assumptions import confirm, propose
from aer.services.exhibits import _field_input, _scenario_input
from aer.services.prices import BETA_ASSUMPTION
from aer.services.residual_income_run import BankValuationOutcome, value_the_bank
from aer.services.scenarios import create_scenario, set_override
from aer.services.sectors import CLASSIFY_STEP
from aer.services.valuation import SENSITIVITY_POINTS
from aer.storage.local import LocalArtefactStore
from aer.workflow.engine import StepContext
from aer.workflow.workflows.vertical_slice_v1 import ASSUMPTIONS_STEP
from aer.workflow.workflows.vertical_slice_v1 import (
    _propose_assumptions as propose_assumptions_step,
)
from aer.workflow.workflows.vertical_slice_v1 import _value as value_step
from tests.assumption_fixtures import a_year, analysed, seed_years

pytestmark = pytest.mark.integration

# A bank's filed year. `equity` is the book value the model starts from, `net_income` and
# `dividends_paid` are what the two drivers are derived from, and a share count is needed to
# divide by. No `operating_income` and no `current_assets`: a bank keeps neither, and the
# scene says so rather than quietly carrying an ordinary company's balance sheet.
_SHARES = Decimal("100")

_BANK = {
    "equity": "1000",
    "net_income": "120",
    "dividends_paid": "48",
    "assets": "12000",
    "liabilities": "11000",
    "shares_outstanding": str(_SHARES),
    "basic_shares_outstanding": str(_SHARES),
    "diluted_shares_outstanding": str(_SHARES),
}

_YEARS = {
    date(2023, 12, 31): a_year(**_BANK),
    date(2024, 12, 31): a_year(**_BANK),
}

# The three a bank's operator confirms. A 12% return against a 9.7% cost of equity, so the
# spread is positive and both treatments produce an answer.
_CONFIRMED: dict[str, str] = {
    "return_on_equity": "0.12",
    "payout_ratio": "0.40",
    "terminal_growth": "0.02",
    RISK_FREE_ASSUMPTION: "0.042",
    BETA_ASSUMPTION: "1.0",
    EQUITY_RISK_PREMIUM_ASSUMPTION: "0.055",
}

BANKS = profile_for("banks")
assert BANKS is not None

MANDATE = mandate_for(
    ValuationModel.RESIDUAL_INCOME,
    subject="BANKCO",
    profile=BANKS,
    confirmed_by="analyst@example.invalid",
)


async def _confirm_all(scene: dict[str, Any], *, omit: str = "", **overrides: str) -> None:
    """Propose and confirm every assumption the model needs, bar one if asked."""
    session: AsyncSession = scene["session"]
    actor = User(email="banker@example.invalid", display_name="B", role=UserRole.OWNER)
    session.add(actor)
    await session.flush()

    values = {**_CONFIRMED, **overrides}
    for name, value in values.items():
        if name == omit:
            continue
        assumption = await propose(
            session,
            request_id=scene["request"].id,
            name=name,
            value=Decimal(value),
            unit="pure",
            justification=f"Scene value for {name}.",
            proposed_by="test",
        )
        await confirm(session, assumption=assumption, actor=actor)


async def _value(
    scene: dict[str, Any], *, years: int = 3, mandate: ValuationMandate = MANDATE
) -> BankValuationOutcome:
    return await value_the_bank(
        scene["session"],
        request=scene["request"],
        job_id=scene["job"].id,
        analysis=await analysed(scene),
        mandate=mandate,
        years=years,
    )


class TestTheValuationRuns:
    async def test_a_bank_is_valued_on_both_treatments(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.ran, outcome.reason
        assert outcome.faded is not None
        assert outcome.perpetual is not None

    async def test_the_book_value_comes_from_the_filings_not_an_assumption(
        self, scene: dict[str, Any]
    ) -> None:
        """The model's whole claim is that it starts from a number the filer published. An
        opening book value somebody typed would make it a dividend discount wearing a
        balance sheet."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.faded is not None
        assert outcome.faded.opening_book_value.value == Decimal("1000")

    async def test_the_discount_rate_is_capm_not_a_wacc(self, scene: dict[str, Any]) -> None:
        """0.042 + 1.0 x 0.055 = 0.097, with no cost of debt blended in. A bank's deposits
        are priced in net interest income; blending them here would charge them twice."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.cost_of_equity is not None
        assert outcome.cost_of_equity.value == Decimal("0.097")

    async def test_a_perpetuity_is_worth_more_than_fading_to_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        """The disagreement the report exists to show. Same drivers, same book value, and
        the answers differ by the whole claim about competition."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.faded is not None
        assert outcome.perpetual is not None
        assert outcome.perpetual.value_per_share.value > outcome.faded.value_per_share.value

    async def test_a_bank_earning_its_spread_is_worth_more_than_book(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.faded is not None
        assert outcome.faded.premium_to_book.value > 0

    async def test_every_step_is_persisted(self, scene: dict[str, Any]) -> None:
        """A figure with no stored row cannot be cited, so the ledger reaching the database
        is what makes the valuation usable rather than merely correct."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        await _value(scene)

        session: AsyncSession = scene["session"]
        names = {row.name for row in (await session.scalars(select(Calculation))).all()}
        assert {
            "net_income_from_roe",
            "equity_charge",
            "residual_income",
            "equity_discount_factor",
            "closing_book_value",
            "residual_income_equity_value",
            "residual_income_per_share",
        } <= names

    async def test_the_forecast_runs_for_the_years_it_was_asked_for(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene, years=4)

        assert outcome.faded is not None
        assert len(outcome.faded.years) == 4


class TestWhatItSays:
    async def test_the_output_names_its_model_before_anything_else(
        self, scene: dict[str, Any]
    ) -> None:
        """Every surface reading this has to branch on the model first: a per-share figure
        means a different thing here than under a discounted cash flow."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        produced = (await _value(scene)).as_dict()

        assert produced["model"] == "residual_income"
        assert produced["valued"] is True

    async def test_a_refusal_still_names_its_model(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit=BETA_ASSUMPTION)

        produced = (await _value(scene)).as_dict()

        assert produced["model"] == "residual_income"
        assert produced["valued"] is False

    async def test_the_clean_surplus_caveat_reaches_the_output(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        produced = (await _value(scene)).as_dict()

        assert CLEAN_SURPLUS_CAVEAT in produced["caveats"]

    async def test_the_disagreement_between_treatments_is_stated(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        produced = (await _value(scene)).as_dict()

        assert any("opposite claims about competition" in item for item in produced["caveats"])

    async def test_a_request_with_no_authored_cases_says_so(self, scene: dict[str, Any]) -> None:
        """Scenarios are written, not generated. A run with none has a base case and two
        grids, and a reader comparing two reports is told which it is looking at."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        produced = (await _value(scene)).as_dict()

        assert produced["scenarios"] == []
        assert any("carries no authored scenarios" in item for item in produced["caveats"])

    async def test_both_per_share_figures_are_reported(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        produced = (await _value(scene)).as_dict()

        assert Decimal(produced["fade_per_share"]) > 0
        assert Decimal(produced["perpetual_per_share"]) > Decimal(produced["fade_per_share"])


class TestWhatItRefuses:
    @pytest.mark.parametrize(
        "missing", [RISK_FREE_ASSUMPTION, BETA_ASSUMPTION, EQUITY_RISK_PREMIUM_ASSUMPTION]
    )
    async def test_an_unconfirmed_cost_of_capital_input_is_named(
        self, scene: dict[str, Any], missing: str
    ) -> None:
        """Named individually. "The discount rate is missing" does not tell an operator
        which row to go and agree to."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit=missing)

        outcome = await _value(scene)

        assert not outcome.ran
        assert missing in outcome.reason

    async def test_an_unconfirmed_driver_is_named(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit="return_on_equity")

        outcome = await _value(scene)

        assert not outcome.ran
        assert "return_on_equity" in outcome.reason

    async def test_an_unconfirmed_terminal_growth_is_named(self, scene: dict[str, Any]) -> None:
        """There is no default. A terminal growth rate this platform picked would be its
        opinion presented as the operator's."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit="terminal_growth")

        outcome = await _value(scene)

        assert not outcome.ran
        assert "terminal_growth" in outcome.reason

    async def test_a_filer_with_no_assembled_period_is_told_so(self, scene: dict[str, Any]) -> None:
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert not outcome.ran
        assert "no book value to start from" in outcome.reason

    async def test_a_bank_below_its_cost_of_equity_keeps_its_fade_valuation(
        self, scene: dict[str, Any]
    ) -> None:
        """The perpetuity refuses to capitalise a shortfall, and that refusal must not cost
        the run the valuation it can produce. The fade answer stands, worth less than book,
        and the reason the second treatment is absent is recorded."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, return_on_equity="0.04")

        outcome = await _value(scene)

        assert outcome.ran
        assert outcome.faded is not None
        assert outcome.faded.premium_to_book.value < 0
        assert outcome.perpetual is None
        assert "extend the forecast" in outcome.perpetual_refusal

    async def test_that_refusal_reaches_the_output(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, return_on_equity="0.04")

        produced = (await _value(scene)).as_dict()

        assert produced["valued"] is True
        assert "perpetual_per_share" not in produced
        assert produced["perpetual_refused"]

    async def test_a_mandate_for_another_model_is_refused(self, scene: dict[str, Any]) -> None:
        """The sector block, at the point of use. A comparables mandate is a perfectly valid
        mandate and is not permission to run this."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        comps = mandate_for(
            ValuationModel.COMPS_MULTIPLES,
            subject="BANKCO",
            profile=BANKS,
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(Exception, match="comps_multiples"):
            await _value(scene, mandate=comps)


class TestTheGateAsksForTheRightThings:
    def test_the_required_names_are_the_ones_this_service_reads(self) -> None:
        """The gate collecting one set and the valuation reading another is a run that
        pauses for numbers nothing uses, then refuses for want of numbers nobody was asked
        for."""
        assert set(RESIDUAL_INCOME_NAMES) == set(_CONFIRMED)


class TestTheGrids:
    """§3.4: the bank model's own axes, on ADR 0028's terms and ADR 0101's."""

    async def test_two_grids_are_built_and_stored(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert [
            (grid.row_axis.field, grid.column_axis.field, grid.treatment.value)
            for grid in outcome.grids
        ] == [
            ("cost_of_equity", "terminal_growth", "perpetual_growth"),
            ("cost_of_equity", "return_on_equity", "fade_to_nothing"),
        ]
        assert outcome.grid_refusals == ()

    async def test_each_grid_is_five_by_five_complete_valuations(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        for grid in outcome.grids:
            assert len(grid.cells) == SENSITIVITY_POINTS * SENSITIVITY_POINTS

    async def test_the_stored_cells_point_at_calculations_that_exist(
        self, scene: dict[str, Any]
    ) -> None:
        """ADR 0028's ordering rule. `sensitivity_cells.calculation_id` is not nullable with
        ON DELETE RESTRICT, so a cell written before its calculation would either fail on the
        foreign key or point at somebody else's arithmetic."""
        session: AsyncSession = scene["session"]
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        await _value(scene)

        stored = list(
            await session.scalars(
                select(Sensitivity)
                .where(Sensitivity.job_id == scene["job"].id)
                .options(selectinload(Sensitivity.cells))
                .order_by(Sensitivity.created_at)
            )
        )
        assert len(stored) == 2
        for grid in stored:
            assert len(grid.cells) == SENSITIVITY_POINTS * SENSITIVITY_POINTS
            for cell in grid.cells:
                assert await session.get(Calculation, cell.calculation_id) is not None

    async def test_a_grid_cell_is_never_recorded_as_the_base_case(
        self, scene: dict[str, Any]
    ) -> None:
        """Otherwise a scenario keyed `base` draws its bar from a grid corner (ADR 0101)."""
        session: AsyncSession = scene["session"]
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        await _value(scene)

        rows = list(
            await session.scalars(
                select(Calculation).where(
                    Calculation.job_id == scene["job"].id,
                    Calculation.name == "residual_income_per_share",
                )
            )
        )
        base_rows = [row for row in rows if row.parameters.get("case") == "base"]
        assert len(base_rows) == 2, "the base case is the two treatments and nothing else"

    async def test_a_fading_return_on_equity_costs_its_grid_and_says_why(
        self, scene: dict[str, Any]
    ) -> None:
        """The rule ADR 0101 settles: a path that moves year to year is several numbers, and
        an axis over it would be labelled for a quantity it does not vary."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit="return_on_equity")
        for year, value in ((1, "0.14"), (2, "0.13"), (3, "0.12")):
            await _confirm_one(scene, f"return_on_equity_y{year}", value)

        outcome = await _value(scene)

        assert [grid.column_axis.field for grid in outcome.grids] == ["terminal_growth"]
        assert any("moves from year to year" in item for item in outcome.grid_refusals)

    async def test_a_bank_below_its_cost_of_equity_loses_the_terminal_grid_whole(
        self, scene: dict[str, Any]
    ) -> None:
        """A hole in a grid is a cell a reader interprets, so the refused corner takes all
        twenty-five. The spread grid survives: it never asks the perpetuity anything."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, **{"return_on_equity": "0.04"})

        outcome = await _value(scene)

        assert [grid.column_axis.field for grid in outcome.grids] == ["return_on_equity"]
        assert len(outcome.grid_refusals) == 1
        assert "cost of equity against terminal growth" in outcome.grid_refusals[0]


class TestTheChartsFindTheBankRows:
    """The exhibits read per-share figures back by calculation name, and the bank's name is
    not the discounted cash flow's. A run whose charts were silently empty would look like a
    company with nothing to show rather than a reader looking in the wrong column."""

    async def test_a_scenario_bar_is_drawn_from_the_bank_ledger(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _a_case(scene, key="bear", label="Bear", name="return_on_equity", value="0.10")
        await _value(scene)

        built = await _scenario_input(scene["session"], job=scene["job"], request=scene["request"])

        assert [bar.key for bar in built.cases] == ["bear"]
        assert built.cases[0].value_per_share > 0

    async def test_the_bar_takes_the_conservative_treatment(self, scene: dict[str, Any]) -> None:
        """One bar cannot show two answers, and ADR 0070 refuses to choose between them for
        a reader who is present. A chart drawn without one picks the claim assuming least."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _a_case(scene, key="bear", label="Bear", name="return_on_equity", value="0.10")
        outcome = await _value(scene)

        built = await _scenario_input(scene["session"], job=scene["job"], request=scene["request"])

        bar = built.cases[0].value_per_share
        priced = outcome.scenarios[0].valued
        assert priced.perpetual is not None
        assert bar == priced.faded.value_per_share.value.quantize(bar)
        assert bar != priced.perpetual.value_per_share.value.quantize(bar)

    async def test_the_value_band_is_named_for_the_model_that_produced_it(
        self, scene: dict[str, Any]
    ) -> None:
        """A bank's band labelled "DCF, terminal methods" would be prose about method that no
        calculation backs, in the place a reader's trust is set (ADR 0063)."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _value(scene)

        field = await _field_input(
            scene["session"], job=scene["job"], request=scene["request"], licence_note=""
        )

        assert [band.label for band in field.bands] == ["Residual income, terminal treatments"]
        assert field.bands[0].low < field.bands[0].high


class TestTheScenarios:
    async def test_every_authored_case_is_valued_both_ways(self, scene: dict[str, Any]) -> None:
        """ADR 0070's reasoning does not weaken for a bear case: choosing between the
        treatments is a judgement about banking whichever assumptions are on the table."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _a_case(scene, key="bear", label="Bear", name="return_on_equity", value="0.10")

        outcome = await _value(scene)

        assert [item.key for item in outcome.scenarios] == ["bear"]
        bear = outcome.scenarios[0]
        assert bear.overridden == ("return_on_equity",)
        assert bear.valued.perpetual is not None
        assert bear.valued.faded.value_per_share.value < (
            outcome.faded.value_per_share.value if outcome.faded else Decimal(0)
        )

    async def test_a_case_is_attributable_in_the_ledger(self, scene: dict[str, Any]) -> None:
        """The whole reason `residual_income_value` takes a `case`. Without it a scenario
        chart cannot be read off the calculations table."""
        session: AsyncSession = scene["session"]
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _a_case(scene, key="bear", label="Bear", name="return_on_equity", value="0.10")

        await _value(scene)

        rows = list(
            await session.scalars(
                select(Calculation).where(
                    Calculation.job_id == scene["job"].id,
                    Calculation.name == "residual_income_per_share",
                )
            )
        )
        assert {row.parameters.get("case") for row in rows} == {"base", "bear", "sensitivity"}

    async def test_the_filed_book_value_is_not_a_scenarios_to_move(
        self, scene: dict[str, Any]
    ) -> None:
        """A case argues with a forecast, not with a balance sheet. Both models start from
        the same filed opening book value however much else they disagree about."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _a_case(scene, key="bull", label="Bull", name="return_on_equity", value="0.15")

        outcome = await _value(scene)

        assert outcome.faded is not None
        assert (
            outcome.scenarios[0].valued.faded.opening_book_value.value
            == outcome.faded.opening_book_value.value
        )

    async def test_the_output_lists_what_each_case_argued_about(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _a_case(scene, key="bear", label="Bear", name="terminal_growth", value="0.01")

        produced = (await _value(scene)).as_dict()

        assert produced["scenarios"] == [
            {"key": "bear", "label": "Bear", "overridden": ["terminal_growth"]}
        ]
        assert not any("carries no authored scenarios" in item for item in produced["caveats"])


async def _confirm_one(scene: dict[str, Any], name: str, value: str) -> None:
    """One extra confirmed assumption, on top of whatever `_confirm_all` left."""
    session: AsyncSession = scene["session"]
    actor = await session.scalar(select(User).where(User.email == "banker@example.invalid"))
    assert actor is not None
    assumption = await propose(
        session,
        request_id=scene["request"].id,
        name=name,
        value=Decimal(value),
        unit="pure",
        justification=f"Scene value for {name}.",
        proposed_by="test",
    )
    await confirm(session, assumption=assumption, actor=actor)


async def _a_case(
    scene: dict[str, Any], *, key: str, label: str, name: str, value: str
) -> Scenario:
    """One authored scenario disagreeing with the base about one assumption."""
    session: AsyncSession = scene["session"]
    case = await create_scenario(
        session,
        request_id=scene["request"].id,
        key=key,
        label=label,
        description=f"{label} case: {name} at {value}.",
    )
    await set_override(
        session,
        scenario=case,
        assumption_name=name,
        value=Decimal(value),
        unit="pure",
        justification=f"The {label.lower()} case argues {name} is {value}.",
    )
    return case


async def _record_step(scene: dict[str, Any], produced: dict[str, Any]) -> None:
    """Store the value step's output, which is what the section renders from."""
    session: AsyncSession = scene["session"]
    session.add(
        JobStep(
            job_id=scene["job"].id,
            step_key="value",
            sequence=9,
            status=JobStatus.SUCCEEDED,
            idempotency_key=f"{scene['job'].id}:value",
            input_hash="0" * 64,
            output_ref=produced,
        )
    )
    await session.flush()


async def _block(scene: dict[str, Any]) -> dict[str, Any]:
    return await valuation_method_block(
        scene["session"], job_id=scene["job"].id, request=scene["request"]
    )


class TestTheReportSurface:
    """ADR 0063: a claim about how a number was produced is a claim about a calculation.

    A residual-income valuation described as a discounted cash flow would be exactly the
    failure that ADR exists to prevent — prose about method no calculation backs, in the
    place a reader's trust is set.
    """

    async def test_the_method_note_never_calls_it_a_cash_flow(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _record_step(scene, (await _value(scene)).as_dict())

        note = (await _block(scene))["method_note"].lower()

        assert "book value" in note
        assert "cost of equity" in note
        assert "free cash flow" not in note
        assert "enterprise value" not in note
        assert "weighted average cost of capital" not in note or "rather than" in note

    async def test_the_note_says_the_terminal_choice_is_a_judgement(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _record_step(scene, (await _value(scene)).as_dict())

        note = (await _block(scene))["method_note"]

        assert "both ways" in note
        assert "judgement about competition" in note

    async def test_the_note_says_when_no_perpetuity_was_shown(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, return_on_equity="0.04")
        await _record_step(scene, (await _value(scene)).as_dict())

        note = (await _block(scene))["method_note"]

        assert "No perpetual-growth valuation is shown" in note

    async def test_the_drivers_shown_are_the_bank_ones(self, scene: dict[str, Any]) -> None:
        """A row for capex intensity on a bank's report is the platform asking a bank about
        accounts it does not keep (gap A64), even when the row is empty."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _record_step(scene, (await _value(scene)).as_dict())

        labels = {row["label"] for row in (await _block(scene))["forecast_drivers"]}

        assert {"Return on equity", "Payout ratio", "Terminal growth"} <= labels
        assert "Capex intensity" not in labels
        assert "Revenue growth" not in labels
        assert "Exit multiple" not in labels

    async def test_both_treatments_reach_the_table_distinguishably(
        self, scene: dict[str, Any]
    ) -> None:
        """Two rows of one name with different answers and nothing saying why is a ledger a
        reader cannot use, which is why the treatment is recorded on each."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _record_step(scene, (await _value(scene)).as_dict())

        labels = {row["label"] for row in (await _block(scene))["terminal_valuations"]}

        assert "Value per share — excess return competed away" in labels
        assert "Value per share — excess return in perpetuity" in labels
        assert "Premium to book value — excess return competed away" in labels

    async def test_a_run_that_did_not_value_is_not_told_it_failed_at_a_dcf(
        self, scene: dict[str, Any]
    ) -> None:
        """ "No discounted cash flow was produced" is false about a bank: none was ever going
        to be, and a reader told that would think the platform tried and failed at something
        it correctly refused to attempt."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit=BETA_ASSUMPTION)
        await _record_step(scene, (await _value(scene)).as_dict())

        note = (await _block(scene))["method_note"]

        assert "No residual-income valuation was produced" in note
        assert "discounted cash flow" not in note

    async def test_a_commentary_calling_it_a_wacc_is_refused(self, scene: dict[str, Any]) -> None:
        """The guard reads the rendered block rather than a fixed list, so a bank's block —
        which names a cost of equity and no WACC — refuses WACC talk without anything here
        being taught about banks."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _record_step(scene, (await _value(scene)).as_dict())
        block = await _block(scene)

        problems = commentary_problems(
            {"commentary": "The cash flows were discounted at the WACC shown above."}, block
        )

        assert problems


# ==========================================================================================
# The workflow steps
# ==========================================================================================


def _draft() -> AssumptionProposalDraft:
    """The two opinions ADR 0046 leaves to a model. Scripted because the step consults it
    whenever a model will run, and a missing script is a test passing against a reply nobody
    chose — which is exactly what FakeProvider refuses."""
    return AssumptionProposalDraft(
        terminal_growth=OpinionProposal(
            value=Decimal("0.021"), justification="Long-run nominal growth.", confidence=0.6
        ),
        exit_multiple=OpinionProposal(
            value=Decimal("11"), justification="Mid-range for the sector.", confidence=0.5
        ),
    )


def _step_context(scene: dict[str, Any], outputs: dict[str, Any], tmp_path: Any) -> StepContext:
    """A context for driving one step directly.

    The steps are where the dispatch lives, and nothing else exercises them: a service that
    values a bank correctly is no use if the step never calls it. Three mutations survived
    the first pass here — the value step always running the discounted cash flow, and the
    assumptions step recording no model — and both are silent in production.
    """
    step = JobStep(
        job_id=scene["job"].id,
        step_key="value",
        sequence=1,
        status=JobStatus.RUNNING,
        idempotency_key=f"{scene['job'].id}:probe",
        input_hash="0" * 64,
    )
    scene["session"].add(step)
    settings = Settings(
        http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
    )
    return StepContext(
        session=scene["session"],
        job=scene["job"],
        step=step,
        services={
            # The assumptions step builds an agent context whenever a model will run. No
            # call is scripted, so the fake provider is never asked for a reply: what is
            # under test is which model the step settles on, not the two opinions.
            "provider": FakeProvider({"AssumptionProposalDraft": _draft()}),
            "router": Router(settings),
            "settings": settings,
            "store": LocalArtefactStore(
                settings.artefact_root, max_bytes=settings.max_artefact_bytes
            ),
        },
        outputs=outputs,
    )


class TestTheStepsDispatchOnTheModel:
    async def test_the_assumptions_step_records_the_model_it_settled_on(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        """The value step reads this back rather than re-deriving it, so a step that records
        nothing leaves a bank with no valuation and nothing saying why."""
        await seed_years(scene, _YEARS)
        context = _step_context(
            scene,
            {
                "acquire": {"company_id": str(scene["company"].id)},
                CLASSIFY_STEP: {"sector_key": "banks"},
            },
            tmp_path,
        )

        result = await propose_assumptions_step(context)

        assert result.output["valuation_model"] == "residual_income"
        assert result.output["dcf_permitted"] is False

    async def test_an_ordinary_company_still_records_the_cash_flow(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        await seed_years(scene, _YEARS)
        context = _step_context(
            scene,
            {"acquire": {"company_id": str(scene["company"].id)}, CLASSIFY_STEP: {}},
            tmp_path,
        )

        result = await propose_assumptions_step(context)

        assert result.output["valuation_model"] == "dcf_fcff"
        assert result.output["dcf_permitted"] is True

    async def test_the_value_step_runs_the_bank_model_for_a_bank(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        """The dispatch itself. Without it a bank reaches `value_the_business`, which
        assembles a discounted cash flow from drivers nobody confirmed and refuses."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        context = _step_context(
            scene,
            {
                "acquire": {"company_id": str(scene["company"].id)},
                CLASSIFY_STEP: {"sector_key": "banks"},
                ASSUMPTIONS_STEP: {"valuation_model": "residual_income", "dcf_permitted": False},
            },
            tmp_path,
        )

        result = await value_step(context)

        assert result.output["model"] == "residual_income"
        assert result.output["valued"] is True

    async def test_a_sector_with_no_model_is_told_so_by_the_value_step(
        self, scene: dict[str, Any], tmp_path: Any
    ) -> None:
        await seed_years(scene, _YEARS)
        context = _step_context(
            scene,
            {
                "acquire": {"company_id": str(scene["company"].id)},
                # Both keys, exactly as `_propose_assumptions` writes them for a sector
                # with no model this build implements.
                "classify": {"sector_key": "reits"},
                ASSUMPTIONS_STEP: {"valuation_model": "", "dcf_permitted": False},
            },
            tmp_path,
        )

        result = await value_step(context)

        assert result.output["valued"] is False
        assert "no valuation model this build implements" in result.output["reason"]
