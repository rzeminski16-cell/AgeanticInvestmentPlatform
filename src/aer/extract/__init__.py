"""Turning archived bytes into text a citation can point at.

**The front door is :func:`extract_text`, and it takes an artefact hash rather than bytes.**
That is not a convenience — it is the reason the layer can be trusted. Every extraction is
therefore of *the archived copy*, verified against its own hash on the way out of the store, and
never of a response someone happened to still be holding in memory. If those two could differ,
the text a claim rests on and the document a citation resolves to would be different things, and
no amount of verification downstream would notice.

The controls this layer applies are threat T5, and they live in:

* :mod:`aer.extract.sniff` — what the bytes actually are, ignoring what they were labelled.
* :mod:`aer.extract.xml` — the one hardened ``lxml`` parser: no entities, no DTD, no network.
* :mod:`aer.extract.sandbox` — size ceiling, then a child process with a clock and a cap.

What comes out is an :class:`aer.core.schemas.extraction.ExtractedText` whose ``extractor`` and
``extractor_version`` pin the function that produced it, so a stored locator keeps meaning
exactly one thing. See that module for the verification contract in full.
"""

from __future__ import annotations

from aer.config import Settings
from aer.core.schemas.extraction import ExtractedText
from aer.extract.errors import (
    DocumentTooLargeError,
    ExtractionError,
    MediaTypeMismatchError,
    ParseFailedError,
    ParseTimeoutError,
    UnextractableError,
)
from aer.extract.result import ExtractedDocument
from aer.extract.sandbox import extract_in_sandbox
from aer.storage.protocol import ArtefactStore

__all__ = [
    "DocumentTooLargeError",
    "ExtractedDocument",
    "ExtractedText",
    "ExtractionError",
    "MediaTypeMismatchError",
    "ParseFailedError",
    "ParseTimeoutError",
    "UnextractableError",
    "extract_bytes",
    "extract_text",
]


async def extract_text(
    store: ArtefactStore,
    *,
    sha256: str,
    extractor: str,
    settings: Settings,
) -> ExtractedDocument:
    """Extract an archived document, by hash.

    The store verifies the digest as it reads, so a tampered artefact fails here rather than
    producing text that would then be cited (threat T8).
    """
    data = await store.read(sha256)
    return await extract_bytes(data, extractor=extractor, settings=settings)


async def extract_bytes(data: bytes, *, extractor: str, settings: Settings) -> ExtractedDocument:
    """Extract from bytes already in hand.

    For the acquisition path, which has just written the artefact and holds the bytes, and for
    tests. **Prefer :func:`extract_text` everywhere else**: reading back by hash is what proves
    the text came from the archived copy, and a caller that passes bytes is asserting that
    rather than demonstrating it.
    """
    return await extract_in_sandbox(
        data,
        extractor=extractor,
        max_bytes=settings.max_parse_bytes,
        timeout_seconds=settings.parse_timeout_seconds,
        memory_limit_bytes=settings.max_parse_memory_bytes,
    )
