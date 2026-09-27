"""A figure computed from filings stands on those filings — ROADMAP §3.19 item 81.

Three of the re-measurement's four approved reports opened their Executive Summary with
*"Insufficient evidence: This section's policy requires at least one primary source (tier 1
or 2); none of its cited evidence is primary."* The summary cites calculations, and a claim
naming a calculation counted for nothing, so a growth rate over two lines of a 10-K was
evidence of nothing at all.

The operator decided the rule on 27 September 2026: a cited calculation counts as primary
evidence only when every input under it is a figure from a primary document. A figure with
an assumption, a price, a statistic or an attestation anywhere under it still counts for
nothing, and an undated document is still never primary (ADR 0111). What is held here:
the rule itself, over lineage trees; the reader that applies it to the ledger; the drafting
step's banner; and the evaluation step's per-section coverage, which the review page and
the low-coverage trigger read and which must say what the banner says.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.custom_section import CustomSectionDraft, ProposedClaim
from aer.calc.basic import growth_rate, ratio
from aer.calc.engine import CalculationContext
from aer.calc.units import DIMENSIONLESS, Quantity, SourceRef, money
from aer.core.enums import ClaimKind, FactBasis, JobStatus, Provider, SourceTier, UserRole
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
    SourceDocument,
    User,
)
from aer.eval.runtime import SectionCoverage
from aer.sections.evidence import (
    Evidence,
    SectionPolicy,
    policy_shortfalls,
    record_draft_claims,
)
from aer.services import calculations as calculation_service
from aer.services.calculations import LineageNode, filed_bases, filed_documents
from aer.services.citations import record_claim
from aer.services.evaluations import section_coverage_for_job
from tests.request_fixtures import research_request

pytestmark = pytest.mark.integration

_FILING = "10-k-document"
_OTHER_FILING = "10-q-document"
_FILED_DATE = date(2025, 7, 30)


# -- The rule, over lineage trees -------------------------------------------------------------


def _fact(document: str = _FILING, *, table: str = "financial_facts") -> LineageNode:
    return LineageNode(
        kind="fact",
        identifier=str(uuid.uuid4()),
        detail={"table": table, "source_document_id": document},
    )


def _calculation(*inputs: LineageNode) -> LineageNode:
    return LineageNode(kind="calculation", identifier=str(uuid.uuid4()), inputs=list(inputs))


class TestTheRule:
    def test_filed_figures_all_the_way_down_name_their_documents(self) -> None:
        tree = _calculation(_calculation(_fact(), _fact(_OTHER_FILING)), _fact())

        assert filed_documents(tree) == frozenset({_FILING, _OTHER_FILING})

    @pytest.mark.parametrize(
        ("leaf", "why"),
        [
            (LineageNode(kind="assumption", identifier="a"), "a number somebody chose"),
            (LineageNode(kind="attestation", identifier="b"), "a number somebody asserted"),
            (_fact(table="securities"), "a price, published but not by the filer"),
            (_fact(table="macro_observations"), "a statistic"),
            (_fact(table="fx_rates"), "an exchange rate"),
            (LineageNode(kind="missing", identifier="c"), "a reference nothing resolves"),
            (LineageNode(kind="truncated", identifier="d"), "a walk cut short"),
            (_fact(""), "a filed figure that names no document"),
        ],
    )
    def test_anything_else_under_it_answers_nothing(self, leaf: LineageNode, why: str) -> None:
        tree = _calculation(_fact(), _calculation(_fact(), leaf))

        assert filed_documents(tree) is None, why

    def test_a_reference_to_a_calculation_expanded_elsewhere_is_not_a_leaf(self) -> None:
        """The walk shows a calculation read twice once, and a stub where it recurs."""
        shared = _calculation(_fact())
        stub = LineageNode(kind="calculation_ref", identifier=shared.identifier)
        tree = _calculation(shared, _calculation(stub, _fact(_OTHER_FILING)))

        assert filed_documents(tree) == frozenset({_FILING, _OTHER_FILING})

    def test_a_calculation_with_nothing_under_it_rests_on_nothing(self) -> None:
        assert filed_documents(_calculation()) is None


# -- The ledger ---------------------------------------------------------------------------------


async def _document(
    session: AsyncSession, *, request: ResearchRequest, job: Job, name: str, dated: bool
) -> SourceDocument:
    artefact = Artefact(
        sha256=hashlib.sha256(name.encode()).hexdigest(),
        media_type="text/html",
        size_bytes=len(name),
        storage_key=f"filed/{name}",
    )
    session.add(artefact)
    await session.flush()
    document = SourceDocument(
        work_order_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        company_id=request.company_id,
        url=f"https://www.sec.gov/Archives/edgar/data/789019/{name}.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        publication_date=_FILED_DATE if dated else None,
        publication_date_latest=_FILED_DATE if dated else None,
        quarantined=False,
    )
    session.add(document)
    await session.flush()
    return document


async def _revenue(
    session: AsyncSession, *, company: Company, document: SourceDocument, year: int, value: str
) -> FinancialFact:
    fact = FinancialFact(
        company_id=company.id,
        source_document_id=document.id,
        concept="revenue",
        value=Decimal(value),
        unit="USD",
        period_end=date(year, 6, 30),
        basis=FactBasis.AS_REPORTED,
        filed_date=_FILED_DATE,
    )
    session.add(fact)
    await session.flush()
    return fact


async def _growth(
    session: AsyncSession, *, job: Job, start: FinancialFact, end: FinancialFact
) -> Calculation:
    """A growth rate over two filed lines, through the real kernel and the real ledger."""
    context = CalculationContext(code_version="test")
    growth_rate(
        context,
        start=money(start.value, "USD", source=SourceRef.financial_fact(start.id, label="revenue")),
        end=money(end.value, "USD", source=SourceRef.financial_fact(end.id, label="revenue")),
    )
    (row,) = await calculation_service.persist_context(session, context, job_id=job.id)
    return row


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="filed@example.invalid", display_name="Filed", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()
    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()
    request = research_request(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=date(2025, 9, 1),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()
    request.company_id = company.id
    job = Job(
        work_order_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    filing = await _document(db_session, request=request, job=job, name="msft-10k", dated=True)
    undated = await _document(db_session, request=request, job=job, name="msft-page", dated=False)

    filed = await _growth(
        db_session,
        job=job,
        start=await _revenue(db_session, company=company, document=filing, year=2024, value="245"),
        end=await _revenue(db_session, company=company, document=filing, year=2025, value="281"),
    )
    on_undated = await _growth(
        db_session,
        job=job,
        start=await _revenue(db_session, company=company, document=undated, year=2022, value="198"),
        end=await _revenue(db_session, company=company, document=undated, year=2023, value="211"),
    )

    # The filed growth rate against an assumption somebody chose: a figure with a judgement
    # under it, as every figure a valuation produces has.
    assumption = Assumption(
        request_id=request.id,
        name="terminal_growth",
        value=Decimal("0.025"),
        unit="pure",
        justification="Long-run nominal growth.",
        confidence=0.6,
        proposed_by="analysis",
    )
    db_session.add(assumption)
    await db_session.flush()
    context = CalculationContext(code_version="test")
    ratio(
        context,
        numerator=Quantity.of(
            filed.output_value,
            DIMENSIONLESS,
            source=SourceRef.calculation(filed.id, label="growth_rate"),
        ),
        denominator=Quantity.of(
            assumption.value,
            DIMENSIONLESS,
            source=SourceRef.assumption(assumption.id, label="terminal_growth"),
        ),
    )
    (judged,) = await calculation_service.persist_context(db_session, context, job_id=job.id)

    definition = await db_session.scalar(
        select(SectionDefinition)
        .where(SectionDefinition.key == "executive_summary")
        .order_by(SectionDefinition.version.desc())
        .limit(1)
    )
    assert definition is not None, "the migrations seed the Executive Summary"
    return {
        "session": db_session,
        "request": request,
        "job": job,
        "filing": filing,
        "undated": undated,
        "filed": filed,
        "on_undated": on_undated,
        "judged": judged,
        "definition": definition,
    }


class TestTheReader:
    async def test_a_growth_rate_over_a_dated_filing_is_primary(
        self, scene: dict[str, Any]
    ) -> None:
        bases = await filed_bases(scene["session"], [scene["filed"].id])

        basis = bases[scene["filed"].id]
        assert basis.documents == {str(scene["filing"].id): SourceTier.T1_REGULATORY}
        assert basis.is_primary

    async def test_a_figure_with_an_assumption_under_it_has_no_filed_basis(
        self, scene: dict[str, Any]
    ) -> None:
        assert await filed_bases(scene["session"], [scene["judged"].id]) == {}

    async def test_an_undated_filing_is_filed_and_never_primary(
        self, scene: dict[str, Any]
    ) -> None:
        """ADR 0111's cap reaches a figure through its lineage as it reaches a citation."""
        basis = (await filed_bases(scene["session"], [scene["on_undated"].id]))[
            scene["on_undated"].id
        ]

        assert basis.documents == {str(scene["undated"].id): SourceTier.T5_SECONDARY}
        assert not basis.is_primary

    async def test_a_calculation_not_in_the_ledger_is_simply_absent(
        self, scene: dict[str, Any]
    ) -> None:
        assert await filed_bases(scene["session"], [uuid.uuid4()]) == {}


# -- The banner, and the coverage that must agree with it ---------------------------------------


def _policy() -> SectionPolicy:
    return SectionPolicy(
        min_sources=1,
        requires_primary=True,
        max_tier_rank=SourceTier.T5_SECONDARY.rank,
        allow_forward_looking=False,
        token_budget=4_000,
    )


async def _section(scene: dict[str, Any]) -> ReportSection:
    """The Executive Summary, generated, as the drafting step leaves it."""
    definition: SectionDefinition = scene["definition"]
    section = ReportSection(
        job_id=scene["job"].id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.GENERATED,
        content={"summary": "Revenue grew."},
    )
    scene["session"].add(section)
    await scene["session"].flush()
    return section


async def _drafted(scene: dict[str, Any], *, names: Calculation) -> list[str]:
    """What the drafting step's banner says of a section whose one claim names ``names``."""
    section = await _section(scene)
    draft = CustomSectionDraft(
        content={"summary": "Revenue grew."},
        claims=[
            ProposedClaim(
                statement="Revenue grew 14.7% over the year.",
                kind="numeric",
                calculation_id=str(names.id),
            )
        ],
    )
    # The pack as a section is dealt it: the figure is offered, and the tier index holds
    # nothing the claim reaches, so only the lineage can put a primary document behind it.
    evidence = Evidence(calculation_ids={str(names.id)})
    _, cited = await record_draft_claims(
        scene["session"], section=section, draft=draft, evidence=evidence
    )
    return policy_shortfalls(cited, evidence=evidence, policy=_policy())


class TestTheBanner:
    async def test_a_summary_citing_a_figure_computed_from_a_filing_has_its_primary_source(
        self, scene: dict[str, Any]
    ) -> None:
        assert await _drafted(scene, names=scene["filed"]) == []

    async def test_a_summary_citing_a_judged_figure_still_has_none(
        self, scene: dict[str, Any]
    ) -> None:
        shortfalls = await _drafted(scene, names=scene["judged"])

        assert any("none of its cited evidence is primary" in item for item in shortfalls)

    async def test_a_summary_citing_a_figure_over_an_undated_page_still_has_none(
        self, scene: dict[str, Any]
    ) -> None:
        shortfalls = await _drafted(scene, names=scene["on_undated"])

        assert any("none of its cited evidence is primary" in item for item in shortfalls)


class TestTheCoverageAgreesWithTheBanner:
    """The review page's coverage table and the low-coverage trigger read these rows."""

    async def _coverage(self, scene: dict[str, Any], *, names: Calculation) -> SectionCoverage:
        section = await _section(scene)
        await record_claim(
            scene["session"],
            section=section,
            kind=ClaimKind.NUMERIC,
            text="Revenue grew 14.7% over the year.",
            calculation_id=names.id,
        )
        rows = await section_coverage_for_job(
            scene["session"], job=scene["job"], request=scene["request"]
        )
        (row,) = [row for row in rows if row.name == section.section_key]
        return row

    async def test_a_filed_figure_puts_its_filing_behind_the_section(
        self, scene: dict[str, Any]
    ) -> None:
        row = await self._coverage(scene, names=scene["filed"])

        assert row.has_primary
        assert row.distinct_sources == 1

    async def test_a_judged_figure_puts_nothing_behind_it(self, scene: dict[str, Any]) -> None:
        row = await self._coverage(scene, names=scene["judged"])

        assert not row.has_primary
        assert row.distinct_sources == 0
