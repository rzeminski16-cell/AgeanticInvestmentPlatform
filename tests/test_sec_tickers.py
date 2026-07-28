"""Ticker and exchange to CIK.

The first step of any US research run, and the one where a mistake is cheapest to catch
and most expensive to miss — every subsequent fact would be about the wrong company.
"""

from __future__ import annotations

import pytest

from aer.errors import ExternalServiceError, ValidationError
from aer.sources.sec.tickers import (
    format_cik,
    normalise_exchange,
    parse_company_tickers,
    resolve_ticker,
)
from tests.sec_fixtures import MSFT_CIK, fixture_bytes


@pytest.fixture
def exchange_records():
    return parse_company_tickers(fixture_bytes("company_tickers_exchange.json"))


@pytest.fixture
def plain_records():
    return parse_company_tickers(fixture_bytes("company_tickers.json"))


class TestFormatCik:
    def test_a_bare_integer_is_zero_padded_to_ten(self):
        # Not cosmetic: data.sec.gov/submissions/CIK789019.json is a 404 and
        # CIK0000789019.json is Microsoft.
        assert format_cik(789019) == "0000789019"

    @pytest.mark.parametrize("value", [789019, "789019", "0000789019", "CIK0000789019", " 789019 "])
    def test_every_form_a_cik_arrives_in_normalises_to_the_same_string(self, value):
        assert format_cik(value) == MSFT_CIK

    def test_a_short_cik_is_padded_not_truncated(self):
        assert format_cik(4962) == "0000004962"

    @pytest.mark.parametrize("value", ["not-a-cik", "12345678901", "", "78.90"])
    def test_nonsense_is_refused(self, value):
        with pytest.raises(ValidationError):
            format_cik(value)


class TestNormaliseExchange:
    @pytest.mark.parametrize(
        ("edgar", "expected"),
        [
            ("Nasdaq", "NASDAQ"),
            ("NYSE", "NYSE"),
            ("NYSE American", "NYSE_AMERICAN"),
            ("NYSEAmerican", "NYSE_AMERICAN"),
            ("OTC", "OTC"),
        ],
    )
    def test_edgar_labels_map_onto_platform_identifiers(self, edgar, expected):
        assert normalise_exchange(edgar) == expected

    def test_an_unknown_venue_is_kept_rather_than_refused(self):
        # EDGAR can add a venue at any time. Refusing to parse the whole file because one
        # row names a new exchange would be a poor trade.
        assert normalise_exchange("Some New Venue") == "SOME_NEW_VENUE"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_stays_absent(self, value):
        assert normalise_exchange(value) is None


class TestParsingTheColumnarFile:
    def test_every_row_becomes_a_record(self, exchange_records):
        assert len(exchange_records) == 7

    def test_the_column_order_is_read_from_the_header(self, exchange_records):
        # Assuming the order would swap name and ticker on a reordering, with no error --
        # the kind of failure only noticed once it is in a report.
        microsoft = next(r for r in exchange_records if r.ticker == "MSFT")

        assert microsoft.cik == MSFT_CIK
        assert microsoft.name == "MICROSOFT CORP"
        assert microsoft.exchange == "NASDAQ"

    def test_a_row_whose_length_disagrees_with_the_header_is_skipped(self):
        payload = b"""{
          "fields": ["cik", "name", "ticker", "exchange"],
          "data": [[789019, "MICROSOFT CORP", "MSFT", "Nasdaq"], [320193, "Apple Inc."]]
        }"""

        records = parse_company_tickers(payload)

        assert [r.ticker for r in records] == ["MSFT"]

    def test_a_file_without_a_cik_column_is_refused(self):
        payload = b'{"fields": ["name", "ticker"], "data": [["MICROSOFT CORP", "MSFT"]]}'

        with pytest.raises(ExternalServiceError, match="no cik"):
            parse_company_tickers(payload)


class TestParsingTheRowKeyedFile:
    def test_the_older_shape_parses_too(self, plain_records):
        assert len(plain_records) == 3
        assert plain_records[0].cik == MSFT_CIK

    def test_it_carries_no_exchange(self, plain_records):
        # Absent, not unknown. A caller filtering on exchange needs to be able to tell
        # that the file simply did not say.
        assert all(record.exchange is None for record in plain_records)


class TestParsingFailures:
    def test_html_instead_of_json_is_refused_as_retryable(self):
        # What an error page or a captcha actually looks like arriving at a JSON parser.
        with pytest.raises(ExternalServiceError) as excinfo:
            parse_company_tickers(b"<html><body>Request Rate Threshold Exceeded</body></html>")

        assert excinfo.value.retryable is True

    def test_a_json_array_is_refused(self):
        with pytest.raises(ExternalServiceError, match="neither the row-keyed"):
            parse_company_tickers(b"[1, 2, 3]")


class TestResolution:
    def test_msft_resolves_to_microsofts_cik(self, exchange_records):
        record = resolve_ticker(exchange_records, "MSFT")

        assert record.cik == "0000789019"
        assert record.name == "MICROSOFT CORP"

    def test_resolution_is_case_insensitive(self, exchange_records):
        assert resolve_ticker(exchange_records, "msft").cik == MSFT_CIK

    def test_the_exchange_narrows_the_search(self, exchange_records):
        record = resolve_ticker(exchange_records, "UPS", exchange="NYSE")

        assert record.cik == format_cik(1090727)

    def test_a_wrong_exchange_says_where_it_is_actually_listed(self, exchange_records):
        with pytest.raises(ValidationError) as excinfo:
            resolve_ticker(exchange_records, "MSFT", exchange="NYSE")

        assert "NASDAQ" in str(excinfo.value)

    def test_an_exchange_filter_still_matches_records_that_carry_none(self, plain_records):
        # company_tickers.json has no exchange column. Filtering it out entirely would
        # make the older file useless for anything but a bare ticker lookup.
        assert resolve_ticker(plain_records, "MSFT", exchange="NASDAQ").cik == MSFT_CIK

    def test_an_ambiguous_ticker_is_refused_rather_than_guessed(self):
        # Two real companies share a symbol on different venues. Picking the first would
        # attach every figure in the report to whichever happened to be first in the file.
        records = parse_company_tickers(fixture_bytes("company_tickers_ambiguous.json"))

        with pytest.raises(ValidationError) as excinfo:
            resolve_ticker(records, "NEX")

        assert "matches 2 companies" in str(excinfo.value)
        assert "NORTHERN EXAMPLE CORP" in str(excinfo.value)

    def test_naming_the_exchange_resolves_the_ambiguity(self):
        records = parse_company_tickers(fixture_bytes("company_tickers_ambiguous.json"))

        record = resolve_ticker(records, "NEX", exchange="NYSE")

        assert record.name == "NEXUS EXAMPLE PLC"

    def test_a_uk_only_listing_is_not_in_edgar(self, exchange_records):
        # The control case. Tesco is listed in London and files nothing with the SEC, so
        # the failure must say why rather than returning an empty result three steps later.
        with pytest.raises(ValidationError) as excinfo:
            resolve_ticker(exchange_records, "TSCO", exchange="LSE")

        assert "not in the SEC's ticker file" in str(excinfo.value)
        assert "UK-listed company with no US listing" in str(excinfo.value)

    def test_a_mistyped_symbol_fails_the_same_way(self, exchange_records):
        with pytest.raises(ValidationError, match="not in the SEC's ticker file"):
            resolve_ticker(exchange_records, "MSFTT")
