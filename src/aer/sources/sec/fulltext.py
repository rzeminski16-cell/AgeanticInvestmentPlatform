"""EDGAR full-text search: finding the filings that mention something.

The submissions index answers *"what has this company filed?"*. This answers *"which of its
filings talk about segment reporting?"* — which is the question that turns one 10-K per run into
the handful of documents a section actually needs.

**URLs are constructed here, never taken from the response.** A hit identifies a filing by
accession number and a document by filename, and the archive URL is built from those plus the
CIK. That is not fastidiousness: the response is untrusted content like any other fetched
document, and a search result carrying its own URL would be a way for whatever EDGAR indexed to
choose what this platform fetches next. Building the URL from identifiers means the worst a
poisoned index entry can do is name a document that does not exist.

**Post-dated hits are kept and marked, not dropped.** :meth:`SearchResults.admissible` splits
rather than filters, so a run can report *"three results were excluded as published after the
as-of date"*. Silently dropping them makes a search that found relevant material look like a
search that found nothing, and those need different responses from a reviewer — the second means
"look elsewhere", the first means "this exists and you may not use it yet".

Coverage is worth knowing: EDGAR's full-text index starts at 2001, so a search bounded before
then returns nothing and that is the index's answer rather than a fault.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Final
from urllib.parse import urlencode

from aer.errors import ValidationError
from aer.sources.base import DocumentRef

__all__ = [
    "FULL_TEXT_SEARCH_URL",
    "FullTextHit",
    "SearchResults",
    "build_search_url",
    "parse_search_results",
]

FULL_TEXT_SEARCH_URL: Final = "https://efts.sec.gov/LATEST/search-index"

# `efts.sec.gov` is a different host from `www.sec.gov`, and both are covered by the `.sec.gov`
# allowlist entry. Stated here because it is the sort of thing a reader checks.

_ARCHIVE_ROOT: Final = "https://www.sec.gov/Archives/edgar/data"

# EDGAR's full-text index does not reach further back than this. A search bounded entirely
# before it returns nothing, which is a fact about the index rather than about the company.
FULL_TEXT_COVERAGE_BEGINS: Final = date(2001, 1, 1)

# How many hits to ask for. EDGAR pages at ten by default; this platform wants a handful of
# strong matches rather than an exhaustive sweep, and every hit is a document that may then be
# fetched, hashed and stored.
_DEFAULT_SIZE: Final = 10


@dataclass(frozen=True, slots=True)
class FullTextHit:
    """One document EDGAR's index matched.

    ``accession`` and ``filename`` together identify the document; ``url`` is built from them
    rather than parsed out of the response. See the module docstring.
    """

    accession: str
    filename: str
    cik: str
    display_name: str
    form: str
    filed: date

    @property
    def url(self) -> str:
        """The archive URL for this document, constructed from its identifiers.

        The CIK is un-padded in an archive path and the accession number has its dashes
        stripped — two conventions that differ from every other place EDGAR uses the same
        identifiers, and each of which is a 404 the first time it is missed.
        """
        return f"{_ARCHIVE_ROOT}/{int(self.cik)}/{self.accession.replace('-', '')}/{self.filename}"

    def to_ref(self, *, entity_name: str | None = None) -> DocumentRef:
        return DocumentRef(
            url=self.url,
            title=f"{self.form} — {entity_name or self.display_name}",
            publication_date=self.filed,
            form=self.form,
            accession=self.accession,
        )


@dataclass(frozen=True, slots=True)
class SearchResults:
    """What a search returned, and how much of it there was.

    ``total`` is the index's own count, which is usually larger than ``len(hits)``: the query
    asks for a page. Kept because "the top ten of four thousand" and "all four of them" are
    different situations and a reviewer should be able to tell.
    """

    hits: tuple[FullTextHit, ...]
    total: int

    def admissible(
        self, as_of_date: date | None
    ) -> tuple[tuple[FullTextHit, ...], tuple[FullTextHit, ...]]:
        """Split the hits into those usable at ``as_of_date`` and those published after it.

        Returns:
            ``(usable, excluded)``. **A split rather than a filter**, so a run can say how many
            results the point-in-time rule cost it. A search that found relevant material and a
            search that found nothing need different responses, and filtering makes them look
            identical.
        """
        if as_of_date is None:
            return self.hits, ()
        usable = tuple(hit for hit in self.hits if hit.filed <= as_of_date)
        excluded = tuple(hit for hit in self.hits if hit.filed > as_of_date)
        return usable, excluded


def build_search_url(
    phrase: str,
    *,
    cik: str | None = None,
    forms: Iterable[str] = (),
    start_date: date | None = None,
    end_date: date | None = None,
    size: int = _DEFAULT_SIZE,
) -> str:
    """The search URL for a phrase, scoped as tightly as the caller can manage.

    Args:
        phrase: Quoted into an exact-phrase query. A bare set of words matches documents
            containing all of them anywhere, which for a filing means nearly every document.
        cik: Restricts the search to one filer. Strongly preferred: an unscoped search over
            every filer returns other companies' documents, and a run that acquired one would
            have cited a competitor's filing for this company's figures.
        end_date: Should be the request's as-of date under point-in-time rules, so the index
            is not even asked about later filings.

    Raises:
        ValidationError: The phrase is empty, or the date range runs backwards.
    """
    cleaned = phrase.strip()
    if not cleaned:
        message = "A full-text search needs a phrase. An empty query matches every filing."
        raise ValidationError(message, context={"phrase": phrase})

    if start_date is not None and end_date is not None and start_date > end_date:
        message = (
            f"The search range runs backwards: {start_date.isoformat()} is after "
            f"{end_date.isoformat()}."
        )
        raise ValidationError(
            message,
            context={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        )

    params: dict[str, str] = {"q": f'"{cleaned}"', "hits": str(size)}
    if cik is not None:
        params["ciks"] = cik
    forms_list = [form.strip() for form in forms if form.strip()]
    if forms_list:
        params["forms"] = ",".join(forms_list)
    if start_date is not None:
        params["startdt"] = start_date.isoformat()
    if end_date is not None:
        params["enddt"] = end_date.isoformat()
    if start_date is not None or end_date is not None:
        params["dateRange"] = "custom"

    return f"{FULL_TEXT_SEARCH_URL}?{urlencode(params)}"


def parse_search_results(payload: bytes) -> SearchResults:
    """Parse a full-text search response.

    Hits that cannot be turned into a document reference are **skipped rather than raised on**.
    EDGAR's index carries entries this platform has no use for and occasionally ones with a
    field missing; failing the whole search because the ninth of ten results is malformed would
    lose eight good ones over a defect in something nobody asked for.

    Raises:
        ValidationError: The payload is not the shape of a search response at all. That is a
            different failure — an error page, a redirect to a login, a changed API — and it
            should not look like "no results".
    """
    try:
        document: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        message = "The full-text search response is not JSON."
        raise ValidationError(message, context={"bytes": len(payload)}) from exc

    if not isinstance(document, dict) or "hits" not in document:
        message = (
            "The full-text search response has no 'hits' object. This is what an error page or "
            "a changed API looks like, and it is not the same as a search that found nothing."
        )
        raise ValidationError(message, context={"keys": sorted(document)[:10]})

    envelope = document["hits"]
    if not isinstance(envelope, dict):
        message = "The full-text search response's 'hits' is not an object."
        raise ValidationError(message, context={})

    hits = [parsed for raw in envelope.get("hits", []) if (parsed := _parse_hit(raw)) is not None]
    return SearchResults(hits=tuple(hits), total=_total(envelope))


# -- Internals -------------------------------------------------------------------------------


def _total(envelope: dict[str, Any]) -> int:
    """The index's own count of matches, which nests differently across EDGAR's versions."""
    total = envelope.get("total")
    if isinstance(total, dict):
        value = total.get("value")
        return int(value) if isinstance(value, int) else 0
    return int(total) if isinstance(total, int) else 0


def _parse_hit(raw: Any) -> FullTextHit | None:
    """One hit, or ``None`` if it is not usable. See :func:`parse_search_results`."""
    if not isinstance(raw, dict):
        return None

    source = raw.get("_source")
    if not isinstance(source, dict):
        return None

    document = _split_identifier(raw.get("_id"))
    cik = _first_string(source.get("ciks"))
    filed = _parse_date(source.get("file_date"))
    form = _first_string(source.get("root_forms")) or str(source.get("file_type") or "").strip()

    if document is None or filed is None or not form:
        return None
    if cik is None or not cik.isdigit():
        return None

    accession, filename = document
    return FullTextHit(
        accession=accession,
        filename=filename,
        cik=cik,
        display_name=_first_string(source.get("display_names")) or "",
        form=form,
        filed=filed,
    )


def _split_identifier(value: Any) -> tuple[str, str] | None:
    """``"<accession>:<filename>"`` into its halves.

    Both are needed and neither appears anywhere else in the response, so a hit without a
    well-formed ``_id`` cannot be turned into a URL at all.
    """
    if not isinstance(value, str) or ":" not in value:
        return None
    accession, _, filename = value.partition(":")
    if not accession.strip() or not filename.strip():
        return None
    return accession.strip(), filename.strip()


def _first_string(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
