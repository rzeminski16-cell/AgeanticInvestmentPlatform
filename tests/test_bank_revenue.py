"""A bank's revenue is derived, and only for a bank (ADR 0114).

The M&T run published a 172.1% net margin. Every guard held: the revenue figure was a
stored fact with a hashed source document, the margin was a recorded calculation carrying
its formula and its inputs, and every citation verified. The number is impossible, because
``RevenueFromContractWithCustomerExcludingAssessedTax`` — the ASC 606 disclosure of a
bank's fee income — was the only tag in the filing that mapped to ``revenue``.

Two halves are tested here, in the order the run performs them. The retag happens before
anything is written, so the partial caption never occupies the name. The derivation happens
after, from stored facts, and writes the sum back as a fact that carries its own
arithmetic.

The trigger is a *confirmed* profile, supplied by the caller. A rule that guessed "this
looks like a bank" would rewrite revenue for an insurer, a specialty lender or a fintech
with an interest line, so every test here passes the profile in explicitly — there is no
code path that infers one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import Quantity, UnitMismatchError
from aer.config import Settings
from aer.core.concepts import CONTRACT_REVENUE_TAGS
from aer.core.enums import (
    Decision,
    FactBasis,
    GateKind,
    JobStatus,
    Provider,
    SourceTier,
    UserRole,
)
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.sectors import profile_for
from aer.db.models import Artefact, Company, FinancialFact, JobStep, SourceDocument, User
from aer.services import approvals as approval_service
from aer.services.facts import derive_sector_revenue, retag_for_sector
from aer.services.sectors import CLASSIFY_STEP, classification_payload
from aer.sources.sec.selection import DUPLICATE_TAGGING_IN_SAME_FILING, select_latest
from aer.storage.local import LocalArtefactStore
from aer.workflow.engine import StepContext
from aer.workflow.workflows.vertical_slice_v1 import _extract
from tests.request_fixtures import research_request
from tests.sec_fixtures import fixture_bytes, make_fact
from tests.workflow_fixtures import AS_OF_DATE, seed_job

BANKS = profile_for("banks")
UTILITIES = profile_for("utilities")

# M&T's FY2025, as ADR 0114 records it: $6,948m of net interest income and $2,742m of
# non-interest income sum to $9,690m, against the $1,657m ASC 606 caption the run published.
NET_INTEREST_INCOME = Decimal("6948000000")
NONINTEREST_INCOME = Decimal("2742000000")
CONTRACT_REVENUE = Decimal("1657000000")
TOTAL_REVENUE = NET_INTEREST_INCOME + NONINTEREST_INCOME


@pytest.fixture
async def bank(db_session: AsyncSession) -> dict[str, Any]:
    """A bank, one archived filing, and nothing else. Facts are added per test."""
    user = User(email="banks@example.invalid", display_name="Banks", role=UserRole.ANALYST)
    db_session.add(user)
    await db_session.flush()

    request = research_request(
        user_id=user.id,
        company_name="M&T Bank Corporation",
        ticker="MTB",
        exchange="NYSE",
        as_of_date=AS_OF_DATE,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    company = Company(
        name="M&T Bank Corporation",
        cik="0000036270",
        ticker="MTB",
        exchange="NYSE",
        sic="6022",
    )
    db_session.add(company)
    await db_session.flush()

    artefact = Artefact(
        sha256="c" * 64,
        media_type="application/json",
        size_bytes=64,
        storage_key="ab/cd/" + "c" * 64,
    )
    db_session.add(artefact)
    await db_session.flush()

    document = SourceDocument(
        work_order_id=request.id,
        artefact_id=artefact.id,
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000036270.json",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        quarantined=False,
    )
    db_session.add(document)
    await db_session.flush()

    return {
        "session": db_session,
        "user": user,
        "request": request,
        "company": company,
        "document": document,
        "artefact": artefact,
    }


async def _add_fact(
    scene: dict[str, Any],
    *,
    concept: str,
    value: Decimal,
    unit: str = "USD",
    period_end: date = date(2025, 12, 31),
    fiscal_period: str | None = "FY",
    filed: date = date(2026, 2, 20),
    document: SourceDocument | None = None,
    accession: str = "0000036270-26-000012",
    scale: int = 0,
) -> FinancialFact:
    fact = FinancialFact(
        company_id=scene["company"].id,
        source_document_id=(document or scene["document"]).id,
        concept=concept,
        raw_concept="us-gaap-tag",
        taxonomy="us-gaap",
        value=value,
        unit=unit,
        scale=scale,
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        fiscal_year=period_end.year,
        fiscal_period=fiscal_period,
        filed_date=filed,
        form="10-K",
        accession=accession,
        basis=FactBasis.AS_REPORTED,
    )
    scene["session"].add(fact)
    await scene["session"].flush()
    return fact


async def _derive(scene: dict[str, Any], profile: Any = BANKS) -> Any:
    return await derive_sector_revenue(
        scene["session"],
        company=scene["company"],
        profile=profile,
        code_version="test-sha",
    )


class TestTheTagStopsBeingTheTopLine:
    """Part four of the decision: the ASC 606 concept keeps its own name."""

    @pytest.mark.parametrize("tag", CONTRACT_REVENUE_TAGS)
    def test_a_confirmed_bank_stores_contract_revenue_under_its_own_name(self, tag: str) -> None:
        retagged = retag_for_sector([make_fact(raw_concept=tag)], profile=BANKS)

        assert [fact.concept for fact in retagged] == ["revenue_from_contracts"]

    @pytest.mark.parametrize("tag", CONTRACT_REVENUE_TAGS)
    def test_an_ordinary_company_keeps_it_as_revenue(self, tag: str) -> None:
        """For Microsoft the ASC 606 caption *is* revenue, and nothing here changes that."""
        retagged = retag_for_sector([make_fact(raw_concept=tag)], profile=UTILITIES)

        assert [fact.concept for fact in retagged] == ["revenue"]

    def test_an_unclassified_run_is_untouched(self) -> None:
        facts = [make_fact(raw_concept=CONTRACT_REVENUE_TAGS[0])]

        assert retag_for_sector(facts, profile=None) == tuple(facts)

    def test_the_figure_is_kept_whole_and_only_renamed(self) -> None:
        """Still extracted, still stored, still available — it is a useful figure about a
        bank's fee business, and losing it would be a second error answering the first."""
        original = make_fact(raw_concept=CONTRACT_REVENUE_TAGS[0], value=1657000000)

        [retagged] = retag_for_sector([original], profile=BANKS)

        assert retagged.value == original.value
        assert retagged.raw_concept == original.raw_concept
        assert retagged.accession == original.accession
        assert retagged.filed_date == original.filed_date

    def test_a_banks_other_lines_are_left_alone(self) -> None:
        facts = [
            make_fact(concept="net_interest_income", raw_concept="InterestIncomeExpenseNet"),
            make_fact(concept="assets", raw_concept="Assets"),
        ]

        retagged = retag_for_sector(facts, profile=BANKS)

        assert [fact.concept for fact in retagged] == ["net_interest_income", "assets"]


class TestTheTopLineIsAssembled:
    async def test_the_two_halves_are_summed_into_a_revenue_fact(
        self, bank: dict[str, Any]
    ) -> None:
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)

        outcome = await _derive(bank)

        assert outcome.written == 1
        assert outcome.derived_periods == ("FY2025",)
        stored = await bank["session"].scalar(
            select(FinancialFact).where(FinancialFact.concept == "revenue")
        )
        assert stored is not None
        assert stored.value == TOTAL_REVENUE
        assert stored.unit == "USD"
        assert stored.basis is FactBasis.DERIVED

    async def test_the_row_carries_its_formula_its_inputs_and_the_code_version(
        self, bank: dict[str, Any]
    ) -> None:
        """A recorded calculation in everything but the table it lives in."""
        spread = await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        fees = await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)

        await _derive(bank)

        stored = await bank["session"].scalar(
            select(FinancialFact).where(FinancialFact.basis == FactBasis.DERIVED)
        )
        assert stored is not None
        derivation = stored.derivation or {}
        assert derivation["formula"] == "revenue = net_interest_income + noninterest_income"
        assert derivation["code_version"] == "test-sha"
        assert derivation["sector"] == "banks"
        assert [row["fact_id"] for row in derivation["inputs"]] == [str(spread.id), str(fees.id)]
        assert {row["unit"] for row in derivation["inputs"]} == {"USD"}

    async def test_it_has_no_filed_tag_because_no_filing_stated_it(
        self, bank: dict[str, Any]
    ) -> None:
        """A reader checking the report against the income statement will not find this
        figure in it, and the row says as much rather than naming a caption that does not
        exist."""
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)

        await _derive(bank)

        stored = await bank["session"].scalar(
            select(FinancialFact).where(FinancialFact.basis == FactBasis.DERIVED)
        )
        assert stored is not None
        assert stored.raw_concept is None
        assert stored.taxonomy is None
        assert stored.source_document_id == bank["document"].id

    async def test_every_period_with_both_halves_is_derived(self, bank: dict[str, Any]) -> None:
        for year in (2023, 2024, 2025):
            ends = date(year, 12, 31)
            await _add_fact(
                bank, concept="net_interest_income", value=Decimal("100"), period_end=ends
            )
            await _add_fact(
                bank, concept="noninterest_income", value=Decimal("50"), period_end=ends
            )

        outcome = await _derive(bank)

        assert outcome.written == 3
        assert outcome.derived_periods == ("FY2023", "FY2024", "FY2025")

    async def test_a_second_run_over_the_same_facts_writes_nothing_new(
        self, bank: dict[str, Any]
    ) -> None:
        """Re-running research is normal, and a derivation that duplicated itself would
        make every count downstream wrong.

        It must also not mistake its own earlier row for a filed caption. Counting one
        would make every re-run report that the filer had stated a total no filing
        contains — a false statement in the record, arrived at by the platform reading its
        own output back.
        """
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)

        await _derive(bank)
        second = await _derive(bank)

        assert second.written == 0
        assert second.derived_periods == ("FY2025",)
        assert second.refused == ()
        rows = list(
            await bank["session"].scalars(
                select(FinancialFact).where(FinancialFact.concept == "revenue")
            )
        )
        assert len(rows) == 1

    async def test_a_quarter_is_labelled_the_way_every_other_surface_labels_one(
        self, bank: dict[str, Any]
    ) -> None:
        await _add_fact(
            bank,
            concept="net_interest_income",
            value=NET_INTEREST_INCOME,
            period_end=date(2025, 9, 30),
            fiscal_period="Q3",
        )
        await _add_fact(
            bank,
            concept="noninterest_income",
            value=NONINTEREST_INCOME,
            period_end=date(2025, 9, 30),
            fiscal_period="Q3",
        )

        outcome = await _derive(bank)

        assert outcome.derived_periods == ("Q3 FY2025",)


class TestWhatItRefuses:
    async def test_one_half_derives_nothing_and_says_which_is_missing(
        self, bank: dict[str, Any]
    ) -> None:
        """Both inputs or nothing. It does not fall back to the ASC 606 concept, because
        falling back is how the original defect happened."""
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="revenue_from_contracts", value=CONTRACT_REVENUE)

        outcome = await _derive(bank)

        assert outcome.written == 0
        assert outcome.refused == (
            {"period": "FY2025", "reason": "no noninterest_income was filed for it"},
        )
        assert (
            await bank["session"].scalar(
                select(FinancialFact).where(FinancialFact.concept == "revenue")
            )
        ) is None

    async def test_a_stated_total_is_left_to_stand(self, bank: dict[str, Any]) -> None:
        """`RevenuesNetOfInterestExpense` is a bank's own total-revenue caption. Where the
        filer stated one, that is the revenue and this adds nothing."""
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)
        await _add_fact(bank, concept="revenue", value=TOTAL_REVENUE)

        outcome = await _derive(bank)

        assert outcome.written == 0
        assert outcome.refused == (
            {"period": "FY2025", "reason": "the filer stated a revenue total"},
        )

    async def test_halves_from_two_filings_are_not_summed(self, bank: dict[str, Any]) -> None:
        """The sum is two captions of one income statement. Assembled across two filings it
        would have no page a footnote could point at.

        Tested on the accession rather than the document, because EDGAR's companyfacts
        response is a single document carrying every filing a company has ever made — so
        "same document" is a test that would have passed on every pair.
        """
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(
            bank,
            concept="noninterest_income",
            value=NONINTEREST_INCOME,
            accession="0000036270-25-000009",
        )

        outcome = await _derive(bank)

        assert outcome.written == 0
        assert outcome.refused == (
            {"period": "FY2025", "reason": "the two halves were not stated in one filing"},
        )

    async def test_components_at_two_scales_raise(self, bank: dict[str, Any]) -> None:
        """Invariant 5's other half: rescaling one to match the other is a coercion."""
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=Decimal("2742"), scale=6)

        with pytest.raises(UnitMismatchError, match="different scales"):
            await _derive(bank)

    async def test_a_currency_mismatch_raises_rather_than_coercing(
        self, bank: dict[str, Any]
    ) -> None:
        """Invariant 5, at the one place a platform is tempted to break it."""
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME, unit="EUR")

        with pytest.raises(UnitMismatchError):
            await _derive(bank)


class TestAStoreFromBeforeTheRule:
    """A database written by a build that did not have ADR 0114.

    The case is M&T's own: the ASC 606 caption sitting under `revenue`, because until this
    rule existed that is where the concept map put it. Left alone, the derivation would
    read it as a total the filer had stated and leave the $1,657m exactly where it is —
    silently, which is the word that makes it unacceptable.
    """

    async def test_a_misfiled_caption_is_moved_and_the_top_line_is_then_derived(
        self, bank: dict[str, Any]
    ) -> None:
        stale = await _add_fact(bank, concept="revenue", value=CONTRACT_REVENUE)
        stale.raw_concept = CONTRACT_REVENUE_TAGS[0]
        await bank["session"].flush()
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)

        outcome = await _derive(bank)

        assert outcome.corrected == 1
        assert outcome.written == 1
        assert outcome.refused == ()

        await bank["session"].refresh(stale)
        assert stale.concept == "revenue_from_contracts"
        assert stale.value == CONTRACT_REVENUE, "the filer's own figure is untouched"

        derived = await bank["session"].scalar(
            select(FinancialFact).where(FinancialFact.basis == FactBasis.DERIVED)
        )
        assert derived is not None
        assert derived.value == TOTAL_REVENUE

    async def test_a_row_whose_corrected_identity_already_exists_is_dropped(
        self, bank: dict[str, Any]
    ) -> None:
        """Two rows for one observation is what the index forbids, so the duplicate goes.

        Nothing is lost: the surviving row is the same filer's same figure for the same
        period from the same filing, already under the right name.
        """
        stale = await _add_fact(bank, concept="revenue", value=CONTRACT_REVENUE)
        stale.raw_concept = CONTRACT_REVENUE_TAGS[0]
        current = await _add_fact(bank, concept="revenue_from_contracts", value=CONTRACT_REVENUE)
        current.raw_concept = CONTRACT_REVENUE_TAGS[0]
        await bank["session"].flush()

        outcome = await _derive(bank)

        assert outcome.corrected == 1
        rows = list(
            await bank["session"].scalars(
                select(FinancialFact).where(FinancialFact.concept == "revenue_from_contracts")
            )
        )
        assert len(rows) == 1

    async def test_an_ordinary_company_is_never_retagged(self, bank: dict[str, Any]) -> None:
        """The same stored row, a profile with no composition, and nothing moves. For
        Microsoft that row is revenue, and correcting it would be the error."""
        stale = await _add_fact(bank, concept="revenue", value=CONTRACT_REVENUE)
        stale.raw_concept = CONTRACT_REVENUE_TAGS[0]
        await bank["session"].flush()

        outcome = await _derive(bank, profile=UTILITIES)

        assert outcome.corrected == 0
        await bank["session"].refresh(stale)
        assert stale.concept == "revenue"


class TestItFiresOnlyForAConfirmedBank:
    async def test_an_ordinary_company_derives_nothing(self, bank: dict[str, Any]) -> None:
        """The same facts, a profile with no composition, and no revenue appears. A
        specialty lender with an interest line is not a bank until somebody says so."""
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)

        outcome = await _derive(bank, profile=UTILITIES)

        assert outcome.written == 0
        assert outcome.derived_periods == ()
        assert (
            await bank["session"].scalar(
                select(FinancialFact).where(FinancialFact.concept == "revenue")
            )
        ) is None

    async def test_an_unclassified_run_derives_nothing(self, bank: dict[str, Any]) -> None:
        await _add_fact(bank, concept="net_interest_income", value=NET_INTEREST_INCOME)
        await _add_fact(bank, concept="noninterest_income", value=NONINTEREST_INCOME)

        outcome = await _derive(bank, profile=None)

        assert outcome.written == 0
        assert outcome.formula == ""


class TestTheRunDoesItAtTheFactLayer:
    """The extract step, over a bank's own companyfacts, with the gate already confirmed.

    The unit tests above supply the profile; this one proves the step obtains it, and that
    it obtains the *confirmed* one. Six readers take the derived row without knowing it is
    derived, and all six read the table this step writes.
    """

    @pytest.fixture
    async def confirmed_bank_run(self, bank: dict[str, Any], tmp_path: Any) -> dict[str, Any]:
        session = bank["session"]
        settings = Settings(
            http_user_agent="Test test@example.invalid", artefact_root=tmp_path / "artefacts"
        )
        store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
        payload = fixture_bytes("companyfacts_bank.json")
        stored = await store.put_bytes(payload)

        job = await seed_job(session, request=bank["request"])
        classification = {
            "sector_key": "banks",
            "sector_label": "Banks",
            "rationale": "SIC 6022 proposed banks.",
            "proposed_by": "sic_lookup",
            "allowed_models": [],
            "blocked_models": [],
            "warnings": [],
        }
        classification["payload_hash"] = sha256_hex(
            canonical_json(classification_payload(classification))
        )
        session.add(
            JobStep(
                job_id=job.id,
                step_key=CLASSIFY_STEP,
                sequence=3,
                status=JobStatus.SUCCEEDED,
                idempotency_key=f"{job.id}:{CLASSIFY_STEP}",
                input_hash="0" * 64,
                output_ref=classification,
            )
        )
        await session.flush()

        actor = bank["user"]
        for gate in (GateKind.PLAN, GateKind.SECTOR_SPECIALIST):
            await approval_service.record_decision(
                session,
                job=job,
                gate=gate,
                decision=Decision.APPROVED,
                actor=actor,
                payload_hash=(
                    str(classification["payload_hash"])
                    if gate is GateKind.SECTOR_SPECIALIST
                    else "1" * 64
                ),
            )

        step = JobStep(
            job_id=job.id,
            step_key="extract",
            sequence=8,
            idempotency_key=f"{job.id}:extract",
            input_hash="0" * 64,
        )
        context = StepContext(
            session=session,
            job=job,
            step=step,
            services={"store": store, "settings": settings},
            outputs={
                "acquire": {
                    "artefact_sha256": stored.sha256,
                    "company_id": str(bank["company"].id),
                    "source_document_id": str(bank["document"].id),
                    "filings": [],
                }
            },
        )
        return {"context": context, "session": session, "company": bank["company"]}

    async def test_the_step_writes_the_top_line_the_filing_never_stated(
        self, confirmed_bank_run: dict[str, Any]
    ) -> None:
        result = await _extract(confirmed_bank_run["context"])

        assert result.output["derived_revenue_written"] == 2
        assert result.output["derived_revenue_periods"] == ["FY2024", "FY2025"]
        assert result.output["derived_revenue_formula"] == (
            "revenue = net_interest_income + noninterest_income"
        )

        stored = await confirmed_bank_run["session"].scalar(
            select(FinancialFact).where(
                FinancialFact.concept == "revenue",
                FinancialFact.period_end == date(2025, 12, 31),
            )
        )
        assert stored is not None
        assert stored.value == TOTAL_REVENUE
        assert stored.basis is FactBasis.DERIVED

    async def test_two_spellings_of_the_caption_are_one_observation_not_a_collision(
        self, confirmed_bank_run: dict[str, Any]
    ) -> None:
        """Retagging happens before selection, which is what makes this arbitrable.

        A filer that tags the same fee income both including and excluding assessed tax has
        reported one number under two labels. Renamed first, the two are rivals for one
        observation and selection records the loser as duplicate tagging; renamed after, they
        would have arrived at the unique index as two rows claiming one identity, and
        whichever the batch reached first would have won.
        """
        facts = retag_for_sector(
            [
                make_fact(raw_concept=CONTRACT_REVENUE_TAGS[0], value=1657000000),
                make_fact(raw_concept=CONTRACT_REVENUE_TAGS[1], value=1699000000),
            ],
            profile=BANKS,
        )

        selection = select_latest(facts)

        assert len(selection.chosen) == 1
        assert selection.chosen[0].concept == "revenue_from_contracts"
        assert [row.reason for row in selection.rejected] == [DUPLICATE_TAGGING_IN_SAME_FILING]

    async def test_the_fee_caption_is_stored_under_its_own_name(
        self, confirmed_bank_run: dict[str, Any]
    ) -> None:
        """The figure that reached the front page as revenue. Still stored, still hashed,
        no longer the top line."""
        await _extract(confirmed_bank_run["context"])

        fees = await confirmed_bank_run["session"].scalar(
            select(FinancialFact).where(FinancialFact.concept == "revenue_from_contracts")
        )
        assert fees is not None
        assert fees.value == CONTRACT_REVENUE
        assert fees.raw_concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
        assert fees.basis is FactBasis.AS_REPORTED

    async def test_the_margin_a_reader_would_recognise(
        self, confirmed_bank_run: dict[str, Any]
    ) -> None:
        """The whole point, in one number. $2,851m over $9,690m is 29.4%; over the ASC 606
        caption it was 172.1%, and every guard in the platform held while it was."""
        await _extract(confirmed_bank_run["context"])

        session = confirmed_bank_run["session"]
        revenue = await session.scalar(
            select(FinancialFact).where(
                FinancialFact.concept == "revenue",
                FinancialFact.period_end == date(2025, 12, 31),
            )
        )
        income = await session.scalar(
            select(FinancialFact).where(FinancialFact.concept == "net_income")
        )
        assert revenue is not None
        assert income is not None

        margin = (income.value / revenue.value).quantize(Decimal("0.001"))
        assert margin == Decimal("0.294")


class TestTheArithmeticItself:
    """The sum is a sum, whatever the halves are — including the signs a bank can report.

    A quarter of loan losses can put non-interest income below zero, and a bank whose
    funding costs exceed its interest income has a negative spread. Neither is a reason to
    refuse; both are reasons not to hand the arithmetic to a prompt.
    """

    @given(
        spread=st.decimals(min_value=-(10**9), max_value=10**12, places=0),
        fees=st.decimals(min_value=-(10**9), max_value=10**12, places=0),
    )
    def test_the_derived_total_is_the_sum_of_its_parts(
        self, spread: Decimal, fees: Decimal
    ) -> None:
        total = Quantity.of(spread, "USD") + Quantity.of(fees, "USD")

        assert total.value == spread + fees
        assert total.unit.symbol == "USD"

    @given(
        other=st.sampled_from(["EUR", "GBP", "JPY"]),
    )
    def test_no_pair_of_different_currencies_ever_sums(self, other: str) -> None:
        with pytest.raises(UnitMismatchError):
            _ = Quantity.of(Decimal(1), "USD") + Quantity.of(Decimal(1), other)
