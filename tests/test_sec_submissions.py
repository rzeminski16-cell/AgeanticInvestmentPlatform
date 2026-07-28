"""The filing index.

Two things are being checked: that the columnar shape is unpacked correctly, and that a
*ragged* columnar shape is refused. The second matters more than it looks — misaligned
parallel arrays produce a filing index that is wrong in a way nothing downstream could
detect.
"""

from __future__ import annotations

from datetime import date

import pytest

from aer.errors import ExternalServiceError
from aer.sources.sec.submissions import (
    ANNUAL_FORMS,
    QUARTERLY_FORMS,
    parse_submissions,
)
from tests.sec_fixtures import MSFT_CIK, fixture_bytes


@pytest.fixture
def index():
    return parse_submissions(fixture_bytes("submissions_msft.json"))


class TestIdentity:
    def test_the_cik_is_zero_padded(self, index):
        assert index.cik == MSFT_CIK

    def test_the_entity_details_are_carried(self, index):
        assert index.name == "MICROSOFT CORP"
        assert index.tickers == ("MSFT",)
        assert index.exchanges == ("NASDAQ",)
        assert index.fiscal_year_end == "0630"
        assert index.sic == "7372"


class TestTheColumnarUnpacking:
    def test_every_row_becomes_a_filing(self, index):
        assert len(index.filings) == 5

    def test_filings_are_newest_first(self, index):
        dates = [f.filing_date for f in index.filings]

        assert dates == sorted(dates, reverse=True)

    def test_each_filing_keeps_the_values_from_its_own_row(self, index):
        # The property a misaligned zip destroys: every field of a filing must come from
        # the same index of every array.
        fy2020 = next(f for f in index.filings if f.accession == "0000789019-20-000039")

        assert fy2020.form == "10-K"
        assert fy2020.filing_date == date(2020, 7, 30)
        assert fy2020.report_date == date(2020, 6, 30)
        assert fy2020.primary_document == "msft-20200630.htm"
        assert fy2020.is_xbrl is True

    def test_an_empty_report_date_becomes_none_rather_than_an_error(self, index):
        # EDGAR writes "" for a form covering no period. An 8-K legitimately has none.
        eight_k = next(f for f in index.filings if f.form == "8-K")

        assert eight_k.report_date is None

    def test_ragged_arrays_are_refused(self):
        # The whole reason this parser checks lengths. Zipping arrays of 3, 2 and 3 would
        # attribute the third filing to no date at all, or to the wrong one, and produce
        # an index that looks entirely normal.
        with pytest.raises(ExternalServiceError) as excinfo:
            parse_submissions(fixture_bytes("submissions_ragged.json"))

        assert "differing lengths" in str(excinfo.value)
        assert excinfo.value.context["lengths"]["filingDate"] == 2

    def test_a_missing_required_column_is_refused(self):
        payload = b'{"cik": "789019", "filings": {"recent": {"accessionNumber": ["x"]}}}'

        with pytest.raises(ExternalServiceError, match="missing the"):
            parse_submissions(payload)


class TestFiltering:
    def test_filed_on_or_before_is_inclusive_of_the_date_itself(self, index):
        on_the_day = index.filed_on_or_before(date(2020, 7, 30))

        assert "0000789019-20-000039" in {f.accession for f in on_the_day}

    def test_a_filing_accepted_after_the_as_of_date_is_excluded(self, index):
        as_at_2021 = index.filed_on_or_before(date(2021, 1, 1))

        assert all(f.filing_date <= date(2021, 1, 1) for f in as_at_2021)
        assert "0000789019-22-000010" not in {f.accession for f in as_at_2021}

    def test_of_form_selects_by_form_type(self, index):
        annuals = index.of_form(ANNUAL_FORMS)

        assert len(annuals) == 3
        assert all(f.form == "10-K" for f in annuals)

    def test_latest_respects_the_as_of_date(self, index):
        # The look-ahead case in miniature: "the latest annual report" means a different
        # filing depending on when you ask.
        as_at_2021 = index.latest(ANNUAL_FORMS, as_of_date=date(2021, 1, 1))
        as_at_2023 = index.latest(ANNUAL_FORMS, as_of_date=date(2023, 1, 1))

        assert as_at_2021 is not None
        assert as_at_2021.accession == "0000789019-20-000039"
        assert as_at_2023 is not None
        assert as_at_2023.accession == "0000789019-22-000010"

    def test_latest_returns_none_when_nothing_matches(self, index):
        assert index.latest(frozenset({"S-1"})) is None

    def test_latest_returns_none_when_everything_is_too_recent(self, index):
        assert index.latest(QUARTERLY_FORMS, as_of_date=date(2019, 1, 1)) is None


class TestUrlConstruction:
    def test_the_archive_url_strips_zeros_and_dashes(self, index):
        # The archive path is the one place EDGAR wants the CIK unpadded and the accession
        # undashed. Every other endpoint wants the opposite, which is why this is built in
        # one place rather than formatted at each call site.
        fy2020 = next(f for f in index.filings if f.accession == "0000789019-20-000039")

        assert fy2020.url(index.cik) == (
            "https://www.sec.gov/Archives/edgar/data/789019/000078901920000039/msft-20200630.htm"
        )

    def test_a_document_ref_carries_the_filing_date_as_its_publication_date(self, index):
        # The date the filing was accepted, not the period it covers. That is when the
        # information became public, and it is the only date a point-in-time rule can
        # honestly use.
        fy2020 = next(f for f in index.filings if f.accession == "0000789019-20-000039")

        ref = fy2020.to_ref(index.cik, entity_name="MICROSOFT CORP")

        assert ref.publication_date == date(2020, 7, 30)
        assert ref.accession == "0000789019-20-000039"
        assert "MICROSOFT CORP" in ref.title


class TestOlderFilings:
    def test_the_reference_to_older_filings_is_parsed(self, index):
        assert len(index.older_files) == 1
        assert index.older_files[0].count == 1024
        assert index.older_files[0].url.endswith("CIK0000789019-submissions-001.json")


class TestParsingFailures:
    def test_a_response_with_no_cik_is_refused(self):
        with pytest.raises(ExternalServiceError, match="no cik field"):
            parse_submissions(b'{"name": "Something"}')

    def test_html_is_refused_as_retryable(self):
        with pytest.raises(ExternalServiceError) as excinfo:
            parse_submissions(b"<html>Rate limited</html>")

        assert excinfo.value.retryable is True

    def test_an_entity_with_no_filings_parses_to_an_empty_index(self):
        # A newly registered filer. Empty is a valid answer, not an error.
        index = parse_submissions(b'{"cik": "1", "name": "New Co", "filings": {"recent": {}}}')

        assert index.filings == ()
