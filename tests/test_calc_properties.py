"""Property-based tests: invariants that must hold for *every* input, not five of them.

Known-answer tests check the cases somebody thought of. These check the ones nobody did.
Each property below is a statement about the arithmetic that is true by definition, so a
counterexample is unambiguously a bug rather than a disagreement about expected values —
which is what makes hypothesis worth its cost in a numerical module.

Two conventions here are load-bearing:

* **Decimals are built from integers and scaled**, never sampled as floats. Drawing a
  float and converting it would test what ``Decimal(float)`` does to binary rounding,
  which is not the subject.
* **Tolerances are absolute wherever the result is a rate.** A growth rate lives in
  roughly [-1, ∞), so an absolute bound of 1e-20 is meaningful and catches drift in the
  last places — the class of error the 34-digit context exists to prevent. The two
  reconstruction properties use a relative bound instead, because reconstructing a
  *value* spanning ten orders of magnitude scales the last-place error with the value
  itself; each says so where it is used.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from aer.calc.basic import cagr, growth_rate, margin, ratio, weighted_average, yoy_series
from aer.calc.engine import CalculationContext
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    money,
)
from aer.calc.units import ratio as pure

SOURCE = SourceRef.fact("property-test")

# Money in a plausible range, exact to the cent. Built from an integer of pennies rather
# than sampled as a float, so nothing is testing binary rounding by accident.
positive_money = st.integers(min_value=1, max_value=10**15).map(
    lambda pennies: money(Decimal(pennies).scaleb(-2), "USD", source=SOURCE)
)
any_money = st.integers(min_value=-(10**15), max_value=10**15).map(
    lambda pennies: money(Decimal(pennies).scaleb(-2), "USD", source=SOURCE)
)
years = st.integers(min_value=1, max_value=40)
scale_factors = st.integers(min_value=1, max_value=10**6)

TOLERANCE = Decimal("1e-20")


def context() -> CalculationContext:
    return CalculationContext(code_version="property")


def close(left: Decimal, right: Decimal, tolerance: Decimal = TOLERANCE) -> bool:
    return abs(left - right) <= tolerance


def scaled(quantity: Quantity, factor: int) -> Quantity:
    """Multiply a quantity and re-attribute it to the same source.

    The re-attribution is necessary, and it demonstrates the design rather than working
    around it: arithmetic on quantities deliberately does *not* carry a source forward,
    because a derived value is not the thing it was derived from. Inside a traced call the
    engine attributes the result to its own record; out here, building test inputs by hand,
    the attribution has to be explicit — and a test that forgot it would be refused, which
    is exactly the behaviour under test elsewhere.
    """
    return (quantity * Quantity.of(factor, Unit())).with_source(SOURCE)


class TestCagrProperties:
    @given(start=positive_money, n=years)
    def test_no_change_is_zero_growth(self, start, n):
        # cagr(x, x, n) == 0 for every x and every n. True by definition, and the first
        # thing a wrong exponent breaks.
        result = cagr(context(), start=start, end=start, years=n)

        assert close(result.value, Decimal(0))

    @given(start=positive_money, end=positive_money, n=years, k=scale_factors)
    def test_scaling_both_endpoints_leaves_the_rate_unchanged(self, start, end, n, k):
        # A CAGR is a ratio of endpoints, so the units of measure cancel: revenue in
        # dollars and the same revenue in thousands must give the same rate. A wrong
        # implementation that subtracted rather than divided would fail this immediately.
        plain = cagr(context(), start=start, end=end, years=n)
        rescaled = cagr(context(), start=scaled(start, k), end=scaled(end, k), years=n)

        assert close(plain.value, rescaled.value)

    @given(start=positive_money, n=years, lower=st.integers(1, 10**9), delta=st.integers(1, 10**9))
    def test_the_rate_increases_with_the_ending_value(self, start, n, lower, delta):
        # Monotonic in `end`. A sign error or an inverted ratio breaks this.
        smaller = money(Decimal(lower), "USD", source=SOURCE)
        larger = money(Decimal(lower + delta), "USD", source=SOURCE)

        low = cagr(context(), start=start, end=smaller, years=n)
        high = cagr(context(), start=start, end=larger, years=n)

        assert high.value > low.value

    @given(start=positive_money, n=years)
    def test_growth_is_positive_exactly_when_the_end_exceeds_the_start(self, start, n):
        doubled = scaled(start, 2)
        halved = (start / Quantity.of(2, Unit())).with_source(SOURCE)
        assume(halved.value > 0)

        assert cagr(context(), start=start, end=doubled, years=n).value > 0
        assert cagr(context(), start=start, end=halved, years=n).value < 0

    @given(start=positive_money, end=positive_money)
    def test_over_one_year_the_cagr_equals_the_simple_growth_rate(self, start, end):
        # Compounding over a single period is not compounding. The two functions are
        # independent implementations, so agreeing here is a real cross-check.
        compound = cagr(context(), start=start, end=end, years=1)
        simple = growth_rate(context(), start=start, end=end)

        assert close(compound.value, simple.value)

    @given(start=positive_money, end=positive_money, n=years)
    def test_the_result_is_always_dimensionless(self, start, end, n):
        assert cagr(context(), start=start, end=end, years=n).unit.is_dimensionless

    @given(start=positive_money, end=positive_money, n=years)
    def test_compounding_the_rate_reproduces_the_endpoint(self, start, end, n):
        # The definitional round trip: start * (1 + r)^n == end. This is what the formula
        # string claims, checked against what the code does.
        rate = cagr(context(), start=start, end=end, years=n)
        one = Quantity.of(1, Unit())
        reconstructed = start * (one + Quantity(rate.value)).power(n)

        # Relative, and loose enough to survive the worst case in the input space: an
        # endpoint ratio spanning ten orders of magnitude passes through a fractional power
        # and back, which amplifies the last place. Twenty significant figures of agreement
        # is still far beyond anything a financial figure needs -- the property being
        # asserted is that the arithmetic is essentially exact, not that it hits a
        # particular bound.
        assert abs(reconstructed.value - end.value) <= abs(end.value) * Decimal("1e-20")


class TestGrowthRateProperties:
    @given(start=any_money)
    def test_no_change_is_zero_growth(self, start):
        assume(start.value != 0)

        assert growth_rate(context(), start=start, end=start).value == 0

    @given(start=positive_money, end=positive_money, k=scale_factors)
    def test_scaling_both_endpoints_leaves_the_rate_unchanged(self, start, end, k):
        plain = growth_rate(context(), start=start, end=end)
        rescaled = growth_rate(context(), start=scaled(start, k), end=scaled(end, k))

        assert close(plain.value, rescaled.value)

    @given(start=positive_money, end=positive_money)
    def test_growth_reconstructs_the_endpoint(self, start, end):
        rate = growth_rate(context(), start=start, end=end)
        reconstructed = start + start * Quantity(rate.value)

        assert close(reconstructed.value, end.value, Decimal("1e-15"))

    @given(start=any_money, end=any_money)
    def test_a_negative_base_still_reports_improvement_as_positive(self, start, end):
        # The absolute-value denominator. A loss narrowing is an improvement, whatever the
        # sign of the base.
        assume(start.value < 0)
        assume(end.value > start.value)

        assert growth_rate(context(), start=start, end=end).value > 0


class TestRatioAndMarginProperties:
    @given(numerator=positive_money, denominator=positive_money)
    def test_a_ratio_of_like_units_is_dimensionless(self, numerator, denominator):
        assert ratio(context(), numerator=numerator, denominator=denominator).unit.is_dimensionless

    @given(numerator=positive_money, denominator=positive_money, k=scale_factors)
    def test_a_ratio_is_scale_invariant(self, numerator, denominator, k):
        plain = ratio(context(), numerator=numerator, denominator=denominator)
        rescaled = ratio(
            context(),
            numerator=scaled(numerator, k),
            denominator=scaled(denominator, k),
        )

        assert close(plain.value, rescaled.value)

    @given(part=positive_money, whole=positive_money)
    def test_a_margin_is_bounded_by_one_exactly_when_the_part_is_smaller(self, part, whole):
        result = margin(context(), part=part, whole=whole)

        assert (result.value <= 1) == (part.value <= whole.value)

    @given(whole=positive_money)
    def test_the_whole_over_itself_is_one(self, whole):
        assert margin(context(), part=whole, whole=whole).value == 1


class TestWeightedAverageProperties:
    @given(values=st.lists(positive_money, min_size=1, max_size=8))
    def test_equal_weights_give_the_arithmetic_mean(self, values):
        weights = [pure(1, source=SOURCE) for _ in values]

        result = weighted_average(context(), values=values, weights=weights)
        expected = sum((v.value for v in values), Decimal(0)) / Decimal(len(values))

        assert close(result.value, expected, Decimal("1e-15"))

    @given(values=st.lists(positive_money, min_size=1, max_size=8), k=scale_factors)
    def test_scaling_every_weight_equally_changes_nothing(self, values, k):
        # Weights are normalised by their own total, so raw market capitalisations can be
        # passed directly rather than having to be turned into fractions first.
        ones = [pure(1, source=SOURCE) for _ in values]
        scaled = [pure(k, source=SOURCE) for _ in values]

        plain = weighted_average(context(), values=values, weights=ones)
        rescaled = weighted_average(context(), values=values, weights=scaled)

        assert close(plain.value, rescaled.value, Decimal("1e-15"))

    @given(values=st.lists(positive_money, min_size=1, max_size=8))
    def test_the_result_lies_between_the_smallest_and_largest_value(self, values):
        # A weighted average that escapes its own inputs is a weighted average with a sign
        # error in it.
        weights = [pure(1, source=SOURCE) for _ in values]

        result = weighted_average(context(), values=values, weights=weights)

        assert min(v.value for v in values) <= result.value <= max(v.value for v in values)

    @given(value=positive_money, weight=st.integers(1, 10**6))
    def test_a_single_value_averages_to_itself(self, value, weight):
        result = weighted_average(context(), values=[value], weights=[pure(weight, source=SOURCE)])

        assert close(result.value, value.value, Decimal("1e-15"))


class TestYoySeriesProperties:
    @given(value=positive_money, length=st.integers(min_value=2, max_value=10))
    def test_a_flat_series_has_zero_mean_growth(self, value, length):
        result = yoy_series(context(), values=[value] * length)

        assert close(result.value, Decimal(0))

    @given(base=st.integers(min_value=1, max_value=10**9), length=st.integers(2, 8))
    def test_a_constant_doubling_series_has_mean_growth_of_one(self, base, length):
        values = [money(Decimal(base * 2**index), "USD", source=SOURCE) for index in range(length)]

        result = yoy_series(context(), values=values)

        assert close(result.value, Decimal(1), Decimal("1e-25"))

    @given(values=st.lists(positive_money, min_size=2, max_size=8))
    def test_the_result_is_always_dimensionless(self, values):
        assert yoy_series(context(), values=values).unit.is_dimensionless


class TestUnitAlgebraProperties:
    currencies = st.sampled_from(["USD", "GBP", "EUR", "JPY", "CHF"])

    @given(code=currencies)
    def test_a_unit_divided_by_itself_is_dimensionless(self, code):
        unit = Unit.currency(code)

        assert (unit / unit).is_dimensionless

    @given(left=currencies, right=currencies)
    def test_multiplication_is_commutative(self, left, right):
        a, b = Unit.currency(left), Unit.currency(right)

        assert a * b == b * a

    @given(code=currencies, power=st.integers(min_value=-6, max_value=6))
    def test_a_symbol_always_round_trips_through_parse(self, code, power):
        # The property the database depends on: a calculation's output unit is stored as
        # text and read back to be used as an input to the next one.
        unit = Unit.currency(code) ** power

        assert Unit.parse(unit.symbol) == unit

    @given(left=currencies, right=currencies)
    def test_adding_different_currencies_always_raises(self, left, right):
        assume(left != right)

        with pytest.raises(CalculationError):
            money(1, left, source=SOURCE) + money(1, right, source=SOURCE)


class TestDecimalFidelity:
    @given(pennies=st.integers(min_value=-(10**18), max_value=10**18))
    def test_a_value_survives_construction_exactly(self, pennies):
        # Above 2^53 a float cannot represent consecutive integers. Revenue figures in raw
        # units pass that threshold routinely.
        exact = Decimal(pennies)

        assert Quantity(exact).value == exact

    @given(a=st.integers(1, 10**12), b=st.integers(1, 10**12))
    def test_addition_is_exact_for_integers(self, a, b):
        total = Quantity.of(a) + Quantity.of(b)

        assert total.value == Decimal(a + b)

    @given(a=st.integers(1, 10**12), b=st.integers(1, 10**12))
    def test_division_then_multiplication_returns_the_original(self, a, b):
        # Within the 34-digit context. A 28-digit default would fail this for the larger
        # operands, which is why the context is set explicitly.
        quotient = Quantity.of(a) / Quantity.of(b)
        restored = quotient * Quantity.of(b)

        assert abs(restored.value - Decimal(a)) <= Decimal(a) * Decimal("1e-30")


# The default of 100 examples per property is right for this module: the functions are
# microseconds each, and the whole file runs in a couple of seconds. Raised only where a
# property is cheap and the input space is large enough to be worth sampling harder.
settings.register_profile("calc", max_examples=200, deadline=None)
