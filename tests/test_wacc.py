"""The cost of capital: hand-worked answers, and the numbers it refuses to invent.

Every expected figure below was worked out on paper from the inputs stated beside it. A test
that compared `wacc()` against a second implementation of a weighted average would prove that
two functions written on the same afternoon agree, which is not the claim being made.

The refusals carry as much weight as the answers. A discount rate is the input a valuation is
most sensitive to and the one nobody can check by eye: 8% and 9% look equally reasonable in a
report and differ by an eighth of the answer. So the tests below spend most of their length on
what happens when an input is absent, unsourced, in the wrong currency, or quoted in per cent
when the arithmetic wanted a fraction.
"""

from __future__ import annotations

import inspect
from decimal import Decimal, localcontext

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aer.calc import wacc as wacc_module
from aer.calc.engine import CalculationContext
from aer.calc.ratios import nopat
from aer.calc.units import (
    CALC_CONTEXT,
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    UnitMismatchError,
    money,
)
from aer.calc.wacc import (
    ALL_EQUITY_NOTE,
    BOOK_WEIGHT_CAVEAT,
    MAX_BETA,
    MAX_QUOTED_PERCENT,
    CapitalStructure,
    EquityBasis,
    after_tax_cost_of_debt,
    average_debt,
    cost_of_capital,
    cost_of_debt,
    cost_of_equity,
    debt_weight,
    effective_tax_rate,
    equity_weight,
    rate_from_percent,
    wacc,
    wacc_all_equity,
)
from aer.sources.macro.series import MACRO_SERIES, series_for

FACT = SourceRef.fact("observation-1")
ASSUMPTION = SourceRef.assumption("assumption-1")


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def rate(value: str, *, source: SourceRef = ASSUMPTION) -> Quantity:
    return Quantity.of(Decimal(value), source=source)


def usd(value: str, *, source: SourceRef = FACT) -> Quantity:
    return money(value, "USD", source=source)


# -- The conversion nothing else is allowed to do ---------------------------------------------


class TestRateFromPercent:
    """A published yield is a percentage; CAPM wants a fraction. Nothing else converts."""

    def test_it_divides_a_published_yield_by_a_hundred(self, context):
        # DGS10 on a real day: 4.36 means 4.36% a year.
        converted = rate_from_percent(context, quoted=Quantity.of(Decimal("4.36"), source=FACT))

        assert converted.value == Decimal("0.0436")
        assert converted.unit.is_dimensionless

    def test_it_keeps_a_negative_yield_negative(self, context):
        converted = rate_from_percent(context, quoted=Quantity.of(Decimal("-0.35"), source=FACT))

        assert converted.value == Decimal("-0.0035")

    def test_it_refuses_a_figure_that_is_not_a_percentage(self, context):
        # A CPI index reading, or a basis-point quote, arriving where a rate belongs.
        with pytest.raises(CalculationError, match="not a rate quoted in per cent"):
            rate_from_percent(context, quoted=Quantity.of(Decimal("436"), source=FACT))

    def test_the_ceiling_is_a_hundred_per_cent(self, context):
        assert rate_from_percent(
            context, quoted=Quantity.of(MAX_QUOTED_PERCENT, source=FACT)
        ).value == Decimal(1)

    def test_it_refuses_a_dimensioned_quantity(self, context):
        with pytest.raises(UnitMismatchError, match="pure number"):
            rate_from_percent(context, quoted=usd("4.36"))

    def test_it_refuses_an_unsourced_figure(self, context):
        with pytest.raises(CalculationError, match="no source"):
            rate_from_percent(context, quoted=Quantity.of(Decimal("4.36")))


class TestThePercentageTrap:
    """The failure the conversion exists to prevent, demonstrated end to end."""

    def test_an_unconverted_yield_is_refused_rather_than_added(self, context):
        # 4.36 (per cent) plus 1.1 * 0.055 (a fraction) is 4.415 -- a cost of equity of 441.5%
        # that no unit check can catch, because both sides genuinely are pure numbers.
        with pytest.raises(CalculationError, match="rate_from_percent"):
            cost_of_equity(
                context,
                risk_free=Quantity.of(Decimal("4.36"), source=FACT),
                beta=rate("1.10"),
                equity_risk_premium=rate("0.055"),
            )

    def test_the_registry_says_which_series_need_converting(self):
        assert series_for("us_treasury_10y").quoted_in_percent
        assert not series_for("us_cpi").quoted_in_percent

    def test_every_series_quoted_as_a_rate_or_a_yield_carries_the_flag(self):
        # An iff, deliberately. A series whose label says "rate" and whose flag says otherwise
        # is the exact configuration that produces a 441.5% cost of equity.
        for series in MACRO_SERIES:
            reads_as_a_rate = "rate" in series.label.lower() or "yield" in series.label.lower()
            assert series.quoted_in_percent is reads_as_a_rate, series.key

    def test_a_converted_yield_reaches_capm_intact(self, context):
        risk_free = rate_from_percent(context, quoted=Quantity.of(Decimal("4.36"), source=FACT))
        equity_cost = cost_of_equity(
            context,
            risk_free=risk_free,
            beta=rate("1.10"),
            equity_risk_premium=rate("0.055"),
        )

        assert equity_cost.value == Decimal("0.1041")


# -- Golden cases ------------------------------------------------------------------------------


class TestGoldenCostOfEquity:
    """Ke = rf + beta * ERP. Worked by hand for each case."""

    @pytest.mark.parametrize(
        ("risk_free", "beta", "premium", "expected"),
        [
            # 0.0436 + 1.10 * 0.055 = 0.0436 + 0.0605
            ("0.0436", "1.10", "0.055", "0.1041"),
            # A defensive stock: 0.04 + 0.65 * 0.05 = 0.04 + 0.0325
            ("0.04", "0.65", "0.05", "0.0725"),
            # A negative beta is real -- gold miners have run one for years.
            # 0.04 + -0.30 * 0.05 = 0.04 - 0.015
            ("0.04", "-0.30", "0.05", "0.025"),
            # A negative risk-free rate is real too. -0.005 + 1.00 * 0.06
            ("-0.005", "1.00", "0.06", "0.055"),
        ],
    )
    def test_capm(self, context, risk_free, beta, premium, expected):
        result = cost_of_equity(
            context,
            risk_free=rate(risk_free, source=FACT),
            beta=rate(beta),
            equity_risk_premium=rate(premium),
        )

        assert result.value == Decimal(expected)


class TestGoldenCostOfDebt:
    """Interest expense over the average balance it was charged on."""

    def test_average_debt_and_the_rate_it_implies(self, context):
        # (9,000 + 11,000) / 2 = 10,000; 425 / 10,000 = 0.0425
        average = average_debt(context, opening=usd("9000"), closing=usd("11000"))
        assert average.value == Decimal("10000")

        result = cost_of_debt(context, interest_expense=usd("425"), debt=average)
        assert result.value == Decimal("0.0425")

    def test_the_tax_shield(self, context):
        # 0.0425 * (1 - 0.20) = 0.034
        result = after_tax_cost_of_debt(context, cost_of_debt=rate("0.0425"), tax_rate=rate("0.20"))

        assert result.value == Decimal("0.034")

    def test_a_closing_balance_alone_would_overstate_the_rate(self, context):
        # Not an assertion about the code so much as about why `average_debt` exists: a
        # company that borrowed during the year pays a full year's interest on a balance
        # that existed for part of it.
        on_average = cost_of_debt(
            context,
            interest_expense=usd("425"),
            debt=average_debt(context, opening=usd("5000"), closing=usd("15000")),
        )
        on_closing = cost_of_debt(context, interest_expense=usd("425"), debt=usd("15000"))

        assert on_average.value > on_closing.value


class TestGoldenEffectiveTaxRate:
    def test_the_rate_the_filer_actually_bore(self, context):
        # 1,200 / 6,000 = 0.20
        result = effective_tax_rate(
            context, income_tax_expense=usd("1200"), pre_tax_income=usd("6000")
        )

        assert result.value == Decimal("0.20")

    def test_it_agrees_with_the_rate_nopat_applies(self, context):
        """`ratios.nopat` applies an effective rate inline. The two must not diverge.

        Asserted rather than shared, because importing this module into `ratios` to reuse one
        division would make the ratio suite depend on the valuation stack for no benefit. What
        matters is that a reader checking one against the other finds them agreeing.
        """
        tax, pre_tax, operating = usd("1200"), usd("6000"), usd("7500")

        explicit = effective_tax_rate(context, income_tax_expense=tax, pre_tax_income=pre_tax)
        implied = nopat(
            context, operating_income=operating, income_tax_expense=tax, pre_tax_income=pre_tax
        )

        with localcontext(CALC_CONTEXT):
            assert implied.value == operating.value * (Decimal(1) - explicit.value)


class TestGoldenWacc:
    """The whole rate, worked by hand from each set of inputs."""

    def test_market_weights(self, context):
        # Ke      = 0.0436 + 1.10 * 0.055        = 0.1041
        # Kd(1-t) = 0.0425 * (1 - 0.20)          = 0.034
        # We, Wd  = 90,000 / 100,000, 10,000 / 100,000 = 0.9, 0.1
        # WACC    = 0.1041 * 0.9 + 0.034 * 0.1   = 0.09369 + 0.0034 = 0.09709
        result = cost_of_capital(
            context,
            risk_free=rate("0.0436", source=FACT),
            beta=rate("1.10"),
            equity_risk_premium=rate("0.055"),
            cost_of_debt_pre_tax=rate("0.0425"),
            tax_rate=rate("0.20"),
            structure=CapitalStructure(
                equity_value=usd("90000"),
                debt_value=usd("10000"),
                basis=EquityBasis.MARKET,
            ),
        )

        assert result.wacc.value == Decimal("0.09709")
        assert result.cost_of_equity.value == Decimal("0.1041")
        assert result.cost_of_debt_after_tax.value == Decimal("0.034")
        assert result.equity_weight.value == Decimal("0.9")
        assert result.debt_weight.value == Decimal("0.1")
        assert result.caveats == ()

    def test_book_weights_carry_the_substitution_as_a_caveat(self, context):
        # Ke      = 0.04 + 0.80 * 0.05         = 0.08
        # Kd(1-t) = 0.06 * (1 - 0.25)          = 0.045
        # We, Wd  = 40,000 / 100,000, 60,000 / 100,000 = 0.4, 0.6
        # WACC    = 0.08 * 0.4 + 0.045 * 0.6   = 0.032 + 0.027 = 0.059
        result = cost_of_capital(
            context,
            risk_free=rate("0.04", source=FACT),
            beta=rate("0.80"),
            equity_risk_premium=rate("0.05"),
            cost_of_debt_pre_tax=rate("0.06"),
            tax_rate=rate("0.25"),
            structure=CapitalStructure(
                equity_value=usd("40000"),
                debt_value=usd("60000"),
                basis=EquityBasis.BOOK,
            ),
        )

        assert result.wacc.value == Decimal("0.059")
        assert result.basis is EquityBasis.BOOK
        assert BOOK_WEIGHT_CAVEAT in result.caveats

    def test_the_basis_is_recorded_on_the_calculation_not_only_on_the_result(self, context):
        """`docs/phase-3-plan.md` task 26: the substitution stated on the calculation."""
        cost_of_capital(
            context,
            risk_free=rate("0.04", source=FACT),
            beta=rate("0.80"),
            equity_risk_premium=rate("0.05"),
            cost_of_debt_pre_tax=rate("0.06"),
            tax_rate=rate("0.25"),
            structure=CapitalStructure(
                equity_value=usd("40000"), debt_value=usd("60000"), basis=EquityBasis.BOOK
            ),
        )

        (record,) = context.named("wacc")
        assert record.parameters["equity_basis"] == EquityBasis.BOOK
        assert record.as_dict()["parameters"]["equity_basis"] == "book"

    def test_an_all_equity_company_has_no_cost_of_debt_rather_than_a_nil_one(self, context):
        # Ke = 0.045 + 1.20 * 0.05 = 0.105, and that is the whole answer.
        result = cost_of_capital(
            context,
            risk_free=rate("0.045", source=FACT),
            beta=rate("1.20"),
            equity_risk_premium=rate("0.05"),
            cost_of_debt_pre_tax=None,
            tax_rate=None,
            structure=CapitalStructure(
                equity_value=usd("50000"), debt_value=usd("0"), basis=EquityBasis.MARKET
            ),
        )

        assert result.wacc.value == Decimal("0.105")
        assert result.cost_of_debt_pre_tax is None
        assert result.cost_of_debt_after_tax is None
        assert result.debt_weight.value == 0
        assert ALL_EQUITY_NOTE in result.caveats
        assert context.named("wacc") == ()
        assert len(context.named("wacc_all_equity")) == 1


# -- Refusals ----------------------------------------------------------------------------------


class TestItRefusesRatherThanDefaulting:
    """The acceptance criterion: no default values anywhere in the module."""

    def test_no_traced_calculation_has_a_default_argument(self):
        traced_functions = [
            value
            for value in vars(wacc_module).values()
            if callable(value) and hasattr(value, "calculation_name")
        ]
        assert len(traced_functions) == 10

        for function in traced_functions:
            parameters = list(inspect.signature(function).parameters.values())
            # The context is machinery and is positional by design; everything after it is a
            # named input with no default.
            for parameter in parameters[1:]:
                assert parameter.default is inspect.Parameter.empty, (
                    f"{function.calculation_name}.{parameter.name} has a default"
                )
                assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
                    f"{function.calculation_name}.{parameter.name} can be passed positionally, "
                    "so beta and the equity risk premium could be swapped silently"
                )

    def test_the_orchestrator_has_no_defaults_either(self):
        parameters = list(inspect.signature(cost_of_capital).parameters.values())
        for parameter in parameters[1:]:
            assert parameter.default is inspect.Parameter.empty, parameter.name

    def test_omitting_an_input_is_an_error_at_the_call_site(self, context):
        with pytest.raises(TypeError, match="equity_risk_premium"):
            cost_of_equity(context, risk_free=rate("0.04"), beta=rate("1.0"))

    def test_a_bare_number_is_refused(self, context):
        with pytest.raises(CalculationError, match="bare Decimal"):
            cost_of_equity(
                context,
                risk_free=rate("0.04"),
                beta=Decimal("1.0"),
                equity_risk_premium=rate("0.05"),
            )

    def test_an_unsourced_quantity_is_refused(self, context):
        with pytest.raises(CalculationError, match="no source"):
            cost_of_equity(
                context,
                risk_free=rate("0.04"),
                beta=Quantity.of(Decimal("1.0")),
                equity_risk_premium=rate("0.05"),
            )

    def test_debt_without_a_cost_of_debt_refuses(self, context):
        with pytest.raises(CalculationError, match="cost_of_debt_pre_tax and tax_rate"):
            cost_of_capital(
                context,
                risk_free=rate("0.04", source=FACT),
                beta=rate("1.0"),
                equity_risk_premium=rate("0.05"),
                cost_of_debt_pre_tax=None,
                tax_rate=None,
                structure=CapitalStructure(
                    equity_value=usd("90000"), debt_value=usd("10000"), basis=EquityBasis.MARKET
                ),
            )

    def test_a_cost_of_debt_without_debt_refuses(self, context):
        with pytest.raises(CalculationError, match="carries no debt"):
            cost_of_capital(
                context,
                risk_free=rate("0.04", source=FACT),
                beta=rate("1.0"),
                equity_risk_premium=rate("0.05"),
                cost_of_debt_pre_tax=rate("0.06"),
                tax_rate=rate("0.25"),
                structure=CapitalStructure(
                    equity_value=usd("90000"), debt_value=usd("0"), basis=EquityBasis.MARKET
                ),
            )


class TestGuards:
    def test_a_negative_equity_risk_premium_is_refused(self, context):
        with pytest.raises(CalculationError, match="equity_risk_premium"):
            cost_of_equity(
                context,
                risk_free=rate("0.04", source=FACT),
                beta=rate("1.0"),
                equity_risk_premium=rate("-0.01"),
            )

    def test_an_implausible_beta_is_refused(self, context):
        with pytest.raises(CalculationError, match="Beta is"):
            cost_of_equity(
                context,
                risk_free=rate("0.04", source=FACT),
                beta=rate(str(MAX_BETA + 1)),
                equity_risk_premium=rate("0.05"),
            )

    def test_a_negative_beta_within_range_is_allowed(self, context):
        assert cost_of_equity(
            context,
            risk_free=rate("0.04", source=FACT),
            beta=rate("-1.5"),
            equity_risk_premium=rate("0.05"),
        ).value == Decimal("-0.035")

    def test_no_debt_has_no_cost_of_debt(self, context):
        with pytest.raises(CalculationError, match="no cost of debt to compute"):
            cost_of_debt(context, interest_expense=usd("425"), debt=usd("0"))

    def test_interest_and_debt_in_different_currencies_are_refused(self, context):
        with pytest.raises(UnitMismatchError, match="is not a rate"):
            cost_of_debt(
                context, interest_expense=usd("425"), debt=money("10000", "GBP", source=FACT)
            )

    def test_net_interest_income_is_not_a_negative_cost_of_debt(self, context):
        with pytest.raises(CalculationError, match="netted off"):
            cost_of_debt(context, interest_expense=usd("-100"), debt=usd("10000"))

    def test_a_negative_debt_balance_is_a_sign_error(self, context):
        with pytest.raises(CalculationError, match="sign error"):
            average_debt(context, opening=usd("-9000"), closing=usd("11000"))

    def test_a_loss_making_year_has_no_effective_rate(self, context):
        with pytest.raises(CalculationError, match="no effective tax rate"):
            effective_tax_rate(context, income_tax_expense=usd("100"), pre_tax_income=usd("-6000"))

    def test_a_tax_credit_is_refused_rather_than_used_as_a_rate(self, context):
        with pytest.raises(CalculationError, match="outside 0-100%"):
            effective_tax_rate(context, income_tax_expense=usd("-500"), pre_tax_income=usd("6000"))

    def test_a_charge_exceeding_the_profit_is_refused(self, context):
        with pytest.raises(CalculationError, match="outside 0-100%"):
            effective_tax_rate(context, income_tax_expense=usd("7000"), pre_tax_income=usd("6000"))

    def test_tax_and_profit_in_different_currencies_are_refused(self, context):
        # 1,200 dollars over 6,000 pounds divides to 0.20, which is in range, plausible, and
        # not a tax rate. The unit that gives it away -- USD/GBP -- is only checked here; by
        # the time it reaches the discount rate the figure looks like every other rate.
        with pytest.raises(UnitMismatchError, match="is not a rate"):
            effective_tax_rate(
                context,
                income_tax_expense=usd("1200"),
                pre_tax_income=money("6000", "GBP", source=FACT),
            )

    def test_a_rate_carrying_a_currency_pair_never_reaches_the_discount_rate(self, context):
        # Defence in depth for the same mistake, one layer down: even if a rate arrived from
        # somewhere with a currency-pair unit, nothing weights it.
        bogus = Quantity.of(Decimal("0.20"), "USD/GBP", source=FACT)

        with pytest.raises(UnitMismatchError, match="pure number"):
            after_tax_cost_of_debt(context, cost_of_debt=rate("0.05"), tax_rate=bogus)

    def test_negative_book_equity_is_refused_rather_than_weighted(self, context):
        with pytest.raises(CalculationError, match="equity value is"):
            equity_weight(context, equity_value=usd("-4000"), debt_value=usd("60000"))

    def test_net_debt_is_not_the_weighting_figure(self, context):
        with pytest.raises(CalculationError, match="gross debt"):
            debt_weight(context, equity_value=usd("90000"), debt_value=usd("-2000"))

    def test_weights_supplied_ready_made_are_refused(self, context):
        # Passing weights directly would leave the basis they were measured on out of the
        # record entirely, which is the thing this task exists to prevent.
        with pytest.raises(UnitMismatchError, match="not a currency amount"):
            equity_weight(context, equity_value=rate("0.9"), debt_value=rate("0.1"))

    def test_equity_and_debt_in_different_currencies_are_refused(self, context):
        with pytest.raises(UnitMismatchError, match="Cannot add"):
            equity_weight(
                context, equity_value=usd("90000"), debt_value=money("10000", "GBP", source=FACT)
            )

    def test_weights_from_different_totals_are_refused(self, context):
        with pytest.raises(CalculationError, match="sum to"):
            wacc(
                context,
                cost_of_equity=rate("0.10"),
                after_tax_cost_of_debt=rate("0.03"),
                equity_weight=rate("0.9"),
                debt_weight=rate("0.2"),
                equity_basis=EquityBasis.MARKET,
            )

    def test_a_free_text_basis_is_refused(self, context):
        with pytest.raises(CalculationError, match="not an EquityBasis"):
            wacc(
                context,
                cost_of_equity=rate("0.10"),
                after_tax_cost_of_debt=rate("0.03"),
                equity_weight=rate("0.9"),
                debt_weight=rate("0.1"),
                equity_basis="market",
            )

    def test_the_all_equity_form_refuses_a_levered_structure(self, context):
        with pytest.raises(CalculationError, match="has a debt side"):
            wacc_all_equity(
                context,
                cost_of_equity=rate("0.10"),
                equity_weight=rate("0.9"),
                equity_basis=EquityBasis.MARKET,
            )


# -- Provenance ---------------------------------------------------------------------------------


class TestProvenance:
    def test_every_step_is_recorded(self, context):
        cost_of_capital(
            context,
            risk_free=rate("0.0436", source=FACT),
            beta=rate("1.10"),
            equity_risk_premium=rate("0.055"),
            cost_of_debt_pre_tax=rate("0.0425"),
            tax_rate=rate("0.20"),
            structure=CapitalStructure(
                equity_value=usd("90000"), debt_value=usd("10000"), basis=EquityBasis.MARKET
            ),
        )

        names = [record.name for record in context.records]
        assert names == [
            "equity_weight",
            "debt_weight",
            "cost_of_equity",
            "after_tax_cost_of_debt",
            "wacc",
        ]

    def test_the_wacc_cites_its_components_rather_than_bare_numbers(self, context):
        cost_of_capital(
            context,
            risk_free=rate("0.0436", source=FACT),
            beta=rate("1.10"),
            equity_risk_premium=rate("0.055"),
            cost_of_debt_pre_tax=rate("0.0425"),
            tax_rate=rate("0.20"),
            structure=CapitalStructure(
                equity_value=usd("90000"), debt_value=usd("10000"), basis=EquityBasis.MARKET
            ),
        )

        (record,) = context.named("wacc")
        assert {source.kind for source in record.input_sources} == {SourceKind.CALCULATION}
        for source in record.input_sources:
            assert context.find(source.identifier) is not None

    def test_a_computed_cost_of_debt_and_an_override_are_told_apart_by_source(self, context):
        """The plan's "documented override", without a flag to forge."""
        computed = cost_of_debt(context, interest_expense=usd("425"), debt=usd("10000"))
        override = rate("0.0525", source=ASSUMPTION)

        from_filing = after_tax_cost_of_debt(context, cost_of_debt=computed, tax_rate=rate("0.20"))
        from_operator = after_tax_cost_of_debt(
            context, cost_of_debt=override, tax_rate=rate("0.20")
        )

        first, second = context.named("after_tax_cost_of_debt")
        assert first.inputs[0].source_kind is SourceKind.CALCULATION
        assert second.inputs[0].source_kind is SourceKind.ASSUMPTION
        assert from_filing.value < from_operator.value

    def test_the_risk_free_rate_keeps_its_vintage_label(self, context):
        vintage = SourceRef.fact("obs-9", label="us_treasury_10y@2024-06-27 (vintage 2024-06-28)")
        result = cost_of_equity(
            context,
            risk_free=rate_from_percent(
                context, quoted=Quantity.of(Decimal("4.36"), source=vintage)
            ),
            beta=rate("1.10"),
            equity_risk_premium=rate("0.055"),
        )

        (conversion,) = context.named("rate_from_percent")
        assert conversion.inputs[0].source_label == vintage.label
        assert result.source is not None


# -- Properties ----------------------------------------------------------------------------------
#
# The invariants `docs/phase-3-plan.md` names for this task. Bounded to inputs the module
# accepts: hypothesis is being asked to find counterexamples inside the domain, not to
# rediscover the guards, which the refusal tests above cover by construction.

risk_free_rates = st.decimals(min_value=Decimal("-0.02"), max_value=Decimal("0.15"), places=4)
betas = st.decimals(min_value=Decimal("-2"), max_value=Decimal("3"), places=3)
premiums = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.12"), places=4)
debt_rates = st.decimals(min_value=Decimal("0.001"), max_value=Decimal("0.20"), places=4)
tax_rates = st.decimals(min_value=Decimal(0), max_value=Decimal("0.50"), places=3)
equity_values = st.decimals(min_value=Decimal(1), max_value=Decimal("1e9"), places=2)
debt_values = st.decimals(min_value=Decimal(0), max_value=Decimal("1e9"), places=2)

# Comfortably wider than the 34-digit context's error and far narrower than any real
# difference between two rates.
EPSILON = Decimal("1e-25")


def blended(
    context, *, risk_free, beta, premium, debt_rate, tax, equity_value, debt_value
) -> Quantity:
    return wacc(
        context,
        cost_of_equity=cost_of_equity(
            context,
            risk_free=rate(str(risk_free), source=FACT),
            beta=rate(str(beta)),
            equity_risk_premium=rate(str(premium)),
        ),
        after_tax_cost_of_debt=after_tax_cost_of_debt(
            context, cost_of_debt=rate(str(debt_rate)), tax_rate=rate(str(tax))
        ),
        equity_weight=equity_weight(
            context, equity_value=usd(str(equity_value)), debt_value=usd(str(debt_value))
        ),
        debt_weight=debt_weight(
            context, equity_value=usd(str(equity_value)), debt_value=usd(str(debt_value))
        ),
        equity_basis=EquityBasis.MARKET,
    )


class TestProperties:
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        risk_free=risk_free_rates,
        beta=betas,
        increase=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1"), places=3),
        premium=premiums,
        debt_rate=debt_rates,
        tax=tax_rates,
        equity_value=equity_values,
        debt_value=debt_values,
    )
    def test_wacc_increases_with_beta(
        self, context, risk_free, beta, increase, premium, debt_rate, tax, equity_value, debt_value
    ):
        common = {
            "risk_free": risk_free,
            "premium": premium,
            "debt_rate": debt_rate,
            "tax": tax,
            "equity_value": equity_value,
            "debt_value": debt_value,
        }
        lower = blended(context, beta=beta, **common)
        higher = blended(context, beta=beta + increase, **common)

        assert higher.value > lower.value

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        risk_free=risk_free_rates,
        increase=st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.05"), places=4),
        beta=betas,
        premium=premiums,
        debt_rate=debt_rates,
        tax=tax_rates,
        equity_value=equity_values,
        debt_value=debt_values,
    )
    def test_wacc_increases_with_the_risk_free_rate(
        self, context, risk_free, increase, beta, premium, debt_rate, tax, equity_value, debt_value
    ):
        common = {
            "beta": beta,
            "premium": premium,
            "debt_rate": debt_rate,
            "tax": tax,
            "equity_value": equity_value,
            "debt_value": debt_value,
        }
        lower = blended(context, risk_free=risk_free, **common)
        higher = blended(context, risk_free=risk_free + increase, **common)

        assert higher.value > lower.value

    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        risk_free=risk_free_rates,
        beta=betas,
        premium=premiums,
        debt_rate=debt_rates,
        tax=tax_rates,
        equity_value=equity_values,
        debt_value=debt_values,
    )
    def test_wacc_lies_between_the_two_costs_it_averages(
        self, context, risk_free, beta, premium, debt_rate, tax, equity_value, debt_value
    ):
        equity_cost = cost_of_equity(
            context,
            risk_free=rate(str(risk_free), source=FACT),
            beta=rate(str(beta)),
            equity_risk_premium=rate(str(premium)),
        )
        debt_cost = after_tax_cost_of_debt(
            context, cost_of_debt=rate(str(debt_rate)), tax_rate=rate(str(tax))
        )
        result = wacc(
            context,
            cost_of_equity=equity_cost,
            after_tax_cost_of_debt=debt_cost,
            equity_weight=equity_weight(
                context, equity_value=usd(str(equity_value)), debt_value=usd(str(debt_value))
            ),
            debt_weight=debt_weight(
                context, equity_value=usd(str(equity_value)), debt_value=usd(str(debt_value))
            ),
            equity_basis=EquityBasis.MARKET,
        )

        low = min(equity_cost.value, debt_cost.value)
        high = max(equity_cost.value, debt_cost.value)
        assert low - EPSILON <= result.value <= high + EPSILON

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        equity_value=equity_values,
        debt_value=debt_values,
        scale=st.decimals(min_value=Decimal("0.01"), max_value=Decimal(1000), places=2),
    )
    def test_the_weights_depend_only_on_the_ratio_of_the_two_values(
        self, context, equity_value, debt_value, scale
    ):
        """Scaling both sides changes nothing. A WACC in thousands equals one in units."""
        unscaled = equity_weight(
            context, equity_value=usd(str(equity_value)), debt_value=usd(str(debt_value))
        )
        with localcontext(CALC_CONTEXT):
            scaled = equity_weight(
                context,
                equity_value=usd(str(equity_value * scale)),
                debt_value=usd(str(debt_value * scale)),
            )

        assert abs(scaled.value - unscaled.value) < EPSILON

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(equity_value=equity_values, debt_value=debt_values)
    def test_the_weights_sum_to_one(self, context, equity_value, debt_value):
        equity = equity_weight(
            context, equity_value=usd(str(equity_value)), debt_value=usd(str(debt_value))
        )
        debt = debt_weight(
            context, equity_value=usd(str(equity_value)), debt_value=usd(str(debt_value))
        )

        assert abs(equity.value + debt.value - Decimal(1)) < EPSILON

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(debt_rate=debt_rates, tax=tax_rates)
    def test_the_tax_shield_never_raises_the_cost_of_debt(self, context, debt_rate, tax):
        pre_tax = rate(str(debt_rate))
        after_tax = after_tax_cost_of_debt(context, cost_of_debt=pre_tax, tax_rate=rate(str(tax)))

        assert after_tax.value <= pre_tax.value
