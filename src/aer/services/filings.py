"""Acquiring the filings themselves, not only the numbers extracted from them.

**A run used to read one document.** The XBRL company-facts aggregate: every figure the
entity ever tagged, and not one sentence of prose. So the research workers had nothing to
investigate — the recent-developments worker finished a live run with five leads and no
findings, because there was nothing recent in front of it — and every section that wanted
to say what the company *said* had only numbers to say it from.

The pieces to fix that were all built and none of them were called. The submissions index
lists every filing with the date it was accepted; :class:`~aer.sources.sec.submissions.Filing`
turns one into a reference the fetch layer accepts; the fetcher archives and hashes. This
module joins them: the latest annual report and the recent current reports, inside the
point-in-time window, fetched, dated, archived and excerpted.

**Every document is dated by its acceptance, not by the period it covers.** The date a
filing became public is the only one a point-in-time rule can honestly use, and it is what
:meth:`Filing.to_ref` carries. A 10-K for the year to June, accepted in August, is
inadmissible to a run as at July — correctly, because nobody could read it then.

**Excerpts are recorded here, not left to the reader.** A source document with no
extractions contributes nothing to a section's evidence pack and cannot be cited, so
acquiring a filing without excerpting it would leave the same silence in a more expensive
way. The excerpts are the document's own paragraphs, in order, which makes them
deterministic, genuinely present in the artefact, and exactly what the citation verifier
re-reads.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import Provider, SourceTier
from aer.core.schemas.extraction import Excerpt
from aer.db.models import ResearchRequest, SourceDocument
from aer.errors import AerError
from aer.extract import extract_text
from aer.services.acquisition import record_acquisition
from aer.services.extractions import record_excerpts
from aer.sources.base import ResolvedEntity
from aer.sources.sec.submissions import ANNUAL_FORMS, Filing, SubmissionsIndex
from aer.storage.protocol import ArtefactStore

__all__ = [
    "CURRENT_FORMS",
    "MAX_CURRENT_REPORTS",
    "AcquiredFilings",
    "acquire_filings",
]

_log = structlog.get_logger("aer.services.filings")

# Material events between the periodic reports: an acquisition, a guidance change, a
# departure. This is what "recent developments" is actually about, and the reason a run
# reading only the annual aggregate had nothing to say about it.
CURRENT_FORMS: Final[frozenset[str]] = frozenset({"8-K", "6-K"})

# How many current reports to take. A large filer files dozens a year, most of them
# routine; the newest handful is where anything the research has not already priced in
# will be. Bounded because each is a fetch under SEC's rate limit and an artefact to keep.
MAX_CURRENT_REPORTS: Final = 5

# Paragraphs excerpted per document. Enough that a section has something to cite, small
# enough that one 10-K does not fill an evidence pack on its own — the pack is assembled
# against a token budget and a document that crowded out every other source would be worse
# than the silence this module exists to end.
MAX_EXCERPTS: Final = 40

# The shortest run of text worth recording as an excerpt. Below this it is a heading, a
# page number or a table cell adrift from its table: a citation pointing at "12" verifies
# and means nothing.
MIN_EXCERPT_CHARS: Final = 120

# Which extractor reads which kind. Anything else is archived and citable but not read:
# the platform holds the bytes either way, and guessing at an extractor is how a parser
# meets content it was not written for.
# A paragraph boundary: one blank line, however much whitespace is on it.
_PARAGRAPH_BREAK: Final[re.Pattern[str]] = re.compile(r"\n[ \t]*\n")

_EXTRACTORS: Final[dict[str, str]] = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/xml": "html",
    "text/xml": "html",
    "application/pdf": "pdf",
}


@dataclass(frozen=True, slots=True)
class AcquiredFilings:
    """What the filing sweep brought back, and what it could not."""

    documents: tuple[SourceDocument, ...] = ()
    excerpts: int = 0
    skipped: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        return {
            "filings": [
                {
                    "source_document_id": str(document.id),
                    "url": document.url,
                    "title": document.title,
                    "publication_date": (
                        document.publication_date.isoformat() if document.publication_date else None
                    ),
                    "quarantined": document.quarantined,
                }
                for document in self.documents
            ],
            "filing_excerpts": self.excerpts,
            "filings_skipped": list(self.skipped),
        }


async def acquire_filings(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    entity: ResolvedEntity,
    settings: Settings,
    job_id: uuid.UUID | None = None,
    max_current: int = MAX_CURRENT_REPORTS,
) -> AcquiredFilings:
    """Fetch this entity's latest annual report and its recent current reports.

    Args:
        client: The SEC client. Typed loosely so a test can substitute a stub without
            constructing one, exactly as the workflow's other steps do.

    Nothing here raises for a document that cannot be had. A filer with no annual report
    inside the window, a fetch the layer refuses, a page that will not extract — each is
    recorded in ``skipped`` and the rest continue, because a run that failed outright for
    one unreachable 8-K would be a run that fails most weeks.

    The submissions index is fetched and not recorded as a source. It is a listing of what
    exists rather than evidence of anything, nothing will ever cite it, and putting it in
    the sources table would bury the documents that matter under the catalogue.
    """
    try:
        index: SubmissionsIndex = (await client.fetch_submissions(entity.identifier)).data
    except AerError as unreachable:
        return AcquiredFilings(
            skipped=(f"The filing index could not be read: {unreachable.message}",)
        )

    wanted, missing = _wanted(index, request=request, max_current=max_current)
    documents: list[SourceDocument] = []
    excerpts = 0
    skipped = list(missing)

    for filing in wanted:
        outcome = await _acquire_one(
            session,
            store,
            client=client,
            request=request,
            entity=entity,
            index=index,
            filing=filing,
            settings=settings,
            job_id=job_id,
        )
        if isinstance(outcome, str):
            skipped.append(outcome)
            continue
        document, recorded = outcome
        documents.append(document)
        excerpts += recorded

    _log.info(
        "filings.acquired",
        cik=index.cik,
        documents=len(documents),
        excerpts=excerpts,
        skipped=len(skipped),
    )
    return AcquiredFilings(documents=tuple(documents), excerpts=excerpts, skipped=tuple(skipped))


def _wanted(
    index: SubmissionsIndex, *, request: ResearchRequest, max_current: int
) -> tuple[list[Filing], list[str]]:
    """Which filings to fetch, and what was not there to fetch.

    Point-in-time is applied here, on the index, before anything is requested — the cheapest
    possible place, and the one where a filing that postdates the as-of date stops being a
    candidate rather than being fetched and then refused.
    """
    as_of = request.as_of_date if request.point_in_time else None
    annual = index.latest(ANNUAL_FORMS, as_of_date=as_of)

    candidates = index.filed_on_or_before(as_of) if as_of else index.filings
    current = sorted(
        (item for item in candidates if item.form in CURRENT_FORMS),
        key=lambda item: (item.filing_date, item.accession),
        reverse=True,
    )[:max_current]

    missing: list[str] = []
    if annual is None:
        missing.append(
            "No annual report (10-K, 20-F or 40-F) is listed for this entity at or before "
            "the as-of date, so the run has no narrative annual filing to read."
        )
    if not current:
        missing.append(
            "No current reports (8-K or 6-K) are listed at or before the as-of date, so "
            "there is nothing recent beyond the periodic filings."
        )

    return ([annual, *current] if annual else list(current)), missing


async def _acquire_one(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    entity: ResolvedEntity,
    index: SubmissionsIndex,
    filing: Filing,
    settings: Settings,
    job_id: uuid.UUID | None,
) -> tuple[SourceDocument, int] | str:
    """One filing: fetched, recorded, excerpted. Returns the reason on any failure."""
    ref = filing.to_ref(index.cik, entity_name=entity.name)
    try:
        result = await client.fetch_document(ref)
    except AerError as refused:
        return f"{filing.form} {filing.accession} could not be fetched: {refused.message}"

    if not result.ok:
        return f"{filing.form} {filing.accession} returned HTTP {result.status_code}."

    acquisition = await record_acquisition(
        session,
        store,
        request=request,
        job_id=job_id,
        result=result,
        provider=Provider.SEC_EDGAR,
        # A filing is the regulatory record itself, which is what T1 means. The company
        # facts aggregate shares the tier because it is assembled from these.
        source_tier=SourceTier.T1_REGULATORY,
        title=ref.title,
        publisher="US Securities and Exchange Commission",
        # The date EDGAR accepted it. Stated on the index rather than inferred, so unlike
        # the aggregate (ADR 0044) this one is certain.
        publication_date=ref.publication_date,
        publication_date_confidence=1.0,
    )
    document = acquisition.source_document

    recorded = await _excerpt(session, store, document=document, settings=settings)
    return document, recorded


async def _excerpt(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    document: SourceDocument,
    settings: Settings,
) -> int:
    """Record the document's paragraphs as excerpts. Returns how many.

    Read back from the artefact rather than from the response in hand, for the reason the
    extract step gives: the artefact is the authoritative copy, and if the two could differ
    then the text a citation verifies against would be a different document from the one
    that was cited.
    """
    extractor = _EXTRACTORS.get(document.artefact.media_type.split(";", 1)[0].strip())
    if extractor is None:
        _log.info(
            "filings.not_extracted",
            url=document.url,
            reason=f"no extractor for {document.artefact.media_type!r}",
        )
        return 0

    try:
        extracted = await extract_text(
            store, sha256=document.artefact.sha256, extractor=extractor, settings=settings
        )
    except AerError as unreadable:
        # A filing the parser cannot read costs its excerpts and nothing else. Raising
        # would trade one unreadable 8-K for the whole sweep, including the ones that read
        # perfectly well.
        _log.info("filings.not_extracted", url=document.url, reason=unreadable.message)
        return 0

    excerpts = _paragraphs(extracted.text)
    if not excerpts:
        return 0

    rows = await record_excerpts(
        session, source_document_id=document.id, extracted=extracted.text, excerpts=excerpts
    )
    return len(rows)


def _paragraphs(extracted: Any) -> list[Excerpt]:
    """The document's substantial paragraphs, in order, as located excerpts.

    Split on blank lines rather than on every newline: the extractor keeps the line breaks
    the filer's own markup had, so a paragraph arrives as several short lines and splitting
    on each would produce fragments too small to mean anything. A paragraph is what sits
    between blank lines, internal wrapping and all.

    Located by searching the extracted text for each candidate, so every locator is the
    real offset in the real artefact and the verifier will find exactly what it is shown.
    ``start`` advances so two identical paragraphs — boilerplate, most often — do not both
    resolve to the first one.
    """
    found: list[Excerpt] = []
    cursor = 0
    for block in _PARAGRAPH_BREAK.split(extracted.text):
        candidate = block.strip()
        if len(candidate) < MIN_EXCERPT_CHARS:
            continue
        excerpt = extracted.locate(candidate, start=cursor)
        if excerpt is None:  # pragma: no cover -- it came from this text
            continue
        found.append(excerpt)
        cursor = excerpt.locator.char_end
        if len(found) >= MAX_EXCERPTS:
            break
    return found
