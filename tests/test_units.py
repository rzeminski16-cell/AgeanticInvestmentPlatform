"""Unit arithmetic: what composes, what refuses, and what never coerces.

The acceptance criterion for this file is that a unit error is **impossible to ignore**.
Every case here is an operation that would produce a plausible-looking wrong number if the
units were dropped, and every one of them raises instead.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aer.calc.units import (
    DIMENSIONLESS,
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    Unit,
    UnitMismatchError,
    UnsourcedValueError,
    money,
    ratio,
    shares,
)

USD = Unit.currency("USD")
GBP = Unit.currency("GBP")
SHARES = Unit.base("shares")
SOURCE = SourceRef.fact("fact-1")


class TestUnitAlgebra:
    def test_like_over_like_is_dimensionless(self):
        # A margin is a pure number. If this were USD/USD as a string, nothing downstream
        # could tell it apart from a dollar amount.
        assert (USD / USD).is_dimensionless
        assert USD / USD == DIMENSIONLESS

    def test_unlike_over_unlike_composes(self):
        assert (USD / SHARES).symbol == "USD/shares"

    def test_dimensionless_times_a_currency_is_that_currency(self):
        # A growth rate times a revenue is a revenue.
        assert DIMENSIONLESS * USD == USD

    def test_multiplication_adds_exponents(self):
        assert (USD * USD).symbol == "USD^2"

    def test_cancellation_produces_equality_not_merely_similar_behaviour(self):
        # Exponents of zero are dropped at construction, so this compares equal to USD
        # rather than being a different unit that happens to behave the same.
        assert USD * SHARES / SHARES == USD

    def test_a_negative_exponent_renders_as_a_denominator(self):
        assert (SHARES**-1).symbol == "1/shares"

    def test_units_are_hashable(self):
        # Needed so a unit can key a dictionary of conversion rates.
        assert len({USD, USD, GBP}) == 2


class TestUnitParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("USD", "USD"),
            ("GBP", "GBP"),
            ("shares", "shares"),
            ("USD/shares", "USD/shares"),
            ("pure", "pure"),
            ("", "pure"),
        ],
    )
    def test_the_forms_edgar_uses_all_parse(self, text, expected):
        assert Unit.parse(text).symbol == expected

    @pytest.mark.parametrize(
        "unit",
        [USD, SHARES, USD / SHARES, DIMENSIONLESS, USD * USD, USD * SHARES, SHARES**-2],
        ids=["usd", "shares", "per-share", "pure", "squared", "product", "inverse-square"],
    )
    def test_a_symbol_round_trips(self, unit):
        # Load-bearing: a calculation's output unit is stored as text and read back to be
        # used as an input to the next one. A unit that renders but does not parse would
        # break exactly the compound and squared cases nobody checks by hand.
        assert Unit.parse(unit.symbol) == unit

    @pytest.mark.parametrize("text", ["dollars", "US$", "widgets", "£", "USD^x"])
    def test_an_unknown_symbol_is_refused(self, text):
        # A unit system that accepts any string is one in which a typo becomes a new
        # dimension, and two figures that should have been added silently do not match.
        with pytest.raises(UnitMismatchError):
            Unit.parse(text)

    def test_a_currency_code_is_case_insensitive(self):
        # Leniency without ambiguity: "usd" and "USD" resolve to the same dimension, so
        # accepting both cannot produce a mismatch. Anything that is not three letters
        # still fails.
        assert Unit.parse("usd") == USD


class TestQuantityConstruction:
    def test_a_float_is_refused(self):
        # The beginning of every rounding complaint anyone will ever make about this
        # platform. Refused at construction, where the fix is one word.
        with pytest.raises(CalculationError, match="must be a Decimal"):
            Quantity(0.1)  # type: ignore[arg-type]

    def test_an_int_or_string_converts_exactly(self):
        assert Quantity.of("0.1").value == Decimal("0.1")
        assert Quantity.of(3).value == Decimal(3)

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_value_is_refused(self, value):
        with pytest.raises(CalculationError, match="not a usable quantity"):
            Quantity(Decimal(value))

    def test_the_convenience_constructors_carry_their_units(self):
        assert money(5, "USD").unit == USD
        assert shares(5).unit == SHARES
        assert ratio(5).unit == DIMENSIONLESS


class TestArithmetic:
    def test_adding_different_currencies_raises(self):
        # The single most important refusal in the module.
        with pytest.raises(UnitMismatchError, match="Cannot add"):
            money(1, "USD") + money(1, "GBP")

    def test_the_currency_refusal_says_what_to_do_instead(self):
        with pytest.raises(UnitMismatchError, match="never convert implicitly"):
            money(1, "USD") + money(1, "GBP")

    def test_subtracting_different_units_raises(self):
        with pytest.raises(UnitMismatchError, match="Cannot subtract"):
            money(1, "USD") - shares(1)

    def test_adding_a_dimensionless_number_to_money_raises(self):
        # The subtle case: both are "just numbers" once the unit is dropped, and adding a
        # growth rate to a revenue would produce a figure nobody could spot as wrong.
        with pytest.raises(UnitMismatchError):
            money(1000, "USD") + ratio("0.15")

    def test_it_raises_with_the_operands_the_other_way_round_too(self):
        # `a + b` dispatches on `a`, so a guard phrased as "is *this* one just a number?"
        # refuses one order and waves the other through -- and only the refused order was
        # tested. A dimensionless left operand is the likely one in practice: a margin or a
        # growth rate is what an expression tends to start with.
        with pytest.raises(UnitMismatchError):
            ratio("0.15") + money(1000, "USD")

    def test_subtraction_refuses_in_either_order(self):
        with pytest.raises(UnitMismatchError):
            ratio("0.15") - money(1000, "USD")
        with pytest.raises(UnitMismatchError):
            money(1000, "USD") - ratio("0.15")

    def test_a_dimensionless_left_operand_does_not_absorb_shares_either(self):
        # Not only currencies: `pure` must not act as a wildcard for any dimension.
        with pytest.raises(UnitMismatchError):
            ratio(3) + shares(4)

    def test_addition_of_matching_units_works(self):
        assert (money(2, "USD") + money(3, "USD")) == money(5, "USD")

    def test_dividing_money_by_shares_gives_per_share(self):
        eps = money(100, "USD") / shares(4)

        assert eps.unit.symbol == "USD/shares"
        assert eps.value == Decimal(25)

    def test_dividing_like_by_like_gives_a_pure_number(self):
        assert (money(1, "USD") / money(4, "USD")).unit.is_dimensionless

    def test_multiplying_a_rate_by_money_gives_money(self):
        assert (ratio("0.1") * money(100, "USD")).unit == USD

    def test_division_by_zero_raises_rather_than_returning_infinity(self):
        # Decimal returns Infinity by default, which propagates silently through every
        # subsequent step to produce a report nobody can explain.
        with pytest.raises(CalculationError, match="Division by zero"):
            money(1, "USD") / money(0, "USD")

    def test_decimal_precision_is_not_lost_to_binary_floating_point(self):
        total = ratio("0.1") + ratio("0.2")

        assert total.value == Decimal("0.3")

    def test_negation_and_absolute_value_keep_the_unit(self):
        assert (-money(5, "USD")).unit == USD
        assert abs(money(-5, "USD")) == money(5, "USD")


class TestPowers:
    def test_an_integer_power_scales_the_unit(self):
        assert money(3, "USD").power(2).unit.symbol == "USD^2"

    def test_a_fractional_power_of_a_dimensionless_number_is_allowed(self):
        # What a CAGR's ^(1/n) needs.
        result = ratio(4).power(Decimal("0.5"))

        assert result.value == Decimal(2)
        assert result.unit.is_dimensionless

    def test_a_fractional_power_of_a_dimensioned_quantity_raises(self):
        # There is no such unit as USD^1.5.
        with pytest.raises(UnitMismatchError, match="fractional power"):
            money(4, "USD").power(Decimal("0.5"))

    def test_a_fractional_power_of_a_negative_number_raises(self):
        with pytest.raises(CalculationError, match="not real"):
            ratio(-4).power(Decimal("0.5"))


class TestCurrencyConversion:
    def test_conversion_needs_a_sourced_rate(self):
        # An FX rate nobody can point at is an assumption pretending to be a fact, and
        # every figure converted with it inherits that.
        unsourced = Quantity.of("0.79", GBP / USD)

        with pytest.raises(UnsourcedValueError, match="must carry a source"):
            money(100, "USD").convert(GBP, rate=unsourced)

    def test_conversion_with_a_sourced_rate_works(self):
        rate = Quantity.of("0.79", GBP / USD, source=SourceRef.assumption("fx-2026-06"))

        converted = money(100, "USD").convert(GBP, rate=rate)

        assert converted.unit == GBP
        assert converted.value == Decimal(79)

    def test_an_upside_down_rate_is_refused(self):
        # The mistake that produces a number roughly right in magnitude and wrong by the
        # square of the rate — which is exactly the kind that survives a sanity check.
        wrong_way = Quantity.of("1.27", USD / GBP, source=SourceRef.assumption("fx"))

        with pytest.raises(UnitMismatchError, match="wrong way up"):
            money(100, "USD").convert(GBP, rate=wrong_way)

    def test_a_rate_for_the_wrong_pair_is_refused(self):
        eur = Unit.currency("EUR")
        eur_rate = Quantity.of("0.85", eur / USD, source=SourceRef.assumption("fx"))

        with pytest.raises(UnitMismatchError):
            money(100, "USD").convert(GBP, rate=eur_rate)


class TestSourceRefs:
    def test_a_quantity_can_be_attributed_after_construction(self):
        attributed = money(5, "USD").with_source(SOURCE)

        assert attributed.source == SOURCE
        assert attributed.value == Decimal(5)

    def test_the_three_kinds_construct(self):
        assert SourceRef.fact("a").kind is SourceKind.FACT
        assert SourceRef.calculation("b").kind is SourceKind.CALCULATION
        assert SourceRef.assumption("c").kind is SourceKind.ASSUMPTION

    def test_a_source_ref_reads_legibly(self):
        assert str(SourceRef.fact("abc")) == "fact:abc"

    def test_arithmetic_does_not_carry_a_source_forward(self):
        # A derived value is not the thing it was derived from. Carrying the source
        # forward would let a computed figure claim to be a reported one -- and the
        # engine's whole job is to attribute derived values to their own record instead.
        total = money(2, "USD", source=SOURCE) + money(3, "USD", source=SOURCE)

        assert total.source is None


class TestEquality:
    def test_provenance_is_not_identity(self):
        # Two quantities of $5 are the same quantity whether one came from a filing and
        # the other from an assumption. Where they came from decides whether a claim is
        # defensible, not whether the numbers are equal.
        from_fact = money(5, "USD", source=SourceRef.fact("a"))
        from_assumption = money(5, "USD", source=SourceRef.assumption("b"))

        assert from_fact == from_assumption

    def test_the_same_number_in_different_units_is_not_equal(self):
        assert money(5, "USD") != money(5, "GBP")
        assert money(5, "USD") != ratio(5)

    def test_comparison_across_units_raises(self):
        with pytest.raises(UnitMismatchError, match="compare"):
            _ = money(5, "USD") < money(5, "GBP")

    def test_comparing_a_plain_number_with_money_raises_either_way(self):
        # `_require_same_unit` guards the comparisons as well as the sums, so it has to be
        # symmetric here for the same reason.
        with pytest.raises(UnitMismatchError, match="compare"):
            _ = ratio(5) < money(5, "USD")
        with pytest.raises(UnitMismatchError, match="compare"):
            _ = money(5, "USD") < ratio(5)

    def test_comparison_within_a_unit_works(self):
        assert money(4, "USD") < money(5, "USD")
        assert money(5, "USD") >= money(5, "USD")


class TestPresentation:
    def test_rounding_is_explicit_and_keeps_the_unit(self):
        rounded = money("1234.5678", "USD").round_to(2)

        assert rounded.value == Decimal("1234.57")
        assert rounded.unit == USD

    def test_rounding_is_banker_s_rounding(self):
        # ROUND_HALF_EVEN. Chosen so that rounding a column of figures does not
        # systematically inflate their total, which ROUND_HALF_UP does.
        assert ratio("0.125").round_to(2).value == Decimal("0.12")
        assert ratio("0.135").round_to(2).value == Decimal("0.14")

    def test_rounding_preserves_the_source(self):
        assert money(1, "USD", source=SOURCE).round_to(2).source == SOURCE

    def test_a_quantity_reads_legibly(self):
        assert str(money("1234.50", "USD")) == "1234.50 USD"
