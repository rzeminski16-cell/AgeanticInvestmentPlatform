"""Creating and reading research requests.

**One validation path, two front doors.** The JSON API and the HTML form both call
:func:`create_request`, and neither validates anything itself. That is the whole point: a
form that checks its own rules eventually checks slightly different ones, and the drift
always goes the same way — the form becomes more permissive than the API, and something
invalid reaches the database through the door nobody was testing.

**Nothing is fetched here.** No ticker lookup, no exchange lookup, no market data.
Writing a request is offline, instant and free, so what is stored is exactly what the
operator typed and ``resolved`` stays false until something external confirms it. The
universe rules therefore work from the typed values alone, which makes them heuristics —
hence exclusion messages that name the rule and explain themselves rather than simply
refusing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.config import Settings
from aer.core.enums import RequestStatus
from aer.core.schemas.request import (
    FieldProblem,
    RequestLimits,
    ResearchRequestCreate,
    check_limits,
)
from aer.core.universe import Exclusion, ExclusionRule, check_universe
from aer.db.models import AuditEvent, ResearchRequest, User
from aer.errors import ValidationError

__all__ = [
    "count_requests",
    "create_request",
    "get_request",
    "limits_from",
    "list_requests",
]

_log = structlog.get_logger("aer.services.requests")

# Which input an exclusion is the operator's fault for, so a form can highlight the field
# they can actually change. Getting an "this is a fund" message next to the exchange box
# is the kind of small wrongness that makes a form feel untrustworthy.
_EXCLUSION_FIELDS: dict[ExclusionRule, str] = {
    ExclusionRule.UNSUPPORTED_EXCHANGE: "exchange",
    ExclusionRule.OTC_VENUE: "exchange",
    ExclusionRule.EXCHANGE_TRADED_FUND: "ticker",
    ExclusionRule.INVESTMENT_TRUST: "company_name",
    ExclusionRule.MICRO_CAP: "ticker",
}


def limits_from(settings: Settings, *, today: datetime | None = None) -> RequestLimits:
    """Build the validation limits from configuration and the clock.

    The clock is read here rather than inside :func:`~aer.core.schemas.request.check_limits`
    so the rule itself stays a pure function of its arguments.

    "Today" is the UTC date. Deterministic beats locally intuitive: a server-side rule
    that depends on the reader's timezone gives different answers to the same request. The
    consequence is that shortly after local midnight in a positive-offset timezone, today's
    date can still be tomorrow by UTC — which is why the rejection message states the date
    it compared against rather than only saying "in the future".
    """
    moment = today or datetime.now(UTC)
    return RequestLimits(
        today=moment.astimezone(UTC).date(),
        per_run_budget_gbp=settings.per_run_budget_gbp,
    )


def _as_problem(exclusion: Exclusion) -> FieldProblem:
    return FieldProblem(
        field=_EXCLUSION_FIELDS.get(exclusion.rule, "ticker"),
        message=exclusion.message,
        code=exclusion.rule.value,
    )


def _reject(problems: Sequence[FieldProblem]) -> ValidationError:
    fields = sorted({problem.field for problem in problems})
    summary = (
        f"The research request was rejected ({len(problems)} problem"
        f"{'' if len(problems) == 1 else 's'}): {', '.join(fields)}."
    )
    return ValidationError(
        summary,
        context={"problems": [problem.as_dict() for problem in problems]},
    )


async def create_request(
    session: AsyncSession,
    *,
    user: User,
    payload: ResearchRequestCreate,
    limits: RequestLimits,
) -> ResearchRequest:
    """Validate and persist a research request in ``DRAFT``.

    Raises:
        ValidationError: If any contextual rule or universe rule rejects it. Every
            problem is reported together — told "wrong exchange", an operator fixes the
            exchange and resubmits only to learn it is also a fund, and one round trip per
            rule is a bad way to discover something was never going to work.
    """
    problems = [
        *check_limits(payload, limits),
        *(
            _as_problem(exclusion)
            for exclusion in check_universe(
                ticker=payload.ticker,
                exchange=payload.exchange,
                company_name=payload.company_name,
            )
        ),
    ]
    if problems:
        _log.info(
            "request.rejected",
            ticker=payload.ticker,
            exchange=payload.exchange,
            problem_fields=sorted({problem.field for problem in problems}),
            problem_codes=sorted({p.code for p in problems if p.code}),
        )
        raise _reject(problems)

    portfolio = payload.portfolio_context
    request = ResearchRequest(
        user_id=user.id,
        company_name=payload.company_name,
        ticker=payload.ticker,
        exchange=payload.exchange,
        isin=payload.isin,
        as_of_date=payload.as_of_date,
        base_currency=payload.base_currency,
        reporting_currency=payload.reporting_currency,
        investment_horizon_months=payload.investment_horizon_months,
        horizon_label=payload.horizon_label,
        analysis_mode=payload.analysis_mode,
        point_in_time=payload.point_in_time,
        # mode="json" so Decimal weights land as JSON strings the database can read back
        # without a float ever being involved. The CHECK constraints on this column cast
        # the text to numeric, which a float's repr would eventually break.
        portfolio_context=portfolio.model_dump(mode="json", exclude_none=True),
        risk_tolerance=payload.risk_tolerance.value if payload.risk_tolerance else None,
        liquidity_constraint_gbp=payload.liquidity_constraint_gbp,
        esg_sensitivity=payload.esg_sensitivity.value if payload.esg_sensitivity else None,
        focus_questions=payload.focus_questions,
        excluded_sources=payload.excluded_sources,
        max_cost_gbp=payload.max_cost_gbp,
        status=RequestStatus.DRAFT,
        # Nothing external has been consulted, so the identity is unverified by
        # construction. Task 8 sets this.
        resolved=False,
    )
    session.add(request)
    await session.flush()

    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=str(user.id),
            event_type="request.created",
            payload={
                "request_id": str(request.id),
                "ticker": request.ticker,
                "exchange": request.exchange,
                "as_of_date": request.as_of_date.isoformat(),
                "analysis_mode": request.analysis_mode.value,
                "point_in_time": request.point_in_time,
                "max_cost_gbp": str(request.max_cost_gbp),
            },
            previous=previous,
            request_id=request.id,
        )
    )
    await session.flush()

    _log.info(
        "request.created",
        request_id=str(request.id),
        ticker=request.ticker,
        exchange=request.exchange,
        analysis_mode=request.analysis_mode.value,
    )
    return request


async def get_request(
    session: AsyncSession,
    request_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
) -> ResearchRequest | None:
    """Fetch one request belonging to ``user_id``, or ``None``.

    Scoped by owner even though the MVP has a single user. An unscoped lookup is a
    horizontal access-control bug the day a second user exists, and it is invisible until
    then — so the filter goes in now, while there is no way for it to be wrong.
    """
    found: ResearchRequest | None = await session.scalar(
        select(ResearchRequest).where(
            ResearchRequest.id == request_id,
            ResearchRequest.user_id == user_id,
        )
    )
    return found


async def list_requests(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[ResearchRequest]:
    """Most recent first."""
    result = await session.scalars(
        select(ResearchRequest)
        .where(ResearchRequest.user_id == user_id)
        .order_by(ResearchRequest.created_at.desc(), ResearchRequest.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.all()


async def count_requests(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.count()).select_from(ResearchRequest).where(ResearchRequest.user_id == user_id)
    )
    return int(total or 0)
