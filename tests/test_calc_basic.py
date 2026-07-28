"""The first six calculations, against answers computed by hand.

Known-answer tests, not round-trips. A test that checks ``cagr`` against a second
implementation of ``cagr`` proves the two agree, which is worth very little when both were
written by the same person on the same afternoon. The expected values below were worked out
independently and are stated as literals.

The guard cases matter as much as the answers. Every one of them is an input for which no
meaningful figure exists, and every one raises rather than returning zero — because a zero
here flows into the next calculation and eventually into a report, where nobody can tell it
apart from a real result.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aer.calc.basic import (
    as_percent,
    cagr,
    growth_rate,
    margin,
    periods_between,
    ratio,
    weighted_average,
    yoy_series,
)
from aer.calc.engine import CalculationContext
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceRef,
    UnitMismatchError,
    money,
    shares,
)
from aer.calc.units import ratio as pure

SOURCE = SourceRef.fact("fact-1")


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def usd(value):
    return money(value, "USD", source=SOURCE)


def pure_sourced(value):
    return pure(value, source=SOURCE)


class TestGrowthRate:
    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (100, 110, "0.1"),
            (100, 90, "-0.1"),
            (100, 200, "1"),
            (100, 0, "-1"),
            (200, 50, "-0.75"),
            (50, 200, "3"),
        ],
        ids=["up-10pc", "down-10pc", "doubled", "wiped-out", "down-75pc", "quadrupled"],
    )
    def test_hand_computed_answers(self, context, start, end, expected):
        assert growth_rate(context, start=usd(start), end=usd(end)).value == Decimal(expected)

    def test_a_negative_base_uses_its_absolute_value(self):
        # A loss narrowing from -100 to -50 is an improvement. Dividing by the signed base
        # gives -50%, which describes the opposite of what happened.
        result = growth_rate(CalculationContext(), start=usd(-100), end=usd(-50))

        assert result.value == Decimal("0.5")

    def test_a_zero_base_raises(self, context):
        # Every increase from zero is infinite. Returning a large number here would put a
        # fabricated figure into a report.
        with pytest.raises(CalculationError, match="base of zero"):
            growth_rate(context, start=usd(0), end=usd(100))

    def test_different_units_raise(self, context):
        with pytest.raises(UnitMismatchError):
            growth_rate(context, start=usd(100), end=shares(100, source=SOURCE))

    def test_the_result_is_dimensionless(self, context):
        assert growth_rate(context, start=usd(100), end=usd(110)).unit.is_dimensionless


class TestCagr:
    @pytest.mark.parametrize(
        ("start", "end", "years", "expected"),
        [
            (100, 100, 5, "0"),
            (100, 200, 1, "1"),
            (100, 121, 2, "0.1"),
            (100, 133.1, 3, "0.1"),
            (100, 800, 3, "1"),
            (100, 50, 1, "-0.5"),
            (200, 100, 2, "-0.2928932188134524755991556378951510"),
        ],
        ids=[
            "flat",
            "doubled-in-one-year",
            "ten-pc-over-two",
            "ten-pc-over-three",
            "doubling-each-year",
            "halved-in-one-year",
            "halved-over-two",
        ],
    )
    def test_hand_computed_answers(self, context, start, end, years, expected):
        result = cagr(context, start=usd(str(start)), end=usd(str(end)), years=years)

        assert result.value == pytest.approx(Decimal(expected), abs=Decimal("1e-25"))

    def test_a_real_five_year_figure(self, context):
        # Microsoft's reported revenue, FY2017 to FY2022: 89.95bn to 198.27bn over five
        # years. Worked out separately: (198.27/89.95)^(1/5) - 1 = 0.17125...
        result = cagr(
            context,
            start=usd("89950000000"),
            end=usd("198270000000"),
            years=5,
        )

        assert as_percent(result) == Decimal("17.13")

    def test_the_result_is_dimensionless(self, context):
        assert cagr(context, start=usd(100), end=usd(200), years=1).unit.is_dimensionless

    @pytest.mark.parametrize("years", [0, -1])
    def test_a_non_positive_number_of_years_raises(self, context, years):
        with pytest.raises(CalculationError, match="at least one compounding period"):
            cagr(context, start=usd(100), end=usd(200), years=years)

    def test_a_zero_start_raises(self, context):
        with pytest.raises(CalculationError, match="undefined"):
            cagr(context, start=usd(0), end=usd(100), years=3)

    @pytest.mark.parametrize(
        ("start", "end"),
        [(-50, 100), (100, -50), (-100, -50)],
        ids=["negative-to-positive", "positive-to-negative", "both-negative"],
    )
    def test_a_sign_change_raises(self, context, start, end):
        # There is no constant percentage that takes -50 to +100. Any number returned here
        # would be an artefact of the arithmetic rather than a fact about the business.
        with pytest.raises(CalculationError, match="undefined"):
            cagr(context, start=usd(start), end=usd(end), years=2)

    def test_different_units_raise(self, context):
        with pytest.raises(UnitMismatchError, match="Cannot compound"):
            cagr(context, start=usd(100), end=shares(200, source=SOURCE), years=2)


class TestRatio:
    def test_like_over_like_is_dimensionless(self, context):
        current_ratio = ratio(context, numerator=usd(200), denominator=usd(100))

        assert current_ratio.value == Decimal(2)
        assert current_ratio.unit.is_dimensionless

    def test_unlike_over_unlike_keeps_both_units(self, context):
        # Earnings per share is USD/shares, and nothing downstream can then add it to a
        # plain dollar figure by accident.
        eps = ratio(context, numerator=usd(100), denominator=shares(4, source=SOURCE))

        assert eps.value == Decimal(25)
        assert eps.unit.symbol == "USD/shares"

    def test_a_zero_denominator_raises(self, context):
        with pytest.raises(CalculationError, match="Division by zero"):
            ratio(context, numerator=usd(1), denominator=usd(0))


class TestMargin:
    @pytest.mark.parametrize(
        ("part", "whole", "expected"),
        [(30, 100, "0.3"), (44281, 143015, "0.309624864524700206272069363353"), (-10, 100, "-0.1")],
        ids=["thirty-pc", "microsoft-fy2020-net-margin", "loss-making"],
    )
    def test_hand_computed_answers(self, context, part, whole, expected):
        result = margin(context, part=usd(part), whole=usd(whole))

        assert result.value == pytest.approx(Decimal(expected), abs=Decimal("1e-25"))

    def test_mixed_currencies_raise(self, context):
        # A gross margin from revenue in dollars and cost in pounds would quietly produce
        # USD/GBP, which is not a margin and is not anything.
        with pytest.raises(UnitMismatchError, match="is not a margin"):
            margin(context, part=usd(30), whole=money(100, "GBP", source=SOURCE))

    def test_a_zero_whole_raises(self, context):
        with pytest.raises(CalculationError, match="base of zero"):
            margin(context, part=usd(30), whole=usd(0))


class TestWeightedAverage:
    def test_a_hand_computed_answer(self, context):
        # (10*1 + 20*3) / (1+3) = 70/4 = 17.5
        result = weighted_average(
            context,
            values=[usd(10), usd(20)],
            weights=[pure_sourced(1), pure_sourced(3)],
        )

        assert result.value == Decimal("17.5")

    def test_the_result_keeps_the_value_unit(self, context):
        # Weights cancel: (USD·pure)/pure is USD.
        result = weighted_average(
            context, values=[usd(10), usd(20)], weights=[pure_sourced(1), pure_sourced(1)]
        )

        assert result.unit.symbol == "USD"

    def test_weights_need_not_sum_to_one(self, context):
        # Raw market capitalisations can be passed directly; they are normalised by their
        # own total.
        result = weighted_average(
            context,
            values=[usd(10), usd(20)],
            weights=[usd(1000000), usd(3000000)],
        )

        assert result.value == Decimal("17.5")

    def test_mismatched_lengths_raise(self, context):
        # Guessing which list is short would silently drop a company from a peer average.
        with pytest.raises(CalculationError, match="one weight per value"):
            weighted_average(context, values=[usd(10), usd(20)], weights=[pure_sourced(1)])

    def test_an_empty_series_raises(self, context):
        with pytest.raises(CalculationError, match="undefined"):
            weighted_average(context, values=[], weights=[])

    def test_zero_weights_raise(self, context):
        with pytest.raises(CalculationError, match="sum to zero"):
            weighted_average(
                context, values=[usd(10), usd(20)], weights=[pure_sourced(0), pure_sourced(0)]
            )

    def test_mixed_value_units_raise(self, context):
        with pytest.raises(UnitMismatchError, match="Averaging across units"):
            weighted_average(
                context,
                values=[usd(10), money(20, "GBP", source=SOURCE)],
                weights=[pure_sourced(1), pure_sourced(1)],
            )

    def test_mixed_weight_units_raise(self, context):
        with pytest.raises(UnitMismatchError, match="commensurable"):
            weighted_average(
                context,
                values=[usd(10), usd(20)],
                weights=[usd(1), shares(1, source=SOURCE)],
            )


class TestYoySeries:
    def test_a_hand_computed_answer(self, context):
        # Steps: 100->110 = +10%, 110->121 = +10%. Mean = 10%.
        result = yoy_series(context, values=[usd(100), usd(110), usd(121)])

        assert result.value == Decimal("0.1")

    def test_the_mean_differs_from_the_cagr_on_a_volatile_series(self, context):
        # 100 -> 200 -> 100. The mean of +100% and -50% is +25%; the CAGR is 0%. The gap
        # is informative, which is why both exist rather than one standing in for the
        # other.
        values = [usd(100), usd(200), usd(100)]

        mean = yoy_series(context, values=values)
        compound = cagr(context, start=usd(100), end=usd(100), years=2)

        assert mean.value == Decimal("0.25")
        assert compound.value == Decimal(0)

    def test_the_result_is_dimensionless(self, context):
        assert yoy_series(context, values=[usd(100), usd(110)]).unit.is_dimensionless

    def test_a_single_observation_raises(self, context):
        with pytest.raises(CalculationError, match="at least two observations"):
            yoy_series(context, values=[usd(100)])

    def test_a_zero_observation_raises(self, context):
        with pytest.raises(CalculationError, match="is zero"):
            yoy_series(context, values=[usd(100), usd(0), usd(50)])

    def test_a_zero_in_the_final_position_is_fine(self, context):
        # Nothing divides by it: it is only ever a numerator.
        result = yoy_series(context, values=[usd(100), usd(0)])

        assert result.value == Decimal(-1)

    def test_a_unit_change_part_way_raises(self, context):
        with pytest.raises(UnitMismatchError, match="two series"):
            yoy_series(context, values=[usd(100), money(110, "GBP", source=SOURCE)])


class TestHelpers:
    @pytest.mark.parametrize(
        ("observations", "expected"), [(2, 1), (4, 3), (6, 5)], ids=["two", "four", "six"]
    )
    def test_periods_between_is_one_fewer_than_the_observations(self, observations, expected):
        # Four annual figures span three years. The off-by-one here produces a CAGR wrong
        # by roughly a third rather than obviously broken.
        assert periods_between(observations) == expected

    def test_zero_observations_raise(self):
        with pytest.raises(CalculationError, match="span no periods"):
            periods_between(0)

    def test_as_percent_scales_and_rounds(self):
        assert as_percent(Quantity.of("0.1712542")) == Decimal("17.13")

    def test_as_percent_rounds_half_to_even(self):
        # 17.125 goes to 17.12, not 17.13, because 2 is even. Banker's rounding, so that
        # rounding a column of percentages does not systematically inflate their total.
        assert as_percent(Quantity.of("0.17125")) == Decimal("17.12")
        assert as_percent(Quantity.of("0.17135")) == Decimal("17.14")

    def test_as_percent_refuses_a_dimensioned_quantity(self):
        # A revenue has no percentage form.
        with pytest.raises(UnitMismatchError, match="not a rate"):
            as_percent(usd(100))
