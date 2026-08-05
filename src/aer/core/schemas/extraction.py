"""Where a piece of text is, precisely enough to find it again.

**This is the foundation the citation verifier stands on**, so the contract it defines matters
more than the code implementing it.

A locator does not point into the archived bytes. It points into the **text a named extractor
at a named version produces from those bytes**, and it says where in that text the excerpt
begins and ends. That indirection is not a convenience; it is the only thing that can work.
Neither ``selectolax`` nor ``lxml`` exposes source positions for text nodes, so a byte offset
into the original HTML is not obtainable — and even if it were, it would point at markup
rather than at the sentence a reader is being asked to check.

So the verification contract is:

    artefact SHA-256 + extractor + extractor version + locator  →  exactly one excerpt

Every part is load-bearing. The hash fixes the input. The extractor and its version fix the
function. The locator fixes the slice. Change any one and the excerpt may legitimately differ,
which is why all four are recorded on every citation rather than being assumed.

**Extraction must therefore be deterministic**, and that is a property the extractors are
tested for directly rather than trusted to have. An extractor whose output varies between runs
would make every citation resting on it unverifiable, without anything failing loudly.

:attr:`ExtractedText.content_hash` exists for the case where the function *does* change:
a locator whose recorded hash no longer matches gets "the extractor changed" rather than
"the excerpt is wrong", and those need different responses.
"""

from __future__ import annotations

import bisect
import unicodedata
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aer.core.hashing import sha256_hex

__all__ = [
    "BBox",
    "Excerpt",
    "ExtractedTable",
    "ExtractedText",
    "Locator",
    "PageMap",
    "PageSpan",
    "TableCell",
    "comparable",
    "normalise_whitespace",
]

# A rectangle on a page, as ``(x0, top, x1, bottom)`` in PDF points measured from the
# **top-left** of the page.
#
# The origin is stated because PDF itself uses the bottom-left and this does not: a bounding box
# is read by a person looking at a rendered page, and every viewer that could highlight one
# measures downward. Getting this wrong flips every box vertically, which is the kind of bug
# that looks plausible on page one of a two-page document.
type BBox = tuple[float, float, float, float]

# Bounding boxes are rounded to this many decimal places before they are stored. A hundredth of a
# point is a five-thousandth of a millimetre, far below anything a rendering could show, so
# nothing is lost.
#
# **This is for legibility, not for correctness.** A round trip through JSON preserves a float
# exactly, so the rounding is not what makes a stored locator survive being written down — that
# was checked, and removing the rounding breaks nothing. What it buys is a coordinate a person can
# read in a database row or a log line without `72.00000000000001` in it, and a value that two
# equal boxes agree on digit for digit.
_BBOX_PLACES: Final = 2


def normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip the ends.

    Applied on both sides of every excerpt comparison. A document reflowed by a different
    parser version, or an excerpt a model echoed back with its line breaks rearranged, is the
    same excerpt — and refusing it would fail a citation that is genuinely correct. Whitespace
    is the only difference tolerated here; a comparison that ignored punctuation or case would
    start accepting excerpts that say something else.
    """
    return " ".join(text.split())


# Characters that carry no meaning a reader could see, and which two extractions of the same
# document can legitimately disagree about. Removed before comparison; nothing else is.
#
# Written as escapes rather than as literals throughout this section. A reader of the source
# cannot tell an en dash from a hyphen, or a non-breaking space from a space, by looking — which
# is the entire reason these tables exist.
_INVISIBLE: Final = str.maketrans(
    {
        "\u00ad": None,  # soft hyphen, inserted at a line break
        "\u200b": None,  # zero-width space
        "\u200c": None,  # zero-width non-joiner
        "\u200d": None,  # zero-width joiner
        "\ufeff": None,  # byte-order mark / zero-width no-break space
    }
)

# Typographic variants that mean the same thing. A filing set in a proportional font and the
# same filing pasted into a plain-text field differ by exactly these.
_PUNCTUATION: Final = str.maketrans(
    {
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark, and the typographic apostrophe
        "\u201c": '"',  # left double quotation mark
        "\u201d": '"',  # right double quotation mark
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2212": "-",  # minus sign
        "\u00a0": " ",  # non-breaking space
        "\u2032": "'",  # prime
    }
)


def comparable(text: str) -> str:
    """The form two excerpts are compared in: invisible differences folded away, nothing else.

    **What is folded, and why each one.** Unicode compatibility composition (so a ligature or
    a full-width digit equals its plain form), the invisible characters above, the typographic
    variants above, and finally whitespace. Every one of those is a difference no reader can
    see and that two extractions of the same bytes can legitimately disagree about.

    **What is not folded: case, punctuation that is not a variant, word order, digits, and
    anything else.** A comparison that ignored case would accept "NOT" for "not"; one that
    ignored punctuation would accept "$1,234" for "$1234", which are different numbers in some
    filings and the same in others — and a verifier is not the place to be deciding which.

    Used by :mod:`aer.verify.citations`, which requires the two forms to be **equal**. Fuzzy
    similarity is reported on a failure and never admits one; see ADR 0025.
    """
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_INVISIBLE).translate(_PUNCTUATION)
    return normalise_whitespace(folded)


class Locator(BaseModel):
    """Where an excerpt sits inside an extraction's text.

    Half-open, like a Python slice: ``text[char_start:char_end]``. Stated because an
    off-by-one in a locator is a citation that points one character away from what it claims,
    which no amount of fuzzy matching makes right.

    ``page`` and ``bbox`` are empty for HTML, which has no pages, and filled for PDF, where a
    character offset alone is not something a human can check against the page in front of them.
    **They are display coordinates and nothing verifies against them** — the check in
    :mod:`aer.verify.citations` slices the text at ``char_start:char_end`` and compares. That
    separation is deliberate: the page and box exist so a reviewer can find the passage, and
    making verification depend on them would make it depend on a rendering geometry that a
    library upgrade can legitimately shift by a fraction of a point.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)

    page: int | None = Field(default=None, ge=1)
    bbox: BBox | None = None

    @model_validator(mode="after")
    def _runs_forwards(self) -> Self:
        if self.char_end <= self.char_start:
            message = (
                f"A locator must span at least one character: char_start={self.char_start} "
                f"is not before char_end={self.char_end}."
            )
            raise ValueError(message)
        return self

    @property
    def length(self) -> int:
        return self.char_end - self.char_start


class PageSpan(BaseModel):
    """One run of extracted text, and the rectangle it occupies on its page.

    The unit is a word. A line would be a tenth of the data and would answer "which row?"
    rather than "which figure?" — and in a filing the thing a reviewer wants boxed is usually a
    single number in a table cell.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    page: int = Field(ge=1)
    bbox: BBox


class PageMap(BaseModel):
    """Where every run of an extraction's text sits on the page it came from.

    **Produced by paginated extractors and by nothing else.** HTML has no pages, so its
    extraction carries no map and its locators carry no boxes; that is an honest absence rather
    than a gap to fill with zeroes.

    Not persisted. Like the extracted text itself it is regenerable from the artefact, and what
    *is* stored — the page and box on a locator — is the answer rather than the lookup table
    that produced it.

    Spans are ordered by ``char_start`` and do not overlap, which is what makes resolution a
    binary search rather than a scan. Both properties come from how the text is built: it is
    assembled *from* the spans, so they cannot disagree with it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    spans: tuple[PageSpan, ...] = ()
    page_count: int = Field(ge=0)

    def resolve(self, char_start: int, char_end: int) -> tuple[int, BBox] | None:
        """The page and bounding box covering ``[char_start, char_end)``, if any.

        The box is the union of every span the range touches **on the first page it touches**.
        An excerpt running across a page break gets the part on the earlier page rather than a
        rectangle spanning both, because a box covering two pages describes a region that does
        not exist on either.

        Returns ``None`` when the range touches no span — which is not a failure. Separators
        live between spans, so a locator over nothing but a line break has no geometry, and
        inventing one would be worse than saying so.
        """
        touched = self._touching(char_start, char_end)
        if not touched:
            return None

        page = touched[0].page
        boxes = [span.bbox for span in touched if span.page == page]
        return page, _union(boxes)

    def locate(self, char_start: int, char_end: int) -> Locator:
        """A locator over ``[char_start, char_end)`` with its page and box filled in."""
        found = self.resolve(char_start, char_end)
        if found is None:
            return Locator(char_start=char_start, char_end=char_end)
        page, bbox = found
        return Locator(char_start=char_start, char_end=char_end, page=page, bbox=bbox)

    def enrich(self, locator: Locator) -> Locator:
        """``locator`` with page and box added. Existing coordinates are left alone."""
        if locator.page is not None:
            return locator
        return self.locate(locator.char_start, locator.char_end)

    def _touching(self, char_start: int, char_end: int) -> list[PageSpan]:
        """Every span overlapping the half-open range, in order.

        The bisect is keyed on ``char_end`` because a span qualifies when it *ends* after the
        range begins; from the first such span, walking forward until one begins at or after the
        range ends visits exactly the overlapping ones and stops.

        Keyed rather than over a precomputed list of ends, because building that list is the
        linear scan the search exists to avoid — and an annual report's map has a span per word.
        """
        if not self.spans or char_end <= char_start:
            return []

        first = bisect.bisect_right(self.spans, char_start, key=lambda span: span.char_end)
        found: list[PageSpan] = []
        for index in range(first, len(self.spans)):
            span = self.spans[index]
            if span.char_start >= char_end:
                break
            found.append(span)
        return found


def _union(boxes: list[BBox]) -> BBox:
    """The smallest box containing all of them, rounded for storage."""
    return (
        round(min(b[0] for b in boxes), _BBOX_PLACES),
        round(min(b[1] for b in boxes), _BBOX_PLACES),
        round(max(b[2] for b in boxes), _BBOX_PLACES),
        round(max(b[3] for b in boxes), _BBOX_PLACES),
    )


class TableCell(BaseModel):
    """One cell of a detected table, and where it is on the page.

    ``text`` is empty for a cell that exists in the grid and holds nothing — which is different
    from a cell that is not in the grid at all, and that one is ``None`` in the row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    bbox: BBox


class ExtractedTable(BaseModel):
    """A table found on a page, with every cell located.

    **Rows are ragged on purpose.** A merged cell leaves a hole in the grid, and filling it with
    a repeat of its neighbour would state something the document does not: that two columns
    reported the same figure. ``None`` says "no cell here", which is what is true.

    This is what makes the acceptance criterion checkable — a number read out of a table can be
    pointed at, on a page, in a box, rather than asserted to be in there somewhere.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int = Field(ge=1)
    bbox: BBox
    rows: tuple[tuple[TableCell | None, ...], ...] = ()

    def cell(self, row: int, column: int) -> TableCell | None:
        """The cell at ``(row, column)``, or ``None`` if the grid has no such cell."""
        if not 0 <= row < len(self.rows):
            return None
        cells = self.rows[row]
        if not 0 <= column < len(cells):
            return None
        return cells[column]

    def as_text(self) -> tuple[tuple[str | None, ...], ...]:
        """The cell text alone, for comparing against a hand-labelled truth set."""
        return tuple(
            tuple(None if cell is None else cell.text for cell in row) for row in self.rows
        )


class Excerpt(BaseModel):
    """A span of text and where it came from. The unit a citation points at."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    locator: Locator

    @property
    def normalised(self) -> str:
        return normalise_whitespace(self.text)


class ExtractedText(BaseModel):
    """The whole text one extractor produced from one document.

    Not persisted. It is regenerable from the artefact by construction — that is the entire
    point of recording the extractor and its version — and storing a second copy of every
    filing's text would double the disk for something derivable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    extractor: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    title: str | None = None

    @property
    def content_hash(self) -> str:
        """The hash of this text, so extractor drift is distinguishable from a bad excerpt.

        Of the text and nothing else. Injection findings travel beside an extraction rather
        than inside it — see :class:`aer.extract.ExtractedDocument` — partly to keep this module
        free of a dependency on the injection vocabulary, and partly because a new heuristic
        noticing something extra must not invalidate every locator recorded before it existed.
        """
        return sha256_hex(self.text.encode("utf-8"))

    def excerpt(self, locator: Locator) -> Excerpt:
        """The excerpt at ``locator``.

        Raises:
            ValueError: The locator runs past the end of the text. Refused rather than
                clamped: a truncated excerpt is a *different* excerpt, and one that would go
                on to verify against itself.
        """
        if locator.char_end > len(self.text):
            message = (
                f"Locator [{locator.char_start}:{locator.char_end}] runs past the end of "
                f"{len(self.text)} characters of extracted text."
            )
            raise ValueError(message)

        return Excerpt(text=self.text[locator.char_start : locator.char_end], locator=locator)

    def locate(self, needle: str, *, start: int = 0) -> Excerpt | None:
        """Find ``needle`` and return it as a located excerpt, or ``None``.

        A literal search, on purpose. This is how a caller that already knows the sentence it
        wants turns it into a locator; it is not a search feature, and making it clever would
        make the resulting locator's meaning unclear.
        """
        found = self.text.find(needle, start)
        if found < 0:
            return None
        return self.excerpt(Locator(char_start=found, char_end=found + len(needle)))
