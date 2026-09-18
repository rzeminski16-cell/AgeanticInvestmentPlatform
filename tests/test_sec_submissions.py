"""The filing index.

Two things are being checked: that the columnar shape is unpacked correctly, and that a
*ragged* columnar shape is refused. The second matters more than it looks — misaligned
parallel arrays produce a filing index that is wrong in a way nothing downstream could
detect.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from aer.errors import ExternalServiceError
from aer.sources.sec.submissions import (
    ANNUAL_FORMS,
    Filing,
    parse_submissions,
)
from tests.sec_fixtures import MSFT_CIK, fixture_bytes


def _payload(**columns: list[str]) -> bytes:
    """One submissions index in the columnar shape EDGAR serves."""
    return json.dumps(
        {
            "cik": MSFT_CIK,
            "name": "MICROSOFT CORP",
            "filings": {"recent": columns, "files": []},
        }
    ).encode()


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
    def test_of_form_selects_by_form_type(self, index):
        annuals = index.of_form(ANNUAL_FORMS)

        assert len(annuals) == 3
        assert all(f.form == "10-K" for f in annuals)

    def test_latest_is_the_newest_filing_of_the_form(self, index):
        # "The latest annual report" is the newest one the index lists (ADR 0113): the
        # FY2022 10-K, not the FY2020 one three filings back.
        newest = index.latest(ANNUAL_FORMS)

        assert newest is not None
        assert newest.accession == "0000789019-22-000010"

    def test_latest_returns_none_when_nothing_matches(self, index):
        assert index.latest(frozenset({"S-1"})) is None


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
        # information became public, and it is the only date a filing's provenance can
        # honestly carry.
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


class TestTheItemCodesSurviveParsing:
    """The column EDGAR uses to say what a filing is about, which the row was discarding.

    It was already in `_OPTIONAL_COLUMNS` and already survived `_validated_columns`; the
    `Filing` dataclass simply had no field for it. So a run chose its five current reports
    by date alone and read *Total Voting Rights* while the half-year results sat one row
    further down (ADR 0126).
    """

    def test_an_item_bearing_row_round_trips(self) -> None:
        index = parse_submissions(
            _payload(
                accessionNumber=["0000789019-26-000001"],
                filingDate=["2026-06-18"],
                form=["8-K"],
                primaryDocument=["c.htm"],
                items=["2.02,9.01"],
            )
        )

        assert index.filings[0].items == "2.02,9.01"

    def test_a_form_with_no_items_carries_an_empty_string(self) -> None:
        """A 6-K has none at all, and absent is not a missing value to be guessed at."""
        index = parse_submissions(
            _payload(
                accessionNumber=["0000789019-26-000002"],
                filingDate=["2026-06-18"],
                form=["6-K"],
                primaryDocument=["c.htm"],
                items=[""],
            )
        )

        assert index.filings[0].items == ""

    def test_an_index_without_the_column_at_all_still_parses(self) -> None:
        index = parse_submissions(
            _payload(
                accessionNumber=["0000789019-26-000003"],
                filingDate=["2026-06-18"],
                form=["8-K"],
                primaryDocument=["c.htm"],
            )
        )

        assert index.filings[0].items == ""


class TestAnAccessionIsAFolder:
    """ADR 0126. The submissions index names one file in it; the folder holds the rest."""

    def test_the_folder_and_the_index_are_built_from_the_identifiers(self) -> None:
        filing = Filing(
            accession="0001193125-26-380280",
            form="8-K",
            filing_date=date(2026, 9, 2),
            report_date=None,
            primary_document="d291965d8k.htm",
            description="",
            is_xbrl=True,
        )

        assert filing.folder("0000789019").endswith("/789019/000119312526380280")
        assert filing.header_url("0000789019").endswith("/0001193125-26-380280-index-headers.html")
        assert filing.url("0000789019").endswith("/d291965d8k.htm")

    def test_an_exhibit_reference_inherits_its_filing_s_date_and_accession(self) -> None:
        """Every file in an accession is published by the filing that opened it, so an
        exhibit's provenance needs no new rule."""
        filing = Filing(
            accession="0001193125-26-380280",
            form="8-K",
            filing_date=date(2026, 9, 2),
            report_date=None,
            primary_document="d291965d8k.htm",
            description="",
            is_xbrl=True,
        )

        ref = filing.exhibit_ref(
            "0000789019",
            filename="d291965dex991.htm",
            document_type="EX-99.1",
            entity_name="MICROSOFT CORP",
        )

        assert ref.url.endswith("/000119312526380280/d291965dex991.htm")
        assert ref.publication_date == filing.filing_date
        assert ref.accession == filing.accession
        assert "EX-99.1" in ref.title
