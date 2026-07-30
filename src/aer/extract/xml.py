"""One hardened ``lxml`` parser, and nothing may construct another.

Every XML document this platform reads is untrusted, so the defences are properties of the
parser rather than checks a caller might forget. Threat T5, stated as four keyword arguments:

``resolve_entities=False``
    The control that stops the billion-laughs attack — a document defining nested entities
    that expand to gigabytes of identical text. **This is why a memory cap is a backstop here
    rather than the defence.** Not expanding the entities means there is nothing to expand.

``no_network=True``
    An external DTD reference is a fetch, made by the parser, to a URL from the document,
    bypassing every rule in :mod:`aer.fetch` — the SSRF guard, the allowlist, the robots
    check, the rate limiter. It would be the one place untrusted bytes could cause an outbound
    request, and it is closed here rather than mitigated.

``load_dtd=False`` and ``dtd_validation=False``
    Belt and braces on the above: no DTD is loaded, so no entity declaration in one is even
    read.

``huge_tree=False``
    ``lxml``'s own depth and node-count limits stay on. Turning them off is occasionally
    necessary for enormous legitimate documents; a filing that needs it is a filing worth
    looking at by hand first.

**`recover=False` is the deliberate odd one out.** Every other setting here is a refusal, but
this one chooses to fail rather than to guess. A recovering parser turns a malformed filing
into a partial tree, and a partial tree extracts partial text — which then gets cited as
though it were the document. A parse error is a fact about the filing and belongs in front of
a person.
"""

from __future__ import annotations

from lxml import etree

__all__ = ["hardened_parser", "parse_xml"]


def hardened_parser() -> etree.XMLParser:
    """A parser safe to point at a document from the internet.

    A new instance each call rather than a module-level singleton: ``lxml`` parsers carry
    mutable error state and are **not thread-safe**, and a shared one would leak one
    document's parse errors into the next document's report.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )


def parse_xml(data: bytes) -> etree._Element:
    """Parse ``data`` with :func:`hardened_parser`.

    Raises:
        lxml.etree.XMLSyntaxError: The document is malformed, or declares entities the
            hardened parser refuses to resolve. Left as the library's own exception here and
            translated at the sandbox boundary, where there is a document identity to attach
            to it.
    """
    return etree.fromstring(data, parser=hardened_parser())
