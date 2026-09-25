"""ADR 0132 §3: the gate shows, before the confirmation, what the value step records after it.

The verdict round's MSFT run confirmed perpetual growth of 3% beside a 14x exit multiple that
implies 6.5%, and the report printed two per-share figures nearly twice apart. The implied
figures that say why were recorded — after the confirmation, in the valuation section. The
preview strikes the same base case over the rows as proposed, so the gate can say so first.

What is under test is the promise the preview makes and the one it must not break: its figures
are the value step's own, to the ledger's stored precision, and none of them is ever recorded.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

import pytest
from sqlalchemy import func, select

from aer.config import HouseStyle
from aer.core.enums import JobStatus, UserRole
from aer.core.sectors import ValuationModel, unclassified_mandate
from aer.db.models import Calculation, JobStep, User
from aer.services.assumption_gate import EQUITY_RISK_PREMIUM_ASSUMPTION, RISK_FREE_ASSUMPTION
from aer.services.assumptions import assumptions_for_request, confirm, propose
from aer.services.preview import TerminalCheck, terminal_check
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation_run import value_the_business
from aer.workflow.workflows.vertical_slice_v1 import FORECAST_YEARS
from tests.assumption_fixtures import a_year, analysed, seed_years

pytestmark = pytest.mark.integration

_SHARES = {
    "shares_outstanding": "100",
    "basic_shares_outstanding": "100",
    "diluted_shares_outstanding": "110",
    "interest_expense": "20",
    "short_term_debt": "0",
}

_YEARS = {
    date(2022, 12, 31): a_year(revenue="1000", operating_income="240", **_SHARES),
    date(2023, 12, 31): a_year(revenue="1150", operating_income="290", **_SHARES),
    date(2024, 12, 31): a_year(revenue="1300", operating_income="340", **_SHARES),
}

# The round's shape: slow growth for ever beside a multiple that implies far faster.
_APART: dict[str, str] = {
    "revenue_growth": "0.05",
    "ebit_margin": "0.25",
    "capex_intensity": "0.06",
    "depreciation_intensity": "0.05",
    "working_capital_intensity": "0.20",
    "tax_rate": "0.21",
    "terminal_growth": "0.01",
    "exit_multiple": "25",
    RISK_FREE_ASSUMPTION: "0.042",
    BETA_ASSUMPTION: "1.1",
    EQUITY_RISK_PREMIUM_ASSUMPTION: "0.055",
}

_NO_PRICE: dict[str, Any] = {"acquired": False}


async def _at_the_gate(
    scene: dict[str, Any],
    *,
    proposed: dict[str, str] = _APART,
    prices: dict[str, Any] | None = _NO_PRICE,
    model: str = ValuationModel.DCF_FCFF.value,
) -> None:
    """A run stopped at the assumptions gate: filings, the steps the value step reads, and
    every row proposed and none confirmed."""
    await seed_years(scene, _YEARS)
    session = scene["session"]
    recorded: dict[str, dict[str, Any]] = {
        "acquire": {"company_id": str(scene["company"].id)},
        "propose_assumptions": {"valuation_model": model},
    }
    if prices is not None:
        recorded["acquire_prices"] = prices
    for sequence, (key, output) in enumerate(recorded.items()):
        session.add(
            JobStep(
                job_id=scene["job"].id,
                step_key=key,
                sequence=sequence,
                status=JobStatus.SUCCEEDED,
                attempt=0,
                idempotency_key=f"{scene['job'].id}:{key}",
                input_hash="a" * 64,
                output_ref=output,
            )
        )
    for name, value in proposed.items():
        await _propose(scene, name, value)
    await session.flush()


async def _propose(scene: dict[str, Any], name: str, value: str) -> None:
    await propose(
        scene["session"],
        request_id=scene["request"].id,
        name=name,
        value=Decimal(value),
        unit="pure",
        justification=f"Scene value for {name}.",
        proposed_by="test",
    )


# The ledger's stored scale: `calculations.output_value` is Numeric(38, 12), so a recorded
# figure is the arithmetic's own rounded there, and a preview figure is compared at it.
_STORED: Final = Decimal("1e-12")


def _stored(value: Decimal) -> Decimal:
    return value.quantize(_STORED, rounding=ROUND_HALF_UP)


async def _recorded(scene: dict[str, Any], name: str, **parameters: str) -> Decimal:
    """The one base-case row of ``name`` the value step struck."""
    rows = [
        row
        for row in await scene["session"].scalars(
            select(Calculation).where(
                Calculation.job_id == scene["job"].id, Calculation.name == name
            )
        )
        if all((row.parameters or {}).get(key) == value for key, value in parameters.items())
    ]
    assert len(rows) == 1, f"{name} {parameters}: {len(rows)} rows"
    found: Decimal = rows[0].output_value
    return found


class TestThePreviewIsTheValueStepBeforeTheConfirmation:
    async def test_every_figure_is_the_one_the_value_step_records(
        self, scene: dict[str, Any]
    ) -> None:
        """Struck before the confirmation, then the rows confirmed unchanged and the value
        step run: the figures agree to every digit the ledger keeps, because it is one
        assembly and one piece of arithmetic rather than two that happen to match."""
        await _at_the_gate(scene)

        check = await terminal_check(scene["session"], job=scene["job"])
        assert check is not None

        actor = User(email="operator@example.invalid", display_name="O", role=UserRole.OWNER)
        scene["session"].add(actor)
        await scene["session"].flush()
        for row in await assumptions_for_request(scene["session"], scene["request"].id):
            await confirm(scene["session"], assumption=row, actor=actor)
        await value_the_business(
            scene["session"],
            request=scene["request"],
            job_id=scene["job"].id,
            analysis=await analysed(scene),
            mandate=unclassified_mandate(ValuationModel.DCF_FCFF, subject="CTSO"),
            years=FORECAST_YEARS,
        )

        base = {"case": "base"}
        assert _stored(check.gordon_per_share) == await _recorded(
            scene, "value_per_share", method="gordon_growth", **base
        )
        assert _stored(check.exit_multiple_per_share) == await _recorded(
            scene, "value_per_share", method="exit_multiple", **base
        )
        assert _stored(check.growth_the_multiple_implies) == await _recorded(
            scene, "implied_terminal_growth", **base
        )
        assert _stored(check.multiple_the_growth_implies) == await _recorded(
            scene, "implied_exit_multiple", **base
        )
        assert _stored(check.disagreement) == await _recorded(scene, "method_disagreement", **base)
        assert check.terminal_growth == Decimal("0.01")
        assert check.exit_multiple == Decimal(25)
        assert check.currency == "USD"

    async def test_nothing_the_preview_strikes_is_recorded(self, scene: dict[str, Any]) -> None:
        """The one place an unconfirmed value enters arithmetic, and the reason it may: the
        ledger it writes to is never persisted, so no report can footnote a figure a
        person did not agree to."""
        await _at_the_gate(scene)

        assert await terminal_check(scene["session"], job=scene["job"]) is not None
        await scene["session"].flush()

        assert await scene["session"].scalar(select(func.count()).select_from(Calculation)) == 0

    async def test_the_rows_stay_unconfirmed(self, scene: dict[str, Any]) -> None:
        await _at_the_gate(scene)

        await terminal_check(scene["session"], job=scene["job"])

        rows = await assumptions_for_request(scene["session"], scene["request"].id)
        assert rows
        assert not any(row.approved for row in rows)


class TestTheGateSpeaksOnlyPastTheBand:
    async def test_two_methods_that_agree_say_nothing(self, scene: dict[str, Any]) -> None:
        """Set the multiple to the one the growth implies and the gap closes: there is
        nothing to say, and the gate says nothing."""
        await _at_the_gate(scene)
        apart = await terminal_check(scene["session"], job=scene["job"])
        assert apart is not None

        await _propose(scene, "exit_multiple", str(round(apart.multiple_the_growth_implies, 4)))

        assert await terminal_check(scene["session"], job=scene["job"]) is None

    async def test_an_input_nobody_has_proposed_says_nothing(self, scene: dict[str, Any]) -> None:
        """The gate lists it as outstanding on its own; a preview that could not be struck
        has nothing to add."""
        without = {name: value for name, value in _APART.items() if name != "exit_multiple"}
        await _at_the_gate(scene, proposed=without)

        assert await terminal_check(scene["session"], job=scene["job"]) is None

    async def test_a_run_whose_price_step_has_not_finished_says_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        """The value step waits for the price; a preview struck without it would weigh
        equity at book and show a discount rate the report will not use."""
        await _at_the_gate(scene, prices=None)

        assert await terminal_check(scene["session"], job=scene["job"]) is None

    async def test_a_bank_says_nothing(self, scene: dict[str, Any]) -> None:
        """A bank's residual income has one terminal assumption, not two to disagree."""
        await _at_the_gate(scene, model=ValuationModel.RESIDUAL_INCOME.value)

        assert await terminal_check(scene["session"], job=scene["job"]) is None

    async def test_a_run_that_has_proposed_nothing_yet_says_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)

        assert await terminal_check(scene["session"], job=scene["job"]) is None


class TestWhatTheGateSays:
    def test_the_rounds_own_figures_read_as_the_adr_gives_them(self) -> None:
        check = TerminalCheck(
            wacc=Decimal("0.097"),
            terminal_growth=Decimal("0.03"),
            exit_multiple=Decimal(14),
            growth_the_multiple_implies=Decimal("0.065"),
            multiple_the_growth_implies=Decimal("6.4"),
            gordon_per_share=Decimal("227.43"),
            exit_multiple_per_share=Decimal("442.01"),
            currency="USD",
            disagreement=Decimal("0.9435"),
        )

        first, second = check.sentences(style=HouseStyle())

        assert first == (
            "At a discount rate of 9.7%, an exit multiple of 14\N{MULTIPLICATION SIGN} implies "
            "perpetual growth of 6.5% a year, and perpetual growth of 3% implies an exit "
            "multiple of 6.4\N{MULTIPLICATION SIGN}."
        )
        assert second == (
            "Confirmed as they stand, the two methods give $227.43 (perpetuity growth) and "
            "$442.01 (exit multiple) a share, the higher 94.4% above the lower, and the report "
            "prints both with these two figures beside them."
        )

    def test_no_range_and_no_advice(self) -> None:
        """Two answers and the reason, in the report's words — never a band, and never a
        suggestion of which to prefer."""
        check = TerminalCheck(
            wacc=Decimal("0.09"),
            terminal_growth=Decimal("0.02"),
            exit_multiple=Decimal(20),
            growth_the_multiple_implies=Decimal("0.07"),
            multiple_the_growth_implies=Decimal(8),
            gordon_per_share=Decimal(40),
            exit_multiple_per_share=Decimal(90),
            currency="GBP",
            disagreement=Decimal("1.25"),
        )

        said = " ".join(check.sentences(style=HouseStyle())).lower()

        assert "£40 (perpetuity growth) and £90 (exit multiple)" in said
        for word in (" to £", "range", "should", "recommend", "prefer"):
            assert word not in said
