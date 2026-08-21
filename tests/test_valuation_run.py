"""The first discounted cash flow this platform has ever produced.

Gap B2c. `aer/calc/dcf.py`, `aer/calc/wacc.py` and `aer/services/valuation.py` were built
through Phase 3 with unit and property tests, and no workflow step ever assembled their
inputs — so the valuation page has been empty since the first live run. This suite is the
proof that the wiring closes: seed a company, confirm every assumption a forecast needs,
and get a per-share value with every step of it recorded.

The refusals matter as much as the answer. A run that cannot value the business is the
ordinary case for a thin filer, and each way of failing has to name the figure that was
missing rather than leaving a blank page.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.wacc import ALL_EQUITY_NOTE, BOOK_WEIGHT_CAVEAT, EquityBasis
from aer.core.enums import UserRole
from aer.core.sectors import ValuationMandate, ValuationModel
from aer.db.models import Calculation, Sensitivity, User
from aer.services.assumption_gate import (
    COST_OF_DEBT_ASSUMPTION,
    EQUITY_RISK_PREMIUM_ASSUMPTION,
    REQUIRED_NAMES,
    RISK_FREE_ASSUMPTION,
)
from aer.services.assumptions import assumptions_for_request, confirm, propose
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation_run import (
    SENSITIVITY_POINTS,
    ValuationOutcome,
    value_the_business,
)
from tests.assumption_fixtures import a_year, analysed, seed_years

pytestmark = pytest.mark.integration

# A filer with everything a forecast needs: a share count to divide by and an interest
# charge to price its borrowings with, neither of which the shared fixture carries because
# the six derived drivers do not need them.
# Diluted deliberately above basic, which is the real-world relation and the only way a
# test can tell which count the valuation divided by. Seeding them equal made the choice
# invisible: a mutation preferring basic passed every assertion.
_BASIC_SHARES = Decimal("100")
_DILUTED_SHARES = Decimal("110")

_VALUABLE = {
    "shares_outstanding": str(_BASIC_SHARES),
    "basic_shares_outstanding": str(_BASIC_SHARES),
    "diluted_shares_outstanding": str(_DILUTED_SHARES),
    "interest_expense": "20",
    "short_term_debt": "0",
}

_YEARS = {
    date(2022, 12, 31): a_year(revenue="1000", operating_income="240", **_VALUABLE),
    date(2023, 12, 31): a_year(revenue="1150", operating_income="290", **_VALUABLE),
    date(2024, 12, 31): a_year(revenue="1300", operating_income="340", **_VALUABLE),
}

# What an operator would confirm at the assumptions gate. Deliberately plain round numbers:
# the arithmetic is `aer.calc.dcf`'s and has its own property tests, and a scene using
# memorable inputs makes a wrong answer here legible.
_CONFIRMED: dict[str, str] = {
    "revenue_growth": "0.05",
    "ebit_margin": "0.25",
    "capex_intensity": "0.06",
    "depreciation_intensity": "0.05",
    "working_capital_intensity": "0.20",
    "tax_rate": "0.21",
    "terminal_growth": "0.02",
    "exit_multiple": "10",
    RISK_FREE_ASSUMPTION: "0.042",
    BETA_ASSUMPTION: "1.1",
    EQUITY_RISK_PREMIUM_ASSUMPTION: "0.055",
}

# The CHRW shape: borrowings on the balance sheet and no interest expense anywhere.
_WITHOUT_INTEREST = {
    period: {key: value for key, value in values.items() if key != "interest_expense"}
    for period, values in _YEARS.items()
}

MANDATE = ValuationMandate(
    model=ValuationModel.DCF_FCFF, subject="CTSO", sector_key="", confirmed_by=""
)


async def _confirm_all(scene: dict[str, Any], *, omit: str = "") -> None:
    """Propose and confirm every assumption a forecast needs, bar one if asked."""
    session: AsyncSession = scene["session"]
    actor = User(email="operator@example.invalid", display_name="O", role=UserRole.OWNER)
    session.add(actor)
    await session.flush()

    for name, value in _CONFIRMED.items():
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


async def _confirm_extra(scene: dict[str, Any], name: str, value: str) -> None:
    """One more confirmed assumption, by the actor `_confirm_all` created."""
    session: AsyncSession = scene["session"]
    actor = await session.scalar(select(User).where(User.email == "operator@example.invalid"))
    assert actor is not None, "_confirm_all has to run first"
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


async def _value(scene: dict[str, Any], *, years: int = 5) -> ValuationOutcome:
    return await value_the_business(
        scene["session"],
        request=scene["request"],
        job_id=scene["job"].id,
        analysis=await analysed(scene),
        mandate=MANDATE,
        years=years,
    )


# ==========================================================================================
# The payoff
# ==========================================================================================


class TestAConfirmedRunProducesAValuation:
    async def test_it_runs_and_reports_a_value_per_share(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.ran, outcome.reason
        assert outcome.base is not None
        assert outcome.base.gordon.value_per_share.value > 0
        assert outcome.base.exit_multiple.value_per_share.value > 0

    async def test_the_forecast_runs_for_the_years_it_was_asked_for(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene, years=5)

        assert outcome.base is not None
        assert len(outcome.base.years) == 5

    async def test_every_step_of_it_is_a_stored_calculation(self, scene: dict[str, Any]) -> None:
        # Invariant 3: no figure reaches a report unless it is a stored fact or a recorded
        # calculation. A valuation that returned a number and persisted nothing would put a
        # per-share value in the report with no lineage behind it.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        session: AsyncSession = scene["session"]

        await _value(scene)

        names = {
            row.name
            for row in await session.scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        }
        assert {"wacc", "cost_of_equity", "gordon_terminal_value", "free_cash_flow"} <= names

    async def test_the_discount_rate_is_decomposed_rather_than_asserted(
        self, scene: dict[str, Any]
    ) -> None:
        # ADR 0046's rule, at the layer that would have been tempted to break it: the WACC
        # is built from three confirmed rows, so the report can take it apart.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.cost_of_capital is not None
        assert outcome.cost_of_capital.cost_of_equity.value > 0
        assert outcome.cost_of_capital.wacc.value > 0
        # Debt is on this balance sheet, so the debt leg must be priced rather than skipped.
        assert outcome.cost_of_capital.cost_of_debt_pre_tax is not None
        assert ALL_EQUITY_NOTE not in outcome.caveats

    async def test_the_cost_of_debt_is_derived_from_the_filings(
        self, scene: dict[str, Any]
    ) -> None:
        # Interest expense over average debt: arithmetic on two filed lines, so it belongs
        # in the ledger rather than on the assumptions page.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        session: AsyncSession = scene["session"]

        await _value(scene)

        names = {
            row.name
            for row in await session.scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        }
        assert "cost_of_debt" in names
        assert "average_debt" in names, "the prior year's balance was not averaged in"

    async def test_the_per_share_figure_divides_by_the_diluted_count(
        self, scene: dict[str, Any]
    ) -> None:
        # A per-share value that ignores options in issue flatters itself. Diluted is the
        # more conservative reading and is the one this platform reports.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.base is not None
        gordon = outcome.base.gordon
        implied = gordon.equity_value.value / gordon.value_per_share.value
        assert implied == pytest.approx(_DILUTED_SHARES, rel=Decimal("0.000001"))
        assert implied != pytest.approx(_BASIC_SHARES, rel=Decimal("0.000001"))

    async def test_the_book_equity_substitution_is_declared(self, scene: dict[str, Any]) -> None:
        # Nothing here acquires a price, so the equity weight is shareholders' funds — which
        # understates the equity weight and produces a WACC that is too low. A reader has to
        # be told, and the enum is what makes the substitution visible rather than assumed.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.cost_of_capital is not None
        assert outcome.cost_of_capital.basis is EquityBasis.BOOK
        assert BOOK_WEIGHT_CAVEAT in outcome.caveats

    async def test_the_bridge_is_net_debt_alone_and_says_so(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert any("net debt alone" in caveat for caveat in outcome.caveats)


# ==========================================================================================
# The grids
# ==========================================================================================


class TestTheSensitivityGrids:
    async def test_two_grids_are_produced_and_stored(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        session: AsyncSession = scene["session"]

        outcome = await _value(scene)

        assert len(outcome.grids) == 2
        stored = list(await session.scalars(select(Sensitivity)))
        assert len(stored) == 2

    async def test_each_grid_is_square_and_complete(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        for grid in outcome.grids:
            assert len(grid.cells) == SENSITIVITY_POINTS * SENSITIVITY_POINTS

    async def test_the_axes_are_the_pairs_that_decide_the_answer(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        pairs = {(grid.row_axis.field, grid.column_axis.field) for grid in outcome.grids}
        assert pairs == {("wacc", "terminal_growth"), ("wacc", "exit_multiple")}

    async def test_each_axis_is_centred_on_the_base_case(self, scene: dict[str, Any]) -> None:
        # Centred rather than starting at the base, so the grid reads as "what if I am wrong
        # in either direction" rather than "what if I am wrong upwards".
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.cost_of_capital is not None
        middle = SENSITIVITY_POINTS // 2
        for grid in outcome.grids:
            assert grid.row_axis.values[middle].value == outcome.cost_of_capital.wacc.value
        growth_grid = next(g for g in outcome.grids if g.column_axis.field == "terminal_growth")
        assert growth_grid.column_axis.values[middle].value == Decimal("0.02")


# ==========================================================================================
# The refusals, each naming what was missing
# ==========================================================================================


class TestWhatStopsAValuation:
    async def test_an_unconfirmed_assumption_stops_it_and_names_the_row(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit="terminal_growth")

        outcome = await _value(scene)

        assert outcome.ran is False
        assert "terminal_growth" in outcome.reason

    async def test_a_missing_cost_of_capital_input_names_itself(
        self, scene: dict[str, Any]
    ) -> None:
        # Named individually: "the cost of capital is missing" does not tell an operator
        # which row to go and agree to.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit=BETA_ASSUMPTION)

        outcome = await _value(scene)

        assert outcome.ran is False
        assert BETA_ASSUMPTION in outcome.reason

    async def test_a_proposed_but_unconfirmed_assumption_is_not_enough(
        self, scene: dict[str, Any]
    ) -> None:
        # The whole point of the gate. `as_quantity` refuses an unconfirmed row, so a
        # proposal sitting on the page must not reach a forecast.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit="exit_multiple")
        await propose(
            scene["session"],
            request_id=scene["request"].id,
            name="exit_multiple",
            value=Decimal("10"),
            unit="pure",
            justification="Proposed and never agreed to.",
            proposed_by="test",
        )

        outcome = await _value(scene)

        assert outcome.ran is False
        assert "exit_multiple" in outcome.reason

    async def test_no_annual_period_stops_it(self, scene: dict[str, Any]) -> None:
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.ran is False
        assert "no base year" in outcome.reason

    async def test_no_share_count_stops_it_and_says_which_figure(
        self, scene: dict[str, Any]
    ) -> None:
        # An enterprise value with no per-share figure is not a recommendation.
        without_shares = {
            period: {key: value for key, value in values.items() if "shares_outstanding" not in key}
            for period, values in _YEARS.items()
        }
        await seed_years(scene, without_shares)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.ran is False
        assert "share count" in outcome.reason

    async def test_debt_with_no_interest_charge_stops_it(self, scene: dict[str, Any]) -> None:
        # A cost of debt this platform invented would be weighted into the discount rate and
        # would look in the report exactly like one somebody sourced.
        without_interest = {
            period: {key: value for key, value in values.items() if key != "interest_expense"}
            for period, values in _YEARS.items()
        }
        await seed_years(scene, without_interest)
        await _confirm_all(scene)

        outcome = await _value(scene)

        assert outcome.ran is False
        assert "interest expense" in outcome.reason
        # And the refusal names the remedy: the CHRW run printed the old message verbatim
        # into a report, and it told the operator what was wrong with no way to act on it.
        assert COST_OF_DEBT_ASSUMPTION in outcome.reason

    async def test_a_refusal_reports_no_figures_at_all(self, scene: dict[str, Any]) -> None:
        # A partial outcome would be read as a valuation by anything rendering it.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit="revenue_growth")

        outcome = await _value(scene)

        assert outcome.as_dict() == {"valued": False, "reason": outcome.reason}
        assert outcome.base is None
        assert outcome.grids == ()


class TestAConfirmedCostOfDebtStandsInWhereNothingWasFiled:
    """Report-quality R13, the valuation half. Some filers tag no interest expense at all
    — the live CHRW run — and for them the cost of debt was derived-or-nothing: the one
    discounted-cash-flow input no person was allowed to supply. A confirmed
    ``cost_of_debt`` row now stands in exactly there, and only there — a filed interest
    line still outranks it, because a filed line outranks an opinion about it."""

    async def test_the_confirmed_rate_lets_the_valuation_run(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _WITHOUT_INTEREST)
        await _confirm_all(scene)
        await _confirm_extra(scene, COST_OF_DEBT_ASSUMPTION, "0.05")

        outcome = await _value(scene)

        assert outcome.ran, outcome.reason
        assert outcome.cost_of_capital is not None
        assert outcome.cost_of_capital.cost_of_debt_pre_tax is not None
        assert outcome.cost_of_capital.cost_of_debt_pre_tax.value == Decimal("0.05")

    async def test_the_supplied_rate_is_not_dressed_as_a_derivation(
        self, scene: dict[str, Any]
    ) -> None:
        # The ledger tells a confirmed assumption and a derived rate apart by source kind;
        # a supplied figure must not leave a `cost_of_debt` calculation implying arithmetic
        # on filed lines that do not exist.
        await seed_years(scene, _WITHOUT_INTEREST)
        await _confirm_all(scene)
        await _confirm_extra(scene, COST_OF_DEBT_ASSUMPTION, "0.05")
        session: AsyncSession = scene["session"]

        await _value(scene)

        names = {
            row.name
            for row in await session.scalars(
                select(Calculation).where(Calculation.job_id == scene["job"].id)
            )
        }
        assert "cost_of_debt" not in names
        assert "average_debt" not in names

    async def test_a_filed_interest_line_outranks_the_confirmed_rate(
        self, scene: dict[str, Any]
    ) -> None:
        # Doctrine: deterministic derivation from filed lines beats an assumption. An
        # operator's 30% must not displace the 5% the filings themselves state.
        await seed_years(scene, _YEARS)
        await _confirm_all(scene)
        await _confirm_extra(scene, COST_OF_DEBT_ASSUMPTION, "0.30")

        outcome = await _value(scene)

        assert outcome.ran, outcome.reason
        assert outcome.cost_of_capital is not None
        assert outcome.cost_of_capital.cost_of_debt_pre_tax is not None
        # 20 of interest over 400 of average debt, from the filings.
        assert outcome.cost_of_capital.cost_of_debt_pre_tax.value == Decimal("0.05")

    async def test_an_unconfirmed_rate_is_not_enough(self, scene: dict[str, Any]) -> None:
        # The same rule every other assumption lives under: proposed is not agreed.
        await seed_years(scene, _WITHOUT_INTEREST)
        await _confirm_all(scene)
        await propose(
            scene["session"],
            request_id=scene["request"].id,
            name=COST_OF_DEBT_ASSUMPTION,
            value=Decimal("0.05"),
            unit="pure",
            justification="Proposed and never agreed to.",
            proposed_by="test",
        )

        outcome = await _value(scene)

        assert outcome.ran is False
        assert "interest expense" in outcome.reason


class TestTheSceneCoversWhatAForecastNeeds:
    def test_the_confirmed_set_is_exactly_the_required_one(self) -> None:
        # A guard on the fixture rather than on the code: if `REQUIRED_NAMES` grows a name,
        # this scene stops covering it and every test above would quietly assert less.
        assert set(_CONFIRMED) == set(REQUIRED_NAMES)


class TestNothingConfirmsItself:
    async def test_valuing_does_not_confirm_anything(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, _YEARS)
        await _confirm_all(scene, omit="terminal_growth")
        await propose(
            scene["session"],
            request_id=scene["request"].id,
            name="terminal_growth",
            value=Decimal("0.02"),
            unit="pure",
            justification="Proposed only.",
            proposed_by="test",
        )

        await _value(scene)

        rows = await assumptions_for_request(scene["session"], scene["request"].id)
        unconfirmed = {row.name for row in rows if not row.approved}
        assert unconfirmed == {"terminal_growth"}
