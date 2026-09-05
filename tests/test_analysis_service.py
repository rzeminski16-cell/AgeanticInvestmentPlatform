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

from aer.core.concepts import canonical_concept
from aer.core.enums import FactBasis, JobStatus, Provider, SourceTier, UserRole
from aer.core.sectors import profile_for
from aer.db.models import (
    Artefact,
    Company,
    FinancialFact,
    Job,
    SourceDocument,
    User,
)
from aer.services.analysis import ANNUAL, FORECAST_CONCEPTS, analyse_company
from aer.services.calculations import new_context, persist_context
from tests.request_fixtures import research_request

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

    request = research_request(
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
        work_order_id=request.id,
        artefact_id=artefact.id,
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        title="Contoso XBRL company facts",
        retrieved_at=datetime.now(UTC),
    )
    job = Job(
        work_order_id=request.id,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert any("preceding year" in note for note in outcome.skipped)

    async def test_no_facts_is_reported_not_raised(self, scene: dict[str, Any]) -> None:
        context = new_context()

        outcome = await analyse_company(
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert outcome.periods == ()
        assert outcome.skipped


class TestTheLedgerRecordsEachDerivationOnce:
    """Gap R14, at the level the symptom appeared. The CHRW approval page listed 118
    calculations for five periods, with exact-value duplicates two different years cannot
    produce. The cause is legitimate re-striking — the ratio suite computes EBITDA for its
    margin and again inside net debt to EBITDA, the cash conversion cycle re-strikes all
    three days-outstanding ratios, a paired quality signal recomputes its own base — and
    the ledger now reuses the row rather than appending a second (`CalculationContext.add`).
    """

    async def test_no_figure_is_recorded_twice_for_the_same_period(
        self, scene: dict[str, Any]
    ) -> None:
        for year in (2021, 2022, 2023):
            await _seed(
                scene,
                _facts(scene, period_end=date(year, 12, 31), filed=date(year + 1, 2, 1)),
            )
        context = new_context()

        await analyse_company(
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )
        rows = await persist_context(scene["session"], context, job_id=scene["job"].id)

        struck: dict[tuple[str, str, str], int] = {}
        for row in rows:
            key = (row.name, str(row.output_value), row.period_label or "")
            struck[key] = struck.get(key, 0) + 1
        repeated = {key: count for key, count in struck.items() if count > 1}

        assert not repeated, f"the same figure was recorded more than once: {repeated}"

    async def test_a_re_struck_figure_still_has_exactly_one_row(
        self, scene: dict[str, Any]
    ) -> None:
        """Named directly rather than left to the sweep above: EBITDA is the one the live
        note showed twice, and a change that stopped re-striking it for some other reason
        should not quietly retire this guard.

        The scene seeds both debt legs so ``total_debt`` derives and net debt to EBITDA
        actually runs. Without them the ratio is skipped, EBITDA is struck once for its
        margin alone, and this test passes whatever the ledger does.
        """
        levered = {**_YEAR, "short_term_debt": "100"}
        for year in (2021, 2022, 2023):
            await _seed(
                scene,
                _facts(
                    scene,
                    period_end=date(year, 12, 31),
                    filed=date(year + 1, 2, 1),
                    values=levered,
                ),
            )
        context = new_context()

        await analyse_company(
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert context.named("net_debt_to_ebitda"), (
            "the scene no longer computes net debt to EBITDA, so nothing re-strikes EBITDA "
            "and this test proves nothing"
        )
        ebitda = context.named("ebitda")
        assert ebitda, "the scene no longer computes EBITDA, so this proves nothing"
        periods = [record.period.label if record.period else "" for record in ebitda]
        assert len(periods) == len(set(periods)), f"EBITDA struck twice in one period: {periods}"


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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
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
            scene["session"],
            context,
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )
        rows = await persist_context(scene["session"], context, job_id=scene["job"].id)

        names = {row.name for row in rows}
        assert len(rows) > 10, f"only {len(rows)} calculations: {sorted(names)}"
        assert str(uuid.UUID(str(rows[0].job_id))) == str(scene["job"].id)


# ==========================================================================================
# What counts as a year, and what a run can see of its own coverage
# ==========================================================================================


def _instant(
    scene: dict[str, Any],
    *,
    on: date,
    filed: date,
    concept: str = "shares_outstanding",
    value: str = "1000000",
) -> FinancialFact:
    """A point-in-time fact: a balance-sheet line, or a cover-page share count.

    `dei:EntityCommonStockSharesOutstanding` is the one that mattered — dated the day the
    annual report was signed, filed under `fp: FY`, and describing no period at all.
    """
    return FinancialFact(
        company_id=scene["company"].id,
        source_document_id=scene["document"].id,
        concept=concept,
        raw_concept="EntityCommonStockSharesOutstanding",
        taxonomy="dei",
        value=Decimal(value),
        unit="shares",
        period_start=None,
        period_end=on,
        fiscal_year=on.year,
        fiscal_period=ANNUAL,
        filed_date=filed,
        form="10-K",
        accession="0000000000-00-000001",
        basis=FactBasis.AS_REPORTED,
    )


def _quarter(
    scene: dict[str, Any], *, period_end: date, filed: date, values: dict[str, str]
) -> list[FinancialFact]:
    """A three-month duration ending on the year end, filed under `FY` by the 10-K."""
    facts = _facts(scene, period_end=period_end, filed=filed, values=values)
    for fact in facts:
        fact.period_start = date(period_end.year, 10, 1)
    return facts


class TestOnlyAFullYearMakesAFiscalYear:
    """Gap A45, reproduced from the live AMZN run.

    `fiscal_period` is EDGAR's `fp`, which describes the filing rather than the fact. A 10-K
    files its cover-page share count and its fourth-quarter stubs under `FY` alongside the
    twelve-month figures, and the old selection took the label at its word.
    """

    async def test_a_cover_date_does_not_become_a_period(self, scene: dict[str, Any]) -> None:
        """The live failure: one fact, dated after the year end, minting a period.

        Every annual report added one, each newer than the year it reported on, so the
        newest-five window filled with them and the fiscal years fell out of it.
        """
        await _seed(
            scene,
            [
                *_facts(scene, period_end=date(2022, 12, 31), filed=date(2023, 2, 1)),
                *_facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)),
                _instant(scene, on=date(2024, 1, 24), filed=date(2024, 2, 1)),
            ],
        )

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert [period.period_end for period in outcome.periods] == [
            date(2023, 12, 31),
            date(2022, 12, 31),
        ]

    async def test_a_balance_sheet_still_joins_the_year_it_closes(
        self, scene: dict[str, Any]
    ) -> None:
        """The other half: an instant dated the year end belongs to that year.

        Dropping instants wholesale would have taken the balance sheet with the furniture.
        """
        await _seed(
            scene,
            [
                *_facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)),
                _instant(scene, on=date(2023, 12, 31), filed=date(2024, 2, 1)),
            ],
        )

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        [period] = outcome.periods
        assert "shares_outstanding" in period.statements.supplementary.present_concepts

    async def test_a_fourth_quarter_stub_cannot_win_the_year(self, scene: dict[str, Any]) -> None:
        """The dangerous one: a quarter's revenue standing in for a year's.

        Both durations end on the year end and both arrive from the same filing, so they
        tie on `(filed_date, accession)` and the winner was whichever row was read first.
        A missing figure is recoverable; a wrong one that looks right is not.
        """
        await _seed(
            scene,
            [
                *_facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)),
                # Filed *later* than the annual figure and still inside the as-of window,
                # which is what made it dangerous: the tie-break prefers the most recent
                # filing, so the stub won. It has to come from a different filing at all,
                # because the unique index over an observation excludes `period_start`.
                *_quarter(
                    scene,
                    period_end=date(2023, 12, 31),
                    filed=date(2024, 3, 1),
                    values={"revenue": "250"},
                ),
            ],
        )

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        [period] = outcome.periods
        revenue = period.statements.income.get("revenue")
        assert revenue is not None
        assert revenue.value == Decimal("1000"), "the twelve-month figure, not the quarter"

    async def test_a_transition_period_is_not_a_year(self, scene: dict[str, Any]) -> None:
        """A company that moved its year end files a short stub. It is not a year."""
        short = _facts(scene, period_end=date(2023, 6, 30), filed=date(2023, 8, 1))
        for fact in short:
            fact.period_start = date(2023, 1, 1)
        await _seed(
            scene, [*short, *_facts(scene, period_end=date(2022, 12, 31), filed=date(2023, 2, 1))]
        )

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert [period.period_end for period in outcome.periods] == [date(2022, 12, 31)]

    async def test_a_fifty_two_week_year_still_counts(self, scene: dict[str, Any]) -> None:
        """364 days is a year on a retailer's calendar, and the band has to admit it."""
        retail = _facts(scene, period_end=date(2024, 1, 27), filed=date(2024, 3, 1))
        for fact in retail:
            fact.period_start = date(2023, 1, 29)
        await _seed(scene, retail)

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert [period.period_end for period in outcome.periods] == [date(2024, 1, 27)]


class TestTheRunMeasuresItsOwnCoverage:
    """Gap A46: coverage was invisible until the assumptions gate asked for nine values."""

    async def test_every_forecast_concept_is_counted(self, scene: dict[str, Any]) -> None:
        await _seed(
            scene,
            [
                *_facts(scene, period_end=date(2022, 12, 31), filed=date(2023, 2, 1)),
                *_facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)),
            ],
        )

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        coverage = outcome.forecast_coverage
        assert set(coverage) == set(FORECAST_CONCEPTS)
        assert coverage["revenue"] == 2
        assert coverage["depreciation_and_amortisation"] == 2

    async def test_a_concept_the_filer_never_reports_counts_zero(
        self, scene: dict[str, Any]
    ) -> None:
        """The live symptom, as a number the run records rather than a surprise at the gate."""
        thin = {name: value for name, value in _YEAR.items() if name != "capital_expenditure"}
        await _seed(
            scene,
            [
                *_facts(scene, period_end=date(2022, 12, 31), filed=date(2023, 2, 1), values=thin),
                *_facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1), values=thin),
            ],
        )

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert outcome.forecast_coverage["capital_expenditure"] == 0
        assert outcome.as_dict()["forecast_coverage"]["capital_expenditure"] == 0

    async def test_the_step_output_carries_it(self, scene: dict[str, Any]) -> None:
        """Recorded, not only logged: "why did the gate ask me for six drivers?" has to be
        answerable from the run's own rows after the fact."""
        await _seed(scene, _facts(scene, period_end=date(2023, 12, 31), filed=date(2024, 2, 1)))

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert outcome.as_dict()["forecast_coverage"]["revenue"] == 1


class TestASectorIsNotAskedForAccountsItDoesNotKeep:
    """Gap A64. A live run on a bank logged `thin_for_forecast: ["current_assets",
    "current_liabilities", "operating_income"]`. None of the three is thin: a bank keeps
    an unclassified balance sheet and has no operating-income line, so the platform was
    asking a filer for accounts its own presentation rules forbid it to report, then
    recording the answer as a disclosure failing.
    """

    @staticmethod
    async def _two_years(scene: dict[str, Any]) -> None:
        """Both debt legs, so ``total_debt`` derives and debt to equity genuinely
        computes. Without them the ratio is absent for want of its inputs and the
        sector exclusion would be green whatever it did."""
        levered = {**_YEAR, "short_term_debt": "100"}
        await _seed(
            scene,
            [
                *_facts(
                    scene,
                    period_end=date(2022, 12, 31),
                    filed=date(2023, 2, 1),
                    values=levered,
                ),
                *_facts(
                    scene,
                    period_end=date(2023, 12, 31),
                    filed=date(2024, 2, 1),
                    values=levered,
                ),
            ],
        )

    async def test_an_undefined_concept_is_not_measured_for_coverage(
        self, scene: dict[str, Any]
    ) -> None:
        await self._two_years(scene)

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
            profile=profile_for("banks"),
        )

        coverage = outcome.forecast_coverage
        assert "current_assets" not in coverage
        assert "current_liabilities" not in coverage
        assert "operating_income" not in coverage

    async def test_what_the_sector_does_define_is_still_measured(
        self, scene: dict[str, Any]
    ) -> None:
        """The exclusion has to be the named three and nothing else, or a bank silently
        stops being measured at all."""
        await self._two_years(scene)

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
            profile=profile_for("banks"),
        )

        assert outcome.forecast_coverage["revenue"] == 2
        assert outcome.forecast_coverage["pre_tax_income"] == 2

    async def test_the_run_records_which_lines_it_never_asked_for(
        self, scene: dict[str, Any]
    ) -> None:
        """Recorded rather than merely absent: "why only five drivers?" is answerable from
        the row, instead of by noticing what is not in it."""
        await self._two_years(scene)

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
            profile=profile_for("banks"),
        )

        assert outcome.as_dict()["undefined_concepts"] == [
            "current_assets",
            "current_liabilities",
            "operating_income",
        ]

    async def test_a_ratio_the_sector_makes_meaningless_is_absent_with_its_reason(
        self, scene: dict[str, Any]
    ) -> None:
        await self._two_years(scene)

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
            profile=profile_for("banks"),
        )

        latest = outcome.latest
        assert latest is not None
        row = next(r for r in latest.ratios if r.key == "debt_to_equity")
        assert row.quantity is None
        assert "Deposits" in row.absent_because

    async def test_an_unclassified_company_is_unchanged(self, scene: dict[str, Any]) -> None:
        """The default must be exactly the old behaviour: every ordinary run measures and
        computes everything, as it did before a bank was mishandled."""
        await self._two_years(scene)

        outcome = await analyse_company(
            scene["session"],
            new_context(),
            company_id=scene["company"].id,
            work_order=scene["request"].work_order,
        )

        assert set(outcome.forecast_coverage) == set(FORECAST_CONCEPTS)
        assert outcome.undefined_concepts == ()
        latest = outcome.latest
        assert latest is not None
        assert next(r for r in latest.ratios if r.key == "debt_to_equity").quantity is not None


class TestTheMapCarriesTheSpellingsFilersUse:
    def test_the_plain_depreciation_and_amortisation_tag_maps(self) -> None:
        assert canonical_concept("us-gaap", "DepreciationAndAmortization") == (
            "depreciation_and_amortisation"
        )

    def test_the_broader_capital_expenditure_tag_maps(self) -> None:
        assert canonical_concept("us-gaap", "PaymentsToAcquireProductiveAssets") == (
            "capital_expenditure"
        )

    def test_bare_depreciation_is_never_the_combined_line(self) -> None:
        """It is a smaller number than the combined line, and mapping it there would
        understate the driver wherever a company reports the two separately. It is its own
        concept instead, and the combined line is derived from the pair
        (`aer.calc.statements`) — which is what the confirmation run's subject needed."""
        assert canonical_concept("us-gaap", "Depreciation") == "depreciation"
        assert canonical_concept("us-gaap", "AmortizationOfIntangibleAssets") == (
            "amortisation_of_intangibles"
        )
        assert canonical_concept("us-gaap", "Depreciation") != "depreciation_and_amortisation"
