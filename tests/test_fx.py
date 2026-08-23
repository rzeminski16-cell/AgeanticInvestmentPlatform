"""Currency conversion: the rate that is chosen, and what happens when none may be.

The round-trip figures below are exact. 1,000,000 USD at 0.79 GBP/USD is 790,000 GBP, and
790,000 GBP back through the inverse is 1,000,000 USD to within the stated tolerance — not
exactly, because the inverse of 0.79 does not terminate, which is why the tolerance is
stated rather than assumed.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from aer.calc.engine import CalculationContext
from aer.calc.fx import (
    MAX_STALENESS_DAYS,
    ROUND_TRIP_TOLERANCE,
    FxRate,
    LookAheadRateError,
    NoRateAvailableError,
    StaleRateError,
    convert,
    convert_at,
    invert,
    round_trips,
    select_rate,
)
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceRef,
    Unit,
    UnitMismatchError,
    UnsourcedValueError,
    money,
    shares,
)

SOURCE = SourceRef.macro_observation("boe-xudlgbd-2024-06-28")
AS_OF = date(2024, 6, 30)


@pytest.fixture
def context():
    return CalculationContext(code_version="testsha")


def rate_quantity(value: str, *, quote: str, base: str, source: SourceRef = SOURCE) -> Quantity:
    return Quantity.of(Decimal(value), Unit.currency(quote) / Unit.currency(base), source=source)


def gbp_per_usd(
    value: str = "0.79", *, on: date = date(2024, 6, 28), source: SourceRef = SOURCE
) -> FxRate:
    return FxRate(
        base="USD",
        quote="GBP",
        rate=rate_quantity(value, quote="GBP", base="USD", source=source),
        observed_on=on,
    )


def usd(value: str) -> Quantity:
    return money(value, "USD", source=SourceRef.financial_fact("fact-1"))


def gbp(value: str) -> Quantity:
    return money(value, "GBP", source=SourceRef.financial_fact("fact-1"))


class TestARateIsAnObservationWithAUnit:
    def test_it_knows_its_pair(self):
        assert gbp_per_usd().pair == ("USD", "GBP")

    def test_a_rate_with_no_source_is_refused(self):
        """Invariant 3 at the point it would be broken, not where it would be noticed."""
        unsourced = Quantity.of(Decimal("0.79"), Unit.parse("GBP/USD"))
        with pytest.raises(UnsourcedValueError):
            FxRate(base="USD", quote="GBP", rate=unsourced, observed_on=AS_OF)

    def test_a_rate_whose_unit_contradicts_its_pair_is_refused(self):
        """A USD/GBP quantity labelled as a USD-to-GBP rate is one that will be misapplied."""
        with pytest.raises(CalculationError, match="must be stated in GBP/USD"):
            FxRate(
                base="USD",
                quote="GBP",
                rate=rate_quantity("1.27", quote="USD", base="GBP"),
                observed_on=AS_OF,
            )

    def test_a_dimensionless_rate_is_refused(self):
        """A bare 0.79 is the mistake the whole unit system exists to catch."""
        bare = Quantity.of(Decimal("0.79"), Unit.parse("pure"), source=SOURCE)
        with pytest.raises(CalculationError):
            FxRate(base="USD", quote="GBP", rate=bare, observed_on=AS_OF)

    @pytest.mark.parametrize("value", ["0", "-0.79"])
    def test_a_non_positive_rate_is_a_parse_failure_not_an_observation(self, value):
        with pytest.raises(CalculationError, match="positive number"):
            FxRate(
                base="USD",
                quote="GBP",
                rate=rate_quantity(value, quote="GBP", base="USD"),
                observed_on=AS_OF,
            )


class TestChoosingTheRate:
    def test_the_most_recent_observation_on_or_before_the_as_of_date_wins(self):
        rates = [
            gbp_per_usd("0.77", on=date(2024, 6, 26)),
            gbp_per_usd("0.79", on=date(2024, 6, 28)),
            gbp_per_usd("0.78", on=date(2024, 6, 27)),
        ]
        chosen = select_rate(rates, base="USD", quote="GBP", as_of=AS_OF)

        assert chosen.observed_on == date(2024, 6, 28)
        assert chosen.rate.value == Decimal("0.79")

    def test_an_observation_on_the_as_of_date_itself_is_usable(self):
        """On the day is not after the day. An off-by-one here loses the freshest rate."""
        chosen = select_rate([gbp_per_usd(on=AS_OF)], base="USD", quote="GBP", as_of=AS_OF)
        assert chosen.observed_on == AS_OF

    def test_a_rate_after_the_as_of_date_is_refused_not_ranked_below(self):
        """The look-ahead case. A valuation may not convert at a rate nobody had."""
        rates = [gbp_per_usd("0.81", on=date(2024, 7, 15))]

        with pytest.raises(LookAheadRateError) as raised:
            select_rate(rates, base="USD", quote="GBP", as_of=AS_OF)

        assert raised.value.context["earliest_available"] == "2024-07-15"
        assert "nobody had" in str(raised.value)

    def test_a_later_rate_does_not_displace_an_earlier_usable_one(self):
        rates = [
            gbp_per_usd("0.79", on=date(2024, 6, 28)),
            gbp_per_usd("0.95", on=date(2024, 9, 30)),
        ]
        chosen = select_rate(rates, base="USD", quote="GBP", as_of=AS_OF)

        assert chosen.rate.value == Decimal("0.79")

    def test_a_rate_older_than_the_staleness_limit_is_refused(self):
        stale_on = AS_OF - timedelta(days=MAX_STALENESS_DAYS + 1)
        rates = [gbp_per_usd("0.79", on=stale_on)]

        with pytest.raises(StaleRateError) as raised:
            select_rate(rates, base="USD", quote="GBP", as_of=AS_OF)

        assert raised.value.context["staleness_days"] == MAX_STALENESS_DAYS + 1

    def test_a_rate_exactly_at_the_limit_is_still_usable(self):
        """The weekend-and-bank-holiday case the limit exists for."""
        on = AS_OF - timedelta(days=MAX_STALENESS_DAYS)
        chosen = select_rate([gbp_per_usd(on=on)], base="USD", quote="GBP", as_of=AS_OF)
        assert chosen.observed_on == on

    def test_the_staleness_limit_can_be_widened_deliberately(self):
        on = AS_OF - timedelta(days=30)
        chosen = select_rate(
            [gbp_per_usd(on=on)],
            base="USD",
            quote="GBP",
            as_of=AS_OF,
            max_staleness_days=45,
        )
        assert chosen.observed_on == on

    def test_rates_for_other_pairs_are_ignored_rather_than_confused(self):
        other = FxRate(
            base="EUR",
            quote="GBP",
            rate=rate_quantity("0.85", quote="GBP", base="EUR"),
            observed_on=date(2024, 6, 29),
        )
        chosen = select_rate(
            [other, gbp_per_usd(on=date(2024, 6, 28))],
            base="USD",
            quote="GBP",
            as_of=AS_OF,
        )
        assert chosen.pair == ("USD", "GBP")

    def test_no_rate_for_the_pair_at_all_says_so(self):
        with pytest.raises(NoRateAvailableError, match="No USD/GBP rate"):
            select_rate([], base="USD", quote="GBP", as_of=AS_OF)

    def test_the_choice_does_not_depend_on_the_order_they_arrived_in(self):
        """Two observations of one pair on one date is a disagreement, not a coin toss."""
        first = gbp_per_usd("0.79", source=SourceRef.financial_fact("aaa"))
        second = gbp_per_usd("0.81", source=SourceRef.financial_fact("bbb"))

        forwards = select_rate([first, second], base="USD", quote="GBP", as_of=AS_OF)
        backwards = select_rate([second, first], base="USD", quote="GBP", as_of=AS_OF)

        assert forwards.rate.value == backwards.rate.value


class TestConverting:
    def test_a_conversion_produces_the_currency_that_was_asked_for(self, context):
        converted = convert(context, amount=usd("1000000"), rate=gbp_per_usd().rate, into="GBP")

        assert converted.value == Decimal("790000")
        assert converted.unit == Unit.currency("GBP")

    def test_a_rate_applied_upside_down_raises(self, context):
        """The failure this module exists for: right magnitude, wrong by the square."""
        upside_down = rate_quantity("1.27", quote="USD", base="GBP")

        with pytest.raises(UnitMismatchError):
            convert(context, amount=usd("1000000"), rate=upside_down, into="GBP")

    def test_a_rate_for_the_wrong_pair_raises(self, context):
        eur_per_chf = rate_quantity("1.03", quote="EUR", base="CHF")

        with pytest.raises(UnitMismatchError):
            convert(context, amount=usd("1000000"), rate=eur_per_chf, into="GBP")

    def test_an_unsourced_rate_is_refused(self, context):
        unsourced = Quantity.of(Decimal("0.79"), Unit.parse("GBP/USD"))

        with pytest.raises(UnsourcedValueError):
            convert(context, amount=usd("1000000"), rate=unsourced, into="GBP")

    def test_converting_something_that_is_not_money_raises(self, context):
        """A share count is the same number in every currency."""
        with pytest.raises(CalculationError, match="not a currency"):
            convert(
                context,
                amount=shares("1000", source=SourceRef.financial_fact("f")),
                rate=gbp_per_usd().rate,
                into="GBP",
            )

    def test_a_conversion_is_a_recorded_calculation_not_an_inline_multiply(self, context):
        convert(context, amount=usd("1000000"), rate=gbp_per_usd().rate, into="GBP")

        record = next(r for r in context.records if r.name == "fx_convert")
        assert record.formula == "converted = amount * rate"

    def test_the_rate_is_one_of_the_recorded_inputs(self, context):
        """ "What rate did this use?" has to be answerable from the ledger, not the code."""
        convert(context, amount=usd("1000000"), rate=gbp_per_usd().rate, into="GBP")

        record = next(r for r in context.records if r.name == "fx_convert")
        assert SOURCE in record.input_sources

    def test_the_converted_figure_carries_its_calculation_as_its_source(self, context):
        converted = convert(context, amount=usd("1000000"), rate=gbp_per_usd().rate, into="GBP")

        assert converted.source is not None
        assert converted.source.kind == "calculation"


class TestConvertingAtASelectedRate:
    def test_it_takes_the_direction_from_the_observation(self, context):
        converted = convert_at(context, amount=usd("1000000"), rate=gbp_per_usd())

        assert converted.value == Decimal("790000")
        assert converted.unit == Unit.currency("GBP")

    def test_an_amount_in_the_wrong_currency_is_refused_here_rather_than_downstream(self, context):
        with pytest.raises(CalculationError, match="USD to GBP rate"):
            convert_at(context, amount=gbp("1000000"), rate=gbp_per_usd())

    def test_it_refuses_before_it_computes(self, context):
        with pytest.raises(CalculationError):
            convert_at(context, amount=gbp("1000000"), rate=gbp_per_usd())

        assert [record for record in context.records if record.name == "fx_convert"] == []


class TestInverting:
    def test_the_pair_turns_round(self):
        inverted = invert(gbp_per_usd())

        assert inverted.pair == ("GBP", "USD")
        assert inverted.rate.unit == Unit.parse("USD/GBP")

    def test_the_value_is_the_reciprocal(self):
        inverted = invert(gbp_per_usd("0.80"))
        assert inverted.rate.value == Decimal("1.25")

    def test_it_keeps_the_original_observation_date(self):
        """A rate read the other way round is the same observation, not a later one."""
        assert invert(gbp_per_usd(on=date(2024, 6, 28))).observed_on == date(2024, 6, 28)

    def test_it_keeps_the_original_source(self):
        """One published figure, read twice. Two sources would claim evidence that is not there."""
        assert invert(gbp_per_usd()).source == SOURCE

    def test_inverting_twice_returns_the_original_pair(self):
        assert invert(invert(gbp_per_usd())).pair == ("USD", "GBP")


class TestTheRoundTrip:
    def test_usd_to_gbp_and_back_returns_the_original(self, context):
        original = usd("1000000")
        rate = gbp_per_usd("0.79")

        pounds = convert_at(context, amount=original, rate=rate)
        back = convert_at(context, amount=pounds, rate=invert(rate))

        assert round_trips(original, back)

    @pytest.mark.parametrize("rate_value", ["0.79", "1.27", "0.0071", "150.25"])
    def test_it_returns_for_rates_across_four_orders_of_magnitude(self, context, rate_value):
        """A tolerance that only holds near parity is a tolerance that has not been tested."""
        original = usd("1234567.89")
        rate = gbp_per_usd(rate_value)

        pounds = convert_at(context, amount=original, rate=rate)
        back = convert_at(context, amount=pounds, rate=invert(rate))

        assert round_trips(original, back), f"failed at {rate_value}"

    def test_a_round_trip_that_lost_a_percent_does_not_count_as_one(self):
        assert not round_trips(usd("1000000"), usd("990000"))

    def test_a_returned_value_in_the_wrong_currency_never_counts(self):
        """Same number, different money. Comparing the values alone would call this a match."""
        assert not round_trips(usd("1000000"), gbp("1000000"))

    def test_zero_returns_exactly_or_not_at_all(self):
        assert round_trips(usd("0"), usd("0"))
        assert not round_trips(usd("0"), usd("0.01"))

    def test_a_drift_just_inside_the_tolerance_counts(self):
        drift = Decimal("1000000") * ROUND_TRIP_TOLERANCE
        assert round_trips(usd("1000000"), usd(str(Decimal("1000000") + drift)))

    def test_a_drift_just_outside_it_does_not(self):
        drift = Decimal("1000000") * ROUND_TRIP_TOLERANCE * 2
        assert not round_trips(usd("1000000"), usd(str(Decimal("1000000") + drift)))
