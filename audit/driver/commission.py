"""Commissioning a request and starting its run, through the platform's own services."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import AnalysisMode
from aer.core.schemas.request import ResearchRequestCreate
from aer.db.models import Job, ResearchRequest, User
from aer.services import requests as request_service
from aer.services import runs as run_service
from audit.subjects import Subject

__all__ = ["commission"]


async def commission(
    session: AsyncSession,
    *,
    subject: Subject,
    actor: User,
    settings: Settings,
    cap_gbp: Decimal,
    analysis_mode: AnalysisMode = AnalysisMode.STANDARD,
) -> tuple[ResearchRequest, Job]:
    """A request in the operator's name at the audit's cap, and the job that will run it.

    ``analysis_mode`` defaults to the depth the operator's own runbook
    (``docs/users/the-confirmation-run.md``) commissions at.
    """
    payload = ResearchRequestCreate(
        company_name=subject.company_name,
        ticker=subject.ticker,
        exchange=subject.exchange,
        base_currency=subject.base_currency,
        reporting_currency=subject.reporting_currency,
        investment_horizon_months=subject.horizon_months,
        analysis_mode=analysis_mode,
        point_in_time=True,
        undated_sources_admissible=True,
        focus_questions=list(subject.focus_questions),
        max_cost_gbp=cap_gbp,
    )
    request = await request_service.create_request(
        session,
        user=actor,
        payload=payload,
        limits=request_service.limits_from(settings),
    )
    job = await run_service.start_run(session, request=request)
    await session.commit()
    return request, job
