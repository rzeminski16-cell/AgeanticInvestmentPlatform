"""The ratio suite: known answers, and what happens when there is no answer.

Every expected figure below was worked out by hand from the fixture at the top, which is a
simplified but internally consistent set of statements. A test that checked `gross_margin`
against a second implementation of gross margin would prove the two agree, which is worth
very little when the same person wrote both on the same afternoon.

The absence cases carry as much weight as the answers. Each one is an input for which no
meaningful ratio exists, and the suite has to say so rather than return zero — a zero here
flows into a comparison table and reads as a company with no margin.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aer.calc.engine import CalculationContext
from aer.calc.ratios import (
    DAYS_IN_YEAR,
    RATIO_DEFINITIONS,
    RatioFamily,
    compute_ratios,
    days_outstanding,
    ebitda,
    interest_cover,
    invested_capital,
    net_debt,
    nopat,
    return_on_equity,
    return_on_invested_capital,
    working_capital,
)
from aer.calc.statements import assemble
from aer.calc.units import (
    CALC_CONTEXT,
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
    money,
)
from aer.core.concepts import CANONICAL_CONCEPTS

SOURCE = SourceRef.financial_fact("fact-1")


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def usd(value: str) -> Quantity:
    return money(value, "USD", source=SOURCE)


# A complete, internally consistent set of statements. Chosen so that every ratio has a
# round answer a reader can check in their head:
#
#   revenue 1000, cost of revenue 600  -> gross profit 400, gross margin 40%
#   operating income 200               -> operating margin 20%
#   D&A 50                             -> EBITDA 250, EBITDA margin 25%
#   pre-tax 160, tax 40, net income 120 -> net margin 12%, effective rate 25%
#   equity 600, assets 2000            -> ROE 20%, ROA 6%
#   debt 500, cash 100                 -> net debt 400, invested capital 1000
#   NOPAT 200 * 0.75 = 150             -> ROIC 15%
#   current assets 800, current liabilities 400 -> current ratio 2
#   cash 100 + STI 50 + AR 250 = 400   -> quick ratio 1
#   net debt 400 / EBITDA 250          -> 1.6x
#   operating income 200 / interest 25 -> 8x cover
#   revenue 1000 / assets 2000         -> asset turnover 0.5
#   AR 250 / revenue 1000 * 365        -> DSO 91.25 days
#   inventory 150 / COGS 600 * 365     -> DIO 91.25 days
#   AP 200 / COGS 600 * 365            -> DPO 121.666... days
COMPLETE = {
    "revenue": "1000",
    "cost_of_revenue": "600",
    "gross_profit": "400",
    "operating_income": "200",
    "depreciation_and_amortisation": "50",
    "interest_expense": "25",
    "pre_tax_income": "160",
    "income_tax_expense": "40",
    "net_income": "120",
    "assets": "2000",
    "current_assets": "800",
    "current_liabilities": "400",
    "equity": "600",
    "cash_and_equivalents": "100",
    "short_term_investments": "50",
    "accounts_receivable": "250",
    "inventory": "150",
    "accounts_payable": "200",
    "total_debt": "500",
    "operating_cash_flow": "180",
}


def facts(**overrides: str) -> dict[str, Quantity]:
    merged = {**COMPLETE, **overrides}
    return {concept: usd(value) for concept, value in merged.items() if value is not None}


def without(*concepts: str) -> dict[str, Quantity]:
    return {c: q for c, q in facts().items() if c not in concepts}


def suite(context: CalculationContext, values: dict[str, Quantity]) -> dict[str, object]:
    statements = assemble(context, values)
    return {result.key: result for result in compute_ratios(context, statements)}


class TestTheTableItself:
    def test_every_ratio_needs_only_canonical_concepts(self):
        for definition in RATIO_DEFINITIONS:
            for concept in definition.needs:
                assert concept in CANONICAL_CONCEPTS, f"{definition.key}: {concept}"

    def test_every_key_is_unique(self):
        keys = [definition.key for definition in RATIO_DEFINITIONS]
        assert len(keys) == len(set(keys))

    def test_every_family_is_represented(self):
        families = {definition.family for definition in RATIO_DEFINITIONS}
        assert families == set(RatioFamily)

    def test_every_ratio_explains_itself(self):
        """A number labelled "ROIC" with no definition beside it is not comparable."""
        for definition in RATIO_DEFINITIONS:
            assert definition.note, definition.key

    def test_the_suite_returns_one_row_per_definition_always(self, context):
        """Even for a filing that supports nothing. Four ratios and four ratios plus
        thirteen unanswered questions must not look the same."""
        results = compute_ratios(context, assemble(context, {}))
        assert len(results) == len(RATIO_DEFINITIONS)
        assert all(not result.present for result in results)


class TestKnownAnswers:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("gross_margin", "0.4"),
            ("operating_margin", "0.2"),
            ("net_margin", "0.12"),
            ("ebitda_margin", "0.25"),
            ("return_on_equity", "0.2"),
            ("return_on_assets", "0.06"),
            ("return_on_invested_capital", "0.15"),
            ("current_ratio", "2"),
            ("quick_ratio", "1"),
            ("debt_to_equity", "0.8333333333333333333333333333333333"),
            ("net_debt_to_ebitda", "1.6"),
            ("interest_cover", "8"),
            ("asset_turnover", "0.5"),
        ],
    )
    def test_it_computes_the_figure_worked_out_by_hand(self, context, key, expected):
        results = suite(context, facts())
        assert results[key].present, results[key].absent_because
        assert results[key].value == Decimal(expected)

    def test_days_sales_outstanding_is_in_days(self, context):
        result = suite(context, facts())["days_sales_outstanding"]
        assert result.value == Decimal("91.25")
        assert result.quantity.unit == Unit.base("day")

    def test_the_cash_conversion_cycle_sums_its_three_parts(self, context):
        results = suite(context, facts())
        # Summed in the calculation context, not Python's default 28 digits. Comparing a
        # 34-digit figure against a 28-digit sum of the same numbers fails on the rounding
        # rather than on the arithmetic.
        with localcontext(CALC_CONTEXT):
            expected = (
                results["days_sales_outstanding"].value
                + results["days_inventory_outstanding"].value
                - results["days_payable_outstanding"].value
            )
        assert results["cash_conversion_cycle"].value == expected

    def test_a_margin_is_dimensionless(self, context):
        """A margin is a pure number. Carrying USD/USD would let it be added to a revenue."""
        assert suite(context, facts())["gross_margin"].quantity.unit.is_dimensionless

    def test_a_negative_cash_conversion_cycle_is_a_position_not_an_error(self, context):
        """A retailer selling for cash and paying suppliers in ninety days is funded by them."""
        values = facts(accounts_receivable="0", inventory="0", accounts_payable="200")
        result = suite(context, values)["cash_conversion_cycle"]
        assert result.present
        assert result.value < 0


class TestAnAbsentInputGivesAnAbsentRatio:
    def test_a_missing_concept_produces_no_value(self, context):
        result = suite(context, without("inventory"))["days_inventory_outstanding"]
        assert not result.present
        assert result.value is None

    def test_it_names_the_concepts_it_needed(self, context):
        result = suite(context, without("inventory"))["days_inventory_outstanding"]
        assert result.missing == ("inventory",)
        assert "inventory" in result.absent_because

    def test_a_ratio_needing_four_lines_names_only_the_missing_ones(self, context):
        result = suite(context, without("short_term_investments"))["quick_ratio"]
        assert result.missing == ("short_term_investments",)

    def test_other_ratios_are_unaffected(self, context):
        """One hole in a filing must not take the whole suite down with it."""
        results = suite(context, without("inventory"))
        assert results["gross_margin"].present
        assert results["current_ratio"].present

    def test_nothing_silently_becomes_zero(self, context):
        """The failure this whole design exists to prevent."""
        for result in compute_ratios(context, assemble(context, {})):
            assert result.value is None


class TestAnUndefinedRatioIsAbsentWithTheGuardsReason:
    def test_return_on_negative_equity_is_undefined(self, context):
        result = suite(context, facts(equity="-100"))["return_on_equity"]
        assert not result.present
        assert "negative book equity" in result.absent_because

    def test_return_on_zero_invested_capital_is_undefined_rather_than_infinite(self, context):
        """Named explicitly in the plan. A net-cash company can land exactly here."""
        values = facts(total_debt="500", equity="600", cash_and_equivalents="1100")
        result = suite(context, values)["return_on_invested_capital"]
        assert not result.present
        assert "undefined rather than infinite" in result.absent_because

    def test_interest_cover_with_no_interest_charge_is_undefined(self, context):
        result = suite(context, facts(interest_expense="0"))["interest_cover"]
        assert not result.present
        assert "no interest cost" in result.absent_because

    def test_a_leverage_multiple_on_negative_ebitda_is_undefined(self, context):
        values = facts(operating_income="-300", depreciation_and_amortisation="50")
        result = suite(context, values)["net_debt_to_ebitda"]
        assert not result.present
        assert "cannot service" in result.absent_because

    @pytest.mark.parametrize("revenue", ["0", "-1000"])
    def test_a_margin_on_a_meaningless_base_is_undefined(self, context, revenue):
        """The guard's own words, not the division trap's.

        At exactly zero the two produce the same outcome — an absent ratio — because the
        trapped division refuses as well. They do not produce the same *message*, and the
        operator reads the message. Asserting on wording only the guard uses is what keeps
        the guard from being deleted as redundant.
        """
        result = suite(context, facts(revenue=revenue))["gross_margin"]
        assert not result.present
        assert "not a percentage of anything" in result.absent_because

    def test_days_of_a_flow_that_did_not_happen_is_undefined(self, context):
        result = suite(context, facts(cost_of_revenue="0"))["days_inventory_outstanding"]
        assert not result.present
        assert "undefined rather than large" in result.absent_because

    def test_a_loss_still_produces_a_margin(self, context):
        """Negative is an answer. Only a meaningless base is a refusal."""
        result = suite(context, facts(net_income="-200"))["net_margin"]
        assert result.present
        assert result.value == Decimal("-0.2")

    def test_an_undefined_ratio_records_no_calculation(self, context):
        """A refused call must not leave a half-finished entry in the ledger."""
        before = len(context.records)
        suite(context, {"revenue": usd("1000"), "equity": usd("-100"), "net_income": usd("50")})
        recorded = [r.name for r in context.records[before:]]
        assert "return_on_equity" not in recorded


class TestAMappingErrorIsNeverSwallowed:
    def test_two_currencies_in_one_ratio_raise(self, context):
        """Invariant 5. This is a mapping error, not a ratio that happens to be undefined."""
        values = facts()
        values["equity"] = money("600", "GBP", source=SOURCE)

        with pytest.raises(UnitMismatchError):
            compute_ratios(context, assemble(context, values))

    def test_it_raises_rather_than_reporting_an_absent_ratio(self, context):
        """The failure mode worth naming: a currency error hidden inside a tidy report."""
        values = {
            "revenue": usd("1000"),
            "gross_profit": money("400", "GBP", source=SOURCE),
        }
        with pytest.raises(UnitMismatchError):
            compute_ratios(context, assemble(context, values))


class TestEverythingResolvesToFacts:
    def test_every_computed_ratio_is_a_recorded_calculation(self, context):
        results = compute_ratios(context, assemble(context, facts()))
        recorded = {record.name for record in context.records}

        for result in results:
            if result.present:
                assert result.quantity.source is not None
                assert result.quantity.source.kind == "calculation"

        assert "gross_margin" in recorded
        assert "return_on_invested_capital" in recorded

    def test_each_ratio_records_itself_by_name(self, context):
        """Seventeen entries all called "ratio" would be a ledger answering nothing."""
        compute_ratios(context, assemble(context, facts()))
        recorded = {record.name for record in context.records}

        for key in ("gross_margin", "current_ratio", "interest_cover", "asset_turnover"):
            assert key in recorded

    def test_a_balance_sheet_ratio_says_it_used_period_end_figures(self, context):
        compute_ratios(context, assemble(context, facts()))
        record = next(r for r in context.records if r.name == "return_on_equity")
        assert any("period-end" in assumption for assumption in record.assumptions)

    def test_roic_traces_through_nopat_and_invested_capital(self, context):
        compute_ratios(context, assemble(context, facts()))
        recorded = [record.name for record in context.records]
        assert "nopat" in recorded
        assert "invested_capital" in recorded

    def test_a_ratio_over_a_derived_line_still_traces_to_the_facts(self, context):
        """Gross profit the filer did not state, used by a ratio that did not notice."""
        values = {c: q for c, q in facts().items() if c != "gross_profit"}
        results = suite(context, values)

        assert results["gross_margin"].present
        assert results["gross_margin"].value == Decimal("0.4")
        assert "subtotal_difference" in [record.name for record in context.records]


class TestWorkingCapital:
    """The one aggregate the suite never reaches, and it feeds the valuation.

    `working_capital` is not in `RATIO_DEFINITIONS`, so `compute_ratios` never computes it
    and the coverage the suite's tests provide says nothing about it. Nor did anything else:
    a mutation turning the subtraction into an addition passed every one of the fifty-nine
    test files that can reach `aer.calc.ratios`.

    Its only caller is `aer.services.valuation_run`, where it becomes the DCF's
    `opening_working_capital`. So a sign error here would not surface as a wrong ratio on the
    ratios page — it would surface as a wrong free cash flow in year one, and from there as a
    wrong valuation, with a full provenance trail behind it.
    """

    def test_it_is_current_assets_less_current_liabilities(self, context):
        result = working_capital(context, current_assets=usd("300"), current_liabilities=usd("120"))
        assert result.value == Decimal(180)

    def test_it_is_negative_when_the_liabilities_are_the_larger(self, context):
        """Allowed, and interesting rather than wrong: a retailer funded by its suppliers.

        Refusing it would refuse a real and common capital structure.
        """
        result = working_capital(context, current_assets=usd("100"), current_liabilities=usd("150"))
        assert result.value == Decimal(-50)

    def test_it_keeps_the_currency(self, context):
        result = working_capital(context, current_assets=usd("300"), current_liabilities=usd("120"))
        assert result.unit == Unit.currency("USD")

    def test_two_currencies_are_refused_rather_than_netted(self, context):
        gbp = money("120", "GBP", source=SOURCE)
        with pytest.raises(UnitMismatchError):
            working_capital(context, current_assets=usd("300"), current_liabilities=gbp)

    def test_it_is_recorded_as_a_calculation_with_both_inputs(self, context):
        """The reason `valuation_run` calls this rather than subtracting two quantities:
        bare arithmetic produces a value with no source, and the DCF refuses one."""
        working_capital(context, current_assets=usd("300"), current_liabilities=usd("120"))

        [record] = [row for row in context.records if row.name == "working_capital"]
        assert len(record.inputs) == 2


class TestTheAggregates:
    def test_ebitda_adds_the_depreciation_charge_back(self, context):
        result = ebitda(context, operating_income=usd("200"), depreciation=usd("50"))
        assert result.value == Decimal(250)

    def test_net_debt_is_borrowings_less_cash(self, context):
        result = net_debt(context, total_debt=usd("500"), cash=usd("100"))
        assert result.value == Decimal(400)

    def test_net_debt_is_negative_for_a_net_cash_company(self, context):
        result = net_debt(context, total_debt=usd("100"), cash=usd("500"))
        assert result.value == Decimal(-400)

    def test_invested_capital_is_debt_plus_equity_less_cash(self, context):
        result = invested_capital(
            context, total_debt=usd("500"), equity=usd("600"), cash=usd("100")
        )
        assert result.value == Decimal(1000)

    def test_nopat_applies_the_effective_rate_to_operating_profit(self, context):
        result = nopat(
            context,
            operating_income=usd("200"),
            income_tax_expense=usd("40"),
            pre_tax_income=usd("160"),
        )
        assert result.value == Decimal(150)

    def test_nopat_refuses_a_loss_making_year(self, context):
        """A loss year's tax charge says nothing about the rate a profit would pay."""
        with pytest.raises(CalculationError, match="no meaningful effective"):
            nopat(
                context,
                operating_income=usd("200"),
                income_tax_expense=usd("40"),
                pre_tax_income=usd("-160"),
            )

    def test_the_year_is_365_days(self):
        assert str(DAYS_IN_YEAR) == "365"

    def test_days_outstanding_does_not_lose_the_last_digits_to_rounding(self, context):
        """A balance of one year's flow is 365 days, not 364.999999999999999999999999999999.

        `balance / flow * year` rounds the quotient and multiplies the error back up. The
        symptom is a days figure that is never quite round and a cash conversion cycle that
        does not quite reconcile, neither of which looks like a bug.
        """
        assert days_outstanding(context, balance=usd("100"), flow=usd("365")).value == Decimal(100)
        assert days_outstanding(context, balance=usd("365"), flow=usd("365")).value == Decimal(365)


class TestProperties:
    """The invariants the plan names, checked over generated inputs."""

    money_values = st.decimals(
        min_value=Decimal("1"), max_value=Decimal("1e9"), places=2, allow_nan=False
    )
    scales = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000"), places=2)

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(income=money_values, equity=money_values, scale=scales)
    def test_a_ratio_is_scale_invariant(self, context, income, equity, scale):
        """Reporting in thousands rather than units must not change a return on equity."""
        plain = return_on_equity(context, net_income=usd(str(income)), equity=usd(str(equity)))
        scaled = return_on_equity(
            context,
            net_income=usd(str(income * scale)),
            equity=usd(str(equity * scale)),
        )
        assert plain.value == scaled.value

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(capital=st.decimals(min_value=Decimal("-1e6"), max_value=Decimal(0), places=2))
    def test_roic_is_undefined_rather_than_infinite_at_or_below_zero_capital(
        self, context, capital
    ):
        with pytest.raises(CalculationError):
            return_on_invested_capital(context, nopat_value=usd("150"), capital=usd(str(capital)))

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(operating=money_values, interest=money_values)
    def test_interest_cover_falls_as_the_interest_bill_rises(self, context, operating, interest):
        low = interest_cover(
            context, operating_income=usd(str(operating)), interest_expense=usd(str(interest))
        )
        high = interest_cover(
            context,
            operating_income=usd(str(operating)),
            interest_expense=usd(str(interest * 2)),
        )
        assert high.value <= low.value

    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(balance=money_values, flow=money_values)
    def test_days_outstanding_is_always_in_days(self, context, balance, flow):
        result = days_outstanding(context, balance=usd(str(balance)), flow=usd(str(flow)))
        assert result.unit == Unit.base("day")
