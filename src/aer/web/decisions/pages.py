"""What you decided to do about a thesis, written before the outcome, and the trades after.

Two screens and four forms. The journal lists every held decision with the thesis it acts
on and the trades that carried it out, and the form to write one; the detail is one
decision in full — what was decided, on what basis, the premises of the thesis it was taken
on, the trades that followed — with the forms to withdraw it or to revise it as a new entry
that supersedes it.

**Nothing on either page is a figure.** A decision's size is a sentence, its horizon a
number of months a reviewer compares with a date, and neither enters arithmetic anywhere
(ADR 0074, ADR 0104). The trades listed are attestations, rendered by the portfolio's own
rules; this page adds the link and nothing else.

**Nothing here decides anything.** The action is a word the operator chose from six; the
platform's contribution is to have the entry written before the trade rather than after.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.core.enums import DecisionAction
from aer.db.models import Decision, Security, Thesis, Transaction
from aer.errors import AerError
from aer.services import decisions as decision_service
from aer.services import portfolio as portfolio_service
from aer.services import theses as thesis_service
from aer.services.decisions import ACTION_WORDS
from aer.web import verdict as verdicts
from aer.web import vocabulary
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render
from aer.web.theses.pages import PremiseRow, premise_rows

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.decisions")

# The order the actions are offered in: the four that move the book, then the two that
# do not. The words are the service's, so the journal and the trade form agree.
ACTION_CHOICES: Final[tuple[DecisionAction, ...]] = (
    DecisionAction.BUY,
    DecisionAction.ADD,
    DecisionAction.TRIM,
    DecisionAction.SELL,
    DecisionAction.HOLD,
    DecisionAction.PASS,
)


@dataclass(frozen=True, slots=True)
class TradeRow:
    kind: str
    trade_date: str
    quantity: str
    price: str
    currency: str


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """One decision as either page shows it."""

    id: uuid.UUID
    thesis_id: uuid.UUID
    thesis_title: str
    action: str
    action_value: str
    statement: str
    basis: str
    decided_on: str
    held_by: str
    security: str
    size_statement: str
    horizon: str
    horizon_months: int | None
    exit_plan: str
    review_by: str
    review_by_iso: str
    moves_the_book: bool
    is_withdrawn: bool
    withdrawn_on: str
    withdrawn_reason: str
    trades: tuple[TradeRow, ...]

    @property
    def carried_out(self) -> bool:
        return bool(self.trades)


def _row(decision: Decision) -> DecisionRow:
    judgement = decision.judgement
    security = decision.security
    return DecisionRow(
        id=decision.judgement_id,
        thesis_id=decision.thesis_id,
        thesis_title=decision.thesis.title,
        action=ACTION_WORDS[decision.action],
        action_value=decision.action.value,
        statement=decision.statement,
        basis=judgement.basis,
        decided_on=f"{judgement.held_at:%d %B %Y}",
        held_by=judgement.held_by,
        security=f"{security.ticker}.{security.exchange}" if security is not None else "",
        size_statement=decision.size_statement or "",
        horizon=(
            f"{decision.horizon_months} month{'s' if decision.horizon_months != 1 else ''}"
            if decision.horizon_months
            else ""
        ),
        horizon_months=decision.horizon_months,
        exit_plan=decision.exit_plan or "",
        review_by=f"{decision.review_by:%d %B %Y}" if decision.review_by else "",
        review_by_iso=decision.review_by.isoformat() if decision.review_by else "",
        moves_the_book=decision.action.moves_the_book,
        is_withdrawn=judgement.is_withdrawn,
        withdrawn_on=f"{judgement.withdrawn_at:%d %B %Y}" if judgement.withdrawn_at else "",
        withdrawn_reason=judgement.withdrawn_reason or "",
        trades=tuple(_trade(row) for row in decision.transactions),
    )


def _trade(row: Transaction) -> TradeRow:
    return TradeRow(
        kind=row.kind.value,
        trade_date=f"{row.trade_date:%d %B %Y}",
        quantity=f"{abs(row.quantity).normalize():f}",
        price=f"{row.price.normalize():f}" if row.price is not None else "",
        currency=row.currency,
    )


def _journal_verdict(rows: list[DecisionRow], *, theses: int) -> verdicts.Verdict:
    to_carry_out = sum(1 for row in rows if row.moves_the_book and not row.carried_out)
    clauses: list[verdicts.Count | str] = [
        verdicts.Count(len(rows), "decision is held", "decisions are held"),
        verdicts.Count(
            to_carry_out, "is not yet carried out by a trade", "are not yet carried out by a trade"
        ),
    ]
    when_none = (
        "Nothing decided yet. A decision is what you do about a thesis, written down first."
        if theses
        else "Nothing to decide about yet. Write a thesis first; a decision acts on one."
    )
    return verdicts.sentence(clauses, when_none=when_none, tone=vocabulary.Tone.INFO)


# -- The journal -----------------------------------------------------------------------------


@router.get("/decisions", response_class=HTMLResponse, summary="Decisions")
async def decisions_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """Every held decision, and the form to write one."""
    withdrawn = request.query_params.get("withdrawn") == "1"
    rows = [
        _row(decision)
        for decision in await decision_service.decisions_for(
            session, user_id=user.id, withdrawn=withdrawn
        )
    ]
    theses = await thesis_service.theses_for(session, user_id=user.id)
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "decisions/index.html",
        {
            "rows": rows,
            "showing_withdrawn": withdrawn,
            "verdict": _journal_verdict(rows, theses=len(theses)),
            "theses": [{"value": str(thesis.id), "label": thesis.title} for thesis in theses],
            "actions": [
                {"value": action.value, "label": ACTION_WORDS[action].capitalize()}
                for action in ACTION_CHOICES
            ],
            "securities": await _dealable(session),
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/decisions", summary="Record a decision")
async def record_decision(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was recorded.")

    thesis = await _thesis(session, submitted.get("thesis_id", ""), user_id=user.id)
    if thesis is None:
        return _problem(request, "That thesis is not one of yours.")

    try:
        fields = await _fields(session, submitted, user_id=user.id)
        decision = await decision_service.record_decision(
            session,
            actor=user,
            thesis=thesis,
            decided_at=_date_at(submitted.get("decided_on", "")),
            **fields,
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    except ValueError as malformed:
        await session.rollback()
        return _problem(request, f"That decision could not be recorded: {malformed}", status=400)

    return RedirectResponse(f"/decisions/{decision.judgement_id}", status_code=HTTP_303_SEE_OTHER)


# -- One decision ------------------------------------------------------------------------------


@router.get("/decisions/{decision_id}", response_class=HTMLResponse, summary="A decision")
async def decision_page(
    decision_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    decision = await decision_service.decision_of(session, decision_id, user_id=user.id)
    if decision is None:
        return _problem(request, "No such decision.")
    thesis = await thesis_service.thesis_of(session, decision.thesis_id, user_id=user.id)
    premises: list[PremiseRow] = premise_rows(thesis) if thesis is not None else []

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "decisions/detail.html",
        {
            "item": _row(decision),
            "subject": (
                await thesis_service.subject_name(session, thesis) if thesis is not None else ""
            ),
            "premises": premises,
            "actions": [
                {"value": action.value, "label": ACTION_WORDS[action].capitalize()}
                for action in ACTION_CHOICES
            ],
            "securities": await _dealable(session),
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/decisions/{decision_id}/withdraw", summary="Withdraw a decision")
async def withdraw_decision(
    decision_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    decision = await decision_service.decision_of(session, decision_id, user_id=user.id)
    if decision is None:
        return _problem(request, "No such decision.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was withdrawn.")

    try:
        await decision_service.withdraw_decision(
            session, decision=decision, actor=user, reason=submitted.get("reason", "")
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)

    return RedirectResponse(f"/decisions/{decision.judgement_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/decisions/{decision_id}/revise", summary="Revise a decision")
async def revise_decision(
    decision_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """A new entry that supersedes this one. The old one stays, withdrawn as superseded."""
    decision = await decision_service.decision_of(session, decision_id, user_id=user.id)
    if decision is None:
        return _problem(request, "No such decision.")
    thesis = await thesis_service.thesis_of(session, decision.thesis_id, user_id=user.id)
    if thesis is None:
        return _problem(request, "The thesis this decision acts on is not on record.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was revised.")

    try:
        fields = await _fields(session, submitted, user_id=user.id)
        revised = await decision_service.revise_decision(
            session, decision=decision, actor=user, thesis=thesis, **fields
        )
        await session.commit()
    except AerError as refused:
        await session.rollback()
        return _problem(request, str(refused), status=refused.http_status)
    except ValueError as malformed:
        await session.rollback()
        return _problem(request, f"That revision could not be recorded: {malformed}", status=400)

    return RedirectResponse(f"/decisions/{revised.judgement_id}", status_code=HTTP_303_SEE_OTHER)


# -- Reading the form ------------------------------------------------------------------------


async def _fields(session: Any, submitted: dict[str, str], *, user_id: uuid.UUID) -> dict[str, Any]:
    """The form's answer to what was decided, as the service's keyword arguments.

    Raises:
        ValueError: For a shape the form cannot mean — an action off the list, a horizon
            that is not a number, a date that is not a date.
    """
    horizon = submitted.get("horizon_months", "").strip()
    review = submitted.get("review_by", "").strip()
    return {
        "action": DecisionAction(submitted.get("action", "")),
        "statement": submitted.get("statement", ""),
        "basis": submitted.get("basis", ""),
        "security": await _security(session, submitted.get("security", "")),
        "portfolio": await portfolio_service.default_book(session, user_id=user_id),
        "size_statement": submitted.get("size_statement", ""),
        "horizon_months": int(horizon) if horizon else None,
        "exit_plan": submitted.get("exit_plan", ""),
        "review_by": date.fromisoformat(review) if review else None,
    }


async def _thesis(session: Any, raw: str, *, user_id: uuid.UUID) -> Thesis | None:
    try:
        identifier = uuid.UUID(raw.strip())
    except ValueError:
        return None
    return await thesis_service.thesis_of(session, identifier, user_id=user_id)


async def _security(session: Any, typed: str) -> Security | None:
    """The listing the operator named, as ``TICKER.EXCHANGE``, or none.

    Raises:
        ValueError: If something was typed and no listing matches it. A decision about a
            listing the platform does not hold is still a decision — leave the box empty —
            but a typo silently dropped would be a decision about nothing in particular.
    """
    cleaned = typed.strip().upper()
    if not cleaned:
        return None
    ticker, _, exchange = cleaned.partition(".")
    statement = select(Security).where(Security.ticker == ticker, Security.is_active.is_(True))
    if exchange:
        statement = statement.where(Security.exchange == exchange)
    found: list[Security] = list(await session.scalars(statement))
    if not found:
        message = f"no listing {cleaned!r} is held; leave the security empty if it is not yet"
        raise ValueError(message)
    if len(found) > 1:
        choices = ", ".join(f"{row.ticker}.{row.exchange}" for row in found)
        message = f"{cleaned!r} is listed more than once ({choices}); say which"
        raise ValueError(message)
    return found[0]


async def _dealable(session: Any) -> list[dict[str, str]]:
    rows = await session.scalars(
        select(Security).where(Security.is_active.is_(True)).order_by(Security.ticker)
    )
    return [
        {"value": f"{row.ticker}.{row.exchange}", "label": row.name or row.ticker} for row in rows
    ]


def _date_at(raw: str) -> datetime | None:
    if not raw.strip():
        return None
    return datetime.combine(date.fromisoformat(raw.strip()), datetime.min.time(), tzinfo=UTC)


async def _submitted(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}


def _problem(request: Request, message: str, *, status: int = HTTP_404_NOT_FOUND) -> Response:
    rendered: Response = render(
        request, "runs/problem.html", {"message": message}, status_code=status
    )
    return rendered


def _refused(request: Request, consequence: str) -> Response:
    return _problem(
        request,
        f"This form's security token was missing or had expired. {consequence}",
        status=HTTP_403_FORBIDDEN,
    )
