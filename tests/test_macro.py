"""Macro with vintages: the same series, two as-of dates, two different answers.

The fixtures are two real vintages of US GDP. On 30 June 2020 the archive said the first
quarter of 2020 was 21,561.139 billion dollars; by 30 June 2024, after three revisions and a
rebasing, it said 21,727.657. **Both are correct**, and a backtest that used the second to
value something as at mid-2020 used a number nobody had.

`TestTwoAsOfDatesGetDifferentAnswers` is the acceptance criterion of task 25 and the reason
the whole table is keyed by vintage. The rest of this file is the ways that guarantee could
be lost quietly: a URL built without the archive parameters, an empty vintage treated as no
news, a UK release date borrowing a US archive's confidence, and an API key in a log.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final

import httpx
import pytest
import respx

from aer.core.enums import Provider
from aer.errors import ExternalServiceError, ValidationError
from aer.fetch.client import SafeFetcher
from aer.sources.macro import fred, ons
from aer.sources.macro.client import MacroClient, redacted
from aer.sources.macro.series import (
    MACRO_SERIES,
    REFUSED_SERIES,
    RISK_FREE_SERIES,
    SeriesRefusedError,
    risk_free_series_for,
    series_for,
)
from tests.fetch_fixtures import public_resolver

pytestmark = pytest.mark.usefixtures("no_real_sockets")

FIXTURES: Final = Path(__file__).parent / "fixtures" / "macro"
API_KEY: Final = "test-fred-key-not-a-real-one"  # pragma: allowlist secret

GDP: Final = series_for("us_gdp_nominal")
TEN_YEAR: Final = series_for("us_treasury_10y")
UK_CPI: Final = series_for("uk_cpi")
UK_GDP: Final = series_for("uk_gdp_real")


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


class TestTheRegistryIsTheAllowlist:
    def test_every_series_states_who_produced_it_and_under_what_terms(self):
        """The copyright question is about the originator, never about the distributor."""
        for series in MACRO_SERIES:
            assert series.originator, series.key
            assert series.licence, series.key

    def test_a_series_that_is_not_allowlisted_is_refused(self):
        with pytest.raises(SeriesRefusedError, match="not a macro series"):
            series_for("us_house_prices")

    def test_a_copyrighted_series_is_refused_by_name(self):
        """Case-Shiller is the one somebody will reach for, so the refusal names it."""
        with pytest.raises(SeriesRefusedError) as raised:
            series_for("CSUSHPINSA")

        assert "S&P Dow Jones Indices" in str(raised.value)
        assert "commercial redistribution" in str(raised.value)

    def test_every_refusal_says_who_holds_the_copyright(self):
        for refused in REFUSED_SERIES:
            assert refused.originator
            assert refused.reason

    def test_no_refused_series_is_also_allowlisted(self):
        """The one way this table could contradict itself."""
        allowed = {series.identifier for series in MACRO_SERIES}
        for refused in REFUSED_SERIES:
            assert refused.identifier not in allowed

    def test_the_uk_series_do_not_come_from_fred(self):
        """FRED's UK figures are OECD-sourced and carry OECD copyright; the ONS's do not."""
        uk = [s for s in MACRO_SERIES if s.key.startswith("uk_")]
        assert uk
        assert all(s.provider is Provider.ONS for s in uk)

    def test_every_us_series_names_the_agency_rather_than_fred(self):
        for series in MACRO_SERIES:
            if series.provider is Provider.FRED:
                assert "Federal Reserve Bank of St Louis" not in series.originator, series.key
                assert "public domain" in series.licence


class TestTheRiskFreeRateIsADocumentedChoice:
    def test_a_usd_valuation_uses_the_ten_year_treasury(self):
        assert risk_free_series_for("USD").identifier == "DGS10"

    def test_the_maturity_choice_is_written_down(self):
        """ "The government yield" hides three decisions. This asserts one is explained."""
        assert "equity" in risk_free_series_for("USD").notes

    def test_a_currency_with_no_documented_series_is_refused(self):
        """Refused rather than defaulted: substituting is wrong by the rate differential."""
        with pytest.raises(SeriesRefusedError, match="rate differential"):
            risk_free_series_for("GBP")

    def test_the_gbp_gap_is_the_bank_of_england_one(self):
        """Pinned so that closing ADR 0026 is what adds it, rather than a passing edit."""
        assert "GBP" not in RISK_FREE_SERIES


class TestTheArchiveParametersAreNotOptional:
    def test_a_url_carries_the_vintage_on_both_ends(self):
        """Omitting them makes ALFRED return today's data. That is the whole failure."""
        url = fred.observations_url(GDP, vintage=date(2020, 6, 30), api_key=API_KEY)

        assert "realtime_start=2020-06-30" in url
        assert "realtime_end=2020-06-30" in url

    def test_the_url_is_built_from_the_registry_not_from_a_caller(self):
        url = fred.observations_url(GDP, vintage=date(2020, 6, 30), api_key=API_KEY)
        assert url.startswith(f"{fred.API_ROOT}/series/observations?")
        assert "series_id=GDP" in url

    def test_asking_fred_for_an_ons_series_raises(self):
        with pytest.raises(ValidationError, match="builds FRED URLs"):
            fred.observations_url(UK_CPI, vintage=date(2024, 6, 30), api_key=API_KEY)

    def test_asking_the_ons_for_a_fred_series_raises(self):
        with pytest.raises(ValidationError, match="builds ONS URLs"):
            ons.timeseries_url(GDP)


class TestTwoAsOfDatesGetDifferentAnswers:
    """The acceptance criterion. Same series, same period, different vintage, different value."""

    def test_the_2020_vintage_reports_what_was_known_then(self):
        parsed = fred.parse_observations(
            fixture("gdp_vintage_2020_06_30"), series=GDP, vintage=date(2020, 6, 30)
        )
        first_quarter = next(o for o in parsed.observations if o.observed_on == date(2020, 1, 1))
        assert first_quarter.value == Decimal("21561.139")

    def test_the_2024_vintage_reports_the_revised_figure(self):
        parsed = fred.parse_observations(
            fixture("gdp_vintage_2024_06_30"), series=GDP, vintage=date(2024, 6, 30)
        )
        first_quarter = next(o for o in parsed.observations if o.observed_on == date(2020, 1, 1))
        assert first_quarter.value == Decimal("21727.657")

    def test_they_disagree_and_both_are_correct(self):
        early = fred.parse_observations(
            fixture("gdp_vintage_2020_06_30"), series=GDP, vintage=date(2020, 6, 30)
        )
        late = fred.parse_observations(
            fixture("gdp_vintage_2024_06_30"), series=GDP, vintage=date(2024, 6, 30)
        )

        assert early.as_at(date(2020, 6, 30)).value != late.as_at(date(2020, 6, 30)).value

    def test_the_later_vintage_knows_a_period_the_earlier_one_did_not(self):
        """Not only revised: the second quarter of 2020 had not been published in June 2020."""
        early = fred.parse_observations(
            fixture("gdp_vintage_2020_06_30"), series=GDP, vintage=date(2020, 6, 30)
        )
        late = fred.parse_observations(
            fixture("gdp_vintage_2024_06_30"), series=GDP, vintage=date(2024, 6, 30)
        )

        assert early.latest.observed_on == date(2020, 1, 1)
        assert late.latest.observed_on == date(2020, 4, 1)

    def test_every_observation_carries_the_vintage_it_was_read_at(self):
        parsed = fred.parse_observations(
            fixture("gdp_vintage_2020_06_30"), series=GDP, vintage=date(2020, 6, 30)
        )
        assert all(o.vintage == date(2020, 6, 30) for o in parsed.observations)


class TestAMissingVintageIsRefused:
    def test_an_empty_archive_response_raises(self):
        """ALFRED answers a pre-series date with an empty list rather than an error."""
        with pytest.raises(fred.VintageMissingError, match="no observations"):
            fred.parse_observations(
                fixture("gdp_vintage_empty"), series=GDP, vintage=date(1940, 1, 1)
            )

    def test_the_refusal_says_it_did_not_fall_back(self):
        with pytest.raises(fred.VintageMissingError) as raised:
            fred.parse_observations(
                fixture("gdp_vintage_empty"), series=GDP, vintage=date(1940, 1, 1)
            )
        assert "rather than falling back" in str(raised.value)

    def test_an_archive_answering_a_different_vintage_raises(self):
        """Real figures from the wrong day, which nothing downstream could detect."""
        with pytest.raises(ExternalServiceError, match="wrong day"):
            fred.parse_observations(
                fixture("gdp_vintage_2020_06_30"), series=GDP, vintage=date(2021, 6, 30)
            )

    def test_a_document_that_is_not_a_series_raises(self):
        with pytest.raises(ExternalServiceError, match="no observations list"):
            fred.parse_observations(b'{"error_code": 400}', series=GDP, vintage=date(2020, 6, 30))

    def test_a_non_json_body_raises(self):
        with pytest.raises(ExternalServiceError, match="not JSON"):
            fred.parse_observations(
                b"<html>rate limited</html>", series=GDP, vintage=date(2020, 6, 30)
            )


class TestAMissingValueIsNotZero:
    def test_a_full_stop_is_skipped_rather_than_read_as_zero(self):
        """ALFRED writes "." for a market holiday. Zero would be a claim, not a gap."""
        parsed = fred.parse_observations(
            fixture("dgs10_vintage"), series=TEN_YEAR, vintage=date(2024, 6, 28)
        )

        dates = [o.observed_on for o in parsed.observations]
        assert date(2024, 6, 26) not in dates
        assert len(parsed.observations) == 4

    def test_the_surrounding_values_survive(self):
        parsed = fred.parse_observations(
            fixture("dgs10_vintage"), series=TEN_YEAR, vintage=date(2024, 6, 28)
        )
        assert parsed.as_at(date(2024, 6, 28)).value == Decimal("4.36")

    def test_as_at_takes_the_latest_period_not_after_the_cutoff(self):
        """The vintage and the period are different dates; a caller wants the second."""
        parsed = fred.parse_observations(
            fixture("dgs10_vintage"), series=TEN_YEAR, vintage=date(2024, 6, 28)
        )
        assert parsed.as_at(date(2024, 6, 26)).observed_on == date(2024, 6, 25)


class TestTheOnsIsHonestAboutWhatItCannotDo:
    def test_it_reads_the_frequency_the_registry_declares(self):
        """The response holds months, quarters and years; the wrong block is wrong data."""
        parsed = ons.parse_timeseries(fixture("ons_cpi"), series=UK_CPI)

        assert len(parsed.observations) == 3
        assert parsed.observations[0].observed_on == date(2024, 3, 1)

    def test_a_period_becomes_the_first_day_of_itself(self):
        parsed = ons.parse_timeseries(fixture("ons_cpi"), series=UK_CPI)
        assert [o.observed_on for o in parsed.observations] == [
            date(2024, 3, 1),
            date(2024, 4, 1),
            date(2024, 5, 1),
        ]

    def test_the_vintage_is_the_release_date(self):
        parsed = ons.parse_timeseries(fixture("ons_cpi"), series=UK_CPI)
        assert parsed.release_date == date(2024, 6, 19)
        assert all(o.vintage == date(2024, 6, 19) for o in parsed.observations)

    def test_it_does_not_claim_to_be_an_archive(self):
        """The claim a UK figure must not borrow from a US one."""
        assert ons.parse_timeseries(fixture("ons_cpi"), series=UK_CPI).is_archived is False

    def test_a_release_after_the_as_of_date_is_refused(self):
        parsed = ons.parse_timeseries(fixture("ons_cpi"), series=UK_CPI)

        with pytest.raises(ons.LookAheadReleaseError, match="after"):
            ons.observations_for(parsed, as_of=date(2024, 5, 31))

    def test_a_release_on_the_as_of_date_is_allowed(self):
        parsed = ons.parse_timeseries(fixture("ons_cpi"), series=UK_CPI)
        assert ons.observations_for(parsed, as_of=date(2024, 6, 19)) is parsed

    def test_a_response_with_no_release_date_is_refused(self):
        """Nothing to check the as-of date against is not point-in-time evidence."""
        with pytest.raises(ExternalServiceError, match="no release date"):
            ons.parse_timeseries(b'{"description": {"title": "x"}, "months": []}', series=UK_CPI)

    def test_a_quarterly_series_is_read_from_the_quarters_block(self):
        """The same document carries years too, at a different scale entirely."""
        parsed = ons.parse_timeseries(fixture("ons_gdp_quarterly"), series=UK_GDP)

        assert len(parsed.observations) == 2
        assert [o.value for o in parsed.observations] == [Decimal("99.8"), Decimal("100.5")]

    def test_a_quarter_becomes_the_first_day_of_itself(self):
        """Q4 2023 is 1 October 2023. Normalised so periods of different frequencies sort
        together and compare against an as-of date without a calendar rule per call site."""
        parsed = ons.parse_timeseries(fixture("ons_gdp_quarterly"), series=UK_GDP)

        assert [o.observed_on for o in parsed.observations] == [
            date(2023, 10, 1),
            date(2024, 1, 1),
        ]

    def test_a_url_needs_a_dataset(self):
        url = ons.timeseries_url(UK_CPI)
        assert url == f"{ons.API_ROOT}/timeseries/d7bt/dataset/mm23/data"


class TestTheApiKeyStaysOutOfEverything:
    def test_a_url_is_redacted_for_logging(self):
        url = fred.observations_url(GDP, vintage=date(2020, 6, 30), api_key=API_KEY)

        assert API_KEY in url
        assert API_KEY not in redacted(url)
        assert "api_key=REDACTED" in redacted(url)

    def test_redaction_leaves_the_rest_of_the_url_intact(self):
        """The URL is what makes a fetch reproducible; only the key must go."""
        url = fred.observations_url(GDP, vintage=date(2020, 6, 30), api_key=API_KEY)
        cleaned = redacted(url)

        assert "series_id=GDP" in cleaned
        assert "realtime_start=2020-06-30" in cleaned

    def test_it_redacts_whatever_the_key_looks_like(self):
        """Matched on the parameter, so a rotated key is still hidden in an old log line."""
        assert redacted("https://x.test/?api_key=anything&b=1") == (
            "https://x.test/?api_key=REDACTED&b=1"
        )


@pytest.fixture
def macro_fetcher(fetch_settings, artefact_store, limiter, breaker, sleeper):
    """A fetcher wired to respx, as `test_fetcher.py` does it and for the same reasons."""
    return SafeFetcher(
        fetch_settings,
        store=artefact_store,
        limiter=limiter,
        breaker=breaker,
        robots=None,
        sleep=sleeper,
        resolver=public_resolver("104.16.0.1"),
        transport_factory=httpx.AsyncHTTPTransport,
    )


class TestTheClientNeverTakesAUrl:
    @respx.mock
    async def test_it_retrieves_a_series_by_key(self, macro_fetcher, artefact_store) -> None:
        respx.get(url__startswith=f"{fred.API_ROOT}/series/observations").mock(
            return_value=httpx.Response(
                200,
                content=fixture("gdp_vintage_2020_06_30"),
                headers={"content-type": "application/json"},
            )
        )
        client = MacroClient(macro_fetcher, artefact_store, fred_api_key=API_KEY)

        response = await client.fetch_series("us_gdp_nominal", as_of=date(2020, 6, 30))

        assert response.is_archived is True
        assert response.vintage == date(2020, 6, 30)
        assert len(response.observations) == 3

    @respx.mock
    async def test_the_request_carries_the_archive_parameters(
        self, macro_fetcher, artefact_store
    ) -> None:
        """End to end: a client call produces a URL that asks the archive, not FRED today."""
        route = respx.get(url__startswith=f"{fred.API_ROOT}/series/observations").mock(
            return_value=httpx.Response(
                200,
                content=fixture("gdp_vintage_2020_06_30"),
                headers={"content-type": "application/json"},
            )
        )
        client = MacroClient(macro_fetcher, artefact_store, fred_api_key=API_KEY)
        await client.fetch_series("us_gdp_nominal", as_of=date(2020, 6, 30))

        requested = str(route.calls[0].request.url)
        assert "realtime_start=2020-06-30" in requested
        assert "realtime_end=2020-06-30" in requested

    @respx.mock
    async def test_a_refused_series_makes_no_request(self, macro_fetcher, artefact_store) -> None:
        """The registry refuses before the fetcher is reached, so nothing is attempted."""
        client = MacroClient(macro_fetcher, artefact_store, fred_api_key=API_KEY)

        with pytest.raises(SeriesRefusedError):
            await client.fetch_series("CSUSHPINSA", as_of=date(2020, 6, 30))

        assert not respx.calls

    @respx.mock
    async def test_a_fred_series_with_no_key_configured_is_refused(
        self, macro_fetcher, artefact_store
    ) -> None:
        from aer.errors import ConfigError  # noqa: PLC0415 -- only this test needs it

        client = MacroClient(macro_fetcher, artefact_store, fred_api_key=None)

        with pytest.raises(ConfigError, match="AER_FRED_API_KEY"):
            await client.fetch_series("us_gdp_nominal", as_of=date(2020, 6, 30))

        assert not respx.calls

    @respx.mock
    async def test_an_ons_series_is_stamped_with_its_release_not_the_as_of_date(
        self, macro_fetcher, artefact_store
    ) -> None:
        respx.get(url__startswith=f"{ons.API_ROOT}/timeseries").mock(
            return_value=httpx.Response(
                200,
                content=fixture("ons_cpi"),
                headers={"content-type": "application/json"},
            )
        )
        client = MacroClient(macro_fetcher, artefact_store, fred_api_key=API_KEY)

        response = await client.fetch_series("uk_cpi", as_of=date(2024, 6, 30))

        assert response.vintage == date(2024, 6, 19)
        assert response.is_archived is False
