"""A run that valued a company from its filings, with every step a preview reads.

Shared by the calculator's tests (ADR 0133) and the workbook's (ADR 0134): both strike the
report's own model again from the run's recorded steps and confirmed assumptions, and both are
only worth testing against a run whose report they can be held to.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.enums import JobStatus, Provider, SourceTier, UserRole
from aer.core.sectors import ValuationModel, unclassified_mandate
from aer.db.models import Artefact, Company, Job, JobStep, SourceDocument, User
from aer.services.assumption_gate import EQUITY_RISK_PREMIUM_ASSUMPTION, RISK_FREE_ASSUMPTION
from aer.services.assumptions import confirm, propose
from aer.services.prices import BETA_ASSUMPTION
from aer.services.valuation_run import value_the_business
from aer.workflow.workflows.vertical_slice_v1 import FORECAST_YEARS
from tests.assumption_fixtures import a_year, analysed, seed_years
from tests.request_fixtures import research_request

__all__ = ["CONFIRMED", "SHARES", "YEARS", "valued_run"]

SHARES = {
    "shares_outstanding": "100",
    "basic_shares_outstanding": "100",
    "diluted_shares_outstanding": "110",
    "interest_expense": "20",
    "short_term_debt": "0",
}

YEARS = {
    date(2022, 12, 31): a_year(revenue="1000", operating_income="240", **SHARES),
    date(2023, 12, 31): a_year(revenue="1150", operating_income="290", **SHARES),
    date(2024, 12, 31): a_year(revenue="1300", operating_income="340", **SHARES),
}

CONFIRMED: dict[str, str] = {
    "revenue_growth": "0.05",
    "ebit_margin": "0.25",
    "capex_intensity": "0.06",
    "depreciation_intensity": "0.05",
    "working_capital_intensity": "0.20",
    "tax_rate": "0.21",
    "terminal_growth": "0.02",
    "exit_multiple": "10",
    RISK_FREE_ASSUMPTION: "0.042",
    BETA_ASSUMPTION: "1.1",
    EQUITY_RISK_PREMIUM_ASSUMPTION: "0.055",
}


def _recorded_figure(value: str) -> dict[str, str]:
    """A figure as the price step records it: the value, and the stored fact behind it."""
    return {"value": value, "source_id": "a-listing", "source_kind": "fact"}


async def valued_run(
    session: AsyncSession, *, value: bool = True, price: str = "40", capitalisation: str = "4400"
) -> dict[str, Any]:
    """A run that valued a company from its filings, with every step a preview reads.

    ``value=False`` stops short of the valuation: every assumption confirmed and nothing
    struck, which is a run with no report figures to reproduce. ``price`` and
    ``capitalisation`` are what the price step recorded; a distinctive price lets a test
    prove the close never reaches an export.
    """
    user = User(email="calculator@example.invalid", display_name="C", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    request = research_request(
        user_id=user.id,
        company_name="Contoso Corporation",
        ticker="CTSO",
        exchange="NASDAQ",
        as_of_date=date(2025, 6, 30),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
    )
    company = Company(
        name="Contoso Corporation", ticker="CTSO", exchange="NASDAQ", cik="0000000002"
    )
    artefact = Artefact(
        sha256="d" * 64, size_bytes=10, media_type="application/json", storage_key="dd/d"
    )
    session.add_all([request, company, artefact])
    await session.flush()
    document = SourceDocument(
        work_order_id=request.id,
        artefact_id=artefact.id,
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000002.json",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        title="Contoso XBRL company facts",
        retrieved_at=datetime.now(UTC),
    )
    job = Job(
        work_order_id=request.id,
        workflow_version="test",
        code_version="abc",
        status=JobStatus.SUCCEEDED,
        started_at=datetime.now(UTC),
    )
    session.add_all([document, job])
    await session.flush()
    scene = {
        "session": session,
        "user": user,
        "request": request,
        "company": company,
        "document": document,
        "job": job,
    }
    await seed_years(scene, YEARS)

    per_share = Unit.currency("USD") / Unit.base("shares")
    close = Quantity.of(
        Decimal(price), per_share, source=SourceRef.security("a-listing", label="close")
    )
    market_value = Quantity.of(
        Decimal(capitalisation),
        Unit.currency("USD"),
        source=SourceRef.security("a-listing", label="market capitalisation"),
    )
    recorded: dict[str, dict[str, Any]] = {
        "acquire": {"company_id": str(company.id)},
        "acquire_prices": {
            "market_capitalisation": _recorded_figure(capitalisation),
            "price_per_share": _recorded_figure(price),
        },
        "propose_assumptions": {"valuation_model": ValuationModel.DCF_FCFF.value},
    }
    for sequence, (key, output) in enumerate(recorded.items()):
        session.add(
            JobStep(
                job_id=job.id,
                step_key=key,
                sequence=sequence,
                status=JobStatus.SUCCEEDED,
                attempt=0,
                idempotency_key=f"{job.id}:{key}",
                input_hash="a" * 64,
                output_ref=output,
            )
        )
    for name, figure in CONFIRMED.items():
        row = await propose(
            session,
            request_id=request.id,
            name=name,
            value=Decimal(figure),
            unit="pure",
            justification=f"Scene value for {name}.",
            proposed_by="test",
        )
        await confirm(session, assumption=row, actor=user)

    if value:
        await value_the_business(
            session,
            request=request,
            job_id=job.id,
            analysis=await analysed(scene),
            mandate=unclassified_mandate(ValuationModel.DCF_FCFF, subject="CTSO"),
            years=FORECAST_YEARS,
            market_capitalisation=market_value,
            price_per_share=close,
        )
    await session.flush()
    return scene
