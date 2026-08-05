"""Price arithmetic: what the adjustment does, and the ways it could be silently wrong.

The acceptance criterion of task 29 is that a price series is reproducible from the archived
response and the recorded adjustments alone. `TestTheAdjustedSeriesIsTheActionsAndNothingElse`
is that criterion: it asserts the unadjusted and adjusted series differ by exactly the
recorded actions and by nothing else.

The rest is the ways that could hold and still be wrong. A split whose ex-date falls on a
public holiday adjusts nothing at all if the loop only meets an action when a bar shares its
date, and a split that fails to apply halves every historical price with no error anywhere. A
dividend subtracted from a price in another currency is wrong by the exchange rate. Two return
series zipped by position rather than paired by date produce a beta wrong by an amount nobody
can see, and every one of those is a plausible-looking number.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from decimal import localcontext as decimal_context

import pytest

from aer.calc.engine import CalculationContext
from aer.calc.prices import (
    MIN_RETURN_OBSERVATIONS,
    AdjustedBar,
    Bar,
    CurrencyMismatchError,
    DividendAction,
    Frequency,
    InsufficientHistoryError,
    LookAheadPriceError,
    SplitAction,
    adjusted_series,
    aligned_returns,
    beta,
    covariance,
    cumulative_split_factor,
    market_capitalisation,
    price_in_major_units,
    ratios_after,
    resample,
    simple_returns,
    total_return,
    variance,
)
from aer.calc.units import (
    CALC_CONTEXT,
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
)

AS_OF = date(2024, 6, 28)
SOURCE = SourceRef.fact("test-fact")


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


def bar(on: date, close: str | Decimal, **overrides: object) -> Bar:
    value = Decimal(close)
    defaults = {
        "on": on,
        "open": value,
        "high": value,
        "low": value,
        "close": value,
        "volume": 1_000,
    }
    return Bar(**(defaults | overrides))  # type: ignore[arg-type]


def pure(value: int | Decimal) -> Quantity:
    """A dimensionless, sourced observation — what a periodic return is."""
    number = value if isinstance(value, Decimal) else Decimal(value)
    return Quantity.of(number, DIMENSIONLESS, source=SOURCE)


def straight_line(*, days: int, start: str = "100", step: str = "1") -> list[Bar]:
    """A series that rises by a fixed amount, so any adjustment stands out against it."""
    first = date(2024, 1, 1)
    return [
        bar(first + timedelta(days=index), Decimal(start) + Decimal(step) * index)
        for index in range(days)
    ]


# -- The adjustment ---------------------------------------------------------------------------


class TestTheAdjustedSeriesIsTheActionsAndNothingElse:
    """Task 29's acceptance criterion, stated as a test."""

    def test_with_no_actions_the_adjusted_series_is_the_printed_one(self):
        bars = straight_line(days=10)
        series = adjusted_series(bars, currency="USD", as_of=AS_OF)

        for printed, adjusted in zip(bars, series.bars, strict=True):
            assert adjusted.close == printed.close
            assert adjusted.split_adjusted_close == printed.close
            assert adjusted.total_return_close == printed.close
            assert adjusted.split_factor == 1

    def test_a_two_for_one_split_halves_everything_before_it(self):
        bars = [
            bar(date(2024, 1, 1), "100"),
            bar(date(2024, 1, 2), "102"),
            bar(date(2024, 1, 3), "50"),
        ]
        series = adjusted_series(
            bars,
            splits=[SplitAction(ex_date=date(2024, 1, 3), ratio=Decimal(2))],
            currency="USD",
            as_of=AS_OF,
        )

        assert [b.split_adjusted_close for b in series.bars] == [
            Decimal(50),
            Decimal(51),
            Decimal(50),
        ]
        assert [b.close for b in series.bars] == [Decimal(100), Decimal(102), Decimal(50)]

    def test_a_consolidation_multiplies_everything_before_it(self):
        """A one-for-ten leaves the ratio below one, so the division raises earlier prices."""
        bars = [bar(date(2024, 1, 1), "5"), bar(date(2024, 1, 2), "50")]
        series = adjusted_series(
            bars,
            splits=[SplitAction(ex_date=date(2024, 1, 2), ratio=Decimal("0.1"))],
            currency="USD",
            as_of=AS_OF,
        )

        assert series.bars[0].split_adjusted_close == Decimal(50)

    def test_a_dividend_leaves_the_price_series_untouched(self):
        """The price genuinely fell by the dividend. Only the total-return series adjusts."""
        bars = [bar(date(2024, 1, 1), "100"), bar(date(2024, 1, 2), "99")]
        series = adjusted_series(
            bars,
            dividends=[DividendAction(ex_date=date(2024, 1, 2), amount=Decimal(1), currency="USD")],
            currency="USD",
            as_of=AS_OF,
        )

        assert [b.split_adjusted_close for b in series.bars] == [Decimal(100), Decimal(99)]
        assert series.bars[0].total_return_close == Decimal(99)

    def test_the_total_return_series_makes_a_dividend_day_flat(self):
        """A holder who took 1 in cash on a share that fell by 1 is exactly even."""
        bars = [bar(date(2024, 1, 1), "100"), bar(date(2024, 1, 2), "99")]
        series = adjusted_series(
            bars,
            dividends=[DividendAction(ex_date=date(2024, 1, 2), amount=Decimal(1), currency="USD")],
            currency="USD",
            as_of=AS_OF,
        )

        returns = simple_returns(series.bars, source=SOURCE)
        assert [(when, value.value) for when, value in returns] == [(date(2024, 1, 2), Decimal(0))]

    def test_the_price_series_alone_reports_that_dividend_as_a_loss(self):
        """Which is why a beta must never be computed from the price series."""
        bars = [bar(date(2024, 1, 1), "100"), bar(date(2024, 1, 2), "99")]
        unadjusted = adjusted_series(bars, currency="USD", as_of=AS_OF)

        assert simple_returns(unadjusted.bars, source=SOURCE)[0][1].value < 0

    def test_splits_and_dividends_compose(self):
        bars = [
            bar(date(2024, 1, 1), "100"),
            bar(date(2024, 1, 2), "99"),
            bar(date(2024, 1, 3), "49.5"),
        ]
        series = adjusted_series(
            bars,
            splits=[SplitAction(ex_date=date(2024, 1, 3), ratio=Decimal(2))],
            dividends=[DividendAction(ex_date=date(2024, 1, 2), amount=Decimal(1), currency="USD")],
            currency="USD",
            as_of=AS_OF,
        )

        first = series.bars[0]
        assert first.split_adjusted_close == Decimal(50)
        assert first.total_return_close == Decimal("49.5")

    def test_the_latest_bar_is_never_adjusted(self):
        """Every factor is a product over actions *after* the bar, and there are none."""
        bars = straight_line(days=5)
        series = adjusted_series(
            bars,
            splits=[SplitAction(ex_date=date(2024, 1, 3), ratio=Decimal(2))],
            currency="USD",
            as_of=AS_OF,
        )

        latest = series.latest
        assert latest.split_adjusted_close == latest.close
        assert latest.total_return_close == latest.close


class TestAnActionOnANonTradingDay:
    """The hole a split can vanish through."""

    def test_a_split_whose_ex_date_is_a_holiday_still_applies(self):
        bars = [bar(date(2024, 1, 5), "100"), bar(date(2024, 1, 8), "50")]
        series = adjusted_series(
            bars,
            # A Sunday. No bar shares this date.
            splits=[SplitAction(ex_date=date(2024, 1, 7), ratio=Decimal(2))],
            currency="USD",
            as_of=AS_OF,
        )

        assert series.bars[0].split_adjusted_close == Decimal(50)
        assert series.bars[1].split_adjusted_close == Decimal(50)

    def test_a_dividend_whose_ex_date_is_a_holiday_still_applies(self):
        bars = [bar(date(2024, 1, 5), "100"), bar(date(2024, 1, 8), "99")]
        series = adjusted_series(
            bars,
            dividends=[DividendAction(ex_date=date(2024, 1, 7), amount=Decimal(1), currency="USD")],
            currency="USD",
            as_of=AS_OF,
        )

        assert series.bars[0].total_return_close == Decimal(99)

    def test_a_dividend_before_the_first_bar_is_reported_not_dropped(self):
        """It adjusts nothing — there is no prior close — and saying so is the point."""
        bars = [bar(date(2024, 1, 5), "100")]
        series = adjusted_series(
            bars,
            dividends=[DividendAction(ex_date=date(2024, 1, 3), amount=Decimal(1), currency="USD")],
            currency="USD",
            as_of=AS_OF,
        )

        assert series.dividends_applied == ()
        assert len(series.dividends_without_a_prior_close) == 1


class TestTheRefusals:
    def test_a_bar_after_the_as_of_date_is_refused(self):
        bars = [bar(date(2024, 6, 27), "100"), bar(date(2024, 6, 29), "101")]

        with pytest.raises(LookAheadPriceError) as excinfo:
            adjusted_series(bars, currency="USD", as_of=AS_OF)
        assert "2024-06-29" in str(excinfo.value.context)

    def test_an_action_after_the_as_of_date_is_refused(self):
        """The one that matters: a September split restating a June valuation."""
        bars = straight_line(days=5)

        with pytest.raises(LookAheadPriceError):
            adjusted_series(
                bars,
                splits=[SplitAction(ex_date=date(2024, 9, 1), ratio=Decimal(2))],
                currency="USD",
                as_of=AS_OF,
            )

    def test_it_refuses_rather_than_filtering(self):
        """A caller who passed an unclamped query learns their query is wrong."""
        bars = straight_line(days=5)

        with pytest.raises(LookAheadPriceError):
            adjusted_series(
                bars,
                dividends=[
                    DividendAction(ex_date=date(2024, 12, 1), amount=Decimal(1), currency="USD")
                ],
                currency="USD",
                as_of=AS_OF,
            )

    def test_a_dividend_in_another_currency_is_refused(self):
        """A London listing in pence paying a dollar dividend. Ordinary, and a trap."""
        bars = [bar(date(2024, 1, 1), "250"), bar(date(2024, 1, 2), "249")]

        with pytest.raises(CurrencyMismatchError) as excinfo:
            adjusted_series(
                bars,
                dividends=[
                    DividendAction(ex_date=date(2024, 1, 2), amount=Decimal(1), currency="USD")
                ],
                currency="GBX",
                as_of=AS_OF,
            )
        assert "GBX" in str(excinfo.value)
        assert "USD" in str(excinfo.value)

    def test_a_dividend_larger_than_the_price_is_refused(self):
        bars = [bar(date(2024, 1, 1), "10"), bar(date(2024, 1, 2), "1")]

        with pytest.raises(CalculationError):
            adjusted_series(
                bars,
                dividends=[
                    DividendAction(ex_date=date(2024, 1, 2), amount=Decimal(20), currency="USD")
                ],
                currency="USD",
                as_of=AS_OF,
            )

    def test_a_nil_split_ratio_is_refused(self):
        with pytest.raises(CalculationError):
            adjusted_series(
                straight_line(days=3),
                splits=[SplitAction(ex_date=date(2024, 1, 2), ratio=Decimal(0))],
                currency="USD",
                as_of=AS_OF,
            )

    def test_two_bars_for_one_day_are_refused(self):
        bars = [bar(date(2024, 1, 1), "100"), bar(date(2024, 1, 1), "101")]

        with pytest.raises(CalculationError) as excinfo:
            adjusted_series(bars, currency="USD", as_of=AS_OF)
        assert "disagreement ladder" in str(excinfo.value)

    def test_a_return_measured_from_a_nil_close_is_refused(self):
        """Unreachable through `adjusted_series`, which the schema and the parsers guard —
        so this calls `simple_returns` directly. A guard nothing exercises is a guard that
        can be deleted without a failing test, which a sabotage pass proved."""
        bars = (
            AdjustedBar(
                on=date(2024, 1, 1),
                close=Decimal(0),
                split_adjusted_close=Decimal(0),
                total_return_close=Decimal(0),
                split_factor=Decimal(1),
                total_return_factor=Decimal(1),
            ),
            AdjustedBar(
                on=date(2024, 1, 2),
                close=Decimal(1),
                split_adjusted_close=Decimal(1),
                total_return_close=Decimal(1),
                split_factor=Decimal(1),
                total_return_factor=Decimal(1),
            ),
        )

        with pytest.raises(CalculationError) as excinfo:
            simple_returns(bars, source=SOURCE)
        assert "undefined" in str(excinfo.value)

    def test_an_empty_series_has_no_latest_price(self):
        series = adjusted_series([], currency="USD", as_of=AS_OF)

        with pytest.raises(InsufficientHistoryError):
            _ = series.latest


# -- Pence -----------------------------------------------------------------------------------


class TestPenceIsNotPounds:
    def test_a_pence_quote_converts_to_pounds(self, context):
        quoted = Quantity.of(
            Decimal(250), Unit.currency("GBX") / Unit.base("shares"), source=SOURCE
        )

        converted = price_in_major_units(context, quoted=quoted)

        assert converted.value == Decimal("2.5")
        assert converted.unit == Unit.currency("GBP") / Unit.base("shares")

    def test_the_conversion_is_recorded(self, context):
        quoted = Quantity.of(Decimal(250), Unit.currency("GBX"), source=SOURCE)
        price_in_major_units(context, quoted=quoted)

        assert [record.name for record in context.records] == ["price_in_major_units"]

    def test_a_currency_already_in_major_units_is_refused(self, context):
        """Converting twice is the failure this refusal exists to make impossible."""
        quoted = Quantity.of(Decimal(250), Unit.currency("GBP"), source=SOURCE)

        with pytest.raises(UnitMismatchError):
            price_in_major_units(context, quoted=quoted)

    def test_a_dimensionless_quantity_is_refused(self, context):
        with pytest.raises(UnitMismatchError):
            price_in_major_units(context, quoted=Quantity.of(Decimal(250), source=SOURCE))


# -- Market capitalisation ---------------------------------------------------------------------


class TestMarketCapitalisation:
    def test_a_price_per_share_times_a_share_count_is_a_currency(self, context):
        price = Quantity.of("446.95", Unit.currency("USD") / Unit.base("shares"), source=SOURCE)
        shares = Quantity.of(Decimal(1_000_000), Unit.base("shares"), source=SOURCE)

        cap = market_capitalisation(context, price=price, shares=shares)

        assert cap.value == Decimal("446950000.00")
        assert cap.unit == Unit.currency("USD")

    def test_a_total_rather_than_a_per_share_price_is_refused(self, context):
        """The failure it prevents is a figure a hundred million times too large."""
        price = Quantity.of("446.95", Unit.currency("USD"), source=SOURCE)
        shares = Quantity.of(Decimal(1_000_000), Unit.base("shares"), source=SOURCE)

        with pytest.raises(UnitMismatchError):
            market_capitalisation(context, price=price, shares=shares)

    def test_a_share_count_that_is_not_shares_is_refused(self, context):
        price = Quantity.of("446.95", Unit.currency("USD") / Unit.base("shares"), source=SOURCE)

        with pytest.raises(UnitMismatchError):
            market_capitalisation(
                context, price=price, shares=Quantity.of(Decimal(1000), source=SOURCE)
            )

    def test_a_nil_share_count_is_refused(self, context):
        price = Quantity.of("446.95", Unit.currency("USD") / Unit.base("shares"), source=SOURCE)
        shares = Quantity.of(Decimal(0), Unit.base("shares"), source=SOURCE)

        with pytest.raises(CalculationError):
            market_capitalisation(context, price=price, shares=shares)


# -- Returns and beta --------------------------------------------------------------------------


class TestResampling:
    def test_daily_passes_through(self):
        series = adjusted_series(straight_line(days=10), currency="USD", as_of=AS_OF)
        assert resample(series, frequency=Frequency.DAILY) == series.bars

    def test_monthly_takes_the_last_bar_of_each_month(self):
        bars = [
            bar(date(2024, 1, 30), "100"),
            bar(date(2024, 1, 31), "101"),
            bar(date(2024, 2, 28), "102"),
            bar(date(2024, 2, 29), "103"),
        ]
        series = adjusted_series(bars, currency="USD", as_of=AS_OF)

        sampled = resample(series, frequency=Frequency.MONTHLY)
        assert [b.on for b in sampled] == [date(2024, 1, 31), date(2024, 2, 29)]

    def test_weekly_takes_the_last_bar_of_each_iso_week(self):
        bars = [
            bar(date(2024, 1, 4), "100"),
            bar(date(2024, 1, 5), "101"),
            bar(date(2024, 1, 11), "102"),
        ]
        series = adjusted_series(bars, currency="USD", as_of=AS_OF)

        sampled = resample(series, frequency=Frequency.WEEKLY)
        assert [b.on for b in sampled] == [date(2024, 1, 5), date(2024, 1, 11)]


class TestAlignment:
    def test_returns_are_paired_by_date_not_by_position(self):
        """A London holiday the US does not keep, in the middle of the window."""
        subject = [(date(2024, 1, 1) + timedelta(days=i), pure(i)) for i in range(30)]
        market = [(when, value) for when, value in subject if when != date(2024, 1, 15)]

        paired_subject, paired_market = aligned_returns(subject, market)

        assert len(paired_subject) == len(market)
        assert all(a.value == b.value for a, b in zip(paired_subject, paired_market, strict=True))

    def test_too_little_overlap_is_refused(self):
        subject = [(date(2024, 1, 1) + timedelta(days=i), pure(i)) for i in range(30)]
        market = subject[:5]

        with pytest.raises(InsufficientHistoryError) as excinfo:
            aligned_returns(subject, market)
        assert str(MIN_RETURN_OBSERVATIONS) in str(excinfo.value)

    def test_two_series_with_no_shared_dates_are_refused(self):
        subject = [(date(2024, 1, 1) + timedelta(days=i), pure(i)) for i in range(30)]
        market = [(date(2025, 1, 1) + timedelta(days=i), pure(i)) for i in range(30)]

        with pytest.raises(InsufficientHistoryError):
            aligned_returns(subject, market)


class TestVarianceAndCovariance:
    def test_variance_is_the_sample_one(self, context):
        observations = [pure(2), pure(4), pure(4), pure(4), pure(5)]
        result = variance(context, observations=observations)

        # Mean 3.8; squared deviations 3.24 + 0.04 + 0.04 + 0.04 + 1.44 = 4.8; over n-1 = 4.
        assert result.value == Decimal("1.2")

    def test_a_covariance_of_a_series_with_itself_is_its_variance(self, context):
        observations = [pure(2), pure(4), pure(4), pure(4), pure(5)]

        joint = covariance(context, subject=observations, market=observations)
        alone = variance(context, observations=observations)

        assert joint.value == alone.value

    def test_series_of_different_lengths_are_refused(self, context):
        with pytest.raises(InsufficientHistoryError):
            covariance(
                context,
                subject=[pure(1), pure(2), pure(3)],
                market=[pure(1), pure(2)],
            )

    def test_one_observation_has_no_variance(self, context):
        with pytest.raises(InsufficientHistoryError):
            variance(context, observations=[pure(1)])


class TestBeta:
    def test_a_series_that_moves_exactly_with_the_market_has_a_beta_of_one(self, context):
        observations = [pure(Decimal(i) / Decimal(100)) for i in range(1, 31)]
        joint = covariance(context, subject=observations, market=observations)
        spread = variance(context, observations=observations)

        result = beta(
            context,
            subject_market_covariance=joint,
            market_variance=spread,
            frequency=Frequency.MONTHLY,
            observations=len(observations),
        )
        assert result.value == Decimal(1)

    def test_a_series_that_moves_twice_as_much_has_a_beta_of_two(self, context):
        market = [pure(Decimal(i) / Decimal(100)) for i in range(1, 31)]
        subject = [pure(value.value * 2) for value in market]

        result = beta(
            context,
            subject_market_covariance=covariance(context, subject=subject, market=market),
            market_variance=variance(context, observations=market),
            frequency=Frequency.MONTHLY,
            observations=len(market),
        )
        assert result.value == Decimal(2)

    def test_too_few_observations_are_refused(self, context):
        market = [pure(Decimal(i) / Decimal(100)) for i in range(1, 6)]

        with pytest.raises(InsufficientHistoryError):
            beta(
                context,
                subject_market_covariance=covariance(context, subject=market, market=market),
                market_variance=variance(context, observations=market),
                frequency=Frequency.MONTHLY,
                observations=len(market),
            )

    def test_a_market_that_did_not_move_has_no_beta(self, context):
        flat = [pure(0)] * 30

        with pytest.raises(CalculationError):
            beta(
                context,
                subject_market_covariance=covariance(context, subject=flat, market=flat),
                market_variance=variance(context, observations=flat),
                frequency=Frequency.MONTHLY,
                observations=len(flat),
            )

    def test_a_free_text_frequency_is_refused(self, context):
        """Typed callers cannot do this; untyped ones can, and the record would be a string."""
        market = [pure(Decimal(i) / Decimal(100)) for i in range(1, 31)]

        with pytest.raises(CalculationError):
            beta(
                context,
                subject_market_covariance=covariance(context, subject=market, market=market),
                market_variance=variance(context, observations=market),
                frequency="5y monthly",  # type: ignore[arg-type]
                observations=len(market),
            )

    def test_the_window_reaches_the_record(self, context):
        """A beta quoted without its window is not reproducible."""
        market = [pure(Decimal(i) / Decimal(100)) for i in range(1, 31)]
        beta(
            context,
            subject_market_covariance=covariance(context, subject=market, market=market),
            market_variance=variance(context, observations=market),
            frequency=Frequency.WEEKLY,
            observations=len(market),
        )

        record = next(r for r in context.records if r.name == "beta")
        assert record.parameters["frequency"] is Frequency.WEEKLY
        assert record.parameters["observations"] == len(market)


class TestTotalReturn:
    def test_it_is_the_ratio_less_one(self, context):
        with decimal_context(CALC_CONTEXT):
            start = Quantity.of("100", Unit.currency("USD"), source=SOURCE)
            end = Quantity.of("125", Unit.currency("USD"), source=SOURCE)

            assert total_return(context, start=start, end=end).value == Decimal("0.25")

    def test_two_different_units_are_refused(self, context):
        start = Quantity.of("100", Unit.currency("USD"), source=SOURCE)
        end = Quantity.of("125", Unit.currency("GBP"), source=SOURCE)

        with pytest.raises(UnitMismatchError):
            total_return(context, start=start, end=end)

    def test_a_nil_start_is_refused(self, context):
        start = Quantity.of("0", Unit.currency("USD"), source=SOURCE)
        end = Quantity.of("125", Unit.currency("USD"), source=SOURCE)

        with pytest.raises(CalculationError):
            total_return(context, start=start, end=end)


def factor_for(context, bar_date, splits):
    """The split factor for a bar, with each ratio sourced to its corporate action."""
    return cumulative_split_factor(
        context,
        ratios=[
            Quantity.of(
                split.ratio, DIMENSIONLESS, source=SourceRef.fact(split.ex_date.isoformat())
            )
            for split in ratios_after(bar_date, splits)
        ],
    )


class TestTheSplitFactorIsRecorded:
    def test_it_is_the_product_of_every_later_split(self, context):
        splits = [
            SplitAction(ex_date=date(2024, 3, 1), ratio=Decimal(2)),
            SplitAction(ex_date=date(2024, 5, 1), ratio=Decimal(3)),
        ]

        assert factor_for(context, date(2024, 1, 1), splits).value == Decimal(6)

    def test_a_split_before_the_bar_does_not_count(self, context):
        splits = [SplitAction(ex_date=date(2024, 3, 1), ratio=Decimal(2))]

        assert factor_for(context, date(2024, 4, 1), splits).value == Decimal(1)

    def test_a_split_on_the_bar_date_does_not_count(self, context):
        """The bar on the ex-date has already stepped; adjusting it would double-count."""
        splits = [SplitAction(ex_date=date(2024, 3, 1), ratio=Decimal(2))]

        assert factor_for(context, date(2024, 3, 1), splits).value == Decimal(1)

    def test_each_ratio_is_its_own_recorded_input(self, context):
        """So the answer names the split rather than merely stating the factor."""
        splits = [
            SplitAction(ex_date=date(2024, 3, 1), ratio=Decimal(2)),
            SplitAction(ex_date=date(2024, 5, 1), ratio=Decimal(3)),
        ]
        factor_for(context, date(2024, 1, 1), splits)

        record = next(r for r in context.records if r.name == "cumulative_split_factor")
        assert [item.source_id for item in record.inputs] == ["2024-03-01", "2024-05-01"]

    def test_a_ratio_with_a_unit_is_refused(self, context):
        with pytest.raises(UnitMismatchError):
            cumulative_split_factor(
                context, ratios=[Quantity.of(2, Unit.currency("USD"), source=SOURCE)]
            )

    def test_the_factor_explains_the_adjusted_price(self, context):
        """ "Why is the 2019 close half what the exchange printed?" is one recorded row."""
        bars = [bar(date(2024, 1, 1), "100"), bar(date(2024, 3, 1), "50")]
        splits = [SplitAction(ex_date=date(2024, 3, 1), ratio=Decimal(2))]
        series = adjusted_series(bars, splits=splits, currency="USD", as_of=AS_OF)

        assert series.bars[0].split_factor == factor_for(context, date(2024, 1, 1), splits).value
