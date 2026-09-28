"""The reader's document: the archived walk, with every marker a control (page specification §8).

The reader page prints the same fragments as the archived document, and the one thing the two
must agree on is a note's number — a marker on the page opens the note the archived document
gives that number. So these hold the reader's markers to :func:`render_html`'s, and hold the
reader's own additions — the drawer's four attributes, the section counts, the heading lifted
out for the page to set — to what the specification asks of them.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal

from aer.render.document import (
    CalculationFootnote,
    HeaderView,
    ReportDocument,
    SectionView,
    SourceFootnote,
    UnresolvedFootnote,
)
from aer.render.html import render_html, render_reader
from aer.sections.render import CitationRef, Heading, Paragraph, Table, TableRow

_MARKER = re.compile(r'<sup class="fn-ref"[^>]*><a[^>]*>(\d+)</a></sup>')


def _document() -> ReportDocument:
    valuation = SectionView(
        key="valuation",
        title="Valuation",
        origin="builtin",
        position=Decimal(1),
        fragments=(
            Heading(level=2, text="Valuation"),
            Paragraph(text="The equity is worth £10.00 a share", markers=(1,)),
            Table(
                columns=("Label", "Value"),
                rows=(
                    TableRow(cells=("Revenue", "£1,000m"), markers=(2,)),
                    TableRow(cells=("Tax", "£200m"), markers=(3,)),
                ),
            ),
            Paragraph(text="The same figure again", markers=(1,)),
        ),
        citations=(),
    )
    risks = SectionView(
        key="risks",
        title="Risks",
        origin="builtin",
        position=Decimal(2),
        fragments=(Heading(level=2, text="Risks"), Paragraph(text="Rates rise.", markers=(2,))),
        citations=(),
    )
    return ReportDocument(
        header=HeaderView(
            company_name="Contoso plc",
            ticker="CTSO",
            exchange="LSE",
            as_of=date(2026, 9, 25),
            base_currency="GBP",
            generated_at=datetime(2026, 9, 25, 9, 30, tzinfo=UTC),
            rating=None,
            confidence=None,
        ),
        sector=None,
        sections=(valuation, risks),
        comps=(),
        footnotes=(
            CalculationFootnote(
                number=1,
                formula="value per share = equity value / shares",
                value="£10.00",
                function_ref="aer.calc.dcf:value_per_share",
                code_version_prefix="3e0ecb0",
            ),
            SourceFootnote(
                number=2,
                title="Contoso plc annual report 2026",
                url="https://example.invalid/annual-report.pdf",
                publisher="Contoso plc",
                publication_date=date(2026, 7, 1),
                retrieved=date(2026, 9, 20),
                tier="a regulatory filing",
            ),
            UnresolvedFootnote(number=3, kind_label="calculation", identifier="gone"),
        ),
        appendix=(),
        citations=[
            CitationRef(kind="calculation", identifier="calc-1"),
            CitationRef(kind="source_document", identifier="doc-2"),
            CitationRef(kind="calculation", identifier="gone"),
        ],
    )


def _link(number: int) -> tuple[str, str]:
    return f"/runs/job/footnotes/{number}", f"/reports/report/notes/{number}"


class TestTheMarkersAreTheDocuments:
    def test_every_marker_carries_the_number_the_archived_document_gives_it(self) -> None:
        document = _document()
        archived = _MARKER.findall(render_html(document))

        reader = render_reader(document, link=_link)
        shown = [n for section in reader.sections for n in _MARKER.findall(section.body)]

        assert shown == archived
        assert shown == ["1", "2", "3", "1", "2"]

    def test_the_archived_document_is_unchanged_by_the_reader(self) -> None:
        document = _document()

        html = render_html(document)

        assert 'href="#fn-1"' in html
        assert "hx-get" not in html


class TestAMarkerOpensItsNote:
    def test_each_marker_is_the_drawers_trigger_and_a_link_without_it(self) -> None:
        reader = render_reader(_document(), link=_link)
        body = reader.sections[0].body

        assert 'href="/runs/job/footnotes/1"' in body
        assert 'hx-get="/reports/report/notes/1"' in body
        assert 'hx-target="#aer-drawer-body"' in body
        assert 'data-drawer-title="Note 1"' in body
        assert 'aria-label="Note 1"' in body

    def test_the_hover_sentence_survives(self) -> None:
        body = render_reader(_document(), link=_link).sections[0].body

        assert 'title="Calculated: value per share = equity value / shares = £10.00.' in body


class TestTheSpineCounts:
    def test_a_section_counts_its_notes_by_what_they_are(self) -> None:
        valuation = render_reader(_document(), link=_link).sections[0]

        assert (valuation.figures, valuation.citations, valuation.unresolved) == (1, 1, 1)

    def test_a_figure_marked_twice_counts_once(self) -> None:
        # Note 1 is marked twice in the valuation section; it is one figure.
        valuation = render_reader(_document(), link=_link).sections[0]

        assert valuation.figures == 1

    def test_a_note_shared_across_sections_counts_in_each(self) -> None:
        risks = render_reader(_document(), link=_link).sections[1]

        assert (risks.figures, risks.citations, risks.unresolved) == (0, 1, 0)


class TestTheHeadingIsThePages:
    def test_the_leading_heading_becomes_the_sections_title(self) -> None:
        valuation = render_reader(_document(), link=_link).sections[0]

        assert valuation.title == "Valuation"
        assert "<h2>" not in valuation.body
