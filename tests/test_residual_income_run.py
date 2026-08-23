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

from aer.calc.residual_income import CLEAN_SURPLUS_CAVEAT
from aer.core.enums import JobStatus, UserRole
from aer.core.sectors import ValuationMandate, ValuationModel, mandate_for, profile_for
from aer.db.models import Calculation, JobStep, User
from aer.sections.valuation_method import commentary_problems, valuation_method_block
from aer.services.assumption_gate import (
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    RESIDUAL_INCOME_NAMES,
    RISK_FREE_ASSUMPTION,
)
from aer.services.assumptions import confirm, propose
from aer.services.prices import BETA_ASSUMPTION
from aer.services.residual_income_run import BankValuationOutcome, value_the_bank
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

    async def test_the_absence_of_scenarios_is_stated_rather_than_quiet(
        self, scene: dict[str, Any]
    ) -> None:
        """The discounted cash flow builds scenarios and grids and this does not. A reader
        comparing two reports has to be told that, not left to notice."""
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        produced = (await _value(scene)).as_dict()

        assert any("No scenarios or sensitivity grids" in item for item in produced["caveats"])

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
