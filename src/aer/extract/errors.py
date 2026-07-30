"""Errors raised while turning archived bytes into text.

Separate classes with stable codes, for the same reason the fetch layer has them: "this PDF
is a scan and has no text in it" and "this file claimed to be HTML and is a zip archive" are
different events needing different responses, and a caller should not have to match on prose
to tell them apart.

**Every one of these is a refusal, not a degradation.** An extractor that returned empty text
for a document it could not read would put a citation-free section in front of a reviewer with
nothing to say why. Saying so loudly is the whole point.
"""

from __future__ import annotations

from typing import ClassVar

from aer.errors import AerError

__all__ = [
    "DocumentTooLargeError",
    "ExtractionError",
    "MediaTypeMismatchError",
    "ParseFailedError",
    "ParseTimeoutError",
    "UnextractableError",
]


class ExtractionError(AerError):
    """Base for anything that went wrong turning a document into text."""

    code: ClassVar[str] = "extraction_error"
    http_status: ClassVar[int] = 422


class DocumentTooLargeError(ExtractionError):
    """The document exceeds the parse ceiling.

    Checked **before** the parser is handed the bytes, which is the only place the check is
    worth anything: a decompression bomb does its damage during parsing, so a size check
    afterwards measures the crater.
    """

    code: ClassVar[str] = "document_too_large"


class MediaTypeMismatchError(ExtractionError):
    """The bytes are not the kind of document the extractor was asked to read.

    Detected from the content itself. ``Content-Type`` is a claim made by whoever served the
    file, and a hostile server has every reason to make a false one — pointing an XML parser
    at a zip archive is how a zip bomb gets started.
    """

    code: ClassVar[str] = "media_type_mismatch"


class ParseTimeoutError(ExtractionError):
    """The parser did not finish inside its wall-clock budget.

    A timeout rather than a size limit because the two catch different things: a small,
    pathological input can take unbounded time, and no byte count would have predicted it.
    """

    code: ClassVar[str] = "parse_timeout"


class ParseFailedError(ExtractionError):
    """The parser rejected the document, or died trying.

    Carries the child process's own reason where there is one. A malformed filing is a fact
    about the filing, and hiding it behind a generic message would send the reader looking for
    a bug in the platform.
    """

    code: ClassVar[str] = "parse_failed"


class UnextractableError(ExtractionError):
    """The document parsed, and contains no text to extract.

    A scanned filing is the usual cause: pages of images with nothing selectable on them. This
    is a distinct outcome from a parse failure, because the document is intact and the honest
    answer is "this needs OCR", not "this is broken".
    """

    code: ClassVar[str] = "unextractable"
