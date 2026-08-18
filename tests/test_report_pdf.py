"""The report PDF pass: byte-stable, permission-flagged, bookmarked, honestly stamped.

The golden HTML fixture — the real template rendered from the fixed scene — is the input
here, so the bookmark and quality assertions run against the document the platform
actually produces, not a toy page. (`tests/test_pdf.py` is task 14's PDF *extraction*;
this file is task 48's PDF *production*.)
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pikepdf
import pytest
from weasyprint import HTML

from aer.render.document import UNDATED_MARKER
from aer.render.pdf import PDF_PRODUCER, finish_pdf, render_pdf

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fx_report"
GOLDEN_HTML = FIXTURES / "golden.html"

REPORT_ID = "11111111-2222-3333-4444-555555555555"
CONTENT_HASH = "c0ffee" * 10 + "beef"
APPROVED_AT = datetime(2022, 7, 2, 10, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
def golden_pdf() -> bytes:
    """One render of the golden document, shared: WeasyPrint is the slow part."""
    return render_pdf(
        GOLDEN_HTML.read_text(encoding="utf-8"),
        report_id=REPORT_ID,
        content_hash=CONTENT_HASH,
        approved_at=APPROVED_AT,
    )


class TestByteStability:
    def test_rendering_twice_produces_identical_bytes_in_time(self, golden_pdf: bytes) -> None:
        started = time.monotonic()
        again = render_pdf(
            GOLDEN_HTML.read_text(encoding="utf-8"),
            report_id=REPORT_ID,
            content_hash=CONTENT_HASH,
            approved_at=APPROVED_AT,
        )
        elapsed = time.monotonic() - started

        assert again == golden_pdf
        assert elapsed < 30

    def test_the_metadata_is_the_approvals_not_the_renders(self, golden_pdf: bytes) -> None:
        """Byte-stability is impossible if any wall clock reaches the file; the dates a
        reader sees are the approval's, which is the only date the document honestly has.
        """
        with pikepdf.open(BytesIO(golden_pdf)) as pdf:
            # The docinfo is synced from the XMP, which normalises the zone to +00'00.
            assert str(pdf.docinfo["/CreationDate"]) == "D:20220702101500+00'00"
            assert str(pdf.docinfo["/ModDate"]) == "D:20220702101500+00'00"
            assert str(pdf.docinfo["/Producer"]) == PDF_PRODUCER


class TestPermissionsAndForms:
    def test_the_permission_bits_refuse_modification(self, golden_pdf: bytes) -> None:
        with pikepdf.open(BytesIO(golden_pdf)) as pdf:
            assert pdf.is_encrypted
            allowed = pdf.allow
            assert not allowed.modify_annotation
            assert not allowed.modify_assembly
            assert not allowed.modify_form
            assert not allowed.modify_other
            # Reading is not restricted: the user password is empty and extraction is
            # allowed. Nothing here is confidential; the flags only refuse edits.
            assert allowed.extract

    def test_a_form_does_not_survive_the_pass(self) -> None:
        """A fillable field is a writable surface on a frozen document."""
        with pikepdf.open(BytesIO(_render_minimal())) as pdf:
            pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(Fields=pikepdf.Array()))
            buffer = BytesIO()
            pdf.save(buffer)

        finished = finish_pdf(
            buffer.getvalue(),
            report_id=REPORT_ID,
            content_hash=CONTENT_HASH,
            approved_at=APPROVED_AT,
        )
        with pikepdf.open(BytesIO(finished)) as pdf:
            assert "/AcroForm" not in pdf.Root


class TestTheXmp:
    def test_it_carries_the_report_id_and_content_hash(self, golden_pdf: bytes) -> None:
        with pikepdf.open(BytesIO(golden_pdf)) as pdf, pdf.open_metadata() as meta:
            assert meta.get("dc:identifier") == REPORT_ID
            description = str(meta.get("dc:description"))
            assert CONTENT_HASH in description
            assert "not regulated investment advice" in description
            assert meta.get("xmp:CreateDate") == "2022-07-02T10:15:00Z"


class TestTheBookmarks:
    def test_every_section_of_the_golden_document_is_bookmarked(self, golden_pdf: bytes) -> None:
        """One bookmark per section heading, from the heading structure alone — which is
        what makes a bookmark appear for a custom section with no code change.

        Compared with the undated-source marker stripped: the C3 dagger travels in the
        heading's own text, and a bookmark built from the heading faithfully carries it,
        so a section resting on an undated source is bookmarked as "Title †".
        """
        titles = {title.removesuffix(f" {UNDATED_MARKER}") for title in _outline_titles(golden_pdf)}

        for expected in (
            "Golden Overview",
            "Golden Warnings",
            "Golden Pending",
            "Golden Failed",
            "Golden Skipped",
            "Golden Unresolved",
            "Notes",
            "Sources",
        ):
            assert expected in titles, expected


def _outline_titles(pdf_bytes: bytes) -> list[str]:
    titles: list[str] = []

    def walk(items: Iterable[pikepdf.models.outlines.OutlineItem]) -> None:
        for item in items:
            titles.append(str(item.title))
            walk(item.children)

    with pikepdf.open(BytesIO(pdf_bytes)) as pdf, pdf.open_outline() as outline:
        walk(outline.root)
    return titles


def _render_minimal() -> bytes:
    """A tiny WeasyPrint PDF for tests that mutate before finishing."""
    produced = HTML(string="<html><body><h2>Alpha</h2></body></html>").write_pdf()
    assert produced is not None
    return produced
