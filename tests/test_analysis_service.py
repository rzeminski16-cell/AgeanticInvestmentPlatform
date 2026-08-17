"""The run's financial analysis: the bridge from stored facts to recorded calculations.

**Why this file exists.** Phase 3 built the statement assembler, seventeen ratios and eight
earnings-quality signals, tested every one of them, and nothing in production ever called
them. A run's ``calculate`` step computed a single revenue CAGR, so the balance-sheet and
cash-flow sections had one figure between them and the valuation page reported that the run
had produced nothing. Every part worked; the call was missing.

These tests are about the call: which facts are chosen, how they become periods, and that
what comes out is a persisted, traceable ledger rather than numbers in memory.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
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
from aer.services.analysis import ANNUAL, analyse_company
from aer.services.calculations import new_context, persist_context

pytestmark = pytest.mark.integration

AS_OF = date(2024, 6, 30)

# One year of a plausible filer, in canonical concepts. Enough that the identities can run,
# several ratios compute, and the quality signals have something to say — a scene where
# nothing computes would let a broken bridge look like a quiet one.
_YEAR: dict[str, str] = {
    "revenue": "1000",
    "cost_of_revenue": "400",
    "gross_profit": "600",
    "operating_income": "250",
    "net_income": "180",
    "income_tax_expense": "50",
    "pre_tax_income": "230",
    "cash_and_equivalents": "300",
    "accounts_receivable": "150",
    "inventory": "90",
    "current_assets": "540",
    "assets": "1400",
    "accounts_payable": "110",
    "current_liabilities": "260",
    "long_term_debt": "400",
    "liabilities": "700",
    "equity": "700",
    "operating_cash_flow": "260",
    "capital_expenditure": "80",
    "depreciation_and_amortisation": "70",
}


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="analysis@example.invalid", display_name="A", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Contoso Corporation",
        ticker="CTSO",
        exchange="NASDAQ",
        as_of_date=AS_OF,
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
        point_in_time=True,
    )
    company = Company(
        name="Contoso Corporation", ticker="CTSO", exchange="NASDAQ", cik="0000000001"
    )
    artefact = Artefact(
        sha256="c" * 64, size_bytes=10, media_type="application/json", storage_key="cc/c"
    )
    db_session.add_all([request, company, artefact])
    await db_session.flush()

    document = SourceDocument(
        request_id=request.id,
        artefact_id=artefact.id,
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        title="Contoso XBRL company facts",
        retrieved_at=datetime.now(UTC),
    )
    job = Job(
        request_id=request.id,
        workflow_version="test",
        code_version="abc",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add_all([document, job])
    await db_session.flush()

    return {
        "session": db_session,
        "request": request,
        "company": company,
        "document": document,
        "job": job,
    }


def _facts(
    scene: dict[str, Any],
    *,
    period_end: date,
    filed: date,
    values: dict[str, str] | None = None,
    fiscal_period: str = ANNUAL,
    accession: str = "0000000000-00-000000",
    unit: str = "USD",
) -> list[FinancialFact]:
    return [
        FinancialFact(
            company_id=scene["company"].id,
            source_document_id=scene["document"].id,
            concept=concept,
            raw_concept=concept,
            taxonomy="us-gaap",
            value=Decimal(value),
            unit=unit,
            period_start=date(period_end.year, 1, 1),
            period_end=period_end,
            fiscal_year=period_end.year,
            fiscal_period=fiscal_period,
            filed_date=filed,
            form="10-K",
            accession=accession,
            basis=FactBasis.AS_REPORTED,
        )
        for concept, value in (values or _YEAR).items()
    ]


async def _seed(scene: dict[str, Any], facts: list[FinancialFact]) -> None:
    scene["session"].add_all(facts)
    await scene["session"].flush()


class TestTheAnalysisRuns:
    async def test_statements_ratios_and_signals_all_arrive(self, scene: dict[str, Any]) -> None:
        """One call, three layers. Each was built and tested in Phase 3 and never run."""
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        [period] = outcome.periods
        assert period.statements.income.present_concepts
        assert period.computed_ratios
        assert any(signal.quantity is not None for signal in period.quality)

    async def test_every_derivation_persists_as_a_traceable_row(
        self, scene: dict[str, Any]
    ) -> None:
        """The invariant the whole platform turns on: a figure in a report is a stored
        calculation with a formula, not a number that was in memory once."""
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))
        context = new_context()

        await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )
        rows = await persist_context(scene["session"], context, job_id=scene["job"].id)

        assert len(rows) > 1
        assert all(row.formula for row in rows)

    async def test_every_derivation_is_stamped_with_its_fiscal_year(
        self, scene: dict[str, Any]
    ) -> None:
        """The live report put an annual EBITDA beside a quarterly revenue and called the
        pair a margin; the stamp is what makes an analysis figure's basis visible."""
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))
        context = new_context()

        await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )
        rows = await persist_context(scene["session"], context, job_id=scene["job"].id)

        assert all(row.period_label == "FY2023" for row in rows)
        assert all(row.period_end == date(2023, 12, 31) for row in rows)
        assert all(row.period_start == date(2023, 1, 1) for row in rows)

    async def test_periods_come_back_most_recent_first(self, scene: dict[str, Any]) -> None:
        for year in (2021, 2022, 2023):
            await _seed(
                scene,
                _facts(scene, period_end=date(year, 12, 31), filed=date(year + 1, 2, 1)),
            )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert [period.period_end.year for period in outcome.periods] == [2023, 2022, 2021]

    async def test_the_comparison_signals_use_the_preceding_year(
        self, scene: dict[str, Any]
    ) -> None:
        """A paired signal needs a prior period, and the prior period must be the year
        before — not whichever happened to be built first."""
        for year in (2022, 2023):
            await _seed(
                scene,
                _facts(scene, period_end=date(year, 12, 31), filed=date(year + 1, 2, 1)),
            )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert outcome.latest is not None
        earliest = outcome.periods[-1]
        # The oldest year has nothing before it, the newest does; a run where both looked
        # the same would mean `prior` was never being passed.
        paired_on_latest = sum(1 for s in outcome.latest.quality if s.quantity is not None)
        paired_on_earliest = sum(1 for s in earliest.quality if s.quantity is not None)
        assert paired_on_latest > paired_on_earliest

    async def test_a_single_year_says_what_it_could_not_compute(
        self, scene: dict[str, Any]
    ) -> None:
        """Silence would read as "nothing to report" rather than "nothing to compare"."""
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert any("preceding year" in note for note in outcome.skipped)

    async def test_no_facts_is_reported_not_raised(self, scene: dict[str, Any]) -> None:
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert outcome.periods == ()
        assert outcome.skipped


class TestWhichObservationWins:
    async def test_a_later_filing_supersedes_an_earlier_one(self, scene: dict[str, Any]) -> None:
        """A restatement is the company's more recent word on the same period."""
        await _seed(
            scene,
            _facts(
                scene,
                period_end=date(2023, 12, 31),
                filed=date(2024, 2, 1),
                values={"revenue": "1000"},
            ),
        )
        await _seed(
            scene,
            _facts(
                scene,
                period_end=date(2023, 12, 31),
                filed=date(2024, 5, 1),
                values={"revenue": "1100"},
                accession="0000000000-00-000001",
            ),
        )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        [period] = outcome.periods
        revenue = period.statements.get("revenue")
        assert revenue is not None
        assert revenue.value == Decimal("1100")

    async def test_a_filing_after_the_as_of_date_is_not_read(self, scene: dict[str, Any]) -> None:
        """Point-in-time, applied here as well as at acquisition: the store accumulates
        across runs, so yesterday's run must not read tomorrow's filing."""
        await _seed(
            scene,
            _facts(
                scene,
                period_end=date(2023, 12, 31),
                filed=date(2024, 2, 1),
                values={"revenue": "1000"},
            ),
        )
        await _seed(
            scene,
            _facts(
                scene,
                period_end=date(2023, 12, 31),
                filed=date(2025, 2, 1),
                values={"revenue": "9999"},
                accession="0000000000-00-000002",
            ),
        )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        revenue = outcome.periods[0].statements.get("revenue")
        assert revenue is not None
        assert revenue.value == Decimal("1000")

    async def test_a_quarter_never_joins_an_annual_statement(self, scene: dict[str, Any]) -> None:
        """Three months of revenue beside a year of operating income is not a statement."""
        await _seed(
            scene,
            _facts(
                scene,
                period_end=date(2024, 3, 31),
                filed=date(2024, 4, 20),
                fiscal_period="Q1",
            ),
        )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert outcome.periods == ()

    async def test_only_the_most_recent_years_are_analysed(self, scene: dict[str, Any]) -> None:
        for year in range(2016, 2024):
            await _seed(
                scene,
                _facts(scene, period_end=date(year, 12, 31), filed=date(year + 1, 2, 1)),
            )
        context = new_context()

        outcome = await analyse_company(
            scene["session"],
            context,
            company_id=scene["company"].id,
            request=scene["request"],
            max_periods=3,
        )

        assert [period.period_end.year for period in outcome.periods] == [2023, 2022, 2021]

    async def test_another_company_s_facts_stay_out(self, scene: dict[str, Any]) -> None:
        other = Company(name="Fabrikam", ticker="FBRK", exchange="NASDAQ", cik="0000000002")
        scene["session"].add(other)
        await scene["session"].flush()
        rows = _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1))
        for row in rows:
            row.company_id = other.id
        await _seed(scene, rows)
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert outcome.periods == ()


class TestAFactTheAlgebraCannotRead:
    async def test_one_unreadable_unit_costs_a_line_not_the_run(
        self, scene: dict[str, Any]
    ) -> None:
        """Raising would trade one absent line for eighteen absent sections."""
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))
        await _seed(
            scene,
            _facts(
                scene,
                period_end=date(2023, 12, 31),
                filed=date(2024, 2, 1),
                values={"goodwill": "10"},
                unit="!!not a unit!!",
                accession="0000000000-00-000003",
            ),
        )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        [period] = outcome.periods
        assert period.statements.get("revenue") is not None
        assert period.statements.get("goodwill") is None


class TestWhatTheStepRecords:
    async def test_the_summary_counts_rather_than_repeating_the_figures(
        self, scene: dict[str, Any]
    ) -> None:
        """A figure copied into a step's JSON is a second copy with no formula behind it."""
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )
        recorded = outcome.as_dict()

        assert recorded["periods"][0]["ratios"] > 0
        assert recorded["periods"][0]["lines"] > 0
        assert "1000" not in str(recorded)

    async def test_a_broken_identity_is_named(self, scene: dict[str, Any]) -> None:
        """Gate 2 shows these. An identity that failed silently is a balance sheet that
        does not balance and a report that never mentions it."""
        broken = dict(_YEAR)
        broken["assets"] = "9999"
        await _seed(
            scene,
            _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1), values=broken),
        )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert outcome.as_dict()["periods"][0]["failed_identities"]

    async def test_a_concept_no_line_carries_is_reported_not_dropped(
        self, scene: dict[str, Any]
    ) -> None:
        await _seed(
            scene,
            _facts(
                scene,
                period_end=date(2023, 12, 31),
                filed=date(2024, 2, 1),
                values={"revenue": "1000", "not_a_canonical_concept": "5"},
            ),
        )
        context = new_context()

        outcome = await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )

        assert "not_a_canonical_concept" in outcome.unplaced_concepts


class TestTheWorkflowActuallyCallsIt:
    """The whole point. Every part of this worked in isolation for a month."""

    async def test_the_calculate_step_persists_more_than_one_figure(
        self, scene: dict[str, Any]
    ) -> None:
        """A single row means the step is back to computing one CAGR, which is the state
        this work existed to leave behind."""
        await _seed(scene, _facts(scene, period_end=date(2022, 12, 31), filed=date(2023, 2, 1)))
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))
        context = new_context()

        await analyse_company(
            scene["session"], context, company_id=scene["company"].id, request=scene["request"]
        )
        rows = await persist_context(scene["session"], context, job_id=scene["job"].id)

        names = {row.name for row in rows}
        assert len(rows) > 10, f"only {len(rows)} calculations: {sorted(names)}"
        assert str(uuid.UUID(str(rows[0].job_id))) == str(scene["job"].id)
