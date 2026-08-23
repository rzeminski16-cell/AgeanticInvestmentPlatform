"""Residual income: a worked example, the identity that proves it, and what it refuses.

The forecast below was worked out on paper before it was run — a bank opening on 1,000 of
book value, earning 12% on it against a 10% cost of equity, paying out 40% — and every
per-year figure is an exact decimal asserted exactly.

The centre of this file is :class:`TestTheDividendIdentity`. Residual income is the dividend
discount rearranged, and over a finite horizon the rearrangement is an *equality*:

    BV_0 + Σ PV(RI_t)  ==  Σ PV(dividend_t) + PV(BV_N)

The test computes the right-hand side from dividends and a closing book value, using nothing
from the module under test, and asserts the two agree. A model that is the identity it claims
to be passes; one with a sign error, an off-by-one in the discounting, or a book value that
rolls forward wrongly cannot.

The worked example is also a demonstration of the choice it forces. The same bank is worth
10.53 a share if competition removes its excess return at the end of the forecast and 12.73
if it earns that return for ever — a 21% difference decided by a parameter nobody can
observe. That is why :class:`~aer.calc.residual_income.TerminalTreatment` has no default.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
from itertools import pairwise

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from aer.calc.engine import CalculationContext
from aer.calc.residual_income import (
    CLEAN_SURPLUS_CAVEAT,
    MAX_FORECAST_YEARS,
    DriverPath,
    ResidualIncomeInputs,
    TerminalTreatment,
    book_value_roll_forward,
    equity_charge,
    equity_discount_factor,
    equity_value,
    explicit_residual_value,
    net_income_from_roe,
    perpetual_residual_value,
    residual_income,
    residual_income_value,
    value_per_share,
)
from aer.calc.units import (
    CALC_CONTEXT,
    CalculationError,
    Quantity,
    SourceRef,
    UnitMismatchError,
    money,
)
from aer.core.sectors import (
    ModelNotPermittedError,
    ValuationModel,
    mandate_for,
    profile_for,
    unclassified_mandate,
)
from aer.eval.replay import registry

ASSUMPTION = SourceRef.assumption("assumption-1")
FACT = SourceRef.financial_fact("fact-1")

BANKS = profile_for("banks")
assert BANKS is not None

# The permission the arithmetic requires. A bank's profile permits residual income and blocks
# free cash flow to the firm, so this mandate exists and a DCF mandate for the same company
# cannot be constructed at all — which is the point of `TestTheMandateIsTheBlock` below.
MANDATE = mandate_for(
    ValuationModel.RESIDUAL_INCOME,
    subject="BANKCO",
    profile=BANKS,
    confirmed_by="analyst@example.invalid",
)


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def rate(value: str) -> Quantity:
    return Quantity.of(Decimal(value), source=ASSUMPTION)


def usd(value: str) -> Quantity:
    return money(value, "USD", source=FACT)


def shares(value: str) -> Quantity:
    return Quantity.of(Decimal(value), "shares", source=FACT)


def flat(name: str, value: str, *, years: int = 3) -> DriverPath:
    return DriverPath.flat(name, rate(value), years=years)


def base_inputs(**overrides) -> ResidualIncomeInputs:
    """The worked example. Every figure round, every intermediate exact."""
    inputs = ResidualIncomeInputs(
        opening_book_value=usd("1000"),
        return_on_equity=flat("return_on_equity", "0.12"),
        payout_ratio=flat("payout_ratio", "0.40"),
        cost_of_equity=rate("0.10"),
        terminal_treatment=TerminalTreatment.FADE_TO_NOTHING,
        terminal_growth=rate("0.02"),
        shares_outstanding=shares("100"),
    )
    return replace(inputs, **overrides) if overrides else inputs


# The tolerance `docs/archive/phase-3-plan.md` asks of a discounted aggregate: tight enough that a
# wrong discounting convention fails, loose enough for a figure quoted to eight significant
# figures.
TOLERANCE = Decimal("0.0001")


def close(actual: Quantity, expected: str) -> bool:
    wanted = Decimal(expected)
    return abs(actual.value - wanted) <= abs(wanted) * TOLERANCE


# -- The worked forecast ---------------------------------------------------------------------


class TestTheForecast:
    """Every line exact. These were computed by hand from the drivers above."""

    @pytest.mark.parametrize(
        ("year", "opening", "earned", "charge", "excess", "closing"),
        [
            # 1,000 at 12% earns 120 against a 100 charge, and retains 60% of 120.
            (1, "1000", "120", "100", "20", "1072"),
            # 1,072 at 12% earns 128.64 against 107.20, and retains 77.184.
            (2, "1072", "128.64", "107.2", "21.44", "1149.184"),
            # 1,149.184 at 12% earns 137.90208 against 114.9184, and retains 82.741248.
            (3, "1149.184", "137.90208", "114.9184", "22.98368", "1231.925248"),
        ],
    )
    def test_each_year(self, context, year, opening, earned, charge, excess, closing):
        projected = residual_income_value(context, base_inputs(), mandate=MANDATE).years[year - 1]

        assert projected.opening_book_value.value == Decimal(opening)
        assert projected.net_income.value == Decimal(earned)
        assert projected.equity_charge.value == Decimal(charge)
        assert projected.residual_income.value == Decimal(excess)
        assert projected.closing_book_value.value == Decimal(closing)

    def test_the_discounted_years(self, context):
        # 20/1.1, 21.44/1.21, 22.98368/1.331
        years = residual_income_value(context, base_inputs(), mandate=MANDATE).years

        assert close(years[0].present_value, "18.18181818")
        assert close(years[1].present_value, "17.71900826")
        assert close(years[2].present_value, "17.26797896")

    def test_the_first_year_is_discounted_one_year_not_none(self, context):
        """Year one is a year away. Discounting it at t=0 overstates every valuation."""
        years = residual_income_value(context, base_inputs(), mandate=MANDATE).years

        assert years[0].discount_factor.value < 1
        assert close(years[0].discount_factor, "0.90909091")

    def test_one_year_opens_where_the_last_one_closed(self, context):
        """The roll-forward is the whole model's spine: break it and the excess is charged
        against a book value that never existed."""
        years = residual_income_value(context, base_inputs(), mandate=MANDATE).years

        for earlier, later in pairwise(years):
            assert later.opening_book_value.value == earlier.closing_book_value.value

    def test_the_first_year_opens_on_the_filed_book_value(self, context):
        years = residual_income_value(context, base_inputs(), mandate=MANDATE).years

        assert years[0].opening_book_value.value == Decimal("1000")


# -- The worked valuation --------------------------------------------------------------------


class TestBothTerminalTreatments:
    def test_fading_to_nothing(self, context):
        # 1,000 of book plus 53.17 of discounted excess, and nothing beyond year three.
        result = residual_income_value(context, base_inputs(), mandate=MANDATE)

        assert close(result.explicit_present_value, "53.16880541")
        # No terminal value, rather than a terminal value of nil: the fade treatment declines
        # to make the claim, and a reader seeing 0.00 would ask which formula produced it.
        assert result.terminal_value is None
        assert result.terminal_present_value is None
        assert close(result.equity_value, "1053.16880541")
        assert close(result.value_per_share, "10.53168805")

    def test_perpetual_growth(self, context):
        # 22.98368 x 1.02 / (0.10 - 0.02) = 293.04192, discounted three years.
        result = residual_income_value(
            context,
            base_inputs(terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH),
            mandate=MANDATE,
        )

        assert result.terminal_value.value == Decimal("293.04192")
        assert close(result.terminal_present_value, "220.16673178")
        assert close(result.equity_value, "1273.33553719")
        assert close(result.value_per_share, "12.73335537")

    def test_the_two_treatments_disagree_by_most_of_the_excess(self, context):
        """The demonstration. Same bank, same drivers, one unobservable parameter, and the
        excess over book quadruples — which is why the treatment has no default."""
        faded = residual_income_value(context, base_inputs(), mandate=MANDATE)
        forever = residual_income_value(
            context,
            base_inputs(terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH),
            mandate=MANDATE,
        )

        assert forever.premium_to_book.value > faded.premium_to_book.value * 4

    def test_the_premium_is_the_answer_less_the_balance_sheet(self, context):
        result = residual_income_value(context, base_inputs(), mandate=MANDATE)

        with localcontext(CALC_CONTEXT):
            expected = result.equity_value.value - Decimal("1000")
        assert result.premium_to_book.value == expected

    def test_every_result_carries_the_clean_surplus_caveat(self, context):
        for treatment in TerminalTreatment:
            result = residual_income_value(
                context, base_inputs(terminal_treatment=treatment), mandate=MANDATE
            )
            assert CLEAN_SURPLUS_CAVEAT in result.caveats

    def test_each_treatment_says_what_it_assumed_beyond_the_forecast(self, context):
        faded = residual_income_value(context, base_inputs(), mandate=MANDATE)
        forever = residual_income_value(
            context,
            base_inputs(terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH),
            mandate=MANDATE,
        )

        assert any("competed away" in caveat for caveat in faded.caveats)
        assert any("in perpetuity" in caveat for caveat in forever.caveats)


# -- The identity ----------------------------------------------------------------------------


class TestTheDividendIdentity:
    """Residual income *is* the dividend discount, and here is the arithmetic saying so.

    Under clean surplus, over any finite horizon:

        BV_0 + Σ RI_t/(1+r)^t  ==  Σ D_t/(1+r)^t + BV_N/(1+r)^N

    because RI_t = D_t + BV_t - (1+r)BV_(t-1), and the book-value terms telescope. The
    right-hand side is computed below from dividends and a closing book value with nothing
    from the module under test, so agreement is evidence rather than a restatement.
    """

    @staticmethod
    def dividend_discount(inputs: ResidualIncomeInputs) -> Decimal:
        """The same bank valued the other way: dividends, plus the book value left at the end."""
        with localcontext(CALC_CONTEXT):
            rate_ = inputs.cost_of_equity.value
            book = inputs.opening_book_value.value
            value = Decimal(0)
            for year in range(1, inputs.years + 1):
                earned = book * inputs.return_on_equity.at(year).value
                dividend = earned * inputs.payout_ratio.at(year).value
                value += dividend / (Decimal(1) + rate_) ** year
                book = book + earned - dividend
            return value + book / (Decimal(1) + rate_) ** inputs.years

    def test_the_worked_example_agrees_with_a_dividend_discount(self, context):
        inputs = base_inputs()
        result = residual_income_value(context, inputs, mandate=MANDATE)

        assert close(result.equity_value, str(self.dividend_discount(inputs)))

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        roe=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.30"), places=4),
        payout=st.decimals(min_value=Decimal(0), max_value=Decimal(1), places=4),
        cost=st.decimals(min_value=Decimal("0.04"), max_value=Decimal("0.20"), places=4),
        years=st.integers(min_value=1, max_value=8),
    )
    def test_the_identity_holds_for_any_forecast(self, context, roe, payout, cost, years):
        inputs = base_inputs(
            return_on_equity=flat("return_on_equity", str(roe), years=years),
            payout_ratio=flat("payout_ratio", str(payout), years=years),
            cost_of_equity=rate(str(cost)),
        )
        result = residual_income_value(context, inputs, mandate=MANDATE)

        assert close(result.equity_value, str(self.dividend_discount(inputs)))

    def test_fading_to_nothing_is_worth_book_value_at_the_horizon(self, context):
        """The identity read the other way. A fade-to-nothing valuation is exactly a
        dividend discount whose terminal assumption is that the bank ends worth its book —
        which is what "the excess is competed away" means, stated as a number."""
        inputs = base_inputs()
        result = residual_income_value(context, inputs, mandate=MANDATE)

        assert close(result.equity_value, str(self.dividend_discount(inputs)))
        assert result.terminal_value is None


# -- What it refuses -------------------------------------------------------------------------


class TestWhatItRefuses:
    def test_a_payout_above_one(self, context):
        with pytest.raises(CalculationError) as excinfo:
            residual_income_value(
                context, base_inputs(payout_ratio=flat("payout_ratio", "1.20")), mandate=MANDATE
            )

        assert "distribution out of capital" in str(excinfo.value)

    def test_a_negative_payout(self, context):
        with pytest.raises(CalculationError) as excinfo:
            residual_income_value(
                context, base_inputs(payout_ratio=flat("payout_ratio", "-0.10")), mandate=MANDATE
            )

        assert "capital raise" in str(excinfo.value)

    def test_terminal_growth_at_the_cost_of_equity(self, context):
        with pytest.raises(CalculationError) as excinfo:
            residual_income_value(
                context,
                base_inputs(
                    terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH,
                    terminal_growth=rate("0.10"),
                ),
                mandate=MANDATE,
            )

        assert "unbounded" in str(excinfo.value)

    def test_terminal_growth_above_the_cost_of_equity(self, context):
        with pytest.raises(CalculationError):
            residual_income_value(
                context,
                base_inputs(
                    terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH,
                    terminal_growth=rate("0.15"),
                ),
                mandate=MANDATE,
            )

    def test_growing_a_shortfall_for_ever(self, context):
        """A bank earning below its cost of equity in the final year, capitalised in
        perpetuity, subtracts an unbounded amount from book value on one year's evidence."""
        with pytest.raises(CalculationError) as excinfo:
            residual_income_value(
                context,
                base_inputs(
                    return_on_equity=flat("return_on_equity", "0.06"),
                    terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH,
                ),
                mandate=MANDATE,
            )

        assert "extend the forecast" in str(excinfo.value)

    def test_a_shortfall_is_a_real_answer_when_nothing_is_capitalised(self, context):
        """The same bank under the fade treatment is simply worth less than book, which is
        the correct answer and not an error."""
        result = residual_income_value(
            context, base_inputs(return_on_equity=flat("return_on_equity", "0.06")), mandate=MANDATE
        )

        assert result.premium_to_book.value < 0
        assert result.equity_value.value < Decimal("1000")

    def test_drivers_covering_different_numbers_of_years(self, context):
        with pytest.raises(CalculationError) as excinfo:
            residual_income_value(
                context,
                base_inputs(payout_ratio=flat("payout_ratio", "0.40", years=5)),
                mandate=MANDATE,
            )

        assert "different numbers of years" in str(excinfo.value)

    def test_a_forecast_longer_than_the_ceiling(self, context):
        years = MAX_FORECAST_YEARS + 1
        with pytest.raises(CalculationError) as excinfo:
            residual_income_value(
                context,
                base_inputs(
                    return_on_equity=flat("return_on_equity", "0.12", years=years),
                    payout_ratio=flat("payout_ratio", "0.40", years=years),
                ),
                mandate=MANDATE,
            )

        assert str(years) in str(excinfo.value)

    def test_a_driver_with_no_years(self):
        with pytest.raises(CalculationError):
            DriverPath("return_on_equity", ())

    def test_a_flat_driver_over_no_years(self):
        with pytest.raises(CalculationError):
            DriverPath.flat("return_on_equity", rate("0.12"), years=0)

    def test_a_share_count_of_nil(self, context):
        with pytest.raises(CalculationError) as excinfo:
            residual_income_value(
                context, base_inputs(shares_outstanding=shares("0")), mandate=MANDATE
            )

        assert "no shares has no per-share anything" in str(excinfo.value)

    def test_a_share_count_that_is_not_shares(self, context):
        with pytest.raises(CalculationError) as excinfo:
            value_per_share(
                context,
                equity_value=usd("1000"),
                shares=usd("100"),
                treatment=TerminalTreatment.FADE_TO_NOTHING,
            )

        assert "not shares" in str(excinfo.value)

    def test_a_book_value_with_no_currency(self, context):
        with pytest.raises(CalculationError) as excinfo:
            net_income_from_roe(
                context, opening_book_value=rate("1000"), return_on_equity=rate("0.12")
            )

        assert "not a currency amount" in str(excinfo.value)

    def test_a_return_on_equity_denominated_in_dollars(self, context):
        with pytest.raises(CalculationError) as excinfo:
            net_income_from_roe(
                context, opening_book_value=usd("1000"), return_on_equity=usd("0.12")
            )

        assert "category error" in str(excinfo.value)

    def test_a_cost_of_equity_that_was_never_divided_by_a_hundred(self, context):
        """10 rather than 0.10 discounts the first year to nothing, and both are
        dimensionless so no unit catches it."""
        with pytest.raises(CalculationError) as excinfo:
            equity_discount_factor(context, cost_of_equity=rate("10"), year=1)

        assert "rate_from_percent" in str(excinfo.value)

    def test_a_discounting_period_before_the_first_year(self, context):
        with pytest.raises(CalculationError) as excinfo:
            equity_discount_factor(context, cost_of_equity=rate("0.10"), year=0)

        assert "undiscounted" in str(excinfo.value)

    def test_subtracting_a_charge_in_another_currency(self, context):
        """Units are carried through: a sterling charge against dollar earnings raises
        rather than quietly producing a number."""
        with pytest.raises(UnitMismatchError):
            residual_income(context, net_income=usd("120"), charge=money("100", "GBP", source=FACT))

    def test_an_explicit_value_over_no_years(self, context):
        with pytest.raises(CalculationError) as excinfo:
            explicit_residual_value(context, discounted_residual_income=[])

        assert "book value wearing a valuation's name" in str(excinfo.value)

    def test_a_free_text_terminal_treatment(self, context):
        """The annotation covers the callers mypy checks. This catches the ones it does not:
        a string recorded verbatim would read as the claim the valuation made about
        competition, and is a string."""
        with pytest.raises(CalculationError) as excinfo:
            equity_value(
                context,
                opening_book_value=usd("1000"),
                explicit_value=usd("53"),
                discounted_terminal_value=None,
                treatment="fades a bit",  # type: ignore[arg-type]
            )

        assert "not a TerminalTreatment" in str(excinfo.value)


# -- The block -------------------------------------------------------------------------------


class TestTheMandateIsTheBlock:
    """`docs/adr/0029`: permitting a model is not exempting it from the gate."""

    def test_a_bank_cannot_be_given_a_free_cash_flow_mandate_at_all(self):
        with pytest.raises(ModelNotPermittedError):
            mandate_for(
                ValuationModel.DCF_FCFF,
                subject="BANKCO",
                profile=BANKS,
                confirmed_by="analyst@example.invalid",
            )

    def test_a_bank_can_be_given_a_residual_income_mandate(self):
        assert MANDATE.model is ValuationModel.RESIDUAL_INCOME
        assert MANDATE.sector_key == "banks"

    def test_a_comparables_mandate_is_not_permission_to_run_this(self, context):
        comps = mandate_for(
            ValuationModel.COMPS_MULTIPLES,
            subject="BANKCO",
            profile=BANKS,
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(ModelNotPermittedError) as excinfo:
            residual_income_value(context, base_inputs(), mandate=comps)

        assert "comps_multiples" in str(excinfo.value)

    def test_a_dividend_discount_mandate_is_not_permission_either(self, context):
        """The nearest miss, and the one worth refusing loudest: this model is the dividend
        discount rearranged, so the two are interchangeable in arithmetic and not in what a
        reader is told they are reading."""
        ddm = mandate_for(
            ValuationModel.DIVIDEND_DISCOUNT,
            subject="BANKCO",
            profile=BANKS,
            confirmed_by="analyst@example.invalid",
        )

        with pytest.raises(ModelNotPermittedError) as excinfo:
            residual_income_value(context, base_inputs(), mandate=ddm)

        assert "dividend_discount" in str(excinfo.value)

    def test_the_valuation_cannot_be_called_without_one(self, context):
        with pytest.raises(TypeError):
            residual_income_value(context, base_inputs())  # type: ignore[call-arg]

    def test_an_unclassified_company_may_still_run_it(self, context):
        """A company nobody classified into a specialist sector is not thereby forbidden the
        model — the gate stops a run whose classifier *proposed* a specialist sector, and
        this is the other case."""
        result = residual_income_value(
            context,
            base_inputs(),
            mandate=unclassified_mandate(ValuationModel.RESIDUAL_INCOME, subject="TESTCO"),
        )

        assert result.equity_value.value > 0


# -- The ledger ------------------------------------------------------------------------------


class TestTheLedger:
    def test_the_discount_rate_is_recorded_as_a_cost_of_equity_not_a_wacc(self, context):
        """A ledger that calls a cost of equity a weighted average cost of capital gives the
        right number under the wrong name, which is the failure this platform exists to make
        impossible."""
        residual_income_value(context, base_inputs(), mandate=MANDATE)

        factors = [r for r in context.records if r.name == "equity_discount_factor"]
        assert factors
        for record in factors:
            named = {i.name for i in record.inputs}
            assert "cost_of_equity" in named
            assert "wacc" not in named
            assert "WACC" not in record.formula

    def test_every_step_of_every_year_is_recorded(self, context):
        residual_income_value(context, base_inputs(), mandate=MANDATE)

        struck = {record.name for record in context.records}
        assert {
            "net_income_from_roe",
            "equity_charge",
            "residual_income",
            "equity_discount_factor",
            "present_value",
            "closing_book_value",
            "explicit_residual_value",
            "residual_income_equity_value",
            "premium_to_book",
            "residual_income_per_share",
        } <= struck

    def test_no_figure_in_the_result_is_arithmetic_nothing_recorded(self, context):
        """Invariant 3. Every quantity a reader can see traces to a row in the ledger, so a
        total summed outside the traced steps — which is how the first draft of this module
        computed the equity value — cannot reach a report."""
        result = residual_income_value(
            context,
            base_inputs(terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH),
            mandate=MANDATE,
        )
        ids = {str(record.id) for record in context.records}

        reported = [
            result.explicit_present_value,
            result.terminal_value,
            result.terminal_present_value,
            result.equity_value,
            result.premium_to_book,
            result.value_per_share,
        ]
        for year in result.years:
            reported += [
                year.net_income,
                year.equity_charge,
                year.residual_income,
                year.discount_factor,
                year.present_value,
                year.closing_book_value,
            ]

        for figure in reported:
            assert figure is not None
            assert figure.source is not None
            assert figure.source.identifier in ids

    def test_the_total_names_every_year_that_went_into_it(self, context):
        """A total nobody can decompose is a total a reader has to take on trust."""
        residual_income_value(context, base_inputs(), mandate=MANDATE)

        total = next(r for r in context.records if r.name == "explicit_residual_value")
        assert len(total.inputs) == 3

    def test_the_total_records_which_claim_about_competition_produced_it(self, context):
        """Same drivers, two answers. Without the treatment on the row, the ledger holds a
        total with nothing saying which of the two it is."""
        for treatment in TerminalTreatment:
            fresh = CalculationContext(code_version="testsha")
            residual_income_value(
                context=fresh,
                inputs=base_inputs(terminal_treatment=treatment),
                mandate=MANDATE,
            )

            row = next(r for r in fresh.records if r.name == "residual_income_equity_value")
            assert row.parameters["treatment"] is treatment

    def test_the_per_share_figure_resolves_to_its_own_record(self, context):
        result = residual_income_value(context, base_inputs(), mandate=MANDATE)

        assert result.value_per_share.source is not None
        ids = {str(record.id) for record in context.records}
        assert result.value_per_share.source.identifier in ids

    def test_the_perpetuity_reuses_the_final_year_factor_rather_than_striking_it_twice(
        self, context
    ):
        """R14. The terminal value is discounted over the same three years as the last
        forecast year, so it is the same derivation — and one derivation is one row."""
        residual_income_value(
            context,
            base_inputs(terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH),
            mandate=MANDATE,
        )

        factors = [r for r in context.records if r.name == "equity_discount_factor"]
        assert len(factors) == 3

    def test_the_per_share_row_does_not_collide_with_the_discounted_cash_flow(self, context):
        """The ledger stores the calculation name, so two functions claiming ``value_per_share``
        would make every stored row of it ambiguous — and it is the figure a reader quotes."""
        residual_income_value(context, base_inputs(), mandate=MANDATE)

        struck = {record.name for record in context.records}
        assert "residual_income_per_share" in struck
        assert "value_per_share" not in struck
        assert "value_per_share" in registry()
        assert registry()["value_per_share"].__module__ == "aer.calc.dcf"

    def test_every_traced_step_here_is_in_the_replay_registry(self, context):
        """A module missing from ``CALC_MODULES`` is arithmetic nothing can replay."""
        residual_income_value(
            context,
            base_inputs(terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH),
            mandate=MANDATE,
        )
        known = registry()

        for record in context.records:
            assert record.name in known, record.name

    def test_each_step_records_what_it_assumed(self, context):
        residual_income_value(context, base_inputs(), mandate=MANDATE)

        roll = next(r for r in context.records if r.name == "closing_book_value")
        assert CLEAN_SURPLUS_CAVEAT in roll.assumptions


# -- Properties ------------------------------------------------------------------------------


returns = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.30"), places=4)
costs = st.decimals(min_value=Decimal("0.04"), max_value=Decimal("0.20"), places=4)
payouts = st.decimals(min_value=Decimal(0), max_value=Decimal(1), places=4)
books = st.decimals(min_value=Decimal(100), max_value=Decimal(100000), places=2)
horizons = st.integers(min_value=1, max_value=8)


def built(*, roe, cost, payout=Decimal("0.40"), book=Decimal(1000), years=3):
    return base_inputs(
        opening_book_value=usd(str(book)),
        return_on_equity=flat("return_on_equity", str(roe), years=years),
        payout_ratio=flat("payout_ratio", str(payout), years=years),
        cost_of_equity=rate(str(cost)),
    )


class TestProperties:
    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(cost=costs, payout=payouts, book=books, years=horizons)
    def test_earning_exactly_the_cost_of_equity_is_worth_book_value(
        self, context, cost, payout, book, years
    ):
        """The model's defining sanity check. A bank earning precisely what its equity costs
        has no excess to value, so it is worth its balance sheet — whatever it pays out, for
        however many years, from whatever opening book value."""
        result = residual_income_value(
            context,
            built(roe=cost, cost=cost, payout=payout, book=book, years=years),
            mandate=MANDATE,
        )

        assert result.equity_value.value == Decimal(str(book))
        assert result.premium_to_book.value == 0

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(roe=returns, cost=costs, payout=payouts, years=horizons)
    def test_the_premium_takes_its_sign_from_the_spread(self, context, roe, cost, payout, years):
        result = residual_income_value(
            context, built(roe=roe, cost=cost, payout=payout, years=years), mandate=MANDATE
        )

        if roe > cost:
            assert result.premium_to_book.value > 0
        elif roe < cost:
            assert result.premium_to_book.value < 0
        else:
            assert result.premium_to_book.value == 0

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        roe=returns,
        increase=st.decimals(min_value=Decimal("0.005"), max_value=Decimal("0.05"), places=4),
        cost=costs,
        payout=payouts,
    )
    def test_value_rises_with_the_return_on_equity(self, context, roe, increase, cost, payout):
        assume(roe + increase <= Decimal("0.30"))

        lower = residual_income_value(
            context, built(roe=roe, cost=cost, payout=payout), mandate=MANDATE
        )
        higher = residual_income_value(
            context, built(roe=roe + increase, cost=cost, payout=payout), mandate=MANDATE
        )

        assert higher.equity_value.value > lower.equity_value.value

    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        roe=returns,
        cost=costs,
        increase=st.decimals(min_value=Decimal("0.005"), max_value=Decimal("0.05"), places=4),
        payout=payouts,
    )
    def test_a_dearer_cost_of_equity_is_worth_less(self, context, roe, cost, increase, payout):
        assume(cost + increase <= Decimal("0.20"))
        assume(roe != cost and roe != cost + increase)

        cheaper = residual_income_value(
            context, built(roe=roe, cost=cost, payout=payout), mandate=MANDATE
        )
        dearer = residual_income_value(
            context, built(roe=roe, cost=cost + increase, payout=payout), mandate=MANDATE
        )

        assert dearer.equity_value.value < cheaper.equity_value.value

    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        roe=returns,
        cost=costs,
        payout=payouts,
        scale=st.decimals(min_value=Decimal("0.5"), max_value=Decimal(10), places=2),
    )
    def test_scaling_the_balance_sheet_scales_the_answer(self, context, roe, cost, payout, scale):
        """Everything in the model is proportional to the opening book value, so a bank twice
        the size on identical drivers is worth twice as much. A term that does not scale is a
        term that came from somewhere other than the balance sheet."""
        small = residual_income_value(
            context, built(roe=roe, cost=cost, payout=payout, book=Decimal(1000)), mandate=MANDATE
        )
        large = residual_income_value(
            context,
            built(roe=roe, cost=cost, payout=payout, book=Decimal(1000) * scale),
            mandate=MANDATE,
        )

        with localcontext(CALC_CONTEXT):
            expected = small.equity_value.value * Decimal(str(scale))
        assert close(large.equity_value, str(expected))

    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(roe=returns, cost=costs, payout=payouts, years=horizons)
    def test_a_perpetuity_is_never_worth_less_than_fading_to_nothing(
        self, context, roe, cost, payout, years
    ):
        assume(roe >= cost)

        faded = residual_income_value(
            context, built(roe=roe, cost=cost, payout=payout, years=years), mandate=MANDATE
        )
        forever = residual_income_value(
            context,
            replace(
                built(roe=roe, cost=cost, payout=payout, years=years),
                terminal_treatment=TerminalTreatment.PERPETUAL_GROWTH,
            ),
            mandate=MANDATE,
        )

        assert forever.equity_value.value >= faded.equity_value.value

    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(roe=returns, cost=costs, payout=payouts, years=horizons)
    def test_the_book_value_only_ever_rises_when_earnings_are_retained(
        self, context, roe, cost, payout, years
    ):
        assume(payout < 1)

        result = residual_income_value(
            context, built(roe=roe, cost=cost, payout=payout, years=years), mandate=MANDATE
        )

        for year in result.years:
            assert year.closing_book_value.value > year.opening_book_value.value


# -- The steps on their own ------------------------------------------------------------------


class TestTheStepsInIsolation:
    def test_net_income_is_the_return_on_the_opening_book(self, context):
        earned = net_income_from_roe(
            context, opening_book_value=usd("1000"), return_on_equity=rate("0.12")
        )

        assert earned.value == Decimal("120.00")
        assert earned.unit.symbol == "USD"

    def test_the_charge_is_the_cost_on_the_opening_book(self, context):
        charge = equity_charge(context, opening_book_value=usd("1000"), cost_of_equity=rate("0.10"))

        assert charge.value == Decimal("100.00")

    def test_the_excess_is_earnings_less_the_charge(self, context):
        excess = residual_income(context, net_income=usd("120"), charge=usd("100"))

        assert excess.value == Decimal("20")

    def test_a_full_payout_leaves_the_book_where_it_started(self, context):
        book = book_value_roll_forward(
            context,
            opening_book_value=usd("1000"),
            net_income=usd("120"),
            payout_ratio=rate("1"),
        )

        assert book.value == Decimal("1000")

    def test_retaining_everything_adds_the_whole_year(self, context):
        book = book_value_roll_forward(
            context,
            opening_book_value=usd("1000"),
            net_income=usd("120"),
            payout_ratio=rate("0"),
        )

        assert book.value == Decimal("1120")

    def test_the_perpetuity_capitalises_the_grown_final_year(self, context):
        terminal = perpetual_residual_value(
            context,
            final_residual_income=usd("22.98368"),
            cost_of_equity=rate("0.10"),
            terminal_growth=rate("0.02"),
        )

        assert terminal.value == Decimal("293.04192")

    def test_the_discount_factor_compounds(self, context):
        first = equity_discount_factor(context, cost_of_equity=rate("0.10"), year=1)
        second = equity_discount_factor(context, cost_of_equity=rate("0.10"), year=2)

        assert close(second, str(first.value * first.value))
