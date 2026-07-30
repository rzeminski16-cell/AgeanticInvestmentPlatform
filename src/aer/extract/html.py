"""HTML to text, deterministically, with offsets a citation can point at.

**Bump :data:`VERSION` whenever the output could change.** Every citation records the version
that produced its locator, so a silent change to the separator, the strip behaviour or the
removal list turns every stored locator into a wrong one — the excerpt shifts and verification
fails for a reason nobody can see. Changing the version instead makes the failure legible: the
verifier reports "the extractor changed", which is true, rather than "the excerpt is wrong",
which is not.

**Hidden text is kept.** ``display:none``, ``visibility:hidden``, white-on-white — all of it
stays in the extracted text. This looks wrong and is deliberate: hidden text is the primary
prompt-injection vector in a filing (threat T2), and an extractor that dropped it would remove
the evidence before the scanner in task 13 could flag it. Extract everything textual; decide
what it means afterwards.

**Script and style content is removed.** Not for safety — nothing here executes anything — but
because it is code rather than prose. Left in, ``var revenue = 198270`` would sit in the
extracted text as a citable sentence, and every offset after it would shift with a
minification change nobody made a decision about.

**Injection scanning happens here**, for a reason that is about isolation rather than about
cohesion: "this text was invisible" is a fact about the markup, the markup exists only during
this parse, and this parse is the one already running inside the sandbox. See
:mod:`aer.extract.injection`. Findings do not affect ``content_hash``, so a new heuristic cannot
invalidate a locator recorded before it existed.
"""

from __future__ import annotations

from typing import Final

from selectolax.parser import HTMLParser

from aer.core.schemas.extraction import ExtractedText
from aer.extract.errors import UnextractableError
from aer.extract.injection import scan_markup, scan_text
from aer.extract.result import ExtractedDocument

__all__ = ["EXTRACTOR", "VERSION", "extract_html"]

EXTRACTOR: Final = "html"
VERSION: Final = "1"

# Text nodes inside these are code, not content. See the module docstring.
_NON_PROSE: Final[tuple[str, ...]] = ("script", "style", "template")

# One newline between nodes, and each node's own edges trimmed. Chosen for readability of the
# result rather than fidelity to the markup: what a locator points at is read by a person
# checking a citation, and `RevenueRevenue was` — which is what no separator produces — is not
# text anybody can check.
_SEPARATOR: Final = "\n"


def extract_html(data: bytes) -> ExtractedDocument:
    """Extract the readable text of an HTML document.

    Encoding is decided by ``selectolax`` from the document's own declaration, which is both
    deterministic and better than guessing here: a filing authored on Windows and served
    without a charset header is common, and its ``<meta charset>`` is the best evidence
    available.

    Raises:
        UnextractableError: The document contains no text. Distinct from a parse failure —
            the document is intact and simply has nothing in it, which is what an image-only
            page looks like.
    """
    tree = HTMLParser(data)

    for selector in _NON_PROSE:
        for node in tree.css(selector):
            node.decompose()

    body = tree.body
    text = "" if body is None else body.text(separator=_SEPARATOR, strip=True)

    if not text.strip():
        message = (
            "The HTML document contains no readable text. An image-only page or a shell whose "
            "content arrives by script will look like this; neither can support a citation."
        )
        raise UnextractableError(message, context={"extractor": EXTRACTOR, "bytes": len(data)})

    # Scanned here, during the parse that is already happening and inside the sandbox that
    # already contains it. The markup signals — what was hidden, what was in a comment — can
    # only be seen before the tree is discarded, and re-parsing untrusted bytes elsewhere to
    # look for attacks would be an odd way to defend against them.
    return ExtractedDocument(
        text=ExtractedText(
            text=text,
            extractor=EXTRACTOR,
            extractor_version=VERSION,
            title=_title(tree),
        ),
        findings=scan_markup(tree, text) + scan_text(text),
    )


def _title(tree: HTMLParser) -> str | None:
    """The document's own title, trimmed, or ``None``.

    Recorded on the source document because "which filing is this?" is a question a reviewer
    asks of a hash, and a hash does not answer it.
    """
    node = tree.css_first("title")
    if node is None:
        return None
    title = node.text(strip=True).strip()
    return title or None
