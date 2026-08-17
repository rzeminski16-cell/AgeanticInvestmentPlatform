"""The one-page summary: a second renderer over the same document, never new analysis.

Gap O8. Forty pages is a reference document; most readings want the view, the numbers
behind it and the risks. This module narrows an assembled :class:`ReportDocument` to the
front matter, the at-a-glance block and the sections whose definition rows claim a place
(``evidence_policy.one_pager`` — data, seeded by migration 0040, so no section key
appears here).

**Footnote numbers are kept, not renumbered.** A marker on the one-pager reads the same
as in the full note, so the summary is an entry point into the reference document rather
than a parallel account of it. The notes block is trimmed to the markers actually shown,
and the sources appendix to the documents those notes cite.
"""

from __future__ import annotations

from dataclasses import replace

from aer.render.document import ReportDocument, SectionView, SourceFootnote
from aer.sections.render import Bullets, Fragment, Paragraph, Table

__all__ = ["summary_document"]


def summary_document(document: ReportDocument) -> ReportDocument:
    """The document narrowed to its one-page summary.

    A document none of whose sections claim a place still summarises honestly: header,
    at-a-glance block and the trimmed apparatus — what the run holds, nothing invented.
    """
    kept: list[SectionView] = [view for view in document.sections if view.one_pager]

    used: set[int] = set()
    for fragment in document.glance:
        used |= _markers_in(fragment)
    for view in kept:
        for fragment in view.fragments:
            used |= _markers_in(fragment)
        for chart in view.charts:
            used.update(chart.markers)

    footnotes = tuple(note for note in document.footnotes if note.number in used)
    cited_urls = {note.url for note in footnotes if isinstance(note, SourceFootnote)}

    return replace(
        document,
        sections=tuple(kept),
        charts=(),
        comps_paragraph=None,
        footnotes=footnotes,
        appendix=tuple(row for row in document.appendix if row.url in cited_urls),
        limitations=tuple(
            (title, note)
            for title, note in document.limitations
            if title in {view.title for view in kept}
        ),
        undated_note=document.undated_note if any(view.undated for view in kept) else None,
    )


def _markers_in(fragment: Fragment) -> set[int]:
    """Every footnote number a fragment shows, whichever shape carries it."""
    found: set[int] = set()
    match fragment:
        case Paragraph(markers=markers):
            found.update(markers)
        case Bullets(items=items):
            for item in items:
                found.update(item.markers)
        case Table(rows=rows):
            for row in rows:
                found.update(row.markers)
                for cell in row.cell_markers:
                    found.update(cell)
    return found
