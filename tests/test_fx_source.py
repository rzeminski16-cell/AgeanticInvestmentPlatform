"""Where an exchange rate comes from, now that there is somewhere for it to come from.

Gap A19. `aer.calc.fx` shipped complete in task 22 and was never given a source: ADR 0026
determined that the Bank of England publishes a CSV download route for programmatic use and
disallows that same route in its own `robots.txt`, so every rate had to be supplied by hand.
The ECB has no such conflict, and this is the adapter for it.

**Two properties carry the whole thing, and each has its own class below.**

The direction. The ECB quotes units of a currency per euro, so a `USD` observation of
1.0712 means one euro buys 1.0712 dollars and the rate converts euros *into* dollars. Get
that backwards and a balance sheet is wrong by the square of the rate, which is plausible
near parity and catastrophic away from it.

The provenance of a cross. There is no GBP/USD reference rate and there never will be, so
one is a division of two published figures — and a derived rate that looked published would
be the platform asserting something nobody stated. `cross` is traced for that reason, and
`TestACrossIsARecordedCalculation` is what keeps it so.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from aer.calc.engine import CalculationContext
from aer.calc.fx import FxRate, convert_at, cross, invert, round_trips, select_rate
from aer.calc.units import (
    CalculationError,
    Quantity,
    SourceKind,
    SourceRef,
    Unit,
    UnsourcedValueError,
)
from aer.core.enums import Provider, SourceTier
from aer.errors import ExternalServiceError, ValidationError
from aer.fetch.policy import DEFAULT_POLICIES, RetentionClass
from aer.sources.macro import ecb
from aer.sources.tiering import DocumentKind, tier_for

FIXTURES: Final = Path(__file__).parent / "fixtures" / "macro"

SOURCE: Final = SourceRef.macro_observation(
    "11111111-1111-1111-1111-111111111111", label="ECB euro reference rates"
)


def fixture(currency: str) -> bytes:
    return (FIXTURES / f"ecb_eurofxref_{currency.lower()}.csv").read_bytes()


def rates_for(currency: str) -> tuple[FxRate, ...]:
    parsed = ecb.parse_reference_rates(fixture(currency), currency=currency)
    return ecb.as_fx_rates(parsed, source=SOURCE)


class TestTheRegistryIsTheAllowlist:
    """No method takes a URL, and no method takes an arbitrary currency either."""

    def test_a_currency_nobody_has_ruled_on_is_refused(self):
        with pytest.raises(ValidationError, match="not a currency this platform retrieves"):
            ecb.reference_rate_url("ZWL")

    def test_the_refusal_lists_what_is_allowed(self):
        with pytest.raises(ValidationError) as raised:
            ecb.reference_rate_url("ZWL")

        assert "USD" in str(raised.value)
        assert "GBP" in str(raised.value)

    def test_the_euro_itself_is_refused(self):
        """Every series here is quoted against the euro, so EUR/EUR is the number one."""
        with pytest.raises(ValidationError, match="no EUR/EUR reference rate"):
            ecb.reference_rate_url("EUR")

    def test_a_backwards_range_is_refused(self):
        with pytest.raises(ValidationError, match="runs backwards"):
            ecb.reference_rate_url("USD", start_date=date(2024, 7, 1), end_date=date(2024, 6, 1))

    def test_the_url_names_the_daily_spot_series_against_the_euro(self):
        url = ecb.reference_rate_url("GBP")

        assert url.startswith(ecb.API_ROOT)
        assert "/EXR/D.GBP.EUR.SP00.A" in url
        assert "format=csvdata" in url

    def test_the_as_of_date_bounds_the_request(self):
        """A saving and a courtesy to the portal. The point-in-time control is
        `select_rate`, applied again over whatever comes back."""
        url = ecb.reference_rate_url("USD", end_date=date(2024, 6, 30))

        assert "endPeriod=2024-06-30" in url

    def test_the_currency_is_case_insensitive_but_the_series_key_is_not(self):
        assert "D.USD.EUR" in ecb.reference_rate_url("usd")


class TestParsingTheObservations:
    def test_the_published_rates_are_read(self):
        parsed = ecb.parse_reference_rates(fixture("USD"), currency="USD")

        assert parsed.currency == "USD"
        assert (date(2024, 6, 24), Decimal("1.0712")) in parsed.observations
        assert parsed.latest == (date(2024, 7, 1), Decimal("1.0730"))

    def test_a_blank_weekend_row_is_skipped_rather_than_read_as_zero(self):
        """The ECB publishes a row for every calendar day and leaves weekends empty. A zero
        there would be a rate, and a catastrophic one."""
        parsed = ecb.parse_reference_rates(fixture("USD"), currency="USD")
        days = {observed for observed, _ in parsed.observations}

        assert date(2024, 6, 29) not in days
        assert all(value > 0 for _, value in parsed.observations)

    def test_rows_out_of_order_are_sorted_into_date_order(self):
        """Asserted against a deliberately shuffled response rather than against the
        fixture, which is already sorted and so proves nothing. `select_rate` walks the
        observations to find the latest one at or before the as-of date; handed them in
        publication order it would pick whichever happened to be last in the file."""
        shuffled = (
            b"TIME_PERIOD,OBS_VALUE\n"
            b"2024-06-27,1.0699\n"
            b"2024-06-24,1.0712\n"
            b"2024-06-28,1.0705\n"
            b"2024-06-26,1.0685\n"
        )

        parsed = ecb.parse_reference_rates(shuffled, currency="USD")
        days = [observed for observed, _ in parsed.observations]

        assert days == sorted(days)
        assert parsed.latest == (date(2024, 6, 28), Decimal("1.0705"))

    def test_the_published_precision_survives(self):
        """Parsed from the text, never through a float.

        `0.84620` and `0.8462` are the same number and not the same statement: the trailing
        zero is the ECB saying it published five decimal places. A float round-trip loses
        it, and that is the visible edge of an error that reaches the last places of every
        figure the rate ever converts.
        """
        parsed = ecb.parse_reference_rates(fixture("GBP"), currency="GBP")
        [(_, first), *_] = parsed.observations

        assert isinstance(first, Decimal)
        assert str(first) == "0.84620"
        assert first.as_tuple().exponent == -5

    def test_a_response_with_no_observation_columns_is_an_error_not_an_empty_series(self):
        """ "The API changed" and "there are no rates" call for different responses, and a
        positional parser would have reported the first as the second."""
        page = b"<html><body>The ECB Data Portal is undergoing maintenance.</body></html>"

        with pytest.raises(ExternalServiceError, match="not an SDMX-CSV observation set"):
            ecb.parse_reference_rates(page, currency="USD")

    def test_the_error_names_the_columns_it_did_find(self):
        with pytest.raises(ExternalServiceError) as raised:
            ecb.parse_reference_rates(b"alpha,beta\n1,2\n", currency="USD")

        assert "alpha" in str(raised.value.context["columns"])

    def test_columns_are_read_by_name_rather_than_position(self):
        """SDMX-CSV carries a dozen dimension columns whose order varies between portal
        versions, so a positional read is a silent wrong-column bug waiting for a release."""
        reordered = b"OBS_VALUE,TIME_PERIOD,KEY\n1.0712,2024-06-24,EXR.D.USD.EUR.SP00.A\n"

        parsed = ecb.parse_reference_rates(reordered, currency="USD")

        assert parsed.observations == ((date(2024, 6, 24), Decimal("1.0712")),)

    def test_an_unexpected_extra_column_is_ignored(self):
        extended = (
            b"KEY,TIME_PERIOD,OBS_VALUE,SOMETHING_NEW\nEXR.D.USD.EUR.SP00.A,2024-06-24,1.0712,x\n"
        )

        assert ecb.parse_reference_rates(extended, currency="USD").observations


class TestTheDirectionOfTheRate:
    """One euro buys 1.0712 dollars, so the rate converts euros into dollars. Reversing
    this is the single most likely error in the whole path."""

    def test_the_unit_is_the_currency_over_the_euro(self):
        [first, *_] = rates_for("USD")

        assert first.base == "EUR"
        assert first.quote == "USD"
        assert first.rate.unit == Unit.currency("USD") / Unit.currency("EUR")

    def test_it_converts_euros_into_dollars(self):
        context = CalculationContext(code_version="test")
        rate = select_rate(rates_for("USD"), base="EUR", quote="USD", as_of=date(2024, 6, 28))

        converted = convert_at(
            context,
            amount=Quantity.of(Decimal(100), Unit.currency("EUR"), source=SOURCE),
            rate=rate,
        )

        assert converted.unit == Unit.currency("USD")
        assert converted.value == Decimal("107.05")

    def test_applying_it_to_dollars_raises_rather_than_producing_a_number(self):
        context = CalculationContext(code_version="test")
        rate = select_rate(rates_for("USD"), base="EUR", quote="USD", as_of=date(2024, 6, 28))

        with pytest.raises(CalculationError, match="rate for a pair the amount is not in"):
            convert_at(
                context,
                amount=Quantity.of(Decimal(100), Unit.currency("USD"), source=SOURCE),
                rate=rate,
            )

    def test_an_inverted_rate_round_trips(self):
        context = CalculationContext(code_version="test")
        rate = select_rate(rates_for("USD"), base="EUR", quote="USD", as_of=date(2024, 6, 28))
        euros = Quantity.of(Decimal(100), Unit.currency("EUR"), source=SOURCE)

        dollars = convert_at(context, amount=euros, rate=rate)
        back = convert_at(context, amount=dollars, rate=invert(rate))

        assert round_trips(euros, back)


class TestPointInTimeStillApplies:
    def test_a_rate_after_the_as_of_date_is_never_selected(self):
        """The endPeriod bound is a saving; this is the control."""
        chosen = select_rate(rates_for("USD"), base="EUR", quote="USD", as_of=date(2024, 6, 28))

        assert chosen.observed_on == date(2024, 6, 28)

    def test_a_weekend_as_of_date_reaches_back_to_the_last_working_day(self):
        """The ECB does not publish on a Saturday, and a run dated to one should not fail
        for a reason that has nothing to do with the data."""
        chosen = select_rate(rates_for("GBP"), base="EUR", quote="GBP", as_of=date(2024, 6, 30))

        assert chosen.observed_on == date(2024, 6, 28)


class TestACrossIsARecordedCalculation:
    """There is no GBP/USD reference rate, so one is arithmetic on two published figures.

    A derived rate that looked published would be the platform asserting something nobody
    stated, which is why this is traced and not a helper.
    """

    @staticmethod
    def _legs(as_of: date = date(2024, 6, 28)) -> tuple[FxRate, FxRate]:
        usd = select_rate(rates_for("USD"), base="EUR", quote="USD", as_of=as_of)
        gbp = select_rate(rates_for("GBP"), base="EUR", quote="GBP", as_of=as_of)
        return usd, gbp

    def test_dividing_two_euro_legs_gives_the_pair_neither_is_quoted_against(self):
        context = CalculationContext(code_version="test")
        usd, gbp = self._legs()

        pair = cross(context, base_leg=usd, quote_leg=gbp)

        assert pair.base == "USD"
        assert pair.quote == "GBP"
        assert pair.rate.unit == Unit.currency("GBP") / Unit.currency("USD")

    def test_the_arithmetic_is_the_quote_leg_over_the_base_leg(self):
        """Stated as the property rather than as a literal: the engine computes at a wider
        precision than the default Decimal context, so a hand-written quotient asserts the
        context as much as the arithmetic."""
        context = CalculationContext(code_version="test")
        usd, gbp = self._legs()

        pair = cross(context, base_leg=usd, quote_leg=gbp)

        # Pounds per dollar, times dollars per euro, is pounds per euro — the published leg.
        reproduced = pair.rate.value * usd.rate.value
        assert abs(reproduced - gbp.rate.value) < Decimal("0.0000000001")
        assert pair.rate.value.quantize(Decimal("0.000001")) == Decimal("0.790705")

    def test_it_is_recorded_with_its_formula_and_both_inputs(self):
        """The point of tracing it. A reader following a converted figure back reaches two
        source documents and a division, not a number that looks published."""
        context = CalculationContext(code_version="test")
        usd, gbp = self._legs()

        cross(context, base_leg=usd, quote_leg=gbp)

        [record] = [row for row in context.records if row.name == "fx_cross_rate"]
        assert record.formula == "cross = quote_leg / base_leg"
        assert len(record.inputs) == 2

    def test_the_record_says_it_is_not_a_market_rate(self):
        """A cross carries no spread and no basis, and a reader should not have to know
        that from somewhere else."""
        context = CalculationContext(code_version="test")
        usd, gbp = self._legs()

        cross(context, base_leg=usd, quote_leg=gbp)

        [record] = [row for row in context.records if row.name == "fx_cross_rate"]
        assert any("not a quoted market rate" in note for note in record.assumptions)

    def test_the_result_is_attributed_to_the_calculation_rather_than_to_a_publisher(self):
        context = CalculationContext(code_version="test")
        usd, gbp = self._legs()

        pair = cross(context, base_leg=usd, quote_leg=gbp)

        assert pair.rate.source is not None
        assert pair.rate.source.kind is SourceKind.CALCULATION

    def test_legs_from_different_days_are_refused(self):
        """Both come from one daily publication, so a mismatch means one was selected
        wrongly — and the cross would be a rate nobody could have transacted at."""
        context = CalculationContext(code_version="test")
        usd = select_rate(rates_for("USD"), base="EUR", quote="USD", as_of=date(2024, 6, 28))
        gbp = select_rate(rates_for("GBP"), base="EUR", quote="GBP", as_of=date(2024, 6, 26))

        with pytest.raises(CalculationError, match="same day"):
            cross(context, base_leg=usd, quote_leg=gbp)

    def test_two_legs_in_the_same_currency_are_refused(self):
        context = CalculationContext(code_version="test")
        usd, _ = self._legs()

        with pytest.raises(CalculationError, match="Both legs are USD"):
            cross(context, base_leg=usd, quote_leg=usd)

    def test_legs_with_no_shared_pivot_are_refused(self):
        """Dividing them would produce a unit no amount can be converted into."""
        context = CalculationContext(code_version="test")
        usd, _ = self._legs()
        unpivoted = FxRate(
            base="JPY",
            quote="GBP",
            rate=Quantity.of(
                Decimal("0.0052"),
                Unit.currency("GBP") / Unit.currency("JPY"),
                source=SOURCE,
            ),
            observed_on=date(2024, 6, 28),
        )

        with pytest.raises(CalculationError, match="same pivot currency"):
            cross(context, base_leg=usd, quote_leg=unpivoted)

    def test_a_cross_converts_the_currency_it_says_it_does(self):
        """The end-to-end property: dollars in, pounds out, and the unit system agrees."""
        context = CalculationContext(code_version="test")
        usd, gbp = self._legs()
        pair = cross(context, base_leg=usd, quote_leg=gbp)

        converted = convert_at(
            context,
            amount=Quantity.of(Decimal(1000), Unit.currency("USD"), source=SOURCE),
            rate=pair,
        )

        assert converted.unit == Unit.currency("GBP")
        assert Decimal("780") < converted.value < Decimal("800")


class TestTheProviderIsConfiguredLikeEveryOther:
    def test_the_hosts_are_allowlisted(self):
        policy = DEFAULT_POLICIES[Provider.ECB]

        assert "data-api.ecb.europa.eu" in policy.allowed_hosts

    def test_reference_rates_are_official_statistics_not_market_data(self):
        """The ECB says plainly these are not intended for market transactions, and T4
        would claim a tradability they do not have."""
        assert DEFAULT_POLICIES[Provider.ECB].source_tier is SourceTier.T3_OFFICIAL_STATS
        assert tier_for(Provider.ECB, DocumentKind.STRUCTURED_DATA) is SourceTier.T3_OFFICIAL_STATS

    def test_the_licence_note_says_commercial_use_is_permitted(self):
        note = DEFAULT_POLICIES[Provider.ECB].licence_note

        assert "commercially" in note
        assert "European Central Bank" in note

    def test_the_licence_note_records_that_they_are_indicative(self):
        assert "not intended" in DEFAULT_POLICIES[Provider.ECB].licence_note

    def test_a_central_bank_publication_is_never_purgeable(self):
        assert DEFAULT_POLICIES[Provider.ECB].retention is RetentionClass.PERMANENT

    def test_the_url_host_is_one_the_policy_allows(self):
        """A registry that built URLs the allowlist refuses would be two lists disagreeing."""
        from urllib.parse import urlsplit  # noqa: PLC0415 -- one assertion needs it

        host = urlsplit(ecb.reference_rate_url("USD")).hostname

        assert host in DEFAULT_POLICIES[Provider.ECB].allowed_hosts


class TestTheBankOfEnglandStaysRefused:
    """ADR 0026 is not superseded by having found another source; it is still the
    determination, and the ECB adapter must not be read as a way round it."""

    def test_no_bank_of_england_provider_exists(self):
        assert not any("bank_of_england" in member.value for member in Provider)

    def test_the_gbp_risk_free_rate_is_still_unresolved(self):
        """The ECB publishes exchange rates, not gilt yields. A sterling valuation still
        has no risk-free series, and pretending otherwise would discount at the wrong one."""
        from aer.sources.macro.series import RISK_FREE_SERIES  # noqa: PLC0415

        assert "GBP" not in RISK_FREE_SERIES


class TestARateStillNeedsASource:
    def test_an_unsourced_rate_is_refused_on_construction(self):
        """A rate nobody can point at is an assumption pretending to be a fact, and every
        figure converted with it inherits that."""
        with pytest.raises(UnsourcedValueError):
            FxRate(
                base="EUR",
                quote="USD",
                rate=Quantity.of(
                    Decimal("1.07"), Unit.currency("USD") / Unit.currency("EUR"), source=None
                ),
                observed_on=date(2024, 6, 28),
            )

    def test_a_rate_whose_unit_disagrees_with_its_pair_is_refused(self):
        """The guard that makes an upside-down rate a raised error rather than a plausible
        wrong number."""
        with pytest.raises(CalculationError, match="must be stated in"):
            FxRate(
                base="EUR",
                quote="USD",
                rate=Quantity.of(
                    Decimal("1.07"), Unit.currency("EUR") / Unit.currency("USD"), source=SOURCE
                ),
                observed_on=date(2024, 6, 28),
            )

    def test_every_parsed_rate_carries_the_document_it_came_from(self):
        for rate in rates_for("USD"):
            assert rate.source == SOURCE
