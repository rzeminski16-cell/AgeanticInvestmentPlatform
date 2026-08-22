"""The six assumptions a filing can answer, and the reasons they carry.

Gap B2. `inputs_from` refuses a forecast without a confirmed assumption for every driver
and scalar, nothing proposed any, and so the assumptions page had always shown an empty
list and no run had ever produced a discounted cash flow.

Two things are under test and the second matters as much as the first.

**The arithmetic**: a compound growth rate rather than the mean of the annual ones, means of
ratios rather than ratios of sums, and a refusal wherever the history cannot honestly
support a starting point. Each of those is a choice that produces a different number, so
each is asserted against a scene where the two answers differ.

**The explanation**: a gate is a control only if the operator can interrogate what they are
approving. Every proposal has to say which measure it used and over which periods, and every
refusal has to say why — an assumption absent from the page with no reason looks like a
defect, and the operator cannot tell whether to wait for it or type it.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from aer.calc.dcf import DRIVER_NAMES
from aer.db.models import (
    Assumption,
)
from aer.services.assumption_proposals import (
    CASH_COST_OF_DEBT_NAME,
    DERIVED_NAMES,
    PROPOSED_BY,
    cash_cost_of_debt,
    derive_assumptions,
    propose_derived,
)
from aer.services.assumptions import UnconfirmedAssumptionError, as_quantity
from aer.services.valuation import SCALAR_NAMES
from tests.assumption_fixtures import a_year, analysed, seed_years

pytestmark = pytest.mark.integration


def named(outcome: Any, name: str) -> Any:
    return next((item for item in outcome.derived if item.name == name), None)


class TestTheSixThatHistoryAnswers:
    @pytest.fixture
    async def three_years(self, scene: dict[str, Any]) -> Any:
        await seed_years(
            scene,
            {
                date(2021, 12, 31): a_year(revenue="1000"),
                date(2022, 12, 31): a_year(revenue="1200", operating_income="300"),
                date(2023, 12, 31): a_year(revenue="1440", operating_income="360"),
            },
        )
        return derive_assumptions(await analysed(scene))

    async def test_every_driver_and_the_tax_rate_are_proposed(self, three_years: Any) -> None:
        """The whole point: the form is no longer empty."""
        assert {item.name for item in three_years.derived} == set(DERIVED_NAMES)

    async def test_the_growth_rate_is_compound_not_the_mean_of_the_annual_ones(
        self, three_years: Any
    ) -> None:
        """1000 to 1440 over two years is 20% compounded. The mean of the annual rates is
        also 20% here only because the growth is even, so the sharper scene is below."""
        growth = named(three_years, "revenue_growth")

        assert growth.value == Decimal("0.200000")

    async def test_a_margin_is_the_mean_of_the_ratios(self, three_years: Any) -> None:
        """250/1000, 300/1200 and 360/1440 are each 0.25."""
        assert named(three_years, "ebit_margin").value == Decimal("0.250000")

    async def test_working_capital_is_computed_not_read(self, three_years: Any) -> None:
        """No filer tags "working capital"; it is current assets less current liabilities,
        a subtraction everybody does and nobody files.

        Asserted on the value rather than on the prose. Net working capital is a flat 280
        across the scene's three years while revenue rises from 1000 to 1440, so the mean
        intensity is 0.2359. Reading current assets alone would give 0.4550 — far enough
        apart that a wrong one could not pass for the other.
        """
        intensity = named(three_years, "working_capital_intensity")

        assert intensity is not None
        assert intensity.value == Decimal("0.235926")
        assert "current assets less current liabilities" in intensity.justification

    async def test_the_tax_rate_comes_from_the_effective_rate(self, three_years: Any) -> None:
        """50/230, not a statutory rate this platform picked."""
        rate = named(three_years, "tax_rate")

        assert rate.value == Decimal("0.217391")
        assert "income tax expense" in rate.justification

    async def test_every_proposal_is_dimensionless(self, three_years: Any) -> None:
        """A ratio of two currency amounts, so the units cancel and the stored unit says so.
        A unit that will not parse is refused at `propose`, several layers from here."""
        assert {item.unit for item in three_years.derived} == {"pure"}

    async def test_nothing_is_proposed_that_the_valuation_does_not_ask_for(
        self, three_years: Any
    ) -> None:
        wanted = set(DRIVER_NAMES) | set(SCALAR_NAMES)

        assert {item.name for item in three_years.derived} <= wanted

    async def test_the_two_opinions_are_never_proposed_here(self, three_years: Any) -> None:
        """ADR 0046's whole boundary. No series answers either, so this module does not
        pretend to — they arrive from the agent, or from the operator."""
        proposed = {item.name for item in three_years.derived}

        assert "terminal_growth" not in proposed
        assert "exit_multiple" not in proposed


class TestCompoundingIsNotAveraging:
    """The choice that produces a different number, on a scene where it shows.

    +50% then -40% averages to +5% and compounds to -5.1%. A company that ended smaller
    than it started must not be proposed a positive growth rate.
    """

    async def test_a_company_that_shrank_is_not_proposed_growth(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(
            scene,
            {
                date(2021, 12, 31): a_year(revenue="1000"),
                date(2022, 12, 31): a_year(revenue="1500"),
                date(2023, 12, 31): a_year(revenue="900"),
            },
        )

        growth = named(derive_assumptions(await analysed(scene)), "revenue_growth")

        assert growth.value < 0
        assert growth.value == Decimal("-0.051317")


# -- What it refuses, and why ------------------------------------------------------------------


class TestARefusalSaysWhy:
    async def test_one_period_proposes_nothing_and_explains(self, scene: dict[str, Any]) -> None:
        await seed_years(scene, {date(2023, 12, 31): a_year()})

        outcome = derive_assumptions(await analysed(scene))

        assert outcome.derived == ()
        assert any("at least two" in reason for reason in outcome.skipped)

    async def test_a_missing_line_is_named_rather_than_silently_absent(
        self, scene: dict[str, Any]
    ) -> None:
        """A filer reporting no capital expenditure has no capex intensity. The operator has
        to be able to tell that from a defect."""
        without_capex = a_year()
        del without_capex["capital_expenditure"]
        await seed_years(
            scene,
            {
                date(2022, 12, 31): without_capex,
                date(2023, 12, 31): without_capex,
            },
        )

        outcome = derive_assumptions(await analysed(scene))

        assert named(outcome, "capex_intensity") is None
        assert any("Capex intensity could not be derived" in reason for reason in outcome.skipped)

    async def test_a_loss_making_year_is_refused_rather_than_averaged_in(
        self, scene: dict[str, Any]
    ) -> None:
        """Averaging a negative margin into a starting point projects a bad year forward as
        though it were the normal state, which is a forecast nobody wrote."""
        await seed_years(
            scene,
            {
                date(2022, 12, 31): a_year(operating_income="-100"),
                date(2023, 12, 31): a_year(operating_income="250"),
            },
        )

        outcome = derive_assumptions(await analysed(scene))

        assert named(outcome, "ebit_margin") is None
        assert any("negative" in reason for reason in outcome.skipped)

    async def test_a_zero_revenue_base_is_refused(self, scene: dict[str, Any]) -> None:
        """A growth rate from nothing is not a rate, and the division would trap."""
        await seed_years(
            scene,
            {
                date(2022, 12, 31): a_year(revenue="0"),
                date(2023, 12, 31): a_year(revenue="1000"),
            },
        )

        outcome = derive_assumptions(await analysed(scene))

        assert named(outcome, "revenue_growth") is None
        assert any("non-positive base" in reason for reason in outcome.skipped)

    async def test_refusing_one_does_not_refuse_the_rest(self, scene: dict[str, Any]) -> None:
        """A filing with one line missing is not a filing with nothing in it."""
        without_capex = a_year()
        del without_capex["capital_expenditure"]
        await seed_years(
            scene,
            {date(2022, 12, 31): without_capex, date(2023, 12, 31): without_capex},
        )

        outcome = derive_assumptions(await analysed(scene))

        assert named(outcome, "ebit_margin") is not None
        assert named(outcome, "tax_rate") is not None


class TestEveryProposalCanBeInterrogated:
    """The gate is a control only if the operator can argue with what it shows."""

    @pytest.fixture
    async def derived(self, scene: dict[str, Any]) -> Any:
        await seed_years(
            scene,
            {
                date(2021, 12, 31): a_year(revenue="1000"),
                date(2022, 12, 31): a_year(revenue="1200"),
                date(2023, 12, 31): a_year(revenue="1440"),
            },
        )
        return derive_assumptions(await analysed(scene))

    async def test_each_one_names_the_periods_it_used(self, derived: Any) -> None:
        for item in derived.derived:
            assert item.periods, item.name
            assert all(isinstance(period, date) for period in item.periods)

    async def test_each_justification_names_the_measure(self, derived: Any) -> None:
        growth = named(derived, "revenue_growth")

        assert "compound annual growth rate" in growth.justification
        assert "2021-12-31" in growth.justification
        assert "2023-12-31" in growth.justification

    async def test_a_mean_shows_the_observations_it_averaged(self, derived: Any) -> None:
        """So a reviewer can see a wild year pulling the average, rather than one number."""
        margin = named(derived, "ebit_margin")

        assert "The individual observations were" in margin.justification

    async def test_the_growth_proposal_says_it_is_held_flat(self, derived: Any) -> None:
        """A trailing rate is one number. Projecting a fade is a judgement this derivation
        does not make, and the operator should know it was not made."""
        assert "Held flat" in named(derived, "revenue_growth").justification

    async def test_the_values_are_not_shown_to_thirty_places(self, derived: Any) -> None:
        """0.114237881922... invites a belief in precision the filings do not have."""
        for item in derived.derived:
            assert -item.value.as_tuple().exponent <= 6, item.name


# -- Persistence ---------------------------------------------------------------------------


class TestProposingWritesUnconfirmedRows:
    @pytest.fixture
    async def persisted(self, scene: dict[str, Any]) -> Any:
        await seed_years(
            scene,
            {date(2022, 12, 31): a_year(), date(2023, 12, 31): a_year(revenue="1200")},
        )
        return await propose_derived(
            scene["session"],
            request_id=scene["request"].id,
            analysis=await analysed(scene),
            job_id=scene["job"].id,
        )

    async def test_a_row_is_written_for_each(self, scene: dict[str, Any], persisted: Any) -> None:
        outcome, rows = persisted
        stored = list(await scene["session"].scalars(select(Assumption)))

        assert {row.name for row in stored} == {item.name for item in outcome.derived}
        assert len(rows) == len(outcome.derived)

    async def test_none_of_them_is_confirmed(self, persisted: Any) -> None:
        """`propose` makes them unconfirmed whatever the caller says, and this module is a
        caller. A derived starting point is still a value somebody has to agree with."""
        _, rows = persisted

        assert rows
        assert not any(row.approved for row in rows)

    async def test_an_unconfirmed_one_cannot_reach_a_calculation(self, persisted: Any) -> None:
        """Invariant 3, at the point the number would be used."""
        _, rows = persisted

        with pytest.raises(UnconfirmedAssumptionError):
            as_quantity(rows[0])

    async def test_the_proposer_is_recorded_as_this_module(self, persisted: Any) -> None:
        """So "which of these did a person choose?" is a query rather than an inspection.

        Pinned to the literal. Comparing the rows against `PROPOSED_BY` passes however the
        constant is edited, including to something that would make a derived value look
        like a person's — which is the one wrong answer that matters here.
        """
        _, rows = persisted

        assert PROPOSED_BY == "aer.services.assumption_proposals"
        assert {row.proposed_by for row in rows} == {"aer.services.assumption_proposals"}
        assert not any(row.approved for row in rows)

    async def test_the_justification_reaches_the_row(self, persisted: Any) -> None:
        _, rows = persisted

        for row in rows:
            assert row.justification.strip()

    async def test_deriving_twice_supersedes_rather_than_duplicating(
        self, scene: dict[str, Any], persisted: Any
    ) -> None:
        await propose_derived(
            scene["session"],
            request_id=scene["request"].id,
            analysis=await analysed(scene),
            job_id=scene["job"].id,
        )

        stored = list(await scene["session"].scalars(select(Assumption)))
        assert len(stored) == len({row.name for row in stored})

    async def test_asking_what_the_numbers_would_be_writes_nothing(
        self, scene: dict[str, Any]
    ) -> None:
        """`derive_assumptions` is pure, so a caller can show a preview without committing
        the run to it."""
        await seed_years(
            scene,
            {date(2022, 12, 31): a_year(), date(2023, 12, 31): a_year(revenue="1200")},
        )

        derive_assumptions(await analysed(scene))

        assert list(await scene["session"].scalars(select(Assumption))) == []

    def test_the_derivation_has_no_session_to_write_with(self) -> None:
        """Purity enforced by the signature rather than by the test above, which can only
        show that it did not write on one occasion. A function with no session cannot."""
        parameters = inspect.signature(derive_assumptions).parameters

        assert list(parameters) == ["analysis"]
        assert "session" not in parameters


# A filer that tags cash interest but no interest charge: the CHRW shape (ADR 0067).
_WITH_CASH = {"interest_paid": "20", "short_term_debt": "0"}


class TestTheCashCostOfDebtProxy:
    """ADR 0067. A filer that tags no interest expense leaves the cost of debt with no
    derivation and the operator with an empty box. The cash figure from the cash-flow
    statement is *a* rate the borrowings cost — it is not the cost of debt, and the
    proposal has to say so itself.
    """

    async def test_it_proposes_cash_interest_over_average_debt(self, scene: dict[str, Any]) -> None:
        # Long-term debt 400 in both years, so the average is 400 and 20/400 = 0.05.
        # 2022 and 2023, not 2024: the scene's as-of is 30 June 2024 and a year is filed
        # the following February, so a 2024 year-end never survives point-in-time.
        await seed_years(
            scene,
            {
                date(2022, 12, 31): a_year(**_WITH_CASH),
                date(2023, 12, 31): a_year(**_WITH_CASH),
            },
        )

        proposal = cash_cost_of_debt(await analysed(scene))

        assert not isinstance(proposal, str), proposal
        assert proposal.name == CASH_COST_OF_DEBT_NAME
        assert proposal.value == Decimal("0.050000")
        assert proposal.unit == "pure"
        assert "the average of" in proposal.justification

    async def test_the_justification_names_the_substitution_and_its_direction(
        self, scene: dict[str, Any]
    ) -> None:
        """The containment ADR 0067 leans on hardest, and the one most likely to erode:
        a proxy whose justification reads like a derivation is the whole failure."""
        await seed_years(
            scene,
            {
                date(2022, 12, 31): a_year(**_WITH_CASH),
                date(2023, 12, 31): a_year(**_WITH_CASH),
            },
        )

        proposal = cash_cost_of_debt(await analysed(scene))

        assert not isinstance(proposal, str), proposal
        said = proposal.justification
        assert "cash-basis proxy" in said
        assert "not the accrual cost of debt" in said
        assert "capitalised" in said
        assert "either side of the true rate" in said

    async def test_a_single_period_prices_off_the_closing_balance_and_says_so(
        self, scene: dict[str, Any]
    ) -> None:
        await seed_years(scene, {date(2023, 12, 31): a_year(**_WITH_CASH)})

        proposal = cash_cost_of_debt(await analysed(scene))

        assert not isinstance(proposal, str), proposal
        assert "closing balance" in proposal.justification
        assert "no prior year to average with" in proposal.justification

    async def test_a_filer_with_no_cash_figure_either_is_refused_with_a_reason(
        self, scene: dict[str, Any]
    ) -> None:
        """Neither the charge nor the cash: there is nothing to derive a rate from, and
        the sentence says that rather than leaving the name unexplained."""
        await seed_years(
            scene,
            {
                date(2022, 12, 31): a_year(short_term_debt="0"),
                date(2023, 12, 31): a_year(short_term_debt="0"),
            },
        )

        proposal = cash_cost_of_debt(await analysed(scene))

        assert isinstance(proposal, str)
        assert proposal.lower().startswith("cost of debt")
        assert "no cash interest paid" in proposal

    async def test_an_implausible_rate_is_declined_rather_than_proposed(
        self, scene: dict[str, Any]
    ) -> None:
        """`propose` raises on a value outside the plausible band, so a proxy that did
        not check the band itself would kill the whole gate assembly for one odd filer."""
        await seed_years(
            scene,
            {
                date(2022, 12, 31): a_year(interest_paid="900", short_term_debt="0"),
                date(2023, 12, 31): a_year(interest_paid="900", short_term_debt="0"),
            },
        )

        proposal = cash_cost_of_debt(await analysed(scene))

        assert isinstance(proposal, str)
        assert "plausible borrowing cost" in proposal

    async def test_a_run_with_no_period_is_refused(self, scene: dict[str, Any]) -> None:
        proposal = cash_cost_of_debt(await analysed(scene))

        assert isinstance(proposal, str)
        assert "no annual period" in proposal


class TestTheDerivedSetMatchesWhatAForecastNeeds:
    def test_no_name_here_is_unknown_to_the_valuation(self) -> None:
        """A proposal for a name `inputs_from` never reads is a row nobody will ever use,
        sitting on the gate looking like it matters."""
        assert set(DERIVED_NAMES) <= set(DRIVER_NAMES) | set(SCALAR_NAMES)

    def test_together_with_the_two_opinions_it_covers_everything(self) -> None:
        """The gate has to be completable. If these six plus the two ADR 0046 names do not
        cover the whole requirement, some assumption has no proposer at all and the run
        stops at a form the operator cannot finish without guessing what is missing."""
        covered = set(DERIVED_NAMES) | {"terminal_growth", "exit_multiple"}

        assert set(DRIVER_NAMES) | set(SCALAR_NAMES) == covered

    def test_the_derived_set_holds_no_duplicates(self) -> None:
        assert len(DERIVED_NAMES) == len(set(DERIVED_NAMES))
