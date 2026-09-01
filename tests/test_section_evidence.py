"""Evidence reaches a section ranked by relevance — gap A39.

A live large-cap run held 18,588 facts and 69 excerpts and still starved every section:
forty facts chosen newest-period-then-alphabetically delivered "Accrued…, Accumulated…,
AvailableForSale…" and never Revenue, excerpts chosen oldest-first delivered the same
signature page to fourteen sections in a row, and the compact listings consumed the
budget so every excerpt was overflow. The report then truthfully described its own
starvation for twenty-three pages. These tests pin the selection rules that stop that:
concept-ranked facts, keyword-ranked excerpts, a substance filter, and a budget share
that keeps excerpts a seat.

The preferences arrive on the :class:`SectionPolicy` — they are rows on the section
definitions (migration 0029), not code keyed by section, so these tests drive the policy
directly and the seed pin in ``test_section_spine`` proves the rows carry them.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import ExtractionKind, FactBasis, JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    Extraction,
    FinancialFact,
    Job,
    SourceDocument,
    User,
)
from aer.sections.evidence import (
    SectionPolicy,
    _is_substantive,
    gather_evidence,
)
from tests.request_fixtures import research_request
from tests.workflow_fixtures import WORKFLOW_VERSION

pytestmark = pytest.mark.integration

_SIGNATURE_BLOCK = (
    "Pursuant to the requirements of the Securities Exchange Act of 1934, the registrant "
    "has duly caused this report to be signed on its behalf by the undersigned, "
    "thereunto duly authorized, in the City of Redmond."
)

_DEBT_NOTE = (
    "The components of long-term debt and the maturity schedule are set out below. The "
    "company maintains a commercial paper programme and a committed credit facility, "
    "and liquidity is managed against contractual lease obligations."
)

_SEGMENT_NOTE = (
    "LinkedIn connects the world's professionals and is monetised through Talent "
    "Solutions, Marketing Solutions, Premium Subscriptions and Sales Solutions, with "
    "segment revenue driven by member engagement."
)

# Preferences in the shape migration 0029 seeds them: the history section leads with the
# income statement, the cash-flow section with its own statement, and the balance-sheet
# section names the liquidity vocabulary.
_HISTORY_PRIORITY = (
    "revenue",
    "cost_of_revenue",
    "operating_expenses",
    "operating_income",
    "net_income",
    "operating_cash_flow",
)
_CASH_FLOW_PRIORITY = (
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capital_expenditure",
    "revenue",
    "net_income",
)
_LIQUIDITY_KEYWORDS = (
    "debt",
    "maturit",
    "liquidity",
    "credit facilit",
    "commercial paper",
    "lease",
    "unearned revenue",
    "cash and cash equivalents",
)


def _policy(
    token_budget: int = 4_000,
    *,
    concept_priority: tuple[str, ...] = (),
    excerpt_keywords: tuple[str, ...] = (),
    fact_basis: str = "any",
) -> SectionPolicy:
    return SectionPolicy(
        min_sources=1,
        requires_primary=True,
        max_tier_rank=SourceTier.T5_SECONDARY.rank,
        allow_forward_looking=False,
        token_budget=token_budget,
        concept_priority=concept_priority,
        excerpt_keywords=excerpt_keywords,
        fact_basis=fact_basis,
    )


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Path) -> dict[str, Any]:
    """A request holding many alphabetically-early facts, a few core ones, and three
    excerpts of very different worth — the live starvation, in miniature."""
    user = User(email="evidence@example.invalid", display_name="Evidence", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()
    request = research_request(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=date(2026, 8, 16),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="12.00",
        portfolio_context={},
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

    artefact = Artefact(
        sha256=hashlib.sha256(b"filing").hexdigest(),
        media_type="text/html",
        size_bytes=6,
        storage_key="aa/bb/filing",
    )
    db_session.add(artefact)
    await db_session.flush()
    document = SourceDocument(
        work_order_id=request.id,
        job_id=job.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        quarantined=False,
    )
    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
    db_session.add(company)
    await db_session.flush()
    # The subject, on the request and on its documents (ADR 0061). Without both, the pack is
    # scoped to a company nothing names and comes back empty — which is the point.
    request.company_id = company.id
    document.company_id = company.id
    db_session.add(document)
    await db_session.flush()

    # Sixty alphabetically-early footnote facts for the newest period — the debris that
    # crowded out the statements on the live run — plus the core lines behind them.
    for index in range(60):
        db_session.add(
            FinancialFact(
                company_id=company.id,
                source_document_id=document.id,
                concept=f"AccruedFootnoteItem{index:02d}",
                value=Decimal(1_000 + index),
                unit="USD",
                period_end=date(2026, 6, 30),
                basis=FactBasis.AS_REPORTED,
                filed_date=date(2026, 7, 29),
            )
        )
    for year, value in ((2026, "281000"), (2025, "245000"), (2024, "211000")):
        for concept in ("revenue", "operating_income", "net_income", "operating_cash_flow"):
            db_session.add(
                FinancialFact(
                    company_id=company.id,
                    source_document_id=document.id,
                    concept=concept,
                    value=Decimal(value),
                    unit="USD",
                    period_end=date(year, 6, 30),
                    basis=FactBasis.AS_REPORTED,
                    filed_date=date(year, 7, 29),
                )
            )

    # Three excerpts, created oldest-first in exactly the wrong order: the signature
    # block first (the live failure's constant companion), the segment note second, the
    # debt note last.
    for offset, text in enumerate((_SIGNATURE_BLOCK, _SEGMENT_NOTE, _DEBT_NOTE)):
        db_session.add(
            Extraction(
                source_document_id=document.id,
                kind=ExtractionKind.TEXT,
                extractor="test",
                extractor_version="1",
                locator={"kind": "test", "index": offset},
                locator_hash=hashlib.sha256(f"loc-{offset}".encode()).hexdigest(),
                excerpt=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                created_at=datetime(2026, 8, 16, 10, offset, tzinfo=UTC),
            )
        )
    await db_session.flush()

    return {"session": db_session, "request": request, "job": job}


def _concepts(evidence: Any) -> list[str]:
    return [item["concept"] for item in evidence.internal if "concept" in item]


def _excerpts(evidence: Any) -> list[str]:
    return [item["text"] for item in evidence.untrusted]


class TestFactsAreRankedByTheSectionsConcepts:
    async def test_the_statements_lines_beat_the_alphabet(self, scene: dict[str, Any]) -> None:
        """The live failure inverted: revenue must outrank sixty alphabetically-earlier
        footnote items, however new their period."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(concept_priority=_HISTORY_PRIORITY),
            categories=frozenset({"search_facts"}),
        )

        concepts = _concepts(evidence)
        assert concepts, "no facts were gathered at all"
        assert concepts[0] == "revenue"
        core = {"revenue", "operating_income", "net_income", "operating_cash_flow"}
        assert core <= set(concepts), "a core statement line was crowded out"

    async def test_a_core_concept_arrives_with_its_history(self, scene: dict[str, Any]) -> None:
        """Three periods of revenue, newest first — a history section needs the series,
        not one year of it."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(concept_priority=_HISTORY_PRIORITY),
            categories=frozenset({"search_facts"}),
        )

        revenue_periods = [
            item["period_end"] for item in evidence.internal if item.get("concept") == "revenue"
        ]
        assert revenue_periods == ["2026-06-30", "2025-06-30", "2024-06-30"]

    async def test_a_cash_flow_priority_leads_with_cash_flow(self, scene: dict[str, Any]) -> None:
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(concept_priority=_CASH_FLOW_PRIORITY),
            categories=frozenset({"search_facts"}),
        )

        concepts = _concepts(evidence)
        assert concepts[0] == "operating_cash_flow"

    async def test_an_empty_priority_still_prefers_the_statements(
        self, scene: dict[str, Any]
    ) -> None:
        """A policy that declares nothing — every custom section, and every definition
        seeded before migration 0029 — must still beat the alphabet."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(),
            categories=frozenset({"search_facts"}),
        )

        concepts = _concepts(evidence)
        assert concepts[0] == "revenue"


class TestExcerptsAreRankedAndFiltered:
    async def test_the_debt_note_outranks_the_segment_note_for_the_balance_sheet(
        self, scene: dict[str, Any]
    ) -> None:
        """Created last, most relevant: keyword affinity must beat creation order."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(excerpt_keywords=_LIQUIDITY_KEYWORDS),
            categories=frozenset({"search_sources"}),
        )

        texts = _excerpts(evidence)
        assert texts, "no excerpts survived at all"
        assert texts[0] == _DEBT_NOTE

    async def test_a_signature_block_never_enters_evidence(self, scene: dict[str, Any]) -> None:
        """The excerpt the live run delivered to fourteen sections is refused a seat."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(excerpt_keywords=_LIQUIDITY_KEYWORDS),
            categories=frozenset({"search_sources"}),
        )

        assert _SIGNATURE_BLOCK not in _excerpts(evidence)

    def test_the_substance_filter_names_the_furniture(self) -> None:
        assert not _is_substantive(_SIGNATURE_BLOCK)
        assert _is_substantive(_DEBT_NOTE)
        assert _is_substantive(_SEGMENT_NOTE)

    async def test_a_source_listing_says_what_each_source_is(self, scene: dict[str, Any]) -> None:
        """Gap R12. Shown only ids and tiers, a live writer described twenty footnotes
        of XBRL-aggregate figures as "a single primary filing, the Form 10-Q" — a right
        citation under a wrong sentence. The listing now carries each source's recorded
        title, so the writer describes what it actually has."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(),
            categories=frozenset({"search_sources"}),
        )

        listings = [
            item
            for item in evidence.internal
            if "source_document_id" in item and "extraction_id" not in item
        ]
        assert listings, "no source listing survived at all"
        assert all(item.get("title") for item in listings)


class TestTheBudgetKeepsExcerptsASeat:
    async def test_excerpts_survive_a_budget_the_facts_could_fill(
        self, scene: dict[str, Any]
    ) -> None:
        """The starvation mechanism itself: listings are capped to a share, so a budget
        the facts alone could exhaust still delivers at least one excerpt."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(token_budget=1_200, excerpt_keywords=_LIQUIDITY_KEYWORDS),
            categories=frozenset({"search_facts", "search_sources"}),
        )

        assert evidence.untrusted, "the listings consumed the whole budget again"
        assert _excerpts(evidence)[0] == _DEBT_NOTE

    async def test_a_dropped_excerpt_is_dropped_whole(self, scene: dict[str, Any]) -> None:
        """The closed world survives the ranking: an excerpt the budget dropped has no
        entry in the citation index either."""
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(token_budget=1_200, excerpt_keywords=_LIQUIDITY_KEYWORDS),
            categories=frozenset({"search_facts", "search_sources"}),
        )

        listed = {item["extraction_id"] for item in evidence.internal if "extraction_id" in item}
        assert listed == set(evidence.extraction_sources)


class TestTheFactBasisFilter:
    """The section's declared basis decides which facts it is even offered.

    Page 11 of the live report compared a quarterly revenue against an annual EBITDA in
    one sentence, because the gatherer handed every section its facts newest-first with
    no regard for basis. A section that declares "annual" now spends its whole fact
    budget on full-year rows.
    """

    @staticmethod
    async def _seed_mixed_bases(scene: dict[str, Any]) -> None:
        session: AsyncSession = scene["session"]
        document_id = (await session.scalars(select(SourceDocument.id).limit(1))).first()
        company_id = (await session.scalars(select(Company.id).limit(1))).first()
        for concept, fiscal_period, start, end in (
            ("annual_revenue", "FY", date(2024, 7, 1), date(2025, 6, 30)),
            ("quarterly_revenue", "Q4", date(2026, 4, 1), date(2026, 6, 30)),
        ):
            session.add(
                FinancialFact(
                    company_id=company_id,
                    source_document_id=document_id,
                    concept=concept,
                    value=Decimal(1),
                    unit="USD",
                    period_start=start,
                    period_end=end,
                    fiscal_year=end.year,
                    fiscal_period=fiscal_period,
                    basis=FactBasis.AS_REPORTED,
                    filed_date=end,
                )
            )
        await session.flush()

    async def test_an_annual_section_is_offered_no_interim_facts(
        self, scene: dict[str, Any]
    ) -> None:
        await self._seed_mixed_bases(scene)
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(fact_basis="annual"),
            categories=frozenset({"search_facts"}),
        )

        concepts = _concepts(evidence)
        assert "annual_revenue" in concepts
        assert "quarterly_revenue" not in concepts

    async def test_an_interim_section_is_offered_no_full_year_facts(
        self, scene: dict[str, Any]
    ) -> None:
        await self._seed_mixed_bases(scene)
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(fact_basis="interim"),
            categories=frozenset({"search_facts"}),
        )

        concepts = _concepts(evidence)
        assert "quarterly_revenue" in concepts
        assert "annual_revenue" not in concepts

    async def test_every_fact_item_names_its_span_and_fiscal_period(
        self, scene: dict[str, Any]
    ) -> None:
        """The label the writer physically lacked: a June quarter and a nine-month
        year-to-date share a period_end, and only the span tells them apart."""
        await self._seed_mixed_bases(scene)
        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            # Prioritised so the sixty debris facts cannot budget these two out — the
            # subject here is the item's fields, not the ranking.
            policy=_policy(concept_priority=("annual_revenue", "quarterly_revenue")),
            categories=frozenset({"search_facts"}),
        )

        annual = next((i for i in evidence.internal if i.get("concept") == "annual_revenue"), None)
        assert annual is not None
        assert annual["fiscal_period"] == "FY"
        assert annual["period_start"] == "2024-07-01"
        assert annual["period_end"] == "2025-06-30"
        assert annual["fiscal_year"] == 2025


class TestCalculationsReachASectionNewestFirst:
    """Gap R8. The ordering used to be ``sequence`` — the order the figures were struck —
    and the analysis loop strikes oldest period first, because each period's paired
    quality signals compare against the one before it. So the cap kept the oldest ratios
    and cut the newest.

    A live August 2026 note built its bear, base and bull cases on fiscal 2021 and 2022
    ratios that disagree violently with each other, and its own red team caught it. The
    section was not choosing stale figures: with five periods struck, roughly twenty-four
    rows a period and a cap of forty, fiscal 2021 and two-thirds of 2022 were the only
    ones it was ever shown.
    """

    @staticmethod
    async def _seed_five_years(scene: dict[str, Any]) -> None:
        """More calculations than the cap, spread over five periods oldest-first — the
        order the analysis service really persists them in."""
        session: AsyncSession = scene["session"]
        sequence = 0
        for year in (2021, 2022, 2023, 2024, 2025):
            for name in ("gross_margin", "operating_margin", "net_margin", "return_on_equity"):
                for variant in range(3):
                    session.add(
                        Calculation(
                            job_id=scene["job"].id,
                            sequence=sequence,
                            name=f"{name}_{variant}",
                            formula="a / b",
                            function_ref="tests",
                            code_version="testsha",
                            inputs=[],
                            parameters={},
                            assumptions=[],
                            output_value=Decimal(year),
                            output_unit="ratio",
                            period_label=f"FY{year}",
                            period_start=date(year, 1, 1),
                            period_end=date(year, 12, 31),
                        )
                    )
                    sequence += 1
        await session.flush()

    @staticmethod
    def _periods(evidence: Any) -> list[str]:
        return [item["period"] for item in evidence.internal if "calculation_id" in item]

    async def test_the_newest_period_survives_the_cap(self, scene: dict[str, Any]) -> None:
        await self._seed_five_years(scene)

        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(),
            categories=frozenset({"search_facts"}),
        )

        periods = self._periods(evidence)
        assert periods, "no calculations reached the section at all"
        assert "FY2025" in periods, f"the newest period was cut: {sorted(set(periods))}"

    async def test_the_oldest_period_is_what_the_cap_cuts(self, scene: dict[str, Any]) -> None:
        """The other half of the same statement, and the one that failed before: with more
        calculations struck than a section can hold, the ones it loses are the stale ones."""
        await self._seed_five_years(scene)

        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(),
            categories=frozenset({"search_facts"}),
        )

        periods = self._periods(evidence)
        assert len(periods) < 60, "the scene no longer overruns the cap, so this proves nothing"
        assert "FY2021" not in periods, f"the oldest period survived ahead of newer: {periods}"

    async def test_the_periods_arrive_newest_first(self, scene: dict[str, Any]) -> None:
        await self._seed_five_years(scene)

        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(),
            categories=frozenset({"search_facts"}),
        )

        periods = self._periods(evidence)
        assert periods == sorted(periods, reverse=True), periods

    async def test_a_grid_of_run_level_figures_cannot_crowd_out_the_periods(
        self, scene: dict[str, Any]
    ) -> None:
        """Why the period-less rows sort *last*.

        Putting them first is the tempting reading — a discount rate belongs to no
        statement period, and it seems a shame to cut it for want of one. But the
        valuation runs under this same job, and a sensitivity grid alone strikes over a
        hundred period-less rows. Sorting those first would fill the cap with grid cells
        and cut every period, which is a worse failure than the one this ordering fixes.
        """
        await self._seed_five_years(scene)
        for cell in range(60):
            scene["session"].add(
                Calculation(
                    job_id=scene["job"].id,
                    sequence=1000 + cell,
                    name=f"grid_cell_{cell}",
                    formula="value = ...",
                    function_ref="tests",
                    code_version="testsha",
                    inputs=[],
                    parameters={},
                    assumptions=[],
                    output_value=Decimal(cell),
                    output_unit="ratio",
                )
            )
        await scene["session"].flush()

        evidence = await gather_evidence(
            scene["session"],
            request=scene["request"],
            evidence_job_id=scene["job"].id,
            policy=_policy(),
            categories=frozenset({"search_facts"}),
        )

        periods = self._periods(evidence)
        assert "FY2025" in periods, "a grid of period-less rows displaced the newest period"
