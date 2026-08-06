"""Extracting "text" from a JSON document: the decoded source, verbatim.

A JSON artefact — an XBRL facts API response, a structured feed — is already text, and the
text a citation should point into is the document itself: any transformation (pretty-
printing, key sorting, flattening) would put locators into a rendering this module chose
rather than into what was fetched, and a second version of this module could then move
every stored locator. So the extraction is the UTF-8 decoding of the bytes and nothing
else, which makes ``content_hash`` stable for as long as the artefact is.

The parse is still performed — a document admitted here claims to be JSON, and one that
does not parse is refused rather than treated as prose. The scan for instruction-shaped
text runs over the decoded source exactly as it does for HTML-extracted prose: a JSON
string value is as capable of carrying "ignore your instructions" as a paragraph is.
"""

from __future__ import annotations

import json
from typing import Final

from aer.core.schemas.extraction import ExtractedText
from aer.extract.errors import UnextractableError
from aer.extract.injection import scan_text
from aer.extract.result import ExtractedDocument

__all__ = ["EXTRACTOR", "VERSION", "extract_json"]

EXTRACTOR: Final = "json"
VERSION: Final = "1"


def extract_json(data: bytes) -> ExtractedDocument:
    """Extract a JSON document's citable text: its own decoded source.

    Raises:
        UnextractableError: The bytes are not UTF-8, or do not parse as JSON. Either way
            the document is not what it was admitted as, and handing its noise onward as
            citable text would be worse than refusing.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        message = "The document is not valid UTF-8, so it cannot be the JSON it claims to be."
        raise UnextractableError(message, context={"extractor": EXTRACTOR}) from exc

    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        message = f"The document does not parse as JSON: {exc.msg} at position {exc.pos}."
        raise UnextractableError(message, context={"extractor": EXTRACTOR}) from exc

    if not text.strip():
        message = "The JSON document is empty; there is nothing to cite."
        raise UnextractableError(message, context={"extractor": EXTRACTOR})

    return ExtractedDocument(
        text=ExtractedText(text=text, extractor=EXTRACTOR, extractor_version=VERSION),
        findings=scan_text(text),
    )
