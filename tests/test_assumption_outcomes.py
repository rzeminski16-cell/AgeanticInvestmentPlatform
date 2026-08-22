"""Assumption outcomes: whether what past research assumed turned out to hold (K3).

The spec's own tests, held exactly: a driver with a closed period and a filed actual
produces a delta; a driver whose period has not closed produces "not yet observable",
never a zero; an assumption the concept map cannot place is skipped with a stated reason
rather than silently. On top of those, what the doctrine demands: the deltas that reach
the comparison section are persisted as traced calculations (invariant 3), the accuracy
aggregate weights by measured count, and point-in-time hides a year filed after the
reading run's as-of date.

The realised value is asserted numerically against hand arithmetic — 106.4 over 100 is
growth of 0.064 and nothing else — because a delta is exactly the kind of figure that
looks plausible while being wrong.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.outcomes import realised_driver
from aer.calc.statements import assemble
from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.enums import FactBasis, JobStatus, Provider, SourceTier
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Artefact,
    Assumption,
    Calculation,
    Company,
    FinancialFact,
    Report,
    SourceDocument,
    User,
)
from aer.services.calculations import new_context
from aer.services.history import (
    MEASURED,
    NOT_MEASURABLE,
    NOT_YET_OBSERVABLE,
    SKIPPED,
    assumption_outcomes_for,
    driver_accuracy_for,
    prior_comparison_content,
)
from aer.services.knowledge import knowledge_stats
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio

PRIOR_AS_OF = date(2022, 6, 30)
READING_AS_OF = date(2024, 6, 30)
USD = Unit.parse("USD")


async def _company(session: AsyncSession, ticker: str = "OUTC") -> Company:
    row = Company(
        name=f"{ticker} Outcomes plc",
        ticker=ticker,
        exchange="NASDAQ",
        cik=f"{abs(hash(ticker)) % 10**10:010d}",
    )
    session.add(row)
    await session.flush()
    return row


async def _prior_report(
    session: AsyncSession,
    *,
    user: User,
    company: Company,
    assumptions: dict[str, Decimal],
    as_of: date = PRIOR_AS_OF,
) -> Report:
    """One approved report whose request carries the given confirmed assumptions."""
    request = await seed_request(session, user=user, as_of_date=as_of)
    job = await seed_job(session, request=request)
    job.status = JobStatus.SUCCEEDED
    await session.flush()

    for name, value in assumptions.items():
        session.add(
            Assumption(
                request_id=request.id,
                job_id=job.id,
                name=name,
                value=value,
                unit="pure",
                justification="Seeded for the outcome measurement.",
                approved=True,
                approved_at=datetime.now(UTC),
                approved_by="test",
            )
        )
    content: dict[str, Any] = {"sections": []}
    report = Report(
        job_id=job.id,
        request_id=request.id,
        company_id=company.id,
        as_of_date=as_of,
        immutable=True,
        approved_by=user.id,
        approved_at=datetime.now(UTC),
        content=content,
        content_hash=sha256_hex(canonical_json(content)),
    )
    session.add(report)
    await session.flush()
    return report


async def _document(session: AsyncSession, *, request_id: Any, job_id: Any) -> SourceDocument:
    payload = b"<html>seeded filing</html>"
    artefact = Artefact(
        sha256=hashlib.sha256(payload).hexdigest(),
        media_type="text/html",
        size_bytes=len(payload),
        storage_key="seeded/filing.html",
    )
    session.add(artefact)
    await session.flush()
    document = SourceDocument(
        work_order_id=request_id,
        request_id=request_id,
        job_id=job_id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/000/outcomes-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
        quarantined=False,
    )
    session.add(document)
    await session.flush()
    return document


async def _fiscal_year(
    session: AsyncSession,
    *,
    company: Company,
    document: SourceDocument,
    period_end: date,
    filed: date,
    lines: dict[str, Decimal],
) -> None:
    """One full fiscal year of duration facts, shaped as the selector requires."""
    for concept, value in lines.items():
        session.add(
            FinancialFact(
                company_id=company.id,
                source_document_id=document.id,
                concept=concept,
                value=value,
                unit="USD",
                period_start=period_end - timedelta(days=364),
                period_end=period_end,
                fiscal_year=period_end.year,
                fiscal_period="FY",
                basis=FactBasis.AS_REPORTED,
                filed_date=filed,
            )
        )
    await session.flush()


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """A prior run assuming 9% growth, and the year it forecast filed at 6.4%."""
    user = await seed_user(db_session, email="outcomes@example.invalid")
    # The reading run finds the company by its request's listing, and `seed_request`
    # files for MSFT on NASDAQ — so the scene's company carries that listing.
    company = await _company(db_session, ticker="MSFT")
    prior = await _prior_report(
        db_session,
        user=user,
        company=company,
        assumptions={
            "revenue_growth": Decimal("0.090000"),
            "terminal_growth": Decimal("0.025000"),
            "a_driver_nobody_maps": Decimal("0.5"),
            "tax_rate": Decimal("0.210000"),
        },
    )
    # A proposal nobody confirmed: it was never the run's forecast, so no outcome may
    # mention it — not even as skipped.
    db_session.add(
        Assumption(
            request_id=prior.request_id,
            name="ebit_margin",
            value=Decimal("0.4"),
            unit="pure",
            justification="Proposed and never approved.",
            approved=False,
        )
    )
    await db_session.flush()
    document = await _document(db_session, request_id=prior.request_id, job_id=prior.job_id)
    # The base year the prior run could see, and the realised year it forecast. The
    # realised year carries no pre-tax income, so the tax-rate outcome must say so.
    await _fiscal_year(
        db_session,
        company=company,
        document=document,
        period_end=PRIOR_AS_OF,
        filed=date(2022, 7, 28),
        lines={"revenue": Decimal("100000000")},
    )
    await _fiscal_year(
        db_session,
        company=company,
        document=document,
        period_end=date(2023, 6, 30),
        filed=date(2023, 7, 27),
        lines={"revenue": Decimal("106400000")},
    )
    # A second realised year, so "the first forecast year" is a choice the tests can
    # see: measuring FY2024 instead of FY2023 would produce a different growth rate.
    await _fiscal_year(
        db_session,
        company=company,
        document=document,
        period_end=date(2024, 6, 30),
        filed=date(2024, 7, 25),
        lines={"revenue": Decimal("120000000")},
    )
    return {"session": db_session, "user": user, "company": company, "prior": prior}


class TestTheRealisedDriver:
    """The pure half, on statements assembled exactly as the analysis assembles them."""

    def _statements(self, lines: dict[str, Decimal]) -> Any:
        context = new_context()
        quantities = {
            concept: Quantity(value=value, unit=USD, source=SourceRef.financial_fact(concept))
            for concept, value in lines.items()
        }
        return assemble(context, quantities)

    def test_the_growth_arithmetic_is_the_hand_arithmetic(self) -> None:
        context = new_context()
        previous = self._statements({"revenue": Decimal("100")})
        current = self._statements({"revenue": Decimal("106.4")})

        actual = realised_driver(context, "revenue_growth", statements=current, previous=previous)

        assert isinstance(actual, Quantity)
        assert actual.value == Decimal("0.064")

    def test_a_missing_line_is_a_stated_reason(self) -> None:
        context = new_context()
        current = self._statements({"revenue": Decimal("100")})

        outcome = realised_driver(context, "tax_rate", statements=current, previous=None)

        assert isinstance(outcome, str)
        assert "does not carry" in outcome

    def test_an_unknown_driver_is_a_stated_reason(self) -> None:
        context = new_context()
        current = self._statements({"revenue": Decimal("100")})

        outcome = realised_driver(context, "moat_width", statements=current, previous=None)

        assert isinstance(outcome, str)
        assert "concept map" in outcome


class TestAssumptionOutcomes:
    async def test_a_closed_period_with_a_filed_actual_produces_a_delta(
        self, scene: dict[str, Any]
    ) -> None:
        context = new_context()
        outcomes = await assumption_outcomes_for(
            scene["session"],
            context,
            prior=scene["prior"],
            as_of=READING_AS_OF,
            point_in_time=True,
        )
        by_name = {outcome.name: outcome for outcome in outcomes}

        growth = by_name["revenue_growth"]
        assert growth.status == MEASURED
        assert growth.actual == "0.064"
        # Assumed 0.09, realised 0.064: the forecast overshot by 0.026.
        assert growth.delta == "-0.026"
        assert "2023-06-30" in growth.basis
        # The arithmetic is on the ledger the caller will persist.
        assert {record.name for record in context.records} >= {
            "growth_rate",
            "assumption_delta",
        }
        # The unapproved ebit_margin proposal was never the run's forecast.
        assert "ebit_margin" not in by_name

    async def test_a_judgement_is_not_measurable_and_says_why(self, scene: dict[str, Any]) -> None:
        outcomes = await assumption_outcomes_for(
            scene["session"],
            new_context(),
            prior=scene["prior"],
            as_of=READING_AS_OF,
            point_in_time=True,
        )
        by_name = {outcome.name: outcome for outcome in outcomes}

        terminal = by_name["terminal_growth"]
        assert terminal.status == NOT_MEASURABLE
        assert "perpetuity" in terminal.basis
        assert terminal.delta is None

    async def test_an_unplaceable_name_is_skipped_with_its_reason(
        self, scene: dict[str, Any]
    ) -> None:
        outcomes = await assumption_outcomes_for(
            scene["session"],
            new_context(),
            prior=scene["prior"],
            as_of=READING_AS_OF,
            point_in_time=True,
        )
        by_name = {outcome.name: outcome for outcome in outcomes}

        unknown = by_name["a_driver_nobody_maps"]
        assert unknown.status == SKIPPED
        assert "concept map" in unknown.basis

        # Placeable, but the measured year does not file the line it needs.
        tax = by_name["tax_rate"]
        assert tax.status == SKIPPED
        assert "does not carry" in tax.basis

    async def test_an_unfiled_year_is_not_yet_observable_never_zero(
        self, db_session: AsyncSession
    ) -> None:
        user = await seed_user(db_session, email="unfiled@example.invalid")
        company = await _company(db_session, ticker="UNFL")
        prior = await _prior_report(
            db_session,
            user=user,
            company=company,
            assumptions={"revenue_growth": Decimal("0.09")},
        )
        document = await _document(db_session, request_id=prior.request_id, job_id=prior.job_id)
        # Only the base year exists; nothing after the prior's as-of date has filed.
        await _fiscal_year(
            db_session,
            company=company,
            document=document,
            period_end=PRIOR_AS_OF,
            filed=date(2022, 7, 28),
            lines={"revenue": Decimal("100000000")},
        )

        outcomes = await assumption_outcomes_for(
            db_session, new_context(), prior=prior, as_of=READING_AS_OF, point_in_time=True
        )

        assert [outcome.status for outcome in outcomes] == [NOT_YET_OBSERVABLE]
        assert outcomes[0].delta is None
        assert "not yet observable" in outcomes[0].basis

    async def test_point_in_time_hides_a_year_filed_after_the_reading_run(
        self, scene: dict[str, Any]
    ) -> None:
        """A run as at 2023-01-01 must not read the July 2023 filing beside it."""
        outcomes = await assumption_outcomes_for(
            scene["session"],
            new_context(),
            prior=scene["prior"],
            as_of=date(2023, 1, 1),
            point_in_time=True,
        )
        by_name = {outcome.name: outcome for outcome in outcomes}

        assert by_name["revenue_growth"].status == NOT_YET_OBSERVABLE

    async def test_a_closed_year_not_yet_filed_is_still_hidden(self, scene: dict[str, Any]) -> None:
        """The filed-date half of point-in-time: the year ended 2023-06-30 but its
        filing landed 2023-07-27, so a run as at 2023-07-01 has not seen it."""
        outcomes = await assumption_outcomes_for(
            scene["session"],
            new_context(),
            prior=scene["prior"],
            as_of=date(2023, 7, 1),
            point_in_time=True,
        )
        by_name = {outcome.name: outcome for outcome in outcomes}

        assert by_name["revenue_growth"].status == NOT_YET_OBSERVABLE


class TestTheComparisonSection:
    async def test_the_rows_join_and_the_calculations_persist(self, scene: dict[str, Any]) -> None:
        """The section shows the delta, and the delta is a recorded calculation."""
        session = scene["session"]
        user = scene["user"]
        reading = await seed_request(session, user=user, as_of_date=READING_AS_OF)
        job = await seed_job(session, request=reading)

        content = await prior_comparison_content(session, job_id=job.id, request=reading)

        aspects = {row["aspect"] for row in content["comparisons"]}
        assert "Assumption — revenue growth" in aspects
        measured_row = next(
            row for row in content["comparisons"] if row["aspect"] == "Assumption — revenue growth"
        )
        assert "0.064" in measured_row["current"]
        assert "-0.026" in measured_row["current"]

        recorded = {
            row.name
            for row in await session.scalars(
                select(Calculation).where(Calculation.job_id == job.id)
            )
        }
        assert "assumption_delta" in recorded
        assert "growth_rate" in recorded


class TestDriverAccuracy:
    async def test_it_aggregates_measured_runs_and_omits_the_unmeasured(
        self, scene: dict[str, Any]
    ) -> None:
        rows = await driver_accuracy_for(scene["session"], company_id=scene["company"].id)

        by_name = {row.name: row for row in rows}
        assert by_name["revenue_growth"].measured == 1
        assert by_name["revenue_growth"].mean_absolute_delta == "0.026"
        # The judgement and the unmeasurable never appear — absence, not zero.
        assert "tax_rate" not in by_name

    async def test_nothing_measured_is_an_empty_list(self, db_session: AsyncSession) -> None:
        user = await seed_user(db_session, email="empty@example.invalid")
        company = await _company(db_session, ticker="EMPT")
        await _prior_report(
            db_session,
            user=user,
            company=company,
            assumptions={"terminal_growth": Decimal("0.02")},
        )

        assert await driver_accuracy_for(db_session, company_id=company.id) == []


class TestKnowledgeAccuracy:
    async def test_the_graph_aggregate_carries_the_driver_record(
        self, scene: dict[str, Any]
    ) -> None:
        stats = await knowledge_stats(scene["session"], as_of=READING_AS_OF)

        by_name = {row.name: row for row in stats.accuracy.drivers}
        assert by_name["revenue_growth"].measured >= 1
        assert by_name["revenue_growth"].mean_absolute_delta == "0.026000"
        assert "accuracy" in stats.as_dict()
