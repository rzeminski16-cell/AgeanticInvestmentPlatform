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

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.calc.basic import growth_rate, ratio
from aer.calc.engine import CalculationContext
from aer.calc.units import DIMENSIONLESS, Quantity, SourceRef, money
from aer.config import Settings
from aer.core.enums import ClaimKind, FactBasis, JobStatus, Provider, SourceTier
from aer.db.models import (
    Artefact,
    Assumption,
    Calculation,
    Company,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    Skill,
    SourceDocument,
    User,
)
from aer.db.models.source_document import NO_PUBLICATION_DATE
from aer.extract.html import extract_html
from aer.services import calculations as calculation_service
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

# Citations whose targets deliberately do not exist. The document renders them as its
# honest unresolved footnotes, and the drill-down must state the same dead end.
MISSING_SOURCE_ID = uuid.UUID(int=0x5001)
MISSING_CALC_ID = uuid.UUID(int=0x5002)

# The root calculation's own formula, as the traced engine records it.
WALK_FORMULA = "ratio = numerator / denominator"
WALK_SECTION_KEY = "evidence_walk_demo"

# A user-authored ("skill") section carrying cited figures, so the document the preview
# renders has footnote markers — and has them from a *custom* section, which is what the
# phase's acceptance line means by "regardless of which section it came from".
_WALK_CONTRACT: dict[str, Any] = {
    "type": "object",
    "title": "Evidence Walk",
    "properties": {
        "figures": {
            "type": "array",
            "title": "Figures",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    "calculation_id": {"type": "string"},
                    "source_document_id": {"type": "string"},
                },
            },
        },
    },
}


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

    calculation, walk_section = await _walk_section(
        session, job=job, request=request, filing=filing
    )

    filing_artefact = await session.get(Artefact, filing.artefact_id)
    assert filing_artefact is not None
    filing_digest = filing_artefact.sha256

    markers = await _walk_markers(
        session, job=job, request=request, filing=filing, calculation=calculation
    )

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
        "calculation": calculation,
        "walk_section": walk_section,
        # The document's marker numbers, read off the same assembly the preview and the
        # drill-down share. Hardcoding them broke the moment the at-a-glance panel began
        # claiming the leading footnote numbers; derived, they follow the document.
        "markers": markers,
    }


async def _walk_markers(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    filing: SourceDocument,
    calculation: Calculation,
) -> dict[str, int]:
    """The walk section's four marker numbers, as the served document assigns them.

    Read from :func:`aer.web.pages._run_document` — the one assembly the preview and the
    footnote drill-down share — so these numbers mean what a browser's do by
    construction rather than by arithmetic this fixture would have to keep in step.
    """
    from aer.web.pages import _run_document  # noqa: PLC0415 -- avoids a web import for every user

    document = await _run_document(session, job=job, research_request=request)
    walk = next(view for view in document.sections if view.key == WALK_SECTION_KEY)
    targets = {
        ("source_document", str(filing.id)): "source",
        ("calculation", str(calculation.id)): "calculation",
        ("source_document", str(MISSING_SOURCE_ID)): "missing_source",
        ("calculation", str(MISSING_CALC_ID)): "missing_calc",
    }
    markers: dict[str, int] = {}
    for ref in walk.citations:
        name = targets.get((ref.kind, ref.identifier))
        if name is not None:
            markers[name] = list(document.citations).index(ref) + 1
    assert set(markers) == set(targets.values()), markers
    return markers


async def _lineage_chain(
    session: AsyncSession, *, job: Job, request: ResearchRequest, filing: SourceDocument
) -> Calculation:
    """A calculation with real lineage: two facts, one assumption, two levels.

    Built through the traced engine rather than by writing ``inputs`` JSON by hand, for
    the reason this whole module exists — a hand-written provenance chain would make the
    walk page pass while proving nothing about what the platform records.

    The walk from the root therefore reaches exactly three leaves: two facts and an
    assumption. Never a calculation: a chain that stopped on one would be incomplete
    without saying so.
    """
    # Companies and observations are shared rows keyed by their natural identity, and this
    # builder commits for real — so a second run against the same database reuses them
    # rather than tripping their unique keys.
    company = await session.scalar(
        select(Company).where(Company.ticker == "MSFT", Company.exchange == "NASDAQ")
    )
    if company is None:
        company = Company(
            name="Microsoft Corporation", cik="0000789019", ticker="MSFT", exchange="NASDAQ"
        )
        session.add(company)
        await session.flush()

    facts: list[FinancialFact] = []
    for year, value, accession in (
        (2025, "168088000000", "0000789019-25-000027"),
        (2026, "198270000000", "0000789019-26-000010"),
    ):
        period_end = date(year, 6, 30)
        fact = await session.scalar(
            select(FinancialFact).where(
                FinancialFact.company_id == company.id,
                FinancialFact.concept == "revenue",
                FinancialFact.period_end == period_end,
            )
        )
        if fact is None:
            fact = FinancialFact(
                company_id=company.id,
                source_document_id=filing.id,
                concept="revenue",
                raw_concept="Revenues",
                taxonomy="us-gaap",
                value=Decimal(value),
                unit="USD",
                period_end=period_end,
                fiscal_year=year,
                fiscal_period="FY",
                filed_date=date(year, 7, 30),
                form="10-K",
                accession=accession,
                basis=FactBasis.AS_REPORTED,
            )
            session.add(fact)
        facts.append(fact)

    # Assumptions belong to the request, which is new every time — no lookup needed.
    assumption = Assumption(
        request_id=request.id,
        name="terminal_growth",
        value=Decimal("0.025"),
        unit="pure",
        justification="Long-run nominal GDP growth for the US, per the CBO projection.",
        confidence=0.6,
        proposed_by="analysis",
    )
    session.add(assumption)
    await session.flush()

    context = CalculationContext(code_version="test")
    growth = growth_rate(
        context,
        start=money(facts[0].value, "USD", source=SourceRef.fact(facts[0].id, label="revenue")),
        end=money(facts[1].value, "USD", source=SourceRef.fact(facts[1].id, label="revenue")),
    )
    ratio(
        context,
        numerator=growth,
        denominator=Quantity.of(
            assumption.value,
            DIMENSIONLESS,
            source=SourceRef.assumption(assumption.id, label="terminal_growth"),
        ),
    )
    rows = await calculation_service.persist_context(session, context, job_id=job.id)
    # The root is the last recorded: `ratio` consumed `growth_rate`'s output.
    return rows[-1]


async def _walk_section(
    session: AsyncSession, *, job: Job, request: ResearchRequest, filing: SourceDocument
) -> tuple[Calculation, ReportSection]:
    """A calculation with lineage, and a skill-authored section whose figures cite it.

    Four markers come out of this in the rendered document: the filing, the calculation,
    and two whose targets deliberately do not exist. Skill-origin rather than built-in,
    so the drill-down is proved on a section the operator authored — the acceptance
    line's "regardless of which section it came from".
    """
    calculation = await _lineage_chain(session, job=job, request=request, filing=filing)

    # Looked up before created, like the user above: this builder commits for real, and a
    # second run against the same database must reuse the identity rows rather than trip
    # their unique keys.
    skill = await session.scalar(select(Skill).where(Skill.key == WALK_SECTION_KEY))
    if skill is None:
        skill = Skill(key=WALK_SECTION_KEY, kind="custom_section", enabled=True)
        session.add(skill)
    await session.flush()

    definition = await session.scalar(
        select(SectionDefinition).where(SectionDefinition.key == WALK_SECTION_KEY)
    )
    if definition is None:
        definition = SectionDefinition(
            key=WALK_SECTION_KEY,
            version=1,
            origin="skill",
            skill_id=skill.id,
            title="Evidence Walk",
            position=Decimal("640"),
            required=False,
            output_contract=_WALK_CONTRACT,
            evidence_policy={"min_sources": 0, "requires_primary": False},
            token_budget=1000,
            allowed_tools=[],
            applicability={},
        )
        session.add(definition)
        await session.flush()

    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content={
            "figures": [
                {
                    "label": "Revenue growth",
                    "value": "0.18",
                    "unit": "ratio",
                    "source_document_id": str(filing.id),
                    "calculation_id": str(calculation.id),
                },
                {
                    "label": "Ghost figure",
                    "value": "9.99",
                    "unit": "x",
                    "source_document_id": str(MISSING_SOURCE_ID),
                },
                {
                    "label": "Ghost arithmetic",
                    "value": "1.23",
                    "unit": "x",
                    "calculation_id": str(MISSING_CALC_ID),
                },
            ],
        },
    )
    session.add(section)
    await session.flush()
    return calculation, section


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
