"""Acquiring the filings themselves, not only the numbers extracted from them.

**A run used to read one document.** The XBRL company-facts aggregate: every figure the
entity ever tagged, and not one sentence of prose. So the research workers had nothing to
investigate — the recent-developments worker finished a live run with five leads and no
findings, because there was nothing recent in front of it — and every section that wanted
to say what the company *said* had only numbers to say it from.

The pieces to fix that were all built and none of them were called. The submissions index
lists every filing with the date it was accepted; :class:`~aer.sources.sec.submissions.Filing`
turns one into a reference the fetch layer accepts; the fetcher archives and hashes. This
module joins them: the latest annual report, the quarterly reports filed since it, and the
recent current reports, inside the point-in-time window, fetched, dated, archived and
excerpted.

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
from aer.db.models import Company, ResearchRequest, SourceDocument
from aer.errors import AerError
from aer.extract import extract_text
from aer.services.acquisition import record_acquisition
from aer.services.extractions import record_excerpts
from aer.sources.base import ResolvedEntity
from aer.sources.sec.submissions import ANNUAL_FORMS, QUARTERLY_FORMS, Filing, SubmissionsIndex
from aer.storage.protocol import ArtefactStore

__all__ = [
    "CURRENT_FORMS",
    "MAX_CURRENT_REPORTS",
    "MAX_QUARTERLY_REPORTS",
    "AcquiredFiling",
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

# How many quarterly reports to take: every one filed since the annual report, and there
# are at most three of those between two annuals. A quarterly the annual has since covered
# is not fetched — its narrative is a subset of a document the run already reads.
MAX_QUARTERLY_REPORTS: Final = 3

# Paragraphs excerpted per document. Enough that a section has something to cite, small
# enough that one 10-K does not fill an evidence pack on its own — the pack is assembled
# against a token budget and a document that crowded out every other source would be worse
# than the silence this module exists to end. Sixty rather than the original forty: a live
# run's whole prose base came to thirty-seven excerpts, and the pack assembler can only
# choose from what was recorded.
MAX_EXCERPTS: Final = 60

# The shortest run of text worth recording as an excerpt. Below this it is a heading, a
# page number or a table cell adrift from its table: a citation pointing at "12" verifies
# and means nothing.
MIN_EXCERPT_CHARS: Final = 120

# Which extractor reads which kind. Anything else is archived and citable but not read:
# the platform holds the bytes either way, and guessing at an extractor is how a parser
# meets content it was not written for.
# A paragraph boundary: one blank line, however much whitespace is on it.
_PARAGRAPH_BREAK: Final[re.Pattern[str]] = re.compile(r"\n[ \t]*\n")

# A statutory item heading at the start of a line. The forms prescribe these, which is what
# makes cutting on them deterministic rather than a guess about how a filer writes.
_ITEM_HEADING: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<item>Item\s+\d+[A-Z]?)\s*[.:\u2014-]", re.IGNORECASE | re.MULTILINE
)

# A part heading. A 10-K numbers its items uniquely across the whole filing; a 10-Q
# restarts at "Item 1" inside each part, so without the part an MD&A cut lands on the
# condensed financial statements instead.
_PART_HEADING: Final[re.Pattern[str]] = re.compile(
    r"^\s*PART\s+(?P<part>[IVX]+)\b", re.IGNORECASE | re.MULTILINE
)

# Where each form is obliged to put the prose a research report wants. The first version
# of this was one 10-K-shaped set applied to everything, and on a 10-Q it selected nothing
# a section could use: a 10-Q's management discussion is Item 2 *of Part I*, not Item 7.
#
# 10-K: the business description, the risk factors, and management's account of the year.
# Item 7A — market risk — is deliberately absent: it is mostly tables, and the tables are
# already in the XBRL facts. 20-F: the closest equivalents under that form's numbering.
# 10-Q: the quarter's management discussion, and the risk-factor *updates* in Part II.
# A form not listed here — an 8-K, a 6-K — is read whole in `_regions`.
_PROSE_ITEMS: Final[dict[str, frozenset[str]]] = {
    "10-K": frozenset({"ITEM1", "ITEM1A", "ITEM7"}),
    "20-F": frozenset({"ITEM3", "ITEM4", "ITEM5"}),
    "10-Q": frozenset({"PARTI.ITEM2", "PARTII.ITEM1A"}),
}

# The vocabulary of the questions a research report asks. Not a model, not a similarity
# measure — a count of the words that distinguish a paragraph about the business from a
# paragraph about the transfer agent's address.
_WORTH_READING: Final[tuple[str, ...]] = (
    "revenue",
    "margin",
    "growth",
    "segment",
    "customer",
    "competition",
    "competitor",
    "market share",
    "pricing",
    "demand",
    "cost",
    "capital",
    "cash flow",
    "operating",
    "risk",
    "regulat",
    "litigation",
    "acquisition",
    "guidance",
    "outlook",
    "strategy",
    "invest",
    "dividend",
    "repurchase",
    "debt",
    "currency",
    "supply",
)

_EXTRACTORS: Final[dict[str, str]] = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/xml": "html",
    "text/xml": "html",
    "application/pdf": "pdf",
}


@dataclass(frozen=True, slots=True)
class AcquiredFiling:
    """One filing the sweep brought back: the record, and what the index said it was.

    The form, accession and artefact digest travel here because the extract step reads
    periodic filings back by hash for the segment sweep, and a ``SourceDocument`` row
    carries none of the three — reparsing them out of a title would be provenance by
    string-matching.
    """

    document: SourceDocument
    form: str
    accession: str
    sha256: str


@dataclass(frozen=True, slots=True)
class AcquiredFilings:
    """What the filing sweep brought back, and what it could not."""

    filings: tuple[AcquiredFiling, ...] = ()
    excerpts: int = 0
    skipped: tuple[str, ...] = field(default=())

    @property
    def documents(self) -> tuple[SourceDocument, ...]:
        return tuple(item.document for item in self.filings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "filings": [
                {
                    "source_document_id": str(item.document.id),
                    "url": item.document.url,
                    "title": item.document.title,
                    "form": item.form,
                    "accession": item.accession,
                    "artefact_sha256": item.sha256,
                    "publication_date": (
                        item.document.publication_date.isoformat()
                        if item.document.publication_date
                        else None
                    ),
                    "quarantined": item.document.quarantined,
                }
                for item in self.filings
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
    company: Company,
    settings: Settings,
    job_id: uuid.UUID | None = None,
    max_current: int = MAX_CURRENT_REPORTS,
) -> AcquiredFilings:
    """Fetch this entity's latest annual report, the quarterlies since it, and its
    recent current reports.

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
    acquired: list[AcquiredFiling] = []
    excerpts = 0
    skipped = list(missing)

    for filing in wanted:
        outcome = await _acquire_one(
            session,
            store,
            client=client,
            request=request,
            entity=entity,
            company=company,
            index=index,
            filing=filing,
            settings=settings,
            job_id=job_id,
        )
        if isinstance(outcome, str):
            skipped.append(outcome)
            continue
        record, recorded = outcome
        acquired.append(record)
        excerpts += recorded

    _log.info(
        "filings.acquired",
        cik=index.cik,
        documents=len(acquired),
        excerpts=excerpts,
        skipped=len(skipped),
    )
    return AcquiredFilings(filings=tuple(acquired), excerpts=excerpts, skipped=tuple(skipped))


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

    # The quarters the annual report has not yet caught up with. A run as at mid-year was
    # reading a narrative up to three quarters stale — the live report's freshest company
    # prose predated three filed 10-Qs — and a quarterly the annual has since covered is
    # deliberately absent, because its account of the year is a subset of the annual's.
    quarterly = sorted(
        (
            item
            for item in candidates
            if item.form in QUARTERLY_FORMS
            and (annual is None or item.filing_date > annual.filing_date)
        ),
        key=lambda item: (item.filing_date, item.accession),
        reverse=True,
    )[:MAX_QUARTERLY_REPORTS]

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

    wanted = [annual, *quarterly, *current] if annual else [*quarterly, *current]
    return wanted, missing


async def _acquire_one(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    entity: ResolvedEntity,
    company: Company,
    index: SubmissionsIndex,
    filing: Filing,
    settings: Settings,
    job_id: uuid.UUID | None,
) -> tuple[AcquiredFiling, int] | str:
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
        company_id=company.id,
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

    recorded = await _excerpt(
        session, store, document=document, settings=settings, form=filing.form
    )
    record = AcquiredFiling(
        document=document,
        form=filing.form,
        accession=filing.accession,
        sha256=acquisition.sha256,
    )
    return record, recorded


async def _excerpt(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    document: SourceDocument,
    settings: Settings,
    form: str,
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

    excerpts = _paragraphs(extracted.text, form=form)
    # Per-document supply numbers (gap A49). The live run drafted every section against
    # a truncated pack built from 43 excerpts across nine documents — a 1.5MB 10-K among
    # them — and the log could not say whether the item cutting, the paragraph splitting
    # or the scoring was what starved it. These figures make the next run answer that:
    # a large document yielding few, long excerpts is a splitting failure; many short
    # candidates cut to few is the ceiling; few characters is the extractor.
    _log.info(
        "filings.excerpted",
        url=document.url,
        form=form,
        characters=len(extracted.text.text),
        excerpts=len(excerpts),
        excerpt_ceiling=MAX_EXCERPTS,
        mean_excerpt_chars=(
            sum(len(item.text) for item in excerpts) // len(excerpts) if excerpts else 0
        ),
    )
    if not excerpts:
        return 0

    rows = await record_excerpts(
        session, source_document_id=document.id, extracted=extracted.text, excerpts=excerpts
    )
    return len(rows)


def _paragraphs(extracted: Any, *, form: str) -> list[Excerpt]:
    """The passages most worth citing, in document order, as located excerpts.

    **Document order alone was the first version and it was nearly useless on a 10-K.**
    Forty paragraphs from the top of an annual report is the cover page, the exchange
    listing table and the auditor's address — every one of them genuinely present in the
    artefact and none of them anything a research section wants to cite.

    Two deterministic passes replace it. First the document is cut at its statutory item
    headings, because a 10-K's structure is prescribed and the useful prose is in three
    known places: the business description, the risk factors and management's discussion.
    Then paragraphs inside those items are scored on the vocabulary a research report
    actually uses, and the best are kept — in document order, because a reader following a
    citation back expects the filing's own sequence.

    No model call. The selection is reproducible run to run, which the replay harness will
    need, and a filing whose headings this does not recognise falls back to the whole
    document rather than to nothing.

    Split on blank lines rather than on every newline: the extractor keeps the line breaks
    the filer's own markup had, so a paragraph arrives as several short lines and splitting
    on each would produce fragments too small to mean anything.

    Located by searching the extracted text for each candidate, so every locator is the
    real offset in the real artefact and the verifier will find exactly what it is shown.
    ``start`` advances so two identical paragraphs — boilerplate, most often — do not both
    resolve to the first one.
    """
    text: str = extracted.text
    candidates = [
        (index, block)
        for index, block in _blocks(text, _regions(text, form=form))
        if len(block) >= MIN_EXCERPT_CHARS
    ]
    if not candidates:
        return []

    ranked = sorted(candidates, key=lambda pair: (-_score(pair[1]), pair[0]))[:MAX_EXCERPTS]

    found: list[Excerpt] = []
    cursor = 0
    for _, block in sorted(ranked, key=lambda pair: pair[0]):
        excerpt = extracted.locate(block, start=cursor)
        if excerpt is None:  # pragma: no cover -- it came from this text
            continue
        found.append(excerpt)
        cursor = excerpt.locator.char_end
    return found


def _regions(text: str, *, form: str) -> list[tuple[int, int]]:
    """The spans of the filing worth reading, or the whole thing if it has no items.

    A periodic form's headings are prescribed, which is what makes this deterministic
    rather than a guess: ``Item 1.``, ``Item 1A.`` and ``Item 7.`` are where a 10-K filer
    is *required* to put the business description, the risk factors and management's own
    account of the year, and :data:`_PROSE_ITEMS` records the equivalent places for the
    other periodic forms. An 8-K has no such structure and no items to find, so it is read
    whole — which is right, because an 8-K is short and entirely about one event.

    An item is matched both bare and part-qualified, so a form whose item numbers are
    unique across the filing (a 10-K) needs no part headings present, while one whose
    numbering restarts each part (a 10-Q) cuts only where the part agrees. An amended
    form (``10-K/A``) cuts as the form it amends.
    """
    wanted = _PROSE_ITEMS.get(form.split("/", 1)[0].strip().upper())
    if wanted is None:
        return [(0, len(text))]

    starts = sorted(
        (match.start(), match.group("item").upper().replace(" ", ""))
        for match in _ITEM_HEADING.finditer(text)
    )
    if not starts:
        return [(0, len(text))]

    parts = sorted(
        (match.start(), match.group("part").upper()) for match in _PART_HEADING.finditer(text)
    )

    regions: list[tuple[int, int]] = []
    for position, (offset, item) in enumerate(starts):
        part = _part_at(parts, offset)
        names = {item} if part is None else {item, f"PART{part}.{item}"}
        if not (names & wanted):
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(text)
        regions.append((offset, end))
    # Every heading matched something the form does not oblige a filer to fill usefully.
    # Reading the whole document beats reading none of it.
    return regions or [(0, len(text))]


def _part_at(parts: list[tuple[int, str]], offset: int) -> str | None:
    """The part an offset falls in — the last part heading before it — or ``None``."""
    current: str | None = None
    for start, part in parts:
        if start > offset:
            break
        current = part
    return current


def _blocks(text: str, regions: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """Paragraphs inside the wanted regions, with where each begins."""
    found: list[tuple[int, str]] = []
    for start, end in regions:
        offset = start
        for block in _PARAGRAPH_BREAK.split(text[start:end]):
            stripped = block.strip()
            if stripped:
                found.append((offset + block.find(stripped), stripped))
            offset += len(block) + 2
    return found


def _score(block: str) -> int:
    """How much a research section is likely to want this paragraph.

    Counting the vocabulary of the questions a report asks — what the business does, what
    it earns, what could go wrong — rather than measuring similarity to anything. A
    deliberately blunt instrument: it is choosing between passages of a filed document,
    not deciding what is true, and a sophisticated ranker here would be a model in
    everything but name.
    """
    lowered = block.lower()
    return sum(1 for term in _WORTH_READING if term in lowered)
