"""Company endpoints: the research history this account has built about one listing.

Visibility follows ownership of the *research*, not of the company row: companies are
shared reference data, so a company this user never requested research about answers 404
exactly as a missing one does — the same no-enumeration rule as every other resource.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from starlette.status import HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession
from aer.errors import AerError
from aer.services import history

__all__ = ["CompanyHistoryRead", "router"]

router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyNotFoundError(AerError):
    """No such company, or this user never researched it."""

    code = "company_not_found"
    http_status = HTTP_404_NOT_FOUND


class MethodValueRead(BaseModel):
    """One terminal method's value per share, and the calculation that recorded it."""

    model_config = ConfigDict(extra="forbid")

    method: str
    value: str
    currency: str
    calculation_id: uuid.UUID


class ApprovedReportRead(BaseModel):
    """One approved report, as history: the conclusion, never the draft."""

    model_config = ConfigDict(extra="forbid")

    report_id: uuid.UUID
    job_id: uuid.UUID
    as_of_date: date
    rating: str | None
    confidence: float | None
    # What each method gave, read back from the run's own rows (§3.19 item 76). A list of
    # named answers, never a low and a high: two methods are not the ends of a range.
    valuation: list[MethodValueRead]


class CompanyHistoryRead(BaseModel):
    """The company, and every approved report about it, oldest first."""

    model_config = ConfigDict(extra="forbid")

    company_id: uuid.UUID
    name: str
    ticker: str
    exchange: str
    reports: list[ApprovedReportRead]


@router.get(
    "/{company_id}/history",
    response_model=CompanyHistoryRead,
    summary="Approved-report history for a company",
)
async def company_history(
    company_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> CompanyHistoryRead:
    """What this account has concluded about the company over time.

    Approved reports only: a draft was never agreed to and a rejected run was declined,
    so neither appears here under any filter.
    """
    company = await history.company_for_user(session, company_id=company_id, user_id=user.id)
    if company is None:
        message = f"No company {company_id}."
        raise CompanyNotFoundError(message, context={"company_id": str(company_id)})

    views = await history.valuation_history_for(session, company_id=company.id)
    return CompanyHistoryRead(
        company_id=company.id,
        name=company.name,
        ticker=company.ticker,
        exchange=company.exchange,
        reports=[
            ApprovedReportRead(
                report_id=view.report_id,
                job_id=view.job_id,
                as_of_date=view.as_of_date,
                rating=view.rating,
                confidence=view.confidence,
                valuation=[
                    MethodValueRead(
                        method=figure.label,
                        value=str(figure.value),
                        currency=figure.currency,
                        calculation_id=figure.calculation_id,
                    )
                    for figure in view.valuation.figures
                ],
            )
            for view in views
        ],
    )
