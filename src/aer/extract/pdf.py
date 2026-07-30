"""PDF to text, with a page and a box for every word.

**Bump :data:`VERSION` whenever the output could change.** Same contract as the HTML extractor
and the same reason: every citation records the version that produced its locator, so a silent
change to the line-grouping tolerance or the page separator moves every stored offset.

**The text is assembled from word geometry, not taken from the library.** ``pdfplumber`` will
hand back a page of text, and on an ordinary filing that string is the same one this module
builds. The difference is not the output, it is where the guarantee comes from: here the text is
built *out of* the spans, so a character offset and a rectangle are two views of one list and
cannot disagree. Calling ``extract_text`` and separately asking for word positions would rely on
two code paths in a third-party library continuing to agree with each other, which is not a
contract it offers — and the failure mode is a citation that highlights the wrong figure, which
is more convincing than no highlight at all.

The tests hold the property rather than the implementation:
:meth:`~aer.core.schemas.extraction.PageMap` resolution is checked against every span slicing
back to exactly the word it names, on a multi-page document, in both directions.

**Rotated text comes out reversed, and this is a known defect.** ``pdfplumber`` orders characters
by their position along the page's x axis, so a 90-degree table heading extracts as
``elbat tnemges syawediS``. It is *extracted* rather than dropped, deliberately — the same rule
as hidden text, evidence first — but its reading order is wrong, and a sideways table in an
annual report will read as nonsense. Fixing it means grouping by the text matrix rather than by
x, which is a change to the layout algorithm and therefore to :data:`VERSION`; it is not folded
into this task. :func:`extract_pdf` does not pretend otherwise, and a test pins the behaviour so
that fixing it has to be a deliberate act.

**A scan is reported, never guessed at.** A PDF with no extractable text is almost always a
scanned document, and the honest answer is that it needs OCR — which is a non-goal. Returning
empty text would put a section with no evidence in front of a reviewer with nothing to say why.

**Injection scanning is inherited.** :func:`~aer.extract.injection.scan_text` needs only a
string, so every phrase-level signal works here with no new code. What is added is the
PDF-shaped equivalent of hidden markup: white-on-white text, glyphs too small to read, and text
positioned off the page. A PDF has no ``display:none``, but it has all three of those.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Final

import pdfplumber
from pdfplumber.utils.text import WordExtractor

from aer.core.schemas.extraction import (
    BBox,
    ExtractedTable,
    ExtractedText,
    PageMap,
    PageSpan,
    TableCell,
)
from aer.core.schemas.injection import Finding, InjectionSignal
from aer.extract.errors import UnextractableError
from aer.extract.injection import scan_text
from aer.extract.result import ExtractedDocument

__all__ = ["EXTRACTOR", "VERSION", "extract_pdf"]

EXTRACTOR: Final = "pdf"
VERSION: Final = "1"

# Words whose tops differ by less than this are on the same line. In PDF points, and the same
# default `pdfplumber` uses for its own line grouping. It has to tolerate something: a footnote
# marker or a superscript sits a point or two above the text it belongs to, and splitting a line
# there would put a line break in the middle of a sentence.
_LINE_TOLERANCE: Final = 3.0

_WORD_SEPARATOR: Final = " "
_LINE_SEPARATOR: Final = "\n"

# A blank line between pages. Enough that a reader can see where one ended, and no more — the
# page numbers live in the map, which is a better place for them than in the prose.
_PAGE_SEPARATOR: Final = "\n\n"

_BBOX_PLACES: Final = 2

# Below this height, in points, a glyph is not being read by anybody. Ordinary filing footnotes
# run to 6pt, so this sits well below them: the target is text sized to be invisible, not text
# sized to be tedious.
_UNREADABLE_POINTS: Final = 2.5

# How close to white counts as white, per channel, from 0 to 1. Not an equality test, because a
# document hiding text has every reason to use `#fefefe`.
_NEAR_WHITE: Final = 0.95

_CMYK_CHANNELS: Final = 4

# Enough hidden characters to be a message rather than an artefact. Matches the HTML scanner's
# threshold, for the same reason: one white space character is not an attack.
_WORTH_REPORTING: Final = 24

# Findings of one kind, per document. A PDF that white-texts every page would otherwise produce
# a finding per page, and a reviewer needs to know that it happened rather than how often.
_FINDINGS_PER_SIGNAL: Final = 5


def extract_pdf(data: bytes) -> ExtractedDocument:
    """Extract the readable text of a PDF, with a page map, its tables and any findings.

    Raises:
        UnextractableError: The document has no extractable text — a scan, or a PDF whose
            content is entirely images. Distinct from a parse failure: the file is intact.
    """
    parts: list[str] = []
    spans: list[PageSpan] = []
    marks: list[_Mark] = []
    tables: list[ExtractedTable] = []
    offset = 0
    page_count = 0

    with pdfplumber.open(io.BytesIO(data)) as document:
        for page in document.pages:
            page_count += 1
            if offset:
                parts.append(_PAGE_SEPARATOR)
                offset += len(_PAGE_SEPARATOR)

            rendered, page_spans, page_marks = _read_page(page, offset=offset)
            parts.append(rendered)
            spans.extend(page_spans)
            marks.extend(page_marks)
            tables.extend(_tables_on(page))
            offset += len(rendered)

    text = "".join(parts)
    if not text.strip():
        message = (
            "The PDF contains no extractable text. A scanned filing looks exactly like this: the "
            "pages are images, and reading them needs OCR, which this platform does not do. "
            "Reported rather than returned as empty text, because a section with no evidence and "
            "no explanation is worse than a refusal."
        )
        raise UnextractableError(
            message, context={"extractor": EXTRACTOR, "pages": page_count, "bytes": len(data)}
        )

    pages = PageMap(spans=tuple(spans), page_count=page_count)

    return ExtractedDocument(
        text=ExtractedText(text=text, extractor=EXTRACTOR, extractor_version=VERSION, title=None),
        findings=_findings(marks, pages) + _located(scan_text(text), pages),
        pages=pages,
        tables=tuple(tables),
    )


def _located(findings: tuple[Finding, ...], pages: PageMap) -> tuple[Finding, ...]:
    """The inherited text findings, with their pages and boxes filled in.

    :func:`~aer.extract.injection.scan_text` knows only about a string, so its locators carry
    offsets and nothing else. Leaving them that way would mean a reviewer could be shown the page
    for a hidden passage but not for the instruction hidden in it — the same document, the same
    scan, two different standards of evidence.
    """
    return tuple(
        finding
        if finding.locator is None
        else finding.model_copy(update={"locator": pages.enrich(finding.locator)})
        for finding in findings
    )


# -- Reading a page ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Mark:
    """A word styled so as not to be read, held until the whole document is known.

    Held rather than turned straight into a finding because the interesting unit is the run:
    "forty characters were white on white" is a finding, and forty findings each saying "this
    word was white" is a list nobody reads.
    """

    signal: InjectionSignal
    what: str
    text: str
    char_start: int
    char_end: int


def _read_page(page: Any, *, offset: int) -> tuple[str, list[PageSpan], list[_Mark]]:
    """One page's text, a span per word, and anything styled to be unreadable.

    ``offset`` is where this page's text begins in the document's text, so spans come out
    already absolute and nothing has to renumber them afterwards.

    ``page`` is untyped because ``pdfplumber`` ships no stubs. Everything read from it is
    converted at the boundary rather than trusted, which is why the ``float(...)`` calls below
    are not redundant.

    **``WordExtractor`` rather than ``page.extract_words``**, because the marks need each word's
    glyphs and this is the one call that yields both. Asking ``extract_words`` for the attributes
    instead, via ``extra_attrs``, makes it split a word wherever an attribute changes — and a
    rotated word's glyphs report a spread of sizes, so ``Sideways`` came back as
    ``S``, ``i``, ``ed``, ``w``, ``a``, ``sy``. The two calls are otherwise the same grouping,
    which a test asserts directly rather than taking on trust.
    """
    words = list(WordExtractor().iter_extract_tuples(page.chars))
    if not words:
        return "", [], []

    parts: list[str] = []
    spans: list[PageSpan] = []
    marks: list[_Mark] = []
    cursor = offset
    previous_top: float | None = None
    number = int(page.page_number)

    for word, chars in words:
        top = float(word["top"])
        if previous_top is not None:
            same_line = abs(top - previous_top) <= _LINE_TOLERANCE
            separator = _WORD_SEPARATOR if same_line else _LINE_SEPARATOR
            parts.append(separator)
            cursor += len(separator)
        previous_top = top

        content = str(word["text"])
        spans.append(
            PageSpan(
                char_start=cursor,
                char_end=cursor + len(content),
                page=number,
                bbox=_bbox(word),
            )
        )
        marks.extend(_marks_for(word, chars, content, char_start=cursor, page=page))
        parts.append(content)
        cursor += len(content)

    return "".join(parts), spans, marks


def _bbox(word: dict[str, Any]) -> BBox:
    """A word's rectangle, measured from the top-left of the page, rounded for storage."""
    return (
        round(float(word["x0"]), _BBOX_PLACES),
        round(float(word["top"]), _BBOX_PLACES),
        round(float(word["x1"]), _BBOX_PLACES),
        round(float(word["bottom"]), _BBOX_PLACES),
    )


# -- Tables --------------------------------------------------------------------------------------


def _tables_on(page: Any) -> list[ExtractedTable]:
    """Every table detected on a page, with each cell located.

    The text and the geometry come from two different calls — ``extract`` for what the cells
    say, ``rows[].cells`` for where they are — and the two grids can disagree in shape when a
    table is malformed. Where they do, the geometry wins for position and the text is taken
    only where a cell exists in both: a cell with text and no box could not be shown to a
    reviewer, and a box with no text has nothing to show.
    """
    found: list[ExtractedTable] = []
    number = int(page.page_number)

    for table in page.find_tables():
        contents = table.extract()
        rows: list[tuple[TableCell | None, ...]] = []

        for index, row in enumerate(table.rows):
            texts = contents[index] if index < len(contents) else []
            cells: list[TableCell | None] = []
            for column, box in enumerate(row.cells):
                if box is None:
                    cells.append(None)
                    continue
                value = texts[column] if column < len(texts) else None
                cells.append(TableCell(text=_flatten(value), bbox=_round(box)))
            rows.append(tuple(cells))

        found.append(ExtractedTable(page=number, bbox=_round(table.bbox), rows=tuple(rows)))

    return found


def _flatten(value: str | None) -> str:
    """Cell text with its internal line breaks collapsed.

    A cell wrapping onto two lines is one value, and ``"Intelligent\\nCloud"`` compares unequal
    to the ``"Intelligent Cloud"`` a person would write in a truth set for no reason a reader
    would accept.
    """
    return " ".join((value or "").split())


def _round(box: tuple[float, ...]) -> BBox:
    x0, top, x1, bottom = (round(float(v), _BBOX_PLACES) for v in box[:4])
    return x0, top, x1, bottom


# -- What a PDF does instead of display:none -------------------------------------------------------


def _marks_for(
    word: dict[str, Any],
    chars: list[dict[str, Any]],
    content: str,
    *,
    char_start: int,
    page: Any,
) -> list[_Mark]:
    """The three ways a PDF hides text where HTML would use ``display:none``.

    Colour and size come from ``chars`` rather than from the word, and the aggregation is chosen
    so that **anything readable makes the whole word readable**: white only if every glyph is
    white, too small only if every glyph is too small. The other way round would flag a word with
    one stray glyph in it, and there is a common source of those — a rotated word's glyphs report
    a spread of sizes derived from the text matrix, so taking the minimum would report every
    sideways heading in an annual report as hidden.
    """
    found: list[_Mark] = []
    char_end = char_start + len(content)

    def mark(signal: InjectionSignal, what: str) -> _Mark:
        return _Mark(
            signal=signal, what=what, text=content, char_start=char_start, char_end=char_end
        )

    if chars and all(_is_white(char.get("non_stroking_color")) for char in chars):
        found.append(mark(InjectionSignal.INVISIBLE_STYLING, "drawn in white on white"))

    sizes = [float(char["size"]) for char in chars if char.get("size") is not None]
    if sizes and max(sizes) < _UNREADABLE_POINTS:
        found.append(mark(InjectionSignal.HIDDEN_TEXT, f"set at {max(sizes):.2g}pt, too small"))

    if _is_offpage(word, page):
        found.append(mark(InjectionSignal.OFFSCREEN_TEXT, "positioned outside the page"))

    return found


def _is_white(colour: object) -> bool:
    """Whether a fill colour is white, or near enough that nothing is readable in it.

    A colour arrives as a tuple whose length says which space the document declared: one channel
    for greyscale, three for RGB, four for CMYK. All three appear in real filings, and **CMYK
    means the opposite of the other two** — it is subtractive, so white is the absence of ink and
    every channel is at zero rather than at one. A check written for RGB reads CMYK white as
    black and misses it entirely, which is worth stating because the two rules look equally
    plausible in either direction until a fixture decides it.

    An unrecognised shape is not white. Guessing would flag colours that are perfectly visible,
    and a badge on an ordinary filing is a badge nobody reads.
    """
    if not isinstance(colour, tuple | list) or not colour:
        return False

    try:
        channels = [float(c) for c in colour]
    except (TypeError, ValueError):  # pragma: no cover -- defensive against odd colour spaces
        return False

    if len(channels) == _CMYK_CHANNELS:
        return all(c <= 1 - _NEAR_WHITE for c in channels)
    return all(c >= _NEAR_WHITE for c in channels)


def _is_offpage(word: dict[str, Any], page: Any) -> bool:
    """Whether a word sits outside the page's own bounds.

    A PDF's canvas is unbounded and only the media box is shown, so text placed outside it is in
    the file, is extracted, and is invisible to a reader. That is the same trick as a large
    negative text indent in HTML, and it has no innocent version in a filing.
    """
    return bool(
        float(word["x1"]) <= 0
        or float(word["bottom"]) <= 0
        or float(word["x0"]) >= float(page.width)
        or float(word["top"]) >= float(page.height)
    )


def _findings(marks: list[_Mark], pages: PageMap) -> tuple[Finding, ...]:
    """Runs of marked words, as findings a reviewer can be shown.

    Adjacent marks of the same kind merge into one finding covering the run, so a hidden
    sentence reads as a hidden sentence rather than as nine hidden words.
    """
    findings: list[Finding] = []
    emitted: dict[InjectionSignal, int] = {}
    suppressed: set[InjectionSignal] = set()

    for run in _runs(marks):
        signal = run[0].signal
        content = _WORD_SEPARATOR.join(mark.text for mark in run)
        if len(content) < _WORTH_REPORTING:
            continue

        # Counted only once a run is worth reporting, so a page of short white spacers cannot
        # spend the budget that the one hidden paragraph needs.
        if emitted.get(signal, 0) >= _FINDINGS_PER_SIGNAL:
            suppressed.add(signal)
            continue
        emitted[signal] = emitted.get(signal, 0) + 1

        findings.append(
            Finding.of(
                signal,
                detail=f"{len(content)} characters were {run[0].what}",
                evidence=content,
                locator=pages.locate(run[0].char_start, run[-1].char_end),
            )
        )

    findings.extend(
        Finding.of(signal, detail="further occurrences of the same kind, not all listed")
        for signal in sorted(suppressed)
    )
    return tuple(findings)


def _runs(marks: list[_Mark]) -> list[list[_Mark]]:
    """Marks grouped into consecutive runs of the same signal.

    Consecutive in the *text*, which is what makes a run a passage: two white words either side
    of a black one are two findings, because a reader sees something in between.
    """
    runs: list[list[_Mark]] = []
    for mark in marks:
        current = runs[-1] if runs else None
        adjoins = (
            current is not None
            and current[-1].signal == mark.signal
            # One separator between them -- a space or a newline -- and nothing else.
            and mark.char_start - current[-1].char_end <= 1
        )
        if adjoins and current is not None:
            current.append(mark)
        else:
            runs.append([mark])
    return runs
