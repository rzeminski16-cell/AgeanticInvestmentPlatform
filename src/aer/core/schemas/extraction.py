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

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aer.core.hashing import sha256_hex

__all__ = ["Excerpt", "ExtractedText", "Locator", "normalise_whitespace"]


def normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip the ends.

    Applied on both sides of every excerpt comparison. A document reflowed by a different
    parser version, or an excerpt a model echoed back with its line breaks rearranged, is the
    same excerpt — and refusing it would fail a citation that is genuinely correct. Whitespace
    is the only difference tolerated here; a comparison that ignored punctuation or case would
    start accepting excerpts that say something else.
    """
    return " ".join(text.split())


class Locator(BaseModel):
    """Where an excerpt sits inside an extraction's text.

    Half-open, like a Python slice: ``text[char_start:char_end]``. Stated because an
    off-by-one in a locator is a citation that points one character away from what it claims,
    which no amount of fuzzy matching makes right.

    ``page`` and ``bbox`` are unused by the HTML extractor and carried for the PDF one, where
    a character offset alone is not something a human can check against the page in front of
    them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)

    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None

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
        """The hash of this text, so extractor drift is distinguishable from a bad excerpt."""
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
