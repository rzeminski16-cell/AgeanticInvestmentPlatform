"""No section is shown another company's evidence — ADR 0061.

The first complete run acquired eight peers' filings under the subject's request, and both
evidence builders selected by ``request_id`` and never by company. An Amazon research note
cited Walmart, Alibaba, eBay, JD.com, MercadoLibre and Target as its evidence, its front
page carried three issuers' figures as one company's quarter, and the drafting model was
handed an annual pool in which the subject did not appear at all.

The predicate that fixes it is two lines. **These tests are the deliverable**, because two
lines are exactly what a later refactor drops without noticing.

Five properties, each tied to a way the live failure actually happened:

1. A peer's facts and documents never reach a pack, for any section in the spine.
2. The subject survives a peer that sorts *above* it — a later period end and a later
   retrieval time, which is what let one issuer take the whole pool.
3. A document about no issuer at all stays visible, so the fix does not cost the macro
   series and regulator notes a section legitimately rests on.
4. A second run of the same company still sees the first run's facts. Facts deduplicate on
   a key that excludes the source document, so re-scoping to the request would have hidden
   every fact the first run wrote — the failure `aer.services.research` already met once,
   and the one that would have made the acceptance rerun worse than the run it verifies.
5. A fact filed after the as-of date does not reach a pack. Request scoping used to bound a
   section to one acquisition and so kept later filings out by accident; company scoping
   does not, which is why the date filter travels with it. Twelve tests failed on this when
   the predicate landed — every one of them a scene dated 30 June carrying facts filed
   28 July, which is a look-ahead the old pack showed a writer without comment.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import aer.render.glance as glance_module
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
from tests.request_fixtures import research_request
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
        work_order_id=request.id,
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

    request = research_request(
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
        work_order_id=request.id,
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


class TestALaterFilingIsNotShownToAnEarlierRun:
    async def test_a_fact_filed_after_the_as_of_date_stays_out_of_the_pack(
        self, scene: dict[str, Any]
    ) -> None:
        """Scoping by company removed the accidental bound request scoping provided.

        The old pack joined to this run's documents, which happened to keep a later run's
        filings out. Company scope does not, so the date filter is part of the same change
        rather than a separate improvement — and this is the section-level statement of it.
        Twelve existing tests failed on this when the predicate landed: their scenes were
        dated 30 June and carried facts filed 28 July.
        """
        session = scene["session"]
        await _fact(
            session,
            company=scene["subject"],
            document=scene["subject_doc"],
            concept="revenue",
            # `_fact` files thirty days after the period ends, so this lands on
            # 30 September — six weeks past the as-of date.
            period_end=date(2026, 8, 31),
            value="7777",
        )
        await session.flush()

        evidence = await _gather(scene)
        assert "7777" not in str(_internals(evidence)), (
            "a fact filed after the as-of date reached a section's evidence pack"
        )


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
        second = research_request(
            user_id=scene["request"].work_order.user_id,
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
            work_order_id=second.id,
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
        glance = await glance_content(scene["session"], job=scene["job"], request=scene["request"])
        assert glance.refused is None
        assert glance.content is not None
        assert "9999" not in str(glance.content)

    async def test_an_unresolved_request_shows_nothing_rather_than_everything(
        self, scene: dict[str, Any]
    ) -> None:
        """Before `acquire` resolves the subject there is no subject, and the honest
        answer is an empty block rather than every issuer the request has touched."""
        scene["request"].company_id = None
        await scene["session"].flush()
        glance = await glance_content(scene["session"], job=scene["job"], request=scene["request"])
        assert glance.content is None or "9999" not in str(glance.content)


class TestTheFrontPageRefusesToMixIssuers:
    """Task P2: the guard behind the query, checked at the point of rendering.

    Under ADR 0061 the query cannot hand the glance another issuer's figures, so these
    scenes reach the guard by defeating the query — which is the exact future this guard
    exists for: the predicate is two lines a refactor can drop, and the front page is
    where that mistake reached a signed PDF.
    """

    async def test_a_mixed_set_is_withheld_with_the_reason_stated(
        self, scene: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def defeated(session: Any, *, request: Any) -> list[Any]:
            facts = await session.scalars(
                select(FinancialFact).order_by(FinancialFact.period_end.desc())
            )
            return list(facts)

        monkeypatch.setattr(glance_module, "_consolidated_facts", defeated)
        glance = await glance_content(scene["session"], job=scene["job"], request=scene["request"])
        assert glance.content is None, "a block that mixes issuers must not render at all"
        assert glance.refused is not None
        assert "withheld" in glance.refused
        assert "ADR 0061" in glance.refused

    async def test_a_set_that_agrees_on_the_wrong_company_is_refused_too(
        self, scene: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Internal agreement is not the test — the subject is. A pool that is uniformly
        the peer's would pass a facts-agree-with-each-other check and still be wrong."""

        async def only_the_peer(session: Any, *, request: Any) -> list[Any]:
            facts = await session.scalars(
                select(FinancialFact).where(FinancialFact.company_id == scene["peer"].id)
            )
            return list(facts)

        monkeypatch.setattr(glance_module, "_consolidated_facts", only_the_peer)
        glance = await glance_content(scene["session"], job=scene["job"], request=scene["request"])
        assert glance.content is None
        assert glance.refused is not None

    async def test_an_empty_set_is_silence_not_refusal(
        self, scene: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing to show stays silent (gap R10); the refusal is only for wrong figures."""

        async def nothing(session: Any, *, request: Any) -> list[Any]:
            return []

        monkeypatch.setattr(glance_module, "_consolidated_facts", nothing)
        glance = await glance_content(scene["session"], job=scene["job"], request=scene["request"])
        assert glance.refused is None


class TestTheFrontPageRefusesTheImpossible:
    """Gap A61, ADR 0066: told, rather than shown a lie.

    The MTB run's front page carried revenue of $442m beside net income of $818m and a
    net margin of 172.1% — each row a stored fact or a recorded calculation, the set
    impossible. The block re-checks the relations over exactly the rows it is about to
    render and withholds itself whole, because code cannot know which leg of an
    impossible relation is the mislabelled one.
    """

    async def test_income_above_revenue_withholds_the_block_with_the_reason(
        self, scene: dict[str, Any]
    ) -> None:
        await _fact(
            scene["session"],
            company=scene["subject"],
            document=scene["subject_doc"],
            concept="net_income",
            period_end=date(2025, 12, 31),
            value="2000",
        )
        await scene["session"].commit()

        glance = await glance_content(scene["session"], job=scene["job"], request=scene["request"])

        assert glance.content is None, "an impossible set must not render at all"
        assert glance.refused is not None
        assert "cannot all be true" in glance.refused
        assert "ADR 0066" in glance.refused
        assert "2000" in glance.refused, "the refusal argues with the values, not a category"

    async def test_income_within_revenue_renders_as_before(self, scene: dict[str, Any]) -> None:
        await _fact(
            scene["session"],
            company=scene["subject"],
            document=scene["subject_doc"],
            concept="net_income",
            period_end=date(2025, 12, 31),
            value="300",
        )
        await scene["session"].commit()

        glance = await glance_content(scene["session"], job=scene["job"], request=scene["request"])

        assert glance.refused is None
        assert glance.content is not None

    def test_a_margin_row_above_one_is_reason_enough(self) -> None:
        """The ratio form alone withholds — the run can hold the margin for years whose
        underlying facts the block no longer shows side by side."""
        content = {
            "ratios": [{"label": "Net margin", "period": "FY2025", "value": "1.7206"}],
        }

        refusal = glance_module._impossibility_refusal(content)

        assert refusal is not None
        assert "1.7206" in refusal

    def test_currencies_that_disagree_are_not_compared(self) -> None:
        """A dollar income against a sterling revenue is not an impossible statement,
        it is two statements — comparing them would be a new error, not a check."""
        content = {
            "latest": [
                {"label": "Revenue", "period": "FY2025", "value": "100", "unit": "GBP"},
                {"label": "Net income", "period": "FY2025", "value": "200", "unit": "USD"},
            ],
        }

        assert glance_module._impossibility_refusal(content) is None
