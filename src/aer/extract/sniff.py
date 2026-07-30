"""What a document actually is, decided from its bytes.

``Content-Type`` is a claim made by whoever served the file. This module ignores it.

That is not paranoia for its own sake — it is threat T5 stated precisely. Handing a zip
archive to an XML parser is how a decompression bomb begins; handing a PDF to an HTML parser
produces a page of binary noise that an extractor will happily report as text and a model will
happily cite. Both are avoided by looking at the first few bytes, which is cheap, and by
refusing when the answer disagrees with what the caller asked for, which is free.

**Pure, and deliberately so.** No I/O, no configuration, no logging. Everything here is a
function of a byte string, which makes the whole table testable with literals.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = ["DetectedType", "sniff"]

# How far in to look. Enough for every signature below plus a generous run of leading
# whitespace, comments and byte-order marks, and small enough that sniffing a 50 MB filing
# costs nothing.
_WINDOW: Final = 2048


class DetectedType(StrEnum):
    """What the bytes look like.

    Coarse on purpose. The question this answers is "may this extractor have these bytes?",
    not "what exactly is this?" — a finer taxonomy would invite callers to branch on
    distinctions the extractors do not make.
    """

    HTML = "html"
    XML = "xml"
    PDF = "pdf"
    JSON = "json"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"


# Byte-order marks, stripped before anything else. A UTF-8 BOM in front of `<html>` is common
# enough from Windows-authored filings that not handling it would misclassify real documents.
_BOMS: Final[tuple[bytes, ...]] = (
    b"\xef\xbb\xbf",
    b"\xff\xfe",
    b"\xfe\xff",
)

# Signatures that identify a format outright, checked before any text heuristic. Archives and
# PDFs are the ones that matter: both are binary, both are refused by every text extractor, and
# both are what a hostile server sends when it labels something `text/html`.
_MAGIC: Final[tuple[tuple[bytes, DetectedType], ...]] = (
    (b"%PDF-", DetectedType.PDF),
    (b"PK\x03\x04", DetectedType.ARCHIVE),  # zip, and therefore xlsx/docx/odt
    (b"PK\x05\x06", DetectedType.ARCHIVE),  # empty zip
    (b"PK\x07\x08", DetectedType.ARCHIVE),  # spanned zip
    (b"\x1f\x8b", DetectedType.ARCHIVE),  # gzip
    (b"BZh", DetectedType.ARCHIVE),  # bzip2
    (b"\xfd7zXZ\x00", DetectedType.ARCHIVE),  # xz
    (b"7z\xbc\xaf\x27\x1c", DetectedType.ARCHIVE),
    (b"Rar!\x1a\x07", DetectedType.ARCHIVE),
)

# Markers that mean HTML even when the document also opens with an XML declaration — which
# every inline-XBRL filing does, and which is exactly the case that must not be misread.
_HTML_MARKERS: Final[tuple[bytes, ...]] = (
    b"<!doctype html",
    b"<html",
    b"<head",
    b"<body",
    b"<?xml-stylesheet",
)


def sniff(data: bytes) -> DetectedType:
    """Classify ``data`` by content.

    Order matters and is the substance of the function:

    1. **Binary signatures first.** They are unambiguous, and getting them wrong is the
       expensive mistake.
    2. **HTML markers before the XML declaration.** An inline-XBRL filing is served as XHTML
       and opens with ``<?xml``, so a naive check would classify every UK annual report as
       plain XML and hand it to the wrong extractor.
    3. **XML, then JSON, then unknown.** Each strictly weaker evidence than the last.

    A document with a leading byte-order mark or leading whitespace is classified as though it
    had neither; a real filing frequently has both.
    """
    window = _strip_bom(data[:_WINDOW])

    for signature, kind in _MAGIC:
        if window.startswith(signature):
            return kind

    stripped = window.lstrip()
    lowered = stripped.lower()

    if any(marker in lowered for marker in _HTML_MARKERS):
        return DetectedType.HTML
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        return DetectedType.XML
    if stripped[:1] in (b"{", b"["):
        return DetectedType.JSON

    return DetectedType.UNKNOWN


def _strip_bom(data: bytes) -> bytes:
    for bom in _BOMS:
        if data.startswith(bom):
            return data[len(bom) :]
    return data
