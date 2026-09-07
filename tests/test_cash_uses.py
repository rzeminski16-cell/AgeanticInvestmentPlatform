"""The capital-allocation figures: what the company did with its cash, recorded.

The Capital Allocation writer computed the year-on-year change in the financing lines and
the sum of buybacks and dividends for itself, and the numeral rule refused every one
(roadmap §2.1). These tests hold the figures that replace that arithmetic to the kernel's
standards: pure, unit-safe, traced, and honest about what a filing does not report.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aer.calc.cash_uses import (
    CASH_USE_DEFINITIONS,
    assess_cash_uses,
    capital_expenditure_change,
    distributions_to_operating_cash_flow,
    dividends_paid_change,
    repayments_of_debt_change,
    share_repurchases_change,
    shareholder_distributions,
)
from aer.calc.engine import CalculationContext
from aer.calc.statements import assemble
from aer.calc.units import CalculationError, Quantity, SourceRef, UnitMismatchError, money
from aer.core.concepts import CANONICAL_CONCEPTS, MAGNITUDE_CONCEPTS

SOURCE = SourceRef.financial_fact("fact-1")

# A year that returned 65 of the 260 it generated: 40 of buybacks, 25 of dividends.
THIS_YEAR = {
    "operating_cash_flow": "260",
    "capital_expenditure": "80",
    "share_repurchases": "40",
    "dividends_paid": "25",
    "repayments_of_debt": "30",
}

# The year before: more buybacks, fewer dividends, the same debt repaid, less capex.
LAST_YEAR = {
    **THIS_YEAR,
    "share_repurchases": "44",
    "dividends_paid": "20",
    "capital_expenditure": "72",
}


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="testsha")


def usd(value: str) -> Quantity:
    return money(value, "USD", source=SOURCE)


def facts(source: dict[str, str], **overrides: str) -> dict[str, Quantity]:
    return {concept: usd(value) for concept, value in {**source, **overrides}.items()}


def figures(
    context: CalculationContext, values: dict[str, Quantity], prior: dict[str, Quantity] | None
) -> dict:
    assessed = assess_cash_uses(
        context,
        assemble(context, values),
        prior=assemble(context, prior) if prior is not None else None,
    )
    return {figure.key: figure for figure in assessed}


# -- The table ---------------------------------------------------------------------------------


class TestTheTableItself:
    def test_every_figure_needs_only_canonical_concepts(self) -> None:
        for definition in CASH_USE_DEFINITIONS:
            for concept in definition.needs:
                assert concept in CANONICAL_CONCEPTS, f"{definition.key}: {concept}"

    def test_every_change_is_over_a_line_held_as_a_magnitude(self) -> None:
        """A change reads as a rise only because both years carry the payment as a
        positive amount; a line that kept the filer's sign would need a different rule."""
        for definition in CASH_USE_DEFINITIONS:
            if definition.needs_prior:
                [concept] = definition.needs
                assert concept in MAGNITUDE_CONCEPTS, definition.key

    def test_every_figure_says_what_it_is(self) -> None:
        for definition in CASH_USE_DEFINITIONS:
            assert definition.note, definition.key
            assert definition.label, definition.key


# -- The arithmetic ----------------------------------------------------------------------------


class TestTheFigures:
    def test_distributions_are_the_two_payments_together(self, context: CalculationContext) -> None:
        result = shareholder_distributions(
            context, share_repurchases=usd("40"), dividends_paid=usd("25")
        )
        assert result.value == Decimal("65")
        assert result.unit.symbol == "USD"

    def test_distributions_to_cash_flow_is_their_share_of_what_was_generated(
        self, context: CalculationContext
    ) -> None:
        result = distributions_to_operating_cash_flow(
            context,
            share_repurchases=usd("40"),
            dividends_paid=usd("25"),
            operating_cash_flow=usd("260"),
        )
        assert result.value == Decimal("0.25")
        assert result.unit.symbol == "pure"

    def test_returning_more_than_was_generated_reads_above_one(
        self, context: CalculationContext
    ) -> None:
        """The case the reader wants pointed at, and the one a guard must not hide."""
        result = distributions_to_operating_cash_flow(
            context,
            share_repurchases=usd("200"),
            dividends_paid=usd("100"),
            operating_cash_flow=usd("240"),
        )
        assert result.value == Decimal("1.25")

    @pytest.mark.parametrize("cash_flow", ["0", "-50"])
    def test_a_share_of_no_cash_is_refused_in_words(
        self, context: CalculationContext, cash_flow: str
    ) -> None:
        with pytest.raises(CalculationError, match="not meaningful"):
            distributions_to_operating_cash_flow(
                context,
                share_repurchases=usd("40"),
                dividends_paid=usd("25"),
                operating_cash_flow=usd(cash_flow),
            )

    @pytest.mark.parametrize(
        ("function", "opening", "closing", "expected"),
        [
            (dividends_paid_change, "20", "25", "5"),
            (share_repurchases_change, "44", "40", "-4"),
            (repayments_of_debt_change, "30", "30", "0"),
            (capital_expenditure_change, "72", "80", "8"),
        ],
    )
    def test_a_change_is_closing_less_opening_in_the_lines_own_unit(
        self, context: CalculationContext, function, opening: str, closing: str, expected: str
    ) -> None:
        result = function(context, opening=usd(opening), closing=usd(closing))
        assert result.value == Decimal(expected)
        assert result.unit.symbol == "USD"

    def test_two_currencies_never_add(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError):
            shareholder_distributions(
                context,
                share_repurchases=usd("40"),
                dividends_paid=money("25", "GBP", source=SOURCE),
            )

    def test_every_figure_is_recorded_with_its_formula(self, context: CalculationContext) -> None:
        """Traced, not merely computed: the ledger is what makes the figure citable."""
        shareholder_distributions(context, share_repurchases=usd("40"), dividends_paid=usd("25"))
        dividends_paid_change(context, opening=usd("20"), closing=usd("25"))

        names = [record.name for record in context.records]
        assert names == ["shareholder_distributions", "dividends_paid_change"]
        assert "prior" in context.records[-1].formula
        assert {input_.name for input_ in context.records[-1].inputs} == {"opening", "closing"}


class TestTheArithmeticHolds:
    money_values = st.decimals(
        min_value=Decimal("0"), max_value=Decimal("1e12"), places=2, allow_nan=False
    )

    @given(a=money_values, b=money_values)
    def test_distributions_never_fall_below_either_payment(self, a: Decimal, b: Decimal) -> None:
        context = CalculationContext(code_version="testsha")
        total = shareholder_distributions(
            context, share_repurchases=usd(str(a)), dividends_paid=usd(str(b))
        ).value
        assert total == a + b
        assert total >= max(a, b)

    @given(opening=money_values, closing=money_values)
    def test_a_change_reversed_is_its_negative(self, opening: Decimal, closing: Decimal) -> None:
        context = CalculationContext(code_version="testsha")
        forward = dividends_paid_change(
            context, opening=usd(str(opening)), closing=usd(str(closing))
        ).value
        backward = dividends_paid_change(
            context, opening=usd(str(closing)), closing=usd(str(opening))
        ).value
        assert forward == -backward
        assert forward == closing - opening


# -- The pass ----------------------------------------------------------------------------------


class TestThePass:
    def test_every_definition_gets_a_row_whatever_the_filing_holds(
        self, context: CalculationContext
    ) -> None:
        assessed = assess_cash_uses(context, assemble(context, {}))
        assert [figure.key for figure in assessed] == [d.key for d in CASH_USE_DEFINITIONS]
        assert all(not figure.present for figure in assessed)

    def test_the_figures_worked_out_by_hand(self, context: CalculationContext) -> None:
        result = figures(context, facts(THIS_YEAR), facts(LAST_YEAR))

        assert result["shareholder_distributions"].value == Decimal("65")
        assert result["distributions_to_operating_cash_flow"].value == Decimal("0.25")
        assert result["dividends_paid_change"].value == Decimal("5")
        assert result["share_repurchases_change"].value == Decimal("-4")
        assert result["repayments_of_debt_change"].value == Decimal("0")
        assert result["capital_expenditure_change"].value == Decimal("8")

    def test_a_missing_line_names_itself(self, context: CalculationContext) -> None:
        held = facts(THIS_YEAR)
        del held["dividends_paid"]

        result = figures(context, held, None)

        absent = result["shareholder_distributions"]
        assert not absent.present
        assert "dividends_paid" in absent.absent_because
        assert absent.missing == ("dividends_paid",)

    def test_without_a_prior_period_the_changes_say_so(self, context: CalculationContext) -> None:
        result = figures(context, facts(THIS_YEAR), None)

        assert result["shareholder_distributions"].present
        change = result["dividends_paid_change"]
        assert not change.present
        assert "only one period" in change.absent_because

    def test_a_line_missing_from_one_year_says_which(self, context: CalculationContext) -> None:
        last = facts(LAST_YEAR)
        del last["capital_expenditure"]

        result = figures(context, facts(THIS_YEAR), last)

        change = result["capital_expenditure_change"]
        assert not change.present
        assert "the prior period" in change.absent_because
        assert change.missing == ("capital_expenditure",)

    def test_a_refused_ratio_carries_the_guards_words(self, context: CalculationContext) -> None:
        result = figures(context, facts(THIS_YEAR, operating_cash_flow="-10"), None)

        refused = result["distributions_to_operating_cash_flow"]
        assert not refused.present
        assert "not meaningful" in refused.absent_because
        # The sum beside it is untouched: one refusal costs one figure.
        assert result["shareholder_distributions"].present

    def test_a_unit_mismatch_is_never_an_absent_figure(self, context: CalculationContext) -> None:
        held = facts(THIS_YEAR)
        held["dividends_paid"] = money("25", "GBP", source=SOURCE)

        with pytest.raises(UnitMismatchError):
            figures(context, held, None)
