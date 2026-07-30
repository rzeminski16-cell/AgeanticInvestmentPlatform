"""Finding documents on an issuer's own investor-relations site.

Tier 2 material: annual report PDFs, results presentations, transcripts. Authoritative where a
regulatory filing does not contradict it, and often the only place a presentation exists at all.

**This is the first adapter whose candidate URLs come out of untrusted content.** Everywhere
else the platform builds URLs from identifiers a regulator issued. Here it reads links off a
page an issuer controls, and a page can link anywhere — to a tracker, to an internal address, to
whatever a compromised CMS was told to serve. Three things stand between that and a fetch:

1. **The domain is supplied by the operator, never discovered.** :func:`discover_documents`
   takes the host it is allowed to read, and a link to any other host is dropped here. There is
   no code path that learns a new domain from a page and then fetches it, because the one thing
   an attacker who controls a page wants is exactly that.
2. **The fetch layer checks again.** Every request goes through
   :class:`~aer.fetch.client.SafeFetcher` with the domain passed as ``extra_hosts``, so the
   allowlist is enforced by the component that owns it. The check in this module is the cheap
   one; that one is the control.
3. **robots.txt is honoured.** The ``ISSUER_IR`` policy leaves ``honours_robots`` at its default
   of true, unlike the regulator APIs, because reading a company's website is crawling and a
   company's stated wishes about crawling apply.

**A link is a candidate, not a document.** Nothing here fetches. Discovery returns what it
found and what it rejected, and the caller decides — which keeps this module pure enough to test
against a page of HTML with no network at all.

**Most IR documents have no discoverable publication date**, and that is recorded rather than
guessed: :attr:`IssuerDocument.publication_date` is optional, and under point-in-time rules an
undated document is quarantined by :mod:`aer.services.sources`. A date invented from a URL slug
would be worse than no date, because it would pass the check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from aer.core.enums import Provider, SourceTier
from aer.extract.dates import from_text
from aer.fetch.policy import host_matches
from aer.sources.tiering import DocumentKind

__all__ = [
    "IssuerDocument",
    "RejectedLink",
    "Rejection",
    "discover_documents",
]

PROVIDER: Final = Provider.ISSUER_IR
SOURCE_TIER: Final = SourceTier.T2_ISSUER

# Schemes worth following. Everything else — `javascript:`, `mailto:`, `data:`, `file:` — is
# either not a document or not a fetch, and `data:` in particular is a way to smuggle content
# past a domain check by not having a domain.
_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# What the link text or the URL has to look like for a link to be worth acquiring. An IR page
# links to dozens of things — governance, careers, the share price widget — and fetching all of
# them would spend a run's budget on a cookie policy.
_INTERESTING: Final[tuple[tuple[re.Pattern[str], DocumentKind], ...]] = (
    (
        re.compile(r"annual[\s_-]*report|form[\s_-]*10-?k|20-?f\b|integrated[\s_-]*report", re.I),
        DocumentKind.ISSUER_PUBLICATION,
    ),
    (
        re.compile(r"interim[\s_-]*report|half[\s_-]*year|quarterly[\s_-]*report|10-?q\b", re.I),
        DocumentKind.ISSUER_PUBLICATION,
    ),
    (
        re.compile(r"results[\s_-]*(presentation|deck|slides)|earnings[\s_-]*presentation", re.I),
        DocumentKind.ISSUER_PUBLICATION,
    ),
    (
        re.compile(r"transcript|prepared[\s_-]*remarks", re.I),
        DocumentKind.ISSUER_PUBLICATION,
    ),
    (
        re.compile(r"financial[\s_-]*statements|annual[\s_-]*accounts", re.I),
        DocumentKind.ISSUER_PUBLICATION,
    ),
    (
        re.compile(r"press[\s_-]*release|news[\s_-]*release", re.I),
        DocumentKind.ISSUER_MARKETING,
    ),
)

# A document extension is strong evidence on its own: a link to a PDF from an IR page is a
# document whatever it is called.
_DOCUMENT_SUFFIXES: Final[tuple[str, ...]] = (".pdf", ".htm", ".html", ".xhtml")

_PDF: Final = ".pdf"

# How many candidates to return. A run acquires a handful of documents; a page that yields two
# hundred is a site map, and taking all of them would spend the budget on navigation.
_MAX_CANDIDATES: Final = 25

# Bound on how much link text is kept as a title.
_TITLE_LIMIT: Final = 200


class Rejection(StrEnum):
    """Why a link on the page was not turned into a candidate.

    Recorded rather than dropped silently, because "the IR page had nothing on it" and "the IR
    page linked to forty documents on a CDN we may not read" are different situations and only
    one of them is the operator's to fix.
    """

    OFF_DOMAIN = "off_domain"
    """A different host. The control this module exists for."""

    UNSUPPORTED_SCHEME = "unsupported_scheme"
    """``mailto:``, ``javascript:``, ``data:`` — not a document, or not a fetch."""

    NOT_A_DOCUMENT = "not_a_document"
    """Nothing in the link suggests a document a research report would cite."""

    MALFORMED = "malformed"
    """The href could not be resolved into a URL at all."""


@dataclass(frozen=True, slots=True)
class IssuerDocument:
    """A document an issuer's own site links to.

    ``publication_date`` is optional here, unlike :class:`~aer.sources.base.DocumentRef`, and
    that difference is the point: an IR page rarely dates its links, and an undated document is
    quarantined under point-in-time rules rather than admitted on a guess.
    """

    url: str
    title: str
    kind: DocumentKind
    publication_date: date | None = None

    @property
    def is_pdf(self) -> bool:
        return urlsplit(self.url).path.lower().endswith(_PDF)


@dataclass(frozen=True, slots=True)
class RejectedLink:
    """A link that was not followed, and why."""

    url: str
    reason: Rejection


@dataclass(frozen=True, slots=True)
class Discovery:
    """What an IR page yielded."""

    documents: tuple[IssuerDocument, ...]
    rejected: tuple[RejectedLink, ...]

    def off_domain(self) -> tuple[RejectedLink, ...]:
        return tuple(link for link in self.rejected if link.reason is Rejection.OFF_DOMAIN)


def discover_documents(
    html: bytes,
    *,
    page_url: str,
    allowed_host: str,
    limit: int = _MAX_CANDIDATES,
) -> Discovery:
    """Candidate documents linked from an issuer's investor-relations page.

    Pure: parses markup and returns references. Nothing here fetches, which is what lets the
    whole rejection table be tested against a page of HTML with no network at all.

    Args:
        page_url: The URL the markup came from, used to resolve relative links. Taken from the
            fetch that produced it rather than from anything in the page — a ``<base href>``
            pointing at another domain is the obvious way to defeat a host check, so it is
            deliberately not honoured.
        allowed_host: The one host whose links may be followed, matched by
            :func:`~aer.fetch.policy.host_matches` so a leading dot admits subdomains. Supplied
            by the operator; **never** learned from a page.
    """
    tree = HTMLParser(html)
    seen: set[str] = set()
    documents: list[IssuerDocument] = []
    rejected: list[RejectedLink] = []

    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href:
            continue

        resolved = _resolve(href, page_url)
        if resolved is None:
            rejected.append(RejectedLink(url=href[:_TITLE_LIMIT], reason=Rejection.MALFORMED))
            continue

        scheme = urlsplit(resolved).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            rejected.append(
                RejectedLink(url=resolved[:_TITLE_LIMIT], reason=Rejection.UNSUPPORTED_SCHEME)
            )
            continue

        if not host_matches(urlsplit(resolved).hostname or "", allowed_host):
            rejected.append(RejectedLink(url=resolved, reason=Rejection.OFF_DOMAIN))
            continue

        if resolved in seen:
            continue

        text = node.text(strip=True).strip()
        kind = _classify(text, resolved)
        if kind is None:
            rejected.append(RejectedLink(url=resolved, reason=Rejection.NOT_A_DOCUMENT))
            continue

        seen.add(resolved)
        documents.append(
            IssuerDocument(
                url=resolved,
                title=_title(text, resolved),
                kind=kind,
                publication_date=_date_near(text),
            )
        )
        if len(documents) >= limit:
            break

    return Discovery(documents=tuple(documents), rejected=tuple(rejected))


# -- Internals -------------------------------------------------------------------------------


def _resolve(href: str, page_url: str) -> str | None:
    """Absolute URL for a link, with its fragment dropped.

    The fragment is removed because two links to the same document differing only after the
    ``#`` are one document, and fetching both would archive the same bytes twice under two
    provenance rows saying different things.
    """
    try:
        absolute = urljoin(page_url, href)
        parts = urlsplit(absolute)
    except ValueError:
        return None
    if not parts.scheme:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _classify(text: str, url: str) -> DocumentKind | None:
    """What kind of document a link points at, or ``None`` if it does not look like one.

    Both the link text and the URL are checked, because IR sites split evenly between the two:
    some link ``Annual Report 2022`` to ``/media/12345.pdf`` and others link ``Download`` to
    ``/investors/annual-report-2022.pdf``.
    """
    haystack = f"{text} {url}"
    for pattern, kind in _INTERESTING:
        if pattern.search(haystack):
            return kind

    # A PDF from an IR page is a document even when nothing names it. HTML is not: that is most
    # of a website.
    if urlsplit(url).path.lower().endswith(_PDF):
        return DocumentKind.ISSUER_PUBLICATION
    return None


def _title(text: str, url: str) -> str:
    """The link's own words, or the filename when it has none.

    A title matters more here than elsewhere: an IR document has no form type and no accession
    number, so the link text is the only human-readable thing distinguishing it from every other
    PDF on the site.
    """
    cleaned = " ".join(text.split())
    if cleaned:
        return cleaned[:_TITLE_LIMIT]
    tail = urlsplit(url).path.rsplit("/", 1)[-1]
    return tail[:_TITLE_LIMIT] or url[:_TITLE_LIMIT]


def _date_near(text: str) -> date | None:
    """A date in the link's own text, where there is an unambiguous one.

    Only from the text, and only when a single candidate is found. An IR page's link often reads
    ``Interim results — 28 July 2022``, which is real evidence; a year in a filename is not, and
    two dates in one link is a range rather than a publication date.
    """
    candidates = from_text(text)
    if len(candidates) != 1:
        return None
    return candidates[0].value


def suffix_is_document(url: str) -> bool:
    """Whether a URL's path ends in something worth fetching as a document."""
    return urlsplit(url).path.lower().endswith(_DOCUMENT_SUFFIXES)
