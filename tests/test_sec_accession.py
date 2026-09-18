"""What is inside one accession, read from EDGAR's own dissemination header.

**Recorded fixtures, not constructed ones.** Everything else in `tests/fixtures/sec/` was
written by hand because the sandbox that built it could not reach `sec.gov`; these two were
fetched from EDGAR. That matters here more than elsewhere, because this module exists
*because* the documented-looking endpoint turned out not to say what the plan assumed —
`index.json` carries an icon name where the type should be — and a constructed fixture
would have reproduced the assumption rather than caught it (ADR 0126).
"""

from __future__ import annotations

from aer.sources.sec.accession import (
    AccessionDocument,
    parse_accession_documents,
    substantive_exhibits,
)
from tests.sec_fixtures import fixture_bytes

MSFT_8K = "accession_headers_msft_8k.html"
BOND_OFFERING_8K = "accession_headers_no_exhibit.html"


class TestTheHeaderNamesEveryDocument:
    def test_the_whole_folder_is_listed(self) -> None:
        documents = parse_accession_documents(fixture_bytes(MSFT_8K))

        assert len(documents) == 35, "the header's own PUBLIC-DOCUMENT-COUNT"

    def test_the_primary_document_comes_first_in_the_filer_s_own_sequence(self) -> None:
        """Order is what makes a cap take the earliest exhibits rather than an arbitrary
        subset: a filer furnishes the release before the slides."""
        documents = parse_accession_documents(fixture_bytes(MSFT_8K))

        assert documents[0].document_type == "8-K"
        assert documents[0].filename == "d291965d8k.htm"
        assert documents[1].document_type == "EX-99.1"

    def test_the_type_is_the_document_s_and_not_an_icon(self) -> None:
        """The whole reason this reads the header. `index.json` answers "text.gif" here,
        and a rule written against that field would have selected on a picture of a file.
        """
        types = {d.document_type for d in parse_accession_documents(fixture_bytes(MSFT_8K))}

        assert "EX-99.1" in types
        assert not any(value.endswith(".gif") for value in types)

    def test_unreadable_bytes_cost_the_exhibits_and_never_raise(self) -> None:
        """The header is an extra read on top of a filing already acquired."""
        assert parse_accession_documents(b"") == ()
        assert parse_accession_documents(b"\xff\xfe not a header at all") == ()

    def test_a_block_with_no_filename_is_not_a_document(self) -> None:
        payload = b"<DOCUMENT>\n<TYPE>EX-99.1\n</DOCUMENT>"

        assert parse_accession_documents(payload) == ()


class TestOnlyTheSubstanceIsTaken:
    def test_the_earnings_release_is_selected(self) -> None:
        documents = parse_accession_documents(fixture_bytes(MSFT_8K))

        kept = substantive_exhibits(documents, primary_document="d291965d8k.htm", limit=2)

        assert [d.filename for d in kept] == ["d291965dex991.htm"]

    def test_the_xbrl_bundle_is_not_an_exhibit(self) -> None:
        """EX-101.SCH and its siblings are the taxonomy the run already holds through
        `fetch_facts`, and they are not prose anything could cite."""
        documents = parse_accession_documents(fixture_bytes(MSFT_8K))

        kept = substantive_exhibits(documents, primary_document="d291965d8k.htm", limit=10)

        assert not any(d.document_type.startswith("EX-101") for d in kept)

    def test_a_bond_offering_brings_back_nothing(self) -> None:
        """EX-1.1, EX-3.1 and EX-4.1 are an underwriting agreement, articles and an
        indenture. A run that acquired those would have spent its fetches on paperwork."""
        documents = parse_accession_documents(fixture_bytes(BOND_OFFERING_8K))

        assert substantive_exhibits(documents, primary_document="d127076d8k.htm", limit=2) == ()

    def test_the_series_is_matched_whatever_the_case(self) -> None:
        documents = (AccessionDocument(document_type="ex-99.1", filename="release.htm"),)

        assert substantive_exhibits(documents, primary_document="cover.htm", limit=2)
