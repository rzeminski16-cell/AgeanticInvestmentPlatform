"""A seeded company with a filed history, shared by the assumption suites.

`tests/test_assumption_proposals.py` builds the six derived proposals against this scene
and `tests/test_assumption_gate.py` builds the whole gate on top of it. The fixture lives
here rather than in either file because a fixture imported *into* a test module shadows the
parameter of every test that asks for it — pytest finds fixtures through `conftest.py`, and
that is the mechanism this file exists to use.
"""

from __future__ import annotations

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
from aer.services.calculations import new_context

__all__ = ["a_year", "analysed", "scene", "seed_years", "unit_for"]

AS_OF = date(2024, 6, 30)


def a_year(**overrides: str) -> dict[str, str]:
    """A plausible filer's year, in canonical concepts."""
    values = {
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
    values.update(overrides)
    return values


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    user = User(email="proposals@example.invalid", display_name="P", role=UserRole.OWNER)
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
        name="Contoso Corporation", ticker="CTSO", exchange="NASDAQ", cik="0000000002"
    )
    artefact = Artefact(
        sha256="d" * 64, size_bytes=10, media_type="application/json", storage_key="dd/d"
    )
    db_session.add_all([request, company, artefact])
    await db_session.flush()

    document = SourceDocument(
        request_id=request.id,
        artefact_id=artefact.id,
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000002.json",
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
        "job": job,
        "document": document,
    }


# Concepts counted rather than priced. A share count filed in the reporting currency is a
# figure with the right number and the wrong meaning, and `aer.calc.dcf` refuses to divide
# by it — correctly, so the fixture has to be right rather than the check relaxed.
_COUNTED = ("shares_outstanding", "basic_shares_outstanding", "diluted_shares_outstanding")


def unit_for(concept: str) -> str:
    return "shares" if concept in _COUNTED else "USD"


async def seed_years(scene: dict[str, Any], years: dict[date, dict[str, str]]) -> None:
    for period_end, values in years.items():
        scene["session"].add_all(
            FinancialFact(
                company_id=scene["company"].id,
                source_document_id=scene["document"].id,
                concept=concept,
                raw_concept=concept,
                taxonomy="us-gaap",
                value=Decimal(value),
                unit=unit_for(concept),
                period_start=date(period_end.year, 1, 1),
                period_end=period_end,
                fiscal_year=period_end.year,
                fiscal_period=ANNUAL,
                filed_date=date(period_end.year + 1, 2, 1),
                form="10-K",
                accession=f"000000000{period_end.year % 10}-00-000000",
                basis=FactBasis.AS_REPORTED,
            )
            for concept, value in values.items()
        )
    await scene["session"].flush()


async def analysed(scene: dict[str, Any]) -> Any:
    return await analyse_company(
        scene["session"],
        new_context(),
        company_id=scene["company"].id,
        request=scene["request"],
    )
