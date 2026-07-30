"""A committed run with real evidence: a verified citation, an unverified one, and a
quarantined source.

Committed rather than flushed, because the HTTP surfaces run through the application's own
session and a rolled-back transaction is invisible to it. A plain builder rather than a
fixture for the reason ``scene_fixtures`` gives: pytest reports a fixture imported into two
modules as a redefinition at every call site.

**The evidence is built through the real services and verified by the real verifier.** A
hand-written ``excerpt_verified = True`` would make every one of these surfaces pass while
proving nothing about whether they show what the platform actually concluded.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.enums import ClaimKind, JobStatus, Provider, SourceTier
from aer.db.models import (
    Artefact,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    SourceDocument,
    User,
)
from aer.db.models.source_document import NO_PUBLICATION_DATE
from aer.extract.html import extract_html
from aer.services.citations import record_citation, record_claim
from aer.services.extractions import record_excerpt
from aer.storage.local import LocalArtefactStore
from aer.verify.citations import verify

__all__ = [
    "FABRICATED",
    "FILING",
    "SUPPORTED_SENTENCE",
    "build_evidence",
    "committed_evidence",
]

FILING = b"""<!DOCTYPE html><html><head><title>Form 10-K</title></head><body>
<p>Total revenue was $198,270 million for fiscal year 2022.</p>
<p>Operating income was $83,383 million for fiscal year 2022.</p>
</body></html>"""

SUPPORTED_SENTENCE = "Total revenue was $198,270 million for fiscal year 2022."

# Plausible, well formed, about the right company, and not in the document. The only thing
# that can tell the difference is re-reading the document.
FABRICATED = "Total revenue was $250,000 million for fiscal year 2022."

PRESS_RELEASE = b"<!DOCTYPE html><html><body><p>An undated marketing page.</p></body></html>"

AS_OF = date(2026, 6, 30)


async def build_evidence(
    session: AsyncSession, store: LocalArtefactStore, settings: Settings, *, email: str
) -> dict[str, Any]:
    """One run carrying every state the surfaces have to show.

    Two claims: one whose citation verifies, one whose citation does not. Two sources: one
    admissible tier-1 filing, one quarantined undated page. Nothing is faked — the verifier
    is run and its verdicts are whatever it produces.
    """
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, display_name="Provenance")
        session.add(user)
        await session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    session.add(request)
    await session.flush()

    job = Job(
        request_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()

    definition = await session.scalar(
        select(SectionDefinition).order_by(SectionDefinition.position).limit(1)
    )
    assert definition is not None, "the migration seeds section definitions"

    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content={"body": "Revenue grew."},
    )
    session.add(section)
    await session.flush()

    # A source that was refused. Present so the table has something to be honest about: a
    # page showing only the sources that were used cannot answer "what did this run reject,
    # and why?".
    #
    # **Acquired first, deliberately.** It is the lower tier, so a table ordered by
    # acquisition time would put it above the filing — which is how the tier ordering gets
    # tested rather than accidentally satisfied.
    quarantined = await _document(
        session,
        store,
        request_id=request.id,
        job_id=job.id,
        payload=PRESS_RELEASE,
        url="https://investors.example-plc.test/news/undated",
        title="An undated announcement",
        provider=Provider.ISSUER_IR,
        tier=SourceTier.T2_ISSUER,
        publication_date=None,
        licence_note="Issuer-published material; quoted under fair dealing.",
        quarantine_reason=NO_PUBLICATION_DATE,
    )

    filing = await _document(
        session,
        store,
        request_id=request.id,
        job_id=job.id,
        payload=FILING,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        title="Microsoft Corporation Form 10-K",
        provider=Provider.SEC_EDGAR,
        tier=SourceTier.T1_REGULATORY,
        publication_date=date(2026, 6, 12),
        licence_note="US government work; not subject to copyright in the United States.",
    )

    extracted = extract_html(FILING).text
    excerpt = extracted.locate(SUPPORTED_SENTENCE)
    assert excerpt is not None
    good_extraction = await record_excerpt(
        session, source_document_id=filing.id, extracted=extracted, excerpt=excerpt
    )

    # The unverifiable one. Recorded at a real locator and then given an excerpt the document
    # does not contain, which is exactly the shape of a hallucinated citation.
    other = extracted.locate("Operating income was $83,383 million for fiscal year 2022.")
    assert other is not None
    bad_extraction = await record_excerpt(
        session, source_document_id=filing.id, extracted=extracted, excerpt=other
    )
    bad_extraction.excerpt = FABRICATED
    await session.flush()

    supported = await record_claim(
        session,
        section=section,
        kind=ClaimKind.FACTUAL,
        text="Total revenue was $198,270 million in fiscal 2022.",
    )
    good_citation = await record_citation(
        session,
        claim=supported,
        source_document_id=filing.id,
        extraction_id=good_extraction.id,
    )

    unsupported = await record_claim(
        session,
        section=section,
        kind=ClaimKind.FACTUAL,
        text="Total revenue was $250,000 million in fiscal 2022.",
    )
    bad_citation = await record_citation(
        session,
        claim=unsupported,
        source_document_id=filing.id,
        extraction_id=bad_extraction.id,
    )

    # The real verifier, on both. Its verdicts are what the surfaces then have to show.
    await verify(session, store, citation=good_citation, settings=settings)
    await verify(session, store, citation=bad_citation, settings=settings)

    filing_artefact = await session.get(Artefact, filing.artefact_id)
    assert filing_artefact is not None
    filing_digest = filing_artefact.sha256

    return {
        "user": user,
        "request": request,
        "job": job,
        "section": section,
        "filing": filing,
        # Read out here rather than through `filing.artefact` at assertion time: the caller
        # may hold these rows after the session closes, and a lazy load on a detached
        # instance raises.
        "filing_sha256": filing_digest,
        "quarantine_reason": quarantined.quarantine_reason,
        "quarantined": quarantined,
        "supported_claim": supported,
        "unsupported_claim": unsupported,
        "good_citation": good_citation,
        "bad_citation": bad_citation,
    }


async def committed_evidence(
    engine: Any, store: LocalArtefactStore, settings: Settings, *, email: str
) -> dict[str, Any]:
    """:func:`build_evidence`, committed so the application's own session can see it."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        built = await build_evidence(session, store, settings, email=email)
        await session.commit()
        return built


async def _document(
    session: AsyncSession,
    store: LocalArtefactStore,
    *,
    request_id: Any,
    job_id: Any,
    payload: bytes,
    url: str,
    title: str,
    provider: Provider,
    tier: SourceTier,
    publication_date: date | None,
    licence_note: str,
    quarantine_reason: str | None = None,
) -> SourceDocument:
    stored = await store.put_bytes(payload)
    artefact = await session.scalar(select(Artefact).where(Artefact.sha256 == stored.sha256))
    if artefact is None:
        artefact = Artefact(
            sha256=stored.sha256,
            media_type="text/html",
            size_bytes=stored.size_bytes,
            storage_key=store.storage_key_for(stored.sha256),
        )
        session.add(artefact)
        await session.flush()

    document = SourceDocument(
        request_id=request_id,
        job_id=job_id,
        artefact_id=artefact.id,
        url=url,
        title=title,
        publisher="Microsoft Corporation",
        provider=provider,
        source_tier=tier,
        publication_date=publication_date,
        publication_date_latest=publication_date,
        publication_date_confidence=0.99 if publication_date else None,
        publication_date_source="filing_index" if publication_date else None,
        retrieved_at=datetime.now(UTC),
        http_status=200,
        licence_note=licence_note,
        robots_allowed=None,
        quarantined=quarantine_reason is not None,
        quarantine_reason=quarantine_reason,
    )
    session.add(document)
    await session.flush()
    return document
