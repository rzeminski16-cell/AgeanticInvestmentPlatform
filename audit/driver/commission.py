"""Commissioning a request and starting its run, through the platform's own services."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import AnalysisMode
from aer.core.schemas.request import ResearchRequestCreate
from aer.db.models import Job, ResearchRequest, User
from aer.errors import ValidationError
from aer.runtime import Registers
from aer.services import requests as request_service
from aer.services import runs as run_service
from aer.services.availability import check_availability
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
    registers: Registers | None = None,
) -> tuple[ResearchRequest, Job]:
    """A request in the operator's name at the audit's cap, and the job that will run it.

    ``analysis_mode`` defaults to the depth the operator's own runbook
    (``docs/users/the-confirmation-run.md``) commissions at.

    Args:
        registers: The registers to ask whether this run can succeed (ADR 0128), or ``None``
            to commission unchecked. **A harness that skipped the check would be a harness
            that commissions runs the product refuses** — the driver's whole purpose is to
            meet what an operator meets — so the two entry points that build one supply it,
            and the fake scene supplies a register that admits everything, exactly as the
            suite's own application fixtures do.

    Raises:
        ValidationError: If the registers refuse the subject. The request is created first and
            left standing, as it is at the route (ADR 0128): a refusal is a sentence beside an
            editable request, not a lost form.
    """
    payload = ResearchRequestCreate(
        company_name=subject.company_name,
        ticker=subject.ticker,
        exchange=subject.exchange,
        base_currency=subject.base_currency,
        reporting_currency=subject.reporting_currency,
        investment_horizon_months=subject.horizon_months,
        analysis_mode=analysis_mode,
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

    if registers is not None:
        available = await check_availability(
            request,
            sec_client=registers.sec_client,
            companies_house_client=registers.companies_house_client,
        )
        if not available.researchable:
            await session.commit()
            raise ValidationError(
                available.reason,
                context={"request_id": str(request.id), "register": available.register.value},
            )

    job = await run_service.start_run(session, request=request)
    await session.commit()
    return request, job
