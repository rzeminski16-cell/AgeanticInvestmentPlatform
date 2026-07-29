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

**A request stops being editable the moment a run exists.** Until then it is a note to
self and correcting a mistyped ticker costs nothing. After that it is the thing a plan was
approved against, the thing evidence was gathered under, and the thing a report cites — so
editing it in place would not correct the record, it would falsify it. Deletion follows
the same line, which is threat T16's retention rule arriving early in its safest form:
nothing that has evidence or a report behind it can be removed by this code at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

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
from aer.db.models import AuditEvent, Job, ResearchRequest, User
from aer.errors import ConflictError, ValidationError

__all__ = [
    "count_requests",
    "create_request",
    "delete_request",
    "get_request",
    "immutable_reason",
    "limits_from",
    "list_requests",
    "update_request",
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


# Every column an operator controls, which is exactly the set :func:`_apply` writes. Kept
# as an explicit tuple because it is what the edit audit entry diffs over, and a test
# checks it against `_apply`'s own assignments so the two cannot drift: a field missing
# here would be edited without the change ever appearing in the audit trail.
_EDITABLE_FIELDS: tuple[str, ...] = (
    "company_name",
    "ticker",
    "exchange",
    "isin",
    "as_of_date",
    "base_currency",
    "reporting_currency",
    "investment_horizon_months",
    "horizon_label",
    "analysis_mode",
    "point_in_time",
    "portfolio_context",
    "risk_tolerance",
    "liquidity_constraint_gbp",
    "esg_sensitivity",
    "focus_questions",
    "excluded_sources",
    "max_cost_gbp",
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


def _refuse_if_invalid(payload: ResearchRequestCreate, limits: RequestLimits) -> None:
    """Apply every contextual and universe rule, reporting all failures together.

    Shared by creation and editing rather than duplicated, so an edit can never sneak past
    a rule a creation would have been refused for. Told "wrong exchange", an operator fixes
    the exchange and resubmits only to learn it is also a fund; one round trip per rule is
    a bad way to discover something was never going to work.
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
    if not problems:
        return

    _log.info(
        "request.rejected",
        ticker=payload.ticker,
        exchange=payload.exchange,
        problem_fields=sorted({problem.field for problem in problems}),
        problem_codes=sorted({p.code for p in problems if p.code}),
    )
    raise _reject(problems)


def _apply(request: ResearchRequest, payload: ResearchRequestCreate) -> None:
    """Write a validated payload onto a request row.

    Every field the operator controls, assigned in one place. Creation and editing share it
    so that a field added to the schema cannot end up settable at creation and silently
    ignored on edit — which would look exactly like an edit that did not save.
    """
    request.company_name = payload.company_name
    request.ticker = payload.ticker
    request.exchange = payload.exchange
    request.isin = payload.isin
    request.as_of_date = payload.as_of_date
    request.base_currency = payload.base_currency
    request.reporting_currency = payload.reporting_currency
    request.investment_horizon_months = payload.investment_horizon_months
    request.horizon_label = payload.horizon_label
    request.analysis_mode = payload.analysis_mode
    request.point_in_time = payload.point_in_time
    # mode="json" so Decimal weights land as JSON strings the database can read back
    # without a float ever being involved. The CHECK constraints on this column cast
    # the text to numeric, which a float's repr would eventually break.
    request.portfolio_context = payload.portfolio_context.model_dump(mode="json", exclude_none=True)
    request.risk_tolerance = payload.risk_tolerance.value if payload.risk_tolerance else None
    request.liquidity_constraint_gbp = payload.liquidity_constraint_gbp
    request.esg_sensitivity = payload.esg_sensitivity.value if payload.esg_sensitivity else None
    request.focus_questions = payload.focus_questions
    request.excluded_sources = payload.excluded_sources
    request.max_cost_gbp = payload.max_cost_gbp


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
    _refuse_if_invalid(payload, limits)

    request = ResearchRequest(
        user_id=user.id,
        status=RequestStatus.DRAFT,
        # Nothing external has been consulted, so the identity is unverified by
        # construction. Task 8 sets this.
        resolved=False,
    )
    _apply(request, payload)
    session.add(request)
    await session.flush()

    await _record(
        session,
        actor=str(user.id),
        event_type="request.created",
        request_id=request.id,
        payload={
            "request_id": str(request.id),
            "ticker": request.ticker,
            "exchange": request.exchange,
            "as_of_date": request.as_of_date.isoformat(),
            "analysis_mode": request.analysis_mode.value,
            "point_in_time": request.point_in_time,
            "max_cost_gbp": str(request.max_cost_gbp),
        },
    )

    _log.info(
        "request.created",
        request_id=str(request.id),
        ticker=request.ticker,
        exchange=request.exchange,
        analysis_mode=request.analysis_mode.value,
    )
    return request


async def immutable_reason(session: AsyncSession, *, request: ResearchRequest) -> str | None:
    """Why this request can no longer be changed, or ``None`` if it still can.

    Returned as prose rather than a boolean because both callers need the sentence: the
    API puts it in the problem detail, and the detail page puts it where the edit button
    would otherwise be. "Editing is disabled" with no reason is the kind of answer that
    sends someone to the source code.

    The run check is the load-bearing one — starting a run leaves the request in ``DRAFT``
    today, so the status check would not catch it on its own. The status check is there for
    the day something else moves a request out of ``DRAFT`` without a job, which is a
    change that should not silently re-open editing.
    """
    started = await session.scalar(select(Job.id).where(Job.request_id == request.id).limit(1))
    if started is not None:
        return (
            "A run has been started for this request, so it can no longer be changed. What "
            "was researched, and what a plan was approved against, has to stay what it was "
            "— editing it now would not correct the record, it would falsify it. Create a "
            "new request instead."
        )

    if request.status is not RequestStatus.DRAFT:
        return (
            f"This request is {request.status.value}, not a draft, so it can no longer be changed."
        )

    return None


async def _refuse_if_immutable(
    session: AsyncSession, *, request: ResearchRequest, verb: str
) -> None:
    reason = await immutable_reason(session, request=request)
    if reason is None:
        return
    raise ConflictError(
        f"This request cannot be {verb}. {reason}",
        context={"request_id": str(request.id), "status": request.status.value},
    )


async def update_request(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    actor: User,
    payload: ResearchRequestCreate,
    limits: RequestLimits,
) -> ResearchRequest:
    """Replace a draft request's contents with a re-validated payload.

    A whole-payload replace, not a patch. The form submits every field it renders, and a
    partial update would need its own rule for what an absent field means — "leave it" and
    "clear it" are both defensible, which is exactly why neither should have to be guessed.

    Raises:
        ConflictError: If a run has been started, or the request has left ``DRAFT``.
        ValidationError: If the new contents fail any rule a new request would fail.
    """
    await _refuse_if_immutable(session, request=request, verb="edited")
    _refuse_if_invalid(payload, limits)

    before = _snapshot(request)
    _apply(request, payload)
    after = _snapshot(request)
    changes = {
        name: [before[name], after[name]]
        for name in _EDITABLE_FIELDS
        if before[name] != after[name]
    }

    if {"ticker", "exchange", "isin"} & changes.keys():
        # The identity changed, so whatever confirmed the old one confirmed something else.
        # Leaving `resolved` true here would be a claim about a security nobody looked up.
        request.resolved = False

    await session.flush()

    await _record(
        session,
        actor=str(actor.id),
        event_type="request.edited",
        request_id=request.id,
        payload={
            "request_id": str(request.id),
            # Before and after, not just the field names: "as_of_date changed" is a fact
            # you cannot act on months later, and the row itself only remembers the after.
            "changes": changes,
        },
    )

    _log.info(
        "request.edited",
        request_id=str(request.id),
        ticker=request.ticker,
        changed_fields=sorted(changes),
    )
    return request


async def delete_request(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    actor: User,
) -> None:
    """Delete a draft request that has never been run.

    The guard is the whole point. A request with a run behind it has evidence, costs and
    possibly a report attached, and the ORM cascade would take all of it — so this refuses
    rather than relying on an operator not to ask. Nothing here can delete anything that
    was researched; that needs an explicit retention policy, which is Phase 6 work.

    The audit entry outlives the row, deliberately: ``audit_events`` carries ``request_id``
    as a plain column with no foreign key, so the record that a request existed and was
    removed survives the removal.

    Raises:
        ConflictError: If a run has been started, or the request has left ``DRAFT``.
    """
    await _refuse_if_immutable(session, request=request, verb="deleted")

    request_id = request.id
    await _record(
        session,
        actor=str(actor.id),
        event_type="request.deleted",
        request_id=request_id,
        payload={
            "request_id": str(request_id),
            # Enough to say what was removed without keeping the row. A deletion whose log
            # entry is just an id answers "was something deleted" and nothing else.
            "ticker": request.ticker,
            "exchange": request.exchange,
            "company_name": request.company_name,
            "as_of_date": request.as_of_date.isoformat(),
            "status": request.status.value,
        },
    )

    await session.delete(request)
    await session.flush()

    _log.info("request.deleted", request_id=str(request_id), actor=str(actor.id))


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


# -- Audit and diffing -----------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    request_id: uuid.UUID,
) -> None:
    """Append one link to the audit chain.

    Always via :meth:`AuditEvent.create_linked`, and always reading the current tail first:
    a hand-built row with a hand-written hash is how a chain silently stops verifying.
    """
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            request_id=request_id,
        )
    )
    await session.flush()


def _jsonable(value: Any) -> Any:
    """Render a column value for the audit payload without going through ``float``.

    ``Decimal`` becomes a string, never a number. JSON has no decimal type, so serialising
    £2.50 as a number is a round trip through binary floating point — and a monetary
    ceiling that comes back as 2.4999999999999996 in the audit trail is worse than useless.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _snapshot(request: ResearchRequest) -> dict[str, Any]:
    """The operator-controlled fields, in a form the audit payload can hold."""
    return {name: _jsonable(getattr(request, name)) for name in _EDITABLE_FIELDS}
