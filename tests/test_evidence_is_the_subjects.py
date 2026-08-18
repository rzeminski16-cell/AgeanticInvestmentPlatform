"""No section is shown another company's evidence — ADR 0061.

The first complete run acquired eight peers' filings under the subject's request, and both
evidence builders selected by ``request_id`` and never by company. An Amazon research note
cited Walmart, Alibaba, eBay, JD.com, MercadoLibre and Target as its evidence, its front
page carried three issuers' figures as one company's quarter, and the drafting model was
handed an annual pool in which the subject did not appear at all.

The predicate that fixes it is two lines. **These tests are the deliverable**, because two
lines are exactly what a later refactor drops without noticing.

Four properties, each tied to a way the live failure actually happened:

1. A peer's facts and documents never reach a pack, for any section in the spine.
2. The subject survives a peer that sorts *above* it — a later period end and a later
   retrieval time, which is what let one issuer take the whole pool.
3. A document about no issuer at all stays visible, so the fix does not cost the macro
   series and regulator notes a section legitimately rests on.
4. A second run of the same company still sees the first run's facts. Facts deduplicate on
   a key that excludes the source document, so re-scoping to the request would have hidden
   every fact the first run wrote — the failure `aer.services.research` already met once,
   and the one that would have made the acceptance rerun worse than the run it verifies.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import FactBasis, JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Company,
    FinancialFact,
    Job,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.render.glance import glance_content
from aer.sections.evidence import SectionPolicy, gather_evidence
from tests.workflow_fixtures import WORKFLOW_VERSION

pytestmark = pytest.mark.integration

_AS_OF = date(2026, 8, 18)

# Every category the pack gates on. Excerpts are not a third: they are selected from the
# source documents the listing admitted, so they inherit its scope and a leak in them would
# be a leak in `search_sources`.
_ALL_CATEGORIES = frozenset({"search_facts", "search_sources"})


def _policy(**overrides: Any) -> SectionPolicy:
    fields: dict[str, Any] = {
        "min_sources": 0,
        "requires_primary": False,
        "max_tier_rank": 6,
        "allow_forward_looking": True,
        "token_budget": 8_000,
        "fact_basis": "any",
        "concept_priority": (),
        "excerpt_keywords": (),
    }
    fields.update(overrides)
    return SectionPolicy(**fields)


async def _company(session: AsyncSession, *, name: str, cik: str, ticker: str) -> Company:
    company = Company(name=name, cik=cik, ticker=ticker, exchange="NASDAQ")
    session.add(company)
    await session.flush()
    return company


async def _document(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    job: Job,
    company: Company | None,
    label: str,
    retrieved_at: datetime,
) -> SourceDocument:
    artefact = Artefact(
        sha256=hashlib.sha256(label.encode()).hexdigest(),
        media_type="text/html",
        size_bytes=len(label),
        storage_key=f"aa/bb/{label}",
    )
    session.add(artefact)
    await session.flush()
    document = SourceDocument(
        request_id=request.id,
        job_id=job.id,
        company_id=company.id if company is not None else None,
        artefact_id=artefact.id,
        url=f"https://example.invalid/{label}",
        title=f"{label} filing",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        publication_date=date(2026, 1, 1),
        retrieved_at=retrieved_at,
        quarantined=False,
    )
    session.add(document)
    await session.flush()
    return document


async def _fact(
    session: AsyncSession,
    *,
    company: Company,
    document: SourceDocument,
    concept: str,
    period_end: date,
    value: str,
) -> FinancialFact:
    fact = FinancialFact(
        company_id=company.id,
        source_document_id=document.id,
        concept=concept,
        value=Decimal(value),
        unit="USD",
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        fiscal_period="FY",
        fiscal_year=period_end.year,
        filed_date=period_end + timedelta(days=30),
        basis=FactBasis.AS_REPORTED,
    )
    session.add(fact)
    await session.flush()
    return fact


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """One request, two companies, and the peer arranged to outrank the subject.

    The peer's year ends three months later and its document is retrieved a day later —
    the two orderings that put eight issuers above the subject on the live run.
    """
    user = User(email="scope@example.invalid", display_name="Scope", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    subject = await _company(db_session, name="SUBJECT INC", cik="0000000001", ticker="SUBJ")
    peer = await _company(db_session, name="PEER PLC", cik="0000000002", ticker="PEER")

    request = ResearchRequest(
        user_id=user.id,
        company_name="Subject Inc",
        ticker="SUBJ",
        exchange="NASDAQ",
        as_of_date=_AS_OF,
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="12.00",
        portfolio_context={},
        company_id=subject.id,
    )
    db_session.add(request)
    await db_session.flush()

    job = Job(
        request_id=request.id,
        workflow_version=WORKFLOW_VERSION,
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    retrieved = datetime.now(UTC)
    subject_doc = await _document(
        db_session,
        request=request,
        job=job,
        company=subject,
        label="subject",
        retrieved_at=retrieved,
    )
    peer_doc = await _document(
        db_session,
        request=request,
        job=job,
        company=peer,
        label="peer",
        # Acquired after the subject, exactly as peer discovery does.
        retrieved_at=retrieved + timedelta(hours=1),
    )
    macro_doc = await _document(
        db_session,
        request=request,
        job=job,
        company=None,
        label="macro",
        retrieved_at=retrieved,
    )

    await _fact(
        db_session,
        company=subject,
        document=subject_doc,
        concept="revenue",
        period_end=date(2025, 12, 31),
        value="1000",
    )
    # A later year end, so recency ordering prefers it. This is the Alibaba shape: a March
    # year end outranking a December one and filling the pool first.
    await _fact(
        db_session,
        company=peer,
        document=peer_doc,
        concept="revenue",
        period_end=date(2026, 3, 31),
        value="9999",
    )
    await db_session.commit()

    return {
        "session": db_session,
        "request": request,
        "job": job,
        "subject": subject,
        "peer": peer,
        "subject_doc": subject_doc,
        "peer_doc": peer_doc,
        "macro_doc": macro_doc,
    }


async def _gather(scene: dict[str, Any], **overrides: Any) -> Any:
    return await gather_evidence(
        scene["session"],
        request=scene["request"],
        evidence_job_id=scene["job"].id,
        policy=_policy(**overrides),
        categories=_ALL_CATEGORIES,
    )


def _internals(evidence: Any) -> list[dict[str, Any]]:
    return list(evidence.internal)


class TestAPeersEvidenceNeverReachesASection:
    async def test_no_fact_in_the_pack_belongs_to_another_company(
        self, scene: dict[str, Any]
    ) -> None:
        evidence = await _gather(scene)
        session = scene["session"]
        for item in _internals(evidence):
            if "fact_id" not in item:
                continue
            fact = await session.get(FinancialFact, item["fact_id"])
            assert fact is not None
            assert fact.company_id == scene["subject"].id, (
                f"a fact belonging to another company reached the pack: {item}"
            )

    async def test_no_source_in_the_pack_belongs_to_another_company(
        self, scene: dict[str, Any]
    ) -> None:
        evidence = await _gather(scene)
        listed = {
            item["source_document_id"]
            for item in _internals(evidence)
            if "source_document_id" in item
        }
        assert str(scene["peer_doc"].id) not in listed
        assert str(scene["subject_doc"].id) in listed

    async def test_the_peers_value_is_nowhere_in_the_prompt(self, scene: dict[str, Any]) -> None:
        """The end-to-end statement of the same thing, in the form the reader met it.

        A writer shown "9999" would print it, cite it correctly, and pass every check —
        which is exactly what happened.
        """
        evidence = await _gather(scene)
        assert "9999" not in str(_internals(evidence))
        assert "1000" in str(_internals(evidence))


class TestTheSubjectSurvivesAPeerThatOutranksIt:
    async def test_a_later_period_end_does_not_displace_the_subject(
        self, scene: dict[str, Any]
    ) -> None:
        evidence = await _gather(scene, fact_basis="annual")
        concepts = [item["concept"] for item in _internals(evidence) if "concept" in item]
        assert concepts, "the annual pool came back empty"
        assert "revenue" in concepts

    async def test_a_later_retrieval_does_not_displace_the_subjects_document(
        self, scene: dict[str, Any]
    ) -> None:
        evidence = await _gather(scene)
        listed = [
            item["source_document_id"]
            for item in _internals(evidence)
            if "source_document_id" in item
        ]
        assert str(scene["subject_doc"].id) in listed


class TestADocumentAboutNoIssuerStaysVisible:
    async def test_the_macro_document_is_listed(self, scene: dict[str, Any]) -> None:
        evidence = await _gather(scene)
        listed = {
            item["source_document_id"]
            for item in _internals(evidence)
            if "source_document_id" in item
        }
        assert str(scene["macro_doc"].id) in listed, (
            "a source with no company was excluded; NULL means 'not about an issuer', "
            "not 'not ours'"
        )


class TestASecondRunStillSeesTheFirstRunsFacts:
    async def test_the_pack_is_not_empty_for_a_repeat_request(self, scene: dict[str, Any]) -> None:
        """The failure `research.py` met, in the section layer.

        Facts deduplicate on an observation key that excludes the source document, so the
        second run of a company inserts nothing — its facts hang off the *first* run's
        document. A pack scoped to the request would therefore be empty here, and the
        acceptance rerun of a fixed platform would produce a report with none of the
        subject's own facts in it.
        """
        session = scene["session"]
        second = ResearchRequest(
            user_id=scene["request"].user_id,
            company_name="Subject Inc",
            ticker="SUBJ",
            exchange="NASDAQ",
            as_of_date=_AS_OF,
            base_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="12.00",
            portfolio_context={},
            company_id=scene["subject"].id,
        )
        session.add(second)
        await session.flush()
        job = Job(
            request_id=second.id,
            workflow_version=WORKFLOW_VERSION,
            code_version="test",
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        session.add(job)
        await session.flush()

        evidence = await gather_evidence(
            session,
            request=second,
            evidence_job_id=job.id,
            policy=_policy(),
            categories=_ALL_CATEGORIES,
        )
        concepts = [item["concept"] for item in _internals(evidence) if "concept" in item]
        assert "revenue" in concepts, (
            "the second run of a company saw none of its facts — the request-scoped join "
            "that aer.services.research already had to remove"
        )


class TestTheFrontPageCarriesOneIssuer:
    async def test_the_glance_holds_only_the_subjects_figures(self, scene: dict[str, Any]) -> None:
        content = await glance_content(scene["session"], job=scene["job"], request=scene["request"])
        assert content is not None
        assert "9999" not in str(content)

    async def test_an_unresolved_request_shows_nothing_rather_than_everything(
        self, scene: dict[str, Any]
    ) -> None:
        """Before `acquire` resolves the subject there is no subject, and the honest
        answer is an empty block rather than every issuer the request has touched."""
        scene["request"].company_id = None
        await scene["session"].flush()
        content = await glance_content(scene["session"], job=scene["job"], request=scene["request"])
        assert content is None or "9999" not in str(content)
