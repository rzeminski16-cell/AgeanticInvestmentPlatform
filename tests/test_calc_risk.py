"""What could go wrong with the book — the arithmetic, and the refusals around it.

Roadmap §3.9 under ADRs 0080 and 0106. The figures are short; what these tests hold is the
edges. A volatility is annualised over trading days and never negative, a drawdown is at or
below nil and does not care what the index is denominated in, an expected shortfall is
never better than the mean and never worse than the worst day, contributions to the book's
risk add to one over the measured holdings, and a scenario reaching nothing has no answer
rather than an answer of nil.

The property tests carry the conventions `test_calc_properties` established: decimals built
from integers and scaled, never sampled as floats; absolute tolerances, because every result
here is a rate.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aer.calc.engine import CalculationContext
from aer.calc.prices import Frequency, InsufficientHistoryError, beta, covariance, variance
from aer.calc.risk import (
    DEFAULT_TAIL_PER_CENT,
    MIN_TAIL_OBSERVATIONS,
    PERIODS_PER_YEAR,
    annualised_volatility,
    combined_shock,
    cumulative_index,
    expected_shortfall,
    max_drawdown,
    risk_contribution,
    scenario_impact,
    scenario_pnl,
    static_weight_returns,
)
from aer.calc.units import (
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceRef,
    UnitMismatchError,
    money,
)
from aer.calc.units import ratio as pure

SOURCE = SourceRef.attestation("risk-test", grade="attested")
DAY_ONE = date(2026, 1, 5)


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


def share(value: str | int) -> Quantity:
    return pure(Decimal(str(value)), source=SOURCE)


def gbp(value: str | int) -> Quantity:
    return money(Decimal(str(value)), "GBP", source=SOURCE)


def dated(values: list[str]) -> list[tuple[date, Quantity]]:
    return [(DAY_ONE + timedelta(days=index), share(value)) for index, value in enumerate(values)]


# Returns in basis points, as integers, so the sampled decimals are exact.
basis_points = st.integers(min_value=-2000, max_value=2000)
weights_in_bp = st.integers(min_value=0, max_value=10_000)


# -- The series -----------------------------------------------------------------------------


class TestTheBooksSeries:
    def test_it_is_the_weighted_sum_over_the_shared_dates(self) -> None:
        a = dated(["0.01"] * 30)
        # The second holding skipped one day; that day is out of the book's series.
        b = dated(["0.03"] * 30)
        del b[10]

        series = static_weight_returns(
            {"A": share("0.5"), "B": share("0.25")}, {"A": a, "B": b}, source=SOURCE
        )

        assert len(series) == 29
        assert all(value.value == Decimal("0.0125") for _, value in series)
        assert all(value.source is SOURCE for _, value in series)
        assert (DAY_ONE + timedelta(days=10)) not in {when for when, _ in series}

    def test_the_holdings_must_match(self) -> None:
        with pytest.raises(CalculationError, match="same holdings"):
            static_weight_returns({"A": share(1)}, {"B": dated(["0.01"] * 30)}, source=SOURCE)
        with pytest.raises(CalculationError, match="same holdings"):
            static_weight_returns({}, {}, source=SOURCE)

    def test_too_few_shared_days_is_refused(self) -> None:
        with pytest.raises(InsufficientHistoryError, match="share 5 trading day"):
            static_weight_returns({"A": share(1)}, {"A": dated(["0.01"] * 5)}, source=SOURCE)

    def test_a_weight_with_a_unit_is_refused(self) -> None:
        with pytest.raises(UnitMismatchError, match="weight of A"):
            static_weight_returns({"A": gbp(1)}, {"A": dated(["0.01"] * 30)}, source=SOURCE)

    def test_the_index_compounds_from_one(self) -> None:
        levels = cumulative_index(dated(["0.1", "-0.5"]), source=SOURCE)

        assert [level.value for level in levels] == [Decimal(1), Decimal("1.1"), Decimal("0.55")]

    def test_a_total_loss_is_refused(self) -> None:
        with pytest.raises(CalculationError, match="nil or below"):
            cumulative_index(dated(["-1"]), source=SOURCE)


# -- Volatility -----------------------------------------------------------------------------


class TestVolatility:
    def test_daily_is_annualised_over_trading_days(self, context: CalculationContext) -> None:
        assert PERIODS_PER_YEAR[Frequency.DAILY] == 252
        vol = annualised_volatility(context, variance=share("0.0001"), periods_per_year=252)

        assert abs(vol.value**2 - Decimal("0.0252")) < Decimal("1e-20")
        assert vol.unit == DIMENSIONLESS

    @given(bp=st.integers(min_value=0, max_value=10**8), periods=st.integers(1, 365))
    @settings(max_examples=60)
    def test_squared_it_is_the_variance_scaled(self, bp: int, periods: int) -> None:
        context = CalculationContext(code_version="test")
        var = share(Decimal(bp).scaleb(-8))

        vol = annualised_volatility(context, variance=var, periods_per_year=periods)

        assert abs(vol.value**2 - var.value * periods) < Decimal("1e-20")
        assert vol.value >= 0

    def test_a_negative_variance_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="never negative"):
            annualised_volatility(context, variance=share("-0.1"), periods_per_year=252)

    def test_a_variance_of_prices_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError):
            annualised_volatility(context, variance=gbp(1), periods_per_year=252)

    def test_it_is_traced(self, context: CalculationContext) -> None:
        annualised_volatility(context, variance=share("0.0001"), periods_per_year=252)

        [record] = context.named("annualised_volatility")
        assert record.parameters["periods_per_year"] == 252


# -- Drawdown -------------------------------------------------------------------------------


class TestDrawdown:
    def test_a_rising_index_never_drew_down(self, context: CalculationContext) -> None:
        assert max_drawdown(context, levels=[share(1), share(2), share(3)]).value == 0

    def test_the_worst_fall_from_the_peak(self, context: CalculationContext) -> None:
        # 100 → 50 is the worst; the later recovery to 75 does not soften it.
        fall = max_drawdown(context, levels=[share(100), share(50), share(75), share(120)])

        assert fall.value == Decimal("-0.5")

    @given(
        levels=st.lists(st.integers(1, 10**6), min_size=1, max_size=40), scale=st.integers(1, 10**4)
    )
    @settings(max_examples=60)
    def test_it_is_a_fraction_at_or_below_nil_and_scale_free(
        self, levels: list[int], scale: int
    ) -> None:
        context = CalculationContext(code_version="test")

        plain = max_drawdown(context, levels=[share(level) for level in levels])
        scaled = max_drawdown(context, levels=[share(level * scale) for level in levels])

        assert Decimal(-1) < plain.value <= 0
        assert plain.value == scaled.value

    def test_nothing_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="no levels"):
            max_drawdown(context, levels=[])

    def test_a_nil_level_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="positive"):
            max_drawdown(context, levels=[share(1), share(0)])


# -- Expected shortfall ---------------------------------------------------------------------


class TestExpectedShortfall:
    def test_the_worst_five_per_cent_averaged(self, context: CalculationContext) -> None:
        # Twenty returns: the tail at 5% is the single worst day.
        observations = [share("0.01")] * 19 + [share("-0.08")]

        assert DEFAULT_TAIL_PER_CENT == 5
        es = expected_shortfall(
            context, observations=observations, tail_per_cent=DEFAULT_TAIL_PER_CENT
        )

        assert es.value == Decimal("-0.08")

    def test_the_tail_rounds_up_to_whole_days(self, context: CalculationContext) -> None:
        # Twenty-one at 5% is 1.05 days, so two: the worst two averaged.
        observations = [share("0.01")] * 19 + [share("-0.08"), share("-0.04")]

        es = expected_shortfall(
            context, observations=observations, tail_per_cent=DEFAULT_TAIL_PER_CENT
        )

        assert es.value == Decimal("-0.06")

    @given(bps=st.lists(basis_points, min_size=MIN_TAIL_OBSERVATIONS, max_size=120))
    @settings(max_examples=60)
    def test_it_lies_between_the_worst_day_and_the_mean(self, bps: list[int]) -> None:
        context = CalculationContext(code_version="test")
        observations = [share(Decimal(bp).scaleb(-4)) for bp in bps]

        es = expected_shortfall(
            context, observations=observations, tail_per_cent=DEFAULT_TAIL_PER_CENT
        )

        mean = sum((item.value for item in observations), Decimal(0)) / len(observations)
        assert min(item.value for item in observations) <= es.value <= mean

    def test_too_few_days_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(InsufficientHistoryError, match="single day"):
            expected_shortfall(
                context, observations=[share("0.01")] * 19, tail_per_cent=DEFAULT_TAIL_PER_CENT
            )

    @pytest.mark.parametrize("tail", [0, 100, 150])
    def test_the_tail_is_a_share(self, context: CalculationContext, tail: int) -> None:
        with pytest.raises(CalculationError, match="strictly between"):
            expected_shortfall(context, observations=[share("0.01")] * 20, tail_per_cent=tail)


# -- Contribution ---------------------------------------------------------------------------


class TestRiskContribution:
    def test_it_is_weight_times_beta(self, context: CalculationContext) -> None:
        assert risk_contribution(
            context, weight=share("0.4"), beta_to_book=share("1.5")
        ).value == Decimal("0.6")

    def test_contributions_to_the_books_own_series_sum_to_one(
        self, context: CalculationContext
    ) -> None:
        """The check ADR 0106 names: measured against the book's own returns, the weighted
        betas are the book's variance over itself."""
        a = dated(["0.01", "-0.02", "0.03", "0.00", "-0.01", "0.02"] * 5)
        b = dated(["-0.01", "0.04", "0.01", "-0.02", "0.02", "0.00"] * 5)
        weights = {"A": share("0.6"), "B": share("0.4")}
        book = static_weight_returns(weights, {"A": a, "B": b}, source=SOURCE)
        book_values = [value for _, value in book]
        book_variance = variance(context, observations=book_values)

        total = Decimal(0)
        for key, series in (("A", a), ("B", b)):
            joint = covariance(context, subject=[value for _, value in series], market=book_values)
            to_book = beta(
                context,
                subject_market_covariance=joint,
                market_variance=book_variance,
                frequency=Frequency.DAILY,
                observations=len(book_values),
            )
            total += risk_contribution(context, weight=weights[key], beta_to_book=to_book).value

        assert abs(total - 1) < Decimal("1e-12")

    def test_a_unit_on_either_is_refused(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError):
            risk_contribution(context, weight=gbp(1), beta_to_book=share(1))


# -- Scenarios ------------------------------------------------------------------------------


class TestAScenario:
    def test_the_profit_and_loss_is_the_shocked_values(self, context: CalculationContext) -> None:
        pnl = scenario_pnl(
            context, values=[gbp(1000), gbp(500)], shocks=[share("-0.2"), share("0.1")]
        )

        assert pnl.value == Decimal("-150")
        assert pnl.unit.symbol == "GBP"

    def test_it_is_a_share_of_the_book(self, context: CalculationContext) -> None:
        impact = scenario_impact(context, pnl=gbp(-150), net_assets=gbp(3000))

        assert impact.value == Decimal("-0.05")

    def test_two_shocks_on_one_position_compound(self, context: CalculationContext) -> None:
        both = combined_shock(context, shocks=[share("-0.2"), share("-0.1")])

        assert both.value == Decimal("-0.28")

    @given(a=basis_points, b=basis_points)
    @settings(max_examples=60)
    def test_compounding_is_symmetric_and_a_single_shock_is_itself(self, a: int, b: int) -> None:
        context = CalculationContext(code_version="test")
        first, second = share(Decimal(a).scaleb(-4)), share(Decimal(b).scaleb(-4))

        assert combined_shock(context, shocks=[first]).value == first.value
        assert (
            combined_shock(context, shocks=[first, second]).value
            == combined_shock(context, shocks=[second, first]).value
        )

    def test_a_scenario_reaching_nothing_has_no_answer(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="reaches no position"):
            scenario_pnl(context, values=[], shocks=[])

    def test_the_pairing_must_hold(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="pairs them"):
            scenario_pnl(context, values=[gbp(1)], shocks=[])

    def test_two_currencies_are_refused(self, context: CalculationContext) -> None:
        with pytest.raises(UnitMismatchError):
            scenario_pnl(
                context,
                values=[gbp(1), money(Decimal(1), "USD", source=SOURCE)],
                shocks=[share(0), share(0)],
            )

    def test_a_book_worth_nothing_has_no_impact(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="no whole"):
            scenario_impact(context, pnl=gbp(-1), net_assets=gbp(0))

    def test_a_total_loss_is_not_a_shock(self, context: CalculationContext) -> None:
        with pytest.raises(CalculationError, match="largest fall"):
            combined_shock(context, shocks=[share("-1")])
