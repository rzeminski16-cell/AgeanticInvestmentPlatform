"""What is inside one accession — the folder the submissions index names one file in.

**A filing is not a document.** EDGAR issues an accession, which is a folder, and
``filings.recent`` names exactly one file in it: ``primaryDocument``. For a 10-K that is
nearly the whole filing. For a current report it is a cover page whose entire content is a
sentence saying the substance is furnished as Exhibit 99.1 — so a platform that acquires the
primary document and stops acquires the sentence and not the earnings release (ADR 0126).

**Where the document types actually are, which is not where the plan said.** The obvious
endpoint, ``index.json`` on the folder, carries a *type* field that is an icon name:

    {"name": "d291965dex991.htm", "type": "text.gif", "size": "34182"}

``text.gif``, ``compressed.gif``, ``image2.gif``. The string ``EX-99`` is nowhere in it. What
carries the real type is ``{accession}-index-headers.html``, which is EDGAR's own
dissemination header, one block per document::

    <DOCUMENT>
    <TYPE>EX-99.1
    <FILENAME>d291965dex991.htm
    <DESCRIPTION>EX-99.1

Ten kilobytes, and the ``<DOCUMENT><TYPE><FILENAME>`` triple is the SGML format's core
rather than a rendering detail — confirmed identical across two filing agents. It arrives
HTML-escaped inside a ``<PRE>`` block, so this module unescapes it and reads the SGML. It
does **not** parse the page: the markup is a wrapper around the format, and the format is
what is being read.

Pure: bytes in, rows out. Fetching is the client's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Final

__all__ = [
    "EXHIBIT_TYPE_PREFIX",
    "AccessionDocument",
    "parse_accession_documents",
    "substantive_exhibits",
]

# The exhibit series a current report's substance lives in. EX-99 is "additional exhibits",
# which is where a filer furnishes the earnings release, the guidance table and the investor
# presentation. EX-1, EX-3, EX-4 and EX-10 are the deal documents — an underwriting
# agreement, articles, an indenture, a contract — and a run that acquired those would have
# spent its fetches on a bond offering's paperwork. M&T's 0001193125-26-310413 is exactly
# that accession, and under this rule it brings back nothing extra, correctly.
EXHIBIT_TYPE_PREFIX: Final = "EX-99"

# One document's block in the dissemination header. `TYPE` is required — a block without one
# is not a document entry — and `FILENAME` is what makes it fetchable; `DESCRIPTION` is
# usually the type repeated and occasionally says something.
_DOCUMENT_BLOCK: Final = re.compile(
    r"<DOCUMENT>(?P<body>.*?)</DOCUMENT>", re.DOTALL | re.IGNORECASE
)
_FIELD: Final = re.compile(r"^<(?P<name>TYPE|FILENAME|DESCRIPTION)>(?P<value>.*)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class AccessionDocument:
    """One file in an accession, as EDGAR's own header describes it."""

    document_type: str
    filename: str
    description: str = ""

    @property
    def is_exhibit(self) -> bool:
        """Whether this is one of the exhibits a current report's substance lives in."""
        return self.document_type.upper().startswith(EXHIBIT_TYPE_PREFIX)


def parse_accession_documents(payload: bytes) -> tuple[AccessionDocument, ...]:
    """Every document the accession's header names, in the order it lists them.

    Order is the filer's sequence, which puts the primary document first and the exhibits
    after it in the order they were furnished — so a cap applied to this list takes the
    earliest exhibits rather than an arbitrary subset.

    Unparseable bytes give an empty tuple rather than raising. The header is an *extra*
    read on top of a filing the run already has: a folder this cannot make sense of should
    cost the exhibits, never the filing.
    """
    text = unescape(payload.decode("utf-8", errors="replace"))
    found: list[AccessionDocument] = []
    for block in _DOCUMENT_BLOCK.finditer(text):
        fields = {
            match.group("name").upper(): match.group("value").strip()
            for match in _FIELD.finditer(block.group("body"))
        }
        document_type = fields.get("TYPE", "")
        filename = fields.get("FILENAME", "")
        if not document_type or not filename:
            continue
        found.append(
            AccessionDocument(
                document_type=document_type,
                filename=filename,
                description=fields.get("DESCRIPTION", ""),
            )
        )
    return tuple(found)


def substantive_exhibits(
    documents: tuple[AccessionDocument, ...], *, primary_document: str, limit: int
) -> tuple[AccessionDocument, ...]:
    """The exhibits worth acquiring beside the primary document, capped.

    The primary document is excluded by name rather than by position: it is already being
    acquired, and a filer that furnished it under an EX-99 type would otherwise be fetched
    twice and recorded twice against one URL.
    """
    kept = [
        document
        for document in documents
        if document.is_exhibit and document.filename != primary_document
    ]
    return tuple(kept[:limit])
