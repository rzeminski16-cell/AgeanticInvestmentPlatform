"""Every calculation a ledger cites is in the ledger — ROADMAP §3.19 item 84.

In each of MSFT's five approved reports, 17 to 26 of the cited figures could not be walked
to a filing. The value step recomputes the company's analysis in a ledger nobody persists,
so that the run's calculations are recorded once; the recomputation mints new ids, and the
valuation it feeds cites them. MSFT files its debt in two parts, so its total debt is a
subtotal the analysis sums, and everything above the cost of debt walked to a row that did
not exist.

The operator chose the fix on 27 September 2026. A ledger is persisted with the rows it
cites from the ledgers it read (`read_from`), and a citation that resolves nowhere is
refused rather than written. What is held here: the refusal, the carrying, and the value
step over a company whose debt is filed in parts.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.basic import ratio
from aer.calc.engine import CalculationContext
from aer.calc.statements import subtotal_sum
from aer.calc.units import Quantity, SourceRef, money
from aer.core.enums import FactBasis, JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Calculation,
    Company,
    FinancialFact,
    Job,
    SourceDocument,
    User,
)
from aer.errors import ValidationError
from aer.services.calculations import lineage, persist_context
from tests.request_fixtures import research_request
from tests.valued_run_fixtures import valued_run

pytestmark = pytest.mark.integration


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """A run with two filed debt lines, as MSFT files its debt."""
    user = User(email="ledger@example.invalid", display_name="Ledger", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()
    company = Company(name="MICROSOFT CORP", cik="0000789019", ticker="MSFT", exchange="NASDAQ")
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
    artefact = Artefact(
        sha256=hashlib.sha256(b"ledger").hexdigest(),
        media_type="text/html",
        size_bytes=6,
        storage_key="ledger/filing",
    )
    db_session.add_all([company, request, artefact])
    await db_session.flush()
    job = Job(
        work_order_id=request.id,
        workflow_version="vertical_slice_v1",
        code_version="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    document = SourceDocument(
        work_order_id=request.id,
        artefact_id=artefact.id,
        url="https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        retrieved_at=datetime.now(UTC),
    )
    db_session.add_all([job, document])
    await db_session.flush()

    lines: dict[str, FinancialFact] = {}
    for concept, value in (
        ("short_term_debt", "2999000000"),
        ("long_term_debt", "40152000000"),
        ("interest_expense", "3051000000"),
    ):
        fact = FinancialFact(
            company_id=company.id,
            source_document_id=document.id,
            concept=concept,
            value=Decimal(value),
            unit="USD",
            period_end=date(2025, 6, 30),
            basis=FactBasis.AS_REPORTED,
            filed_date=date(2025, 7, 30),
        )
        db_session.add(fact)
        lines[concept] = fact
    await db_session.flush()
    return {"session": db_session, "job": job, "lines": lines}


def _filed(fact: FinancialFact) -> Quantity:
    return money(fact.value, "USD", source=SourceRef.financial_fact(fact.id, label=fact.concept))


def _analysis(scene: dict[str, Any]) -> tuple[CalculationContext, Quantity]:
    """The analysis as the value step recomputes it: total debt summed, never persisted.

    The analysis also strikes rows the valuation never reads, as it does in a run.
    """
    analysis = CalculationContext(code_version="test")
    lines = scene["lines"]
    total_debt = subtotal_sum(
        analysis, a=_filed(lines["short_term_debt"]), b=_filed(lines["long_term_debt"])
    )
    subtotal_sum(analysis, a=_filed(lines["interest_expense"]), b=_filed(lines["interest_expense"]))
    return analysis, total_debt


def _valuation(scene: dict[str, Any], total_debt: Quantity) -> CalculationContext:
    """The valuation's ledger: a cost of debt struck over the analysis's total debt."""
    ledger = CalculationContext(code_version="test")
    ratio(ledger, numerator=_filed(scene["lines"]["interest_expense"]), denominator=total_debt)
    return ledger


async def _rows(scene: dict[str, Any]) -> list[Calculation]:
    return list(
        await scene["session"].scalars(
            select(Calculation)
            .where(Calculation.job_id == scene["job"].id)
            .order_by(Calculation.sequence)
        )
    )


class TestADanglingCitationIsRefused:
    async def test_a_ledger_citing_a_row_nobody_recorded_is_not_written(
        self, scene: dict[str, Any]
    ) -> None:
        _, total_debt = _analysis(scene)

        with pytest.raises(ValidationError) as refused:
            await persist_context(
                scene["session"], _valuation(scene, total_debt), job_id=scene["job"].id
            )

        assert "ratio cites" in str(refused.value)
        assert "read_from" in str(refused.value), "the refusal names the remedy"
        assert await _rows(scene) == []

    async def test_a_citation_to_something_that_is_no_id_is_refused(
        self, scene: dict[str, Any]
    ) -> None:
        """The price step once sourced every figure to the literal "market_capitalisation"."""
        ledger = CalculationContext(code_version="test")
        ratio(
            ledger,
            numerator=_filed(scene["lines"]["interest_expense"]),
            denominator=money(
                Decimal("4400"),
                "USD",
                source=SourceRef.calculation("market_capitalisation", label="market cap"),
            ),
        )

        with pytest.raises(ValidationError):
            await persist_context(scene["session"], ledger, job_id=scene["job"].id)


class TestTheRowsALedgerReadAreWrittenWithIt:
    async def test_the_cited_subtotal_is_written_first_and_nothing_else_of_the_analysis(
        self, scene: dict[str, Any]
    ) -> None:
        analysis, total_debt = _analysis(scene)
        ledger = _valuation(scene, total_debt)

        rows = await persist_context(
            scene["session"], ledger, job_id=scene["job"].id, read_from=(analysis,)
        )

        assert [row.name for row in rows] == ["subtotal_sum", "ratio"]
        assert total_debt.source is not None
        assert str(rows[0].id) == total_debt.source.identifier
        assert [row.sequence for row in rows] == [0, 1]
        # The other subtotal the analysis struck is nobody's input here.
        assert len(await _rows(scene)) == 2

    async def test_the_cost_of_debt_walks_to_the_filings(self, scene: dict[str, Any]) -> None:
        analysis, total_debt = _analysis(scene)
        rows = await persist_context(
            scene["session"],
            _valuation(scene, total_debt),
            job_id=scene["job"].id,
            read_from=(analysis,),
        )

        tree = await lineage(scene["session"], rows[-1].id)

        assert not [node for node in tree.walk() if node.kind == "missing"]
        assert {node.detail["concept"] for node in tree.leaves} == {
            "interest_expense",
            "short_term_debt",
            "long_term_debt",
        }

    async def test_rows_are_carried_through_any_number_of_steps(
        self, scene: dict[str, Any]
    ) -> None:
        analysis, total_debt = _analysis(scene)
        # A figure of the analysis over its own subtotal; the valuation cites only the top.
        leverage = ratio(analysis, numerator=total_debt, denominator=total_debt)
        ledger = CalculationContext(code_version="test")
        ratio(ledger, numerator=leverage, denominator=_filed(scene["lines"]["interest_expense"]))

        rows = await persist_context(
            scene["session"], ledger, job_id=scene["job"].id, read_from=(analysis,)
        )

        assert [row.name for row in rows] == ["subtotal_sum", "ratio", "ratio"]

    async def test_a_cited_row_already_stored_is_not_written_twice(
        self, scene: dict[str, Any]
    ) -> None:
        """The value step's scenarios and grids cite what the base case already wrote."""
        analysis, total_debt = _analysis(scene)
        session: AsyncSession = scene["session"]
        await persist_context(
            session, _valuation(scene, total_debt), job_id=scene["job"].id, read_from=(analysis,)
        )

        second = await persist_context(
            session,
            _valuation(scene, total_debt),
            job_id=scene["job"].id,
            read_from=(analysis, analysis),
        )

        assert [row.name for row in second] == ["ratio"]
        assert total_debt.source is not None
        stored = await session.scalar(
            select(func.count())
            .select_from(Calculation)
            .where(Calculation.id == uuid.UUID(total_debt.source.identifier))
        )
        assert stored == 1


class TestTheValueStep:
    async def test_every_citation_in_a_valued_run_resolves(self, db_session: AsyncSession) -> None:
        """The shape that broke MSFT: debt filed in parts, valued over a recomputed analysis.

        `valued_run` files long-term and short-term debt and values the business over an
        analysis struck in a ledger nobody persists, as the value step does.
        """
        scene = await valued_run(db_session)

        job_id = scene["job"].id
        rows = list(
            await db_session.scalars(select(Calculation).where(Calculation.job_id == job_id))
        )
        held = {str(row.id) for row in rows}
        cited = {
            str(entry["source"]["id"])
            for row in rows
            for entry in row.inputs
            if entry.get("source", {}).get("kind") == "calculation"
        }

        assert cited, "the valuation cites calculations"
        assert cited <= held, f"cited and never recorded: {sorted(cited - held)}"
        assert "subtotal_sum" in {row.name for row in rows}, "the total debt came with it"
