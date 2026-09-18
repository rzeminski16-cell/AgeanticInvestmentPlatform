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
recent current reports, as the index stands, fetched, dated, archived and excerpted.

**Every document is dated by its acceptance, not by the period it covers.** The date a
filing became public is what its provenance honestly records, and it is what
:meth:`Filing.to_ref` carries: a 10-K for the year to June, accepted in August, is dated
August, because that is when anyone could first have read it.

**Excerpts are recorded here, not left to the reader.** A source document with no
extractions contributes nothing to a section's evidence pack and cannot be cited, so
acquiring a filing without excerpting it would leave the same silence in a more expensive
way. The excerpts are the document's own paragraphs, in order, which makes them
deterministic, genuinely present in the artefact, and exactly what the citation verifier
re-reads.

**Two registers, one path through this module** (ADR 0121). :func:`acquire_filings` sweeps
EDGAR; :func:`acquire_accounts` sweeps a Companies House filing history. They differ in what
they ask for — the SEC's index says what each filing is *about* and offers quarterlies and
current reports, where the UK register offers a company's accounts and nothing else — and
they converge on one routine for a single document, because the hash, the tier, the dating,
the excerpting and the reason a document was skipped must not have two answers.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.dates import format_date
from aer.core.enums import Provider, SourceTier
from aer.core.schemas.extraction import Excerpt
from aer.core.sectors import SicScheme
from aer.db.models import Company, ResearchRequest, SourceDocument
from aer.errors import AerError
from aer.extract import extract_text
from aer.services.acquisition import acquisition_root, record_acquisition
from aer.services.extractions import MAX_EXCERPT_CHARS, record_excerpts
from aer.sources.base import DocumentRef, ResolvedEntity
from aer.sources.sec.accession import substantive_exhibits
from aer.sources.sec.submissions import ANNUAL_FORMS, QUARTERLY_FORMS, Filing, SubmissionsIndex
from aer.sources.uk.companies_house import FACT_DEPTH as CH_FACT_DEPTH
from aer.sources.uk.companies_house import NOT_TAGGED_STATUS
from aer.storage.protocol import ArtefactStore

__all__ = [
    "CURRENT_FORMS",
    "MAX_CURRENT_REPORTS",
    "MAX_QUARTERLY_REPORTS",
    "AcquiredFiling",
    "AcquiredFilings",
    "acquire_accounts",
    "acquire_filings",
]

_log = structlog.get_logger("aer.services.filings")

# Who published the document, as the sources page prints it. Named because it is written on
# every EDGAR artefact and read by nobody who would notice a typo in one of them.
SEC_PUBLISHER: Final = "US Securities and Exchange Commission"
COMPANIES_HOUSE_PUBLISHER: Final = "Companies House"

# Material events between the periodic reports: an acquisition, a guidance change, a
# departure. This is what "recent developments" is actually about, and the reason a run
# reading only the annual aggregate had nothing to say about it.
CURRENT_FORMS: Final[frozenset[str]] = frozenset({"8-K", "6-K"})

# How many current reports to take. A large filer files dozens a year, most of them
# routine; the newest handful is where anything the research has not already priced in
# will be. Bounded because each is a fetch under SEC's rate limit and an artefact to keep.
MAX_CURRENT_REPORTS: Final = 5

# The 8-K item codes that mean a filer is reporting how it did, rather than registering a
# share issue or a director's departure. 2.02 is "Results of Operations and Financial
# Condition"; 7.01 is "Regulation FD Disclosure", which is where guidance and the investor
# presentation are furnished. Everything else fills the remaining slots by date.
MATERIAL_ITEM_CODES: Final[frozenset[str]] = frozenset({"2.02", "7.01"})

# What a foreign private issuer's 6-K headline says when it is a results announcement. A
# 6-K carries no item codes, so its `primaryDocDescription` — the RNS headline EDGAR stores
# and the run already records as the title — is the only thing that says what it is.
_RESULTS_HEADLINE_WORDS: Final[tuple[str, ...]] = (
    "result",
    "earnings",
    "interim",
    "half-year",
    "half year",
    "full year",
    "full-year",
    "quarter",
    "trading statement",
    "trading update",
    "guidance",
    "outlook",
)

# How many exhibits one current report may bring with it (ADR 0126). An 8-K can carry a
# dozen; the run must not be able to spend its fetch budget inside one folder. Two, because
# a filer furnishing more than that under EX-99 is furnishing slides and photographs after
# the release itself, and the release is first in the filer's own sequence.
MAX_EXHIBITS_PER_FILING: Final = 2

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

# The longest text one excerpt may carry lives with the rows it bounds — see
# `aer.services.extractions`, which owns them and which ADR 0119's printing gate reads it
# from. Splitting on blank lines assumed the extractor's text has them; iXBRL-derived
# filings mostly do not, and the MTB run's 10-K arrived as nine blocks averaging 36,000
# characters — whole statutory items, none of them a citable passage, and every section
# drafted against a pack truncated to fit them (gap A49's instrumentation measured this).
# A block past the bound is re-cut at the last line break inside it, failing that the last
# sentence end, failing that the last space, so a filing with no blank lines still yields
# paragraph-sized excerpts.

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

    **The index is also where the filer says what kind of business it is** — the one place
    this run sees a SIC code — so the company's classification is filled from it here.
    ADR 0029 blocks a bank's discounted cash flow at the type level, and `classify`
    proposes from `Company.sic`; with nothing filling that column, M&T Bank's audit run
    classified nothing, met no sector gate and took the standard model. Recording it costs
    no extra request.
    """
    try:
        index: SubmissionsIndex = (await client.fetch_submissions(entity.identifier)).data
    except AerError as unreachable:
        return AcquiredFilings(
            skipped=(f"The filing index could not be read: {unreachable.message}",)
        )

    _record_classification(company, index)

    wanted, missing = _wanted(index, max_current=max_current)
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

        # And whatever the accession holds beside it (ADR 0126). After the primary
        # document, never instead of it: an exhibit that could not be had costs an
        # exhibit, and the cover page is already recorded.
        extra, extra_excerpts, extra_skipped = await _acquire_exhibits(
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
        acquired.extend(extra)
        excerpts += extra_excerpts
        skipped.extend(extra_skipped)

    _log.info(
        "filings.acquired",
        cik=index.cik,
        documents=len(acquired),
        excerpts=excerpts,
        skipped=len(skipped),
    )
    return AcquiredFilings(filings=tuple(acquired), excerpts=excerpts, skipped=tuple(skipped))


async def acquire_accounts(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    entity: ResolvedEntity,
    company: Company,
    settings: Settings,
    job_id: uuid.UUID | None = None,
    depth: int = CH_FACT_DEPTH,
) -> AcquiredFilings:
    """A UK company's own accounts, newest first (ADR 0121).

    Args:
        client: The Companies House client. Typed loosely for the same reason its EDGAR
            sibling is: a test substitutes a stub without constructing one.
        depth: How many accounts filings to take. **A stated number, not a window**, because
            Companies House publishes no aggregate: the cost of a UK acquisition is linear in
            this, and left unbounded it would be a function of how long the company has
            existed rather than of what the research needs.

    **Only the filings the register holds a tagged copy of**, and that is a measurement
    rather than a preference. Asked for inline XBRL, the register serves it where the company
    filed through software and answers **406** where it does not — and what sits behind the
    other representation, for a listed company, is a scanned annual report of 8 to 36 MB with
    no tagged figures and no extractable text at all. Acquiring those would cost the bandwidth
    of a run to store pictures of pages nothing can read, so an untagged filing is recorded in
    ``skipped`` saying so, which is a fact about the filing rather than a failure.

    **A tagged document is both halves of the evidence at once**, which is what makes the UK
    path different rather than merely differently-sourced. A US run reads its numbers from
    EDGAR's aggregate and its prose from the filings beside it; here the accounts document is
    the only thing there is, so the same artefact is excerpted for citation *and* parsed for
    every figure the company tagged. Nothing here parses it — `extract` does, from the
    artefact, by hash, for the reason that step gives.

    Nothing raises for a document that cannot be had, exactly as in the EDGAR sweep: an
    unreadable year costs its own facts and leaves the other three standing.
    """
    try:
        refs = await client.discover_documents(entity)
    except AerError as unreachable:
        return AcquiredFilings(
            skipped=(f"The filing history could not be read: {unreachable.message}",)
        )

    wanted = refs[:depth]
    acquired: list[AcquiredFiling] = []
    excerpts = 0
    skipped: list[str] = []
    if not wanted:
        skipped.append(
            f"{entity.name} has filed no accounts this platform can fetch. The register lists "
            "the filing history and the older entries are index records with no document "
            "behind them."
        )

    for ref in wanted:
        outcome = await _acquire_ref(
            session,
            store,
            client=client,
            request=request,
            company=company,
            settings=settings,
            job_id=job_id,
            ref=ref,
            form=ref.form or "accounts",
            accession=ref.accession or "",
            provider=Provider.COMPANIES_HOUSE,
            publisher=COMPANIES_HOUSE_PUBLISHER,
            status_reasons={
                NOT_TAGGED_STATUS: (
                    f"The accounts filed on {_filed_on(ref)} are not tagged: the register "
                    "holds them as a scanned document rather than as inline XBRL, so they "
                    "carry no figures this platform can read and no text it can quote. "
                    "That is how a listed company's accounts are filed."
                )
            },
        )
        if isinstance(outcome, str):
            skipped.append(outcome)
            continue
        record, recorded = outcome
        acquired.append(record)
        excerpts += recorded

    _log.info(
        "accounts.acquired",
        company_number=entity.identifier,
        documents=len(acquired),
        excerpts=excerpts,
        skipped=len(skipped),
    )
    return AcquiredFilings(filings=tuple(acquired), excerpts=excerpts, skipped=tuple(skipped))


def _filed_on(ref: DocumentRef) -> str:
    """The day the register accepted this filing, for a sentence an operator reads.

    Through `format_date`, which expands the no-padding directive before the C library sees
    it: `%-d` strips the leading zero on glibc and raises on Windows, and this string is
    read by a person rather than parsed.
    """
    return format_date(ref.publication_date, "%-d %B %Y")


def _record_classification(company: Company, index: SubmissionsIndex) -> None:
    """Keep the filer's own SIC code on the company row.

    An index that carries no code leaves what is there alone: absent is not a correction,
    and the permissive state must be reached by the data saying nothing, never by a later
    fetch overwriting what an earlier one knew.

    The scheme is written with the code because this index is EDGAR's, and EDGAR's codes are
    US SIC (ADR 0121). Stated rather than assumed from the column default: a company acquired
    from Companies House first and read here second would otherwise keep a UK label on a US
    code, and `631` means different industries in the two.
    """
    if index.sic and company.sic != index.sic:
        company.sic = index.sic
        company.sic_description = index.sic_description or ""
        company.sic_scheme = SicScheme.US_SIC
        _log.info(
            "filings.classification_recorded",
            cik=index.cik,
            sic=index.sic,
            sic_description=company.sic_description,
        )


def _current_reports(candidates: Sequence[Filing], *, limit: int) -> list[Filing]:
    """The current reports worth reading, materiality first and then recency (ADR 0126).

    Recency alone read the wrong five. AstraZeneca's second run acquired *Admission of
    Further Securities to Trading*, *Total Voting Rights* and *Admission to Trading — EUR2.55
    billion Bond Offering*, and not the half-year results announcement, which is where a
    foreign private issuer states its guidance; its own worker recorded the consequence as a
    lead it could not follow.

    EDGAR says what a filing is about and the platform was discarding it. A domestic filer's
    8-K carries item codes — 2.02 is results of operations, 7.01 is Reg FD — and a foreign
    private issuer's 6-K carries none, so for that one the headline in ``description`` is
    read instead, against a short keyword list.

    **Before recency, never instead of it.** The cap does not move and the rest of the slots
    still fill by date, so this can only substitute a results release for the *least* recent
    routine notice. It can never make a run read more, or older, or less.
    """
    ordered = sorted(
        (item for item in candidates if item.form in CURRENT_FORMS),
        key=lambda item: (item.filing_date, item.accession),
        reverse=True,
    )
    material = [item for item in ordered if _reports_results(item)]
    chosen = material[:limit]
    chosen.extend(item for item in ordered if item not in chosen)
    return chosen[:limit]


def _reports_results(filing: Filing) -> bool:
    """Whether this current report is about results rather than about housekeeping."""
    codes = {code.strip() for code in filing.items.split(",") if code.strip()}
    if codes:
        return bool(codes & MATERIAL_ITEM_CODES)
    # A 6-K has no item codes at all, so the RNS headline is the only thing that says what
    # it is. Keyword-matched rather than classified: the list is short, the failure mode is
    # falling back to the date ordering, and a model call to read a headline would be a
    # model call in the acquisition step.
    headline = filing.description.lower()
    return any(word in headline for word in _RESULTS_HEADLINE_WORDS)


def _wanted(index: SubmissionsIndex, *, max_current: int) -> tuple[list[Filing], list[str]]:
    """Which filings to fetch, and what was not there to fetch.

    The whole index is a candidate: the newest annual report, the quarters it has not yet
    caught up with, and the most recent current reports (ADR 0113). A filing's date is
    carried on the record for the reader, never used to hide it.
    """
    annual = index.latest(ANNUAL_FORMS)

    candidates = index.filings

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

    current = _current_reports(candidates, limit=max_current)

    missing: list[str] = []
    if annual is None:
        missing.append(
            "No annual report (10-K, 20-F or 40-F) is listed for this entity, so the run "
            "has no narrative annual filing to read."
        )
    if not current:
        missing.append(
            "No current reports (8-K or 6-K) are listed for this entity, so there is "
            "nothing recent beyond the periodic filings."
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
    return await _acquire_ref(
        session,
        store,
        client=client,
        request=request,
        company=company,
        settings=settings,
        job_id=job_id,
        ref=filing.to_ref(index.cik, entity_name=entity.name),
        form=filing.form,
        accession=filing.accession,
    )


async def _acquire_ref(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    client: Any,
    request: ResearchRequest,
    company: Company,
    settings: Settings,
    job_id: uuid.UUID | None,
    ref: DocumentRef,
    form: str,
    accession: str,
    provider: Provider = Provider.SEC_EDGAR,
    publisher: str = SEC_PUBLISHER,
    status_reasons: Mapping[int, str] | None = None,
) -> tuple[AcquiredFiling, int] | str:
    """One filed document: fetched, recorded, excerpted. The reason on any failure.

    Shared by the primary document, by the exhibits beside it (ADR 0126) and by a UK
    company's accounts (ADR 0121), so each is acquired *identically* — the same fetch
    layer, the same hash, the same tier, the same excerpting, the same date. A second path
    would be a second set of answers to questions this one has already settled.

    ``status_reasons`` lets a caller say what one status *means* for its publisher, in the
    sentence an operator reads. Companies House answers 406 to mean "this filing has no
    tagged copy", which is a fact about the filing; "returned HTTP 406" says the same thing
    in a language nobody outside this file speaks.
    """
    try:
        result = await client.fetch_document(ref)
    except AerError as refused:
        return f"{form} {accession} could not be fetched: {refused.message}"

    if not result.ok:
        stated = (status_reasons or {}).get(result.status_code)
        return stated or f"{form} {accession} returned HTTP {result.status_code}."

    acquisition = await record_acquisition(
        session,
        store,
        work_order=await acquisition_root(session, request),
        job_id=job_id,
        company_id=company.id,
        result=result,
        provider=provider,
        # A filing is the regulatory record itself, which is what T1 means. The company
        # facts aggregate shares the tier because it is assembled from these.
        source_tier=SourceTier.T1_REGULATORY,
        title=ref.title,
        publisher=publisher,
        # The date the register accepted it. Stated on the index rather than inferred, so
        # unlike the aggregate (ADR 0044) this one is certain.
        publication_date=ref.publication_date,
        publication_date_confidence=1.0,
    )
    document = acquisition.source_document

    recorded = await _excerpt(session, store, document=document, settings=settings, form=form)
    record = AcquiredFiling(
        document=document,
        form=form,
        accession=accession,
        sha256=acquisition.sha256,
    )
    return record, recorded


async def _acquire_exhibits(
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
) -> tuple[list[AcquiredFiling], int, list[str]]:
    """A current report's EX-99 exhibits, acquired beside it (ADR 0126).

    **This is where the substance of an 8-K is.** Its primary document is a cover page
    whose whole content is a sentence saying the information is furnished as Exhibit 99.1,
    so a run that acquired the primary document and stopped acquired the sentence. The
    earnings release the console won its comparison on was one file away, in a folder this
    platform had already opened.

    Current reports only, EX-99 only, capped per filing, and nothing raises: the header is
    an extra read on top of a filing already acquired, so a folder that cannot be read
    costs the exhibits and never the filing.
    """
    if filing.form not in CURRENT_FORMS:
        return [], 0, []

    documents = await client.fetch_accession_documents(filing, cik=index.cik)
    exhibits = substantive_exhibits(
        documents, primary_document=filing.primary_document, limit=MAX_EXHIBITS_PER_FILING
    )
    acquired: list[AcquiredFiling] = []
    excerpts = 0
    skipped: list[str] = []
    for exhibit in exhibits:
        outcome = await _acquire_ref(
            session,
            store,
            client=client,
            request=request,
            company=company,
            settings=settings,
            job_id=job_id,
            ref=filing.exhibit_ref(
                index.cik,
                filename=exhibit.filename,
                document_type=exhibit.document_type,
                entity_name=entity.name,
            ),
            form=filing.form,
            accession=filing.accession,
        )
        if isinstance(outcome, str):
            skipped.append(outcome)
            continue
        record, recorded = outcome
        acquired.append(record)
        excerpts += recorded
    return acquired, excerpts, skipped


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
    on each would produce fragments too small to mean anything. But blank lines are a
    property of the markup, not of the form — an iXBRL filing can arrive with almost none,
    which handed the MTB run whole items as single "paragraphs" — so any block past
    :data:`MAX_EXCERPT_CHARS` is re-cut into paragraph-sized pieces before scoring.

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
    """Paragraphs inside the wanted regions, with where each begins.

    A block the blank-line split leaves oversized — an iXBRL filing can deliver a whole
    statutory item as one — is cut into paragraph-sized pieces rather than kept whole,
    because an excerpt the length of an item is not a passage anyone can cite.
    """
    found: list[tuple[int, str]] = []
    for start, end in regions:
        offset = start
        for block in _PARAGRAPH_BREAK.split(text[start:end]):
            stripped = block.strip()
            if stripped and len(stripped) <= MAX_EXCERPT_CHARS:
                found.append((offset + block.find(stripped), stripped))
            elif stripped:
                at = offset + block.find(stripped)
                for inside, piece in _pieces(stripped):
                    trimmed = piece.strip()
                    if trimmed:
                        found.append((at + inside + piece.find(trimmed), trimmed))
            offset += len(block) + 2
    return found


def _pieces(block: str) -> list[tuple[int, str]]:
    """Contiguous spans of an oversized block, each within the excerpt bound.

    Cut at the last line break before the bound, failing that the last sentence end,
    failing that the last space — a hard cut only when a single unbroken run leaves no
    choice. Every piece is a verbatim slice of the block, so the locator search that makes
    an excerpt citable finds exactly the text that was kept.
    """
    pieces: list[tuple[int, str]] = []
    position = 0
    while position < len(block):
        remainder = block[position:]
        if len(remainder) <= MAX_EXCERPT_CHARS:
            pieces.append((position, remainder))
            break
        window = remainder[:MAX_EXCERPT_CHARS]
        cut = window.rfind("\n")
        if cut < MIN_EXCERPT_CHARS:
            sentence_end = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
            cut = sentence_end + 1 if sentence_end >= MIN_EXCERPT_CHARS else -1
        if cut < MIN_EXCERPT_CHARS:
            cut = window.rfind(" ")
        if cut < MIN_EXCERPT_CHARS:
            cut = len(window)
        pieces.append((position, window[:cut]))
        position += cut
        while position < len(block) and block[position].isspace():
            position += 1
    return pieces


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
