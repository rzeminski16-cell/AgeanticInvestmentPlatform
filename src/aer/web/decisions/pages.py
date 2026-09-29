"""What you decided to do about a thesis, written before the outcome, and the trades after.

Two screens and four forms. The journal lists every held decision with the thesis it acts
on and the trades that carried it out, and the form to write one; the detail is one
decision in full — what was decided, on what basis, the premises of the thesis it was taken
on, the trades that followed — with the forms to withdraw it or to revise it as a new entry
that supersedes it.

**No figure here is *about the decision*.** A decision's size is a sentence, its horizon a
number of months a reviewer compares with a date, and neither enters arithmetic anywhere
(ADR 0074, ADR 0104). The trades listed are attestations, rendered by the portfolio's own
rules; this page adds the link and nothing else.

**The pre-trade check is the one place figures appear, and they are about the book** (F12).
It states what the operator already holds — net assets, this listing's weight, the largest
five, the sector's share, and their own stated shocks — from
:func:`aer.services.risk.check_before_recording` and the risk page's own row formatter, so
the two surfaces cannot disagree. It **states the book now and never what the book becomes**,
because there is no stored intended weight for anything to multiply, and it **never blocks**,
because no ceiling exists in this platform to be breached.

**Nothing here decides anything.** The action is a word the operator chose from six; the
platform's contribution is to have the entry written before the trade rather than after.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from aer.api.deps import CurrentUser, DbSession, SettingsDep
from aer.core.dates import format_date, spoken_date
from aer.core.enums import DecisionAction
from aer.db.models import BookLimit, Decision, Portfolio, Report, Security, Thesis, Transaction
from aer.errors import AerError
from aer.services import decisions as decision_service
from aer.services import limits as limit_service
from aer.services import portfolio as portfolio_service
from aer.services import risk as risk_service
from aer.services import theses as thesis_service
from aer.services.calculations import new_context
from aer.services.decisions import ACTION_WORDS
from aer.web import verdict as verdicts
from aer.web import vocabulary
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.risk.pages import scenario_row
from aer.web.templating import render
from aer.web.theses.pages import PremiseRow, premise_rows

__all__ = ["Change", "DecisionRow", "TradeRow", "router"]

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
        decided_on=f"{spoken_date(judgement.held_at)}",
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
        review_by=f"{spoken_date(decision.review_by)}" if decision.review_by else "",
        review_by_iso=decision.review_by.isoformat() if decision.review_by else "",
        moves_the_book=decision.action.moves_the_book,
        is_withdrawn=judgement.is_withdrawn,
        withdrawn_on=f"{spoken_date(judgement.withdrawn_at)}" if judgement.withdrawn_at else "",
        withdrawn_reason=judgement.withdrawn_reason or "",
        trades=tuple(_trade(row) for row in decision.transactions),
    )


@dataclass(frozen=True, slots=True)
class Change:
    """One field a revision changed: what the earlier entry said and what this one says."""

    label: str
    before: str
    after: str


_COMPARED: Final[tuple[tuple[str, str], ...]] = (
    ("What was decided", "action"),
    ("In a line", "statement"),
    ("On what basis", "basis"),
    ("Listing", "security"),
    ("How much", "size_statement"),
    ("Intended holding period", "horizon"),
    ("Reversed if", "exit_plan"),
    ("Review by", "review_by"),
)


def changes_between(earlier: DecisionRow, later: DecisionRow) -> tuple[Change, ...]:
    """What the later entry says differently, field by field, in the words each page shows.

    The basis is always new — a revision must give one — so it is listed when it differs,
    which is nearly always; a reviewer wants to see both. A field left as it was is not a
    change and is not listed.
    """
    return tuple(
        Change(
            label=label, before=getattr(earlier, field) or "—", after=getattr(later, field) or "—"
        )
        for label, field in _COMPARED
        if getattr(earlier, field) != getattr(later, field)
    )


@dataclass(frozen=True, slots=True)
class Revision:
    """The other entry in a supersession pair, and what changed between the two."""

    id: uuid.UUID
    decided_on: str
    is_later: bool
    changes: tuple[Change, ...]


async def _revision(session: Any, decision: Decision) -> Revision | None:
    """The entry this one replaced, or the one that replaced it, with the diff either way."""
    this = _row(decision)
    earlier = await decision_service.supersedes(session, decision)
    if earlier is not None:
        before = _row(earlier)
        return Revision(
            id=before.id,
            decided_on=before.decided_on,
            is_later=False,
            changes=changes_between(before, this),
        )
    later = await decision_service.superseded_by(session, decision)
    if later is not None:
        after = _row(later)
        return Revision(
            id=after.id,
            decided_on=after.decided_on,
            is_later=True,
            changes=changes_between(this, after),
        )
    return None


def _candidate(row: Transaction) -> dict[str, str]:
    shown = _trade(row)
    listing = f"{row.security.ticker}.{row.security.exchange}" if row.security is not None else ""
    label = f"{shown.kind.capitalize()} on {shown.trade_date}: {shown.quantity}"
    if shown.price:
        label += f" at {shown.price} {shown.currency}"
    if listing:
        label += f" ({listing})"
    return {"value": str(row.attestation_id), "label": label}


def _trade(row: Transaction) -> TradeRow:
    return TradeRow(
        kind=row.kind.value,
        trade_date=f"{spoken_date(row.trade_date)}",
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
async def decisions_page(request: Request, session: DbSession, user: CurrentUser) -> Response:
    """Every held decision, and the way to the page that records the next one."""
    withdrawn = request.query_params.get("withdrawn") == "1"
    rows = [
        _row(decision)
        for decision in await decision_service.decisions_for(
            session, user_id=user.id, withdrawn=withdrawn
        )
    ]
    theses = await thesis_service.theses_for(session, user_id=user.id)
    named = request.query_params.get("security", "").strip()
    response: Response = render(
        request,
        "decisions/index.html",
        {
            "rows": rows,
            "showing_withdrawn": withdrawn,
            "verdict": _journal_verdict(rows, theses=len(theses)),
            "has_theses": bool(theses),
            # The record page takes the listing along, so a journal opened from a company
            # starts the decision about that company.
            "record_href": "/decisions/new" + (f"?security={quote(named)}" if named else ""),
        },
    )
    return response


@router.get("/decisions/new", response_class=HTMLResponse, summary="Record a decision")
async def new_decision_page(
    request: Request, session: DbSession, settings: SettingsDep, user: CurrentUser
) -> Response:
    """The drawn page (page specification §11): the form, and beside it the check.

    Declared above `/decisions/{decision_id}`, which would otherwise read *new* as an id.
    The check's what-if reloads this page with scripting off; with it on, only the check
    is asked for again (`/decisions/check`), so what the operator has typed stays put.
    """
    named = request.query_params.get("security", "").strip()
    weight, typed_problem = _typed_weight(request.query_params.get("what_if", ""))
    theses = await thesis_service.theses_for(session, user_id=user.id)
    check = await _pre_trade(session, named, user_id=user.id, weight_after=weight)
    if check is not None and typed_problem:
        check["after_problem"] = typed_problem
    token = new_csrf_token(settings)
    chosen_action = request.query_params.get("action", "")
    response: Response = render(
        request,
        "decisions/new.html",
        {
            "subject": await _subject_of(session, named),
            "theses": await _thesis_choices(session, theses),
            "chosen_thesis": request.query_params.get("thesis", ""),
            "actions": [
                {"value": action.value, "label": _ACTION_BUTTONS[action]}
                for action in ACTION_CHOICES
            ],
            "chosen_action": chosen_action
            if chosen_action in {action.value for action in ACTION_CHOICES}
            else "",
            "securities": await _dealable(session),
            "named_security": named,
            "check": check,
            "what_if": request.query_params.get("what_if", "").strip(),
            "swap_record_label": False,
            "today": datetime.now(UTC).date().isoformat(),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


async def _subject_of(session: Any, named: str) -> str:
    """The listing as the page's eyebrow names it: the company's own name where the
    platform holds the listing, else what was typed."""
    try:
        security = await _security(session, named)
    except ValueError:
        return named
    if security is None:
        return ""
    return security.name or security.ticker


@router.get(
    "/decisions/check", response_class=HTMLResponse, summary="What a weight would do to the book"
)
async def decision_check(request: Request, session: DbSession, user: CurrentUser) -> Response:
    """The check's body alone, for the record page to swap in as its what-if changes.

    Records nothing (ADR 0137): the figures are struck in a ledger this request drops.
    """
    named = request.query_params.get("security", "").strip()
    weight, typed_problem = _typed_weight(request.query_params.get("what_if", ""))
    check = await _pre_trade(session, named, user_id=user.id, weight_after=weight)
    if check is not None and typed_problem:
        check["after_problem"] = typed_problem
    fragment: Response = render(
        request,
        "decisions/_check.html",
        {
            "check": check,
            "named_security": named,
            "what_if": request.query_params.get("what_if", "").strip(),
            "swap_record_label": True,
        },
    )
    return fragment


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
    candidates = await decision_service.trades_that_could_carry_out(
        session,
        decision=decision,
        portfolio=await portfolio_service.default_book(session, user_id=user.id),
    )

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
            "revision": await _revision(session, decision),
            "candidates": [_candidate(row) for row in candidates],
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


@router.post("/decisions/{decision_id}/carry-out", summary="Attribute a trade to a decision")
async def carry_out_decision(
    decision_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """A trade already in the book, attributed to this decision after the fact.

    The same rule as the trade form's *Carries out* (ADR 0104 §2): the trade points at the
    decision, the service refuses a trade that could not have carried it out, and the picker
    only ever offered trades the service would accept — so a refusal here means the page was
    stale, and it says so.
    """
    decision = await decision_service.decision_of(session, decision_id, user_id=user.id)
    if decision is None:
        return _problem(request, "No such decision.")

    submitted = await _submitted(request)
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _refused(request, "Nothing was attributed.")

    trade = await _trade_of(session, submitted.get("transaction_id", ""), user_id=user.id)
    if trade is None:
        return _problem(request, "That trade is not one in your book.")

    try:
        await decision_service.carry_out(session, transaction=trade, decision=decision, actor=user)
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


async def _trade_of(session: Any, raw: str, *, user_id: uuid.UUID) -> Transaction | None:
    """A trade in one of this person's books, by attestation id, or ``None``."""
    try:
        identifier = uuid.UUID(raw.strip())
    except ValueError:
        return None
    found: Transaction | None = await session.scalar(
        select(Transaction)
        .join(Portfolio, Portfolio.id == Transaction.portfolio_id)
        .where(Transaction.attestation_id == identifier, Portfolio.user_id == user_id)
    )
    return found


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


# The drawn buttons' words (page specification §11): the verb alone, and Pass the same weight
# as the others rather than a footnote beneath them.
_ACTION_BUTTONS: Final[dict[DecisionAction, str]] = {
    DecisionAction.BUY: "Open",
    DecisionAction.ADD: "Add",
    DecisionAction.TRIM: "Trim",
    DecisionAction.SELL: "Exit",
    DecisionAction.HOLD: "Keep holding",
    DecisionAction.PASS: "Pass",
}


async def _thesis_choices(session: Any, theses: list[Thesis]) -> list[dict[str, str]]:
    """Each thesis as the drawn select names it: its title, when it was written, and the
    report it was written against — which is the report a decision about it rests on (F10,
    corrected 24 September 2026: the report is the thesis's own link, not the decision's)."""
    report_ids = [thesis.report_id for thesis in theses if thesis.report_id is not None]
    approved: dict[uuid.UUID, datetime | None] = {}
    if report_ids:
        found = await session.execute(
            select(Report.id, Report.approved_at).where(Report.id.in_(report_ids))
        )
        approved = dict(found.tuples())
    choices: list[dict[str, str]] = []
    for thesis in theses:
        parts = [thesis.title, f"written {format_date(thesis.created_at.date(), '%-d %b %Y')}"]
        on = approved.get(thesis.report_id) if thesis.report_id is not None else None
        parts.append(
            f"on the report of {format_date(on.date(), '%-d %b %Y')}"
            if on is not None
            else "on no report"
        )
        choices.append({"value": str(thesis.id), "label": " · ".join(parts)})
    return choices


# -- The check before it is recorded -------------------------------------------------------


async def _pre_trade(
    session: Any, named: str, *, user_id: uuid.UUID, weight_after: Decimal | None = None
) -> dict[str, Any] | None:
    """What the book already says, for the form to show before it is submitted.

    ``None`` when this person keeps no book: a concentration figure with no denominator is
    not a warning, it is a blank the operator has to interpret.

    **Every figure here comes from `risk_service.check_before_recording`**, and every
    scenario is rendered by the risk page's own :func:`~aer.web.risk.pages.scenario_row`.
    F12 asks for one implementation of the exposure arithmetic surfaced twice; importing
    both halves is how the two surfaces are stopped from disagreeing rather than tested for
    agreeing.
    """
    book = await portfolio_service.default_book(session, user_id=user_id)
    if book is None:
        return None
    try:
        security = await _security(session, named)
    except ValueError:
        # A listing nobody holds, typed or linked in. The book-wide half of the check still
        # reads, and the record form says its own piece about the name when it is submitted.
        security = None

    as_of = await portfolio_service.latest_close(session, portfolio=book)
    limits = await limit_service.limits_of(session, portfolio=book)
    context = new_context()
    try:
        check = await risk_service.check_before_recording(
            session,
            context,
            portfolio=book,
            security=security,
            as_of=as_of,
            weight_after=weight_after,
        )
    except AerError as problem:
        _log.warning("decisions.check_failed", portfolio=str(book.id), error=str(problem))
        return {
            "problem": str(problem),
            "as_of": f"{spoken_date(as_of)}",
            "figures": [],
            "scenarios": [],
            "after_rows": [],
            "after_problem": "",
            "crossings": [],
            "ticker": "",
            "caveat": risk_service.NO_INTENDED_SIZE,
        }

    currency = book.base_currency
    figures: list[dict[str, str]] = []
    if check.net_assets is not None:
        figures.append(
            {
                "label": "The book",
                "value": risk_service.money(check.net_assets.value, currency),
                "note": f"Net assets as at {spoken_date(as_of)}.",
            }
        )
    if check.held is not None and check.held.weight is not None:
        figures.append(
            {
                "label": f"Already held in {check.held.security.ticker}",
                "value": risk_service.percent(check.held.weight.value).lstrip("+"),
                "note": "Of net assets, before anything this decision leads to.",
            }
        )
    elif security is not None:
        figures.append(
            {
                "label": f"Already held in {security.ticker}",
                "value": "nothing",
                "note": "The book holds none of this listing as at this date.",
            }
        )
    if check.top_holdings is not None:
        figures.append(
            {
                "label": "Largest five holdings",
                "value": risk_service.percent(check.top_holdings.value).lstrip("+"),
                "note": "Their combined share of net assets.",
            }
        )
    if check.sector is not None:
        figures.append(
            {
                "label": f"Already in {check.sector.label}",
                "value": risk_service.percent(check.sector.share.value).lstrip("+"),
                "note": f"{len(check.sector.members)} of the book's holdings sit in this sector.",
            }
        )
    return {
        "problem": check.problem,
        "as_of": f"{spoken_date(as_of)}",
        "ticker": security.ticker if security is not None else "",
        "figures": figures,
        "scenarios": [scenario_row(row, currency) for row in check.scenarios],
        "after_rows": _after_rows(
            check.after, ticker=security.ticker if security else "", limits=limits
        ),
        "after_problem": check.after_problem,
        "cash_short": check.after is not None and check.after.cash_after.value < 0,
        "crossings": _crossings(check.after, limits, ticker=security.ticker if security else ""),
        "caveat": risk_service.NO_INTENDED_SIZE,
    }


def _crossings(
    after: risk_service.BookAfter | None, limits: limit_service.Limits, *, ticker: str
) -> list[str]:
    """Each limit the what-if would take the book past, in words (§11.1, ADR 0136/0137).

    Compared in code over the after-figures the check has already struck; the sentence names
    the operator's own value and nothing new. It never blocks: the record control only says
    *Record it anyway*.
    """
    if after is None:
        return []
    crossed: list[str] = []
    if limit_service.is_over(after.weight_after.value, limits.single_position):
        assert limits.single_position is not None
        crossed.append(
            f"{ticker} would be past {limit_service.ceiling_words(limits.single_position)}."
        )
    if limit_service.is_over(after.top_after.value, limits.five_largest):
        assert limits.five_largest is not None
        crossed.append(
            f"The five largest would be past {limit_service.ceiling_words(limits.five_largest)}."
        )
    sector_limit = limits.for_sector(after.sector) if after.sector else None
    if after.sector_after is not None and limit_service.is_over(
        after.sector_after.value, sector_limit
    ):
        assert sector_limit is not None
        crossed.append(f"{after.sector} would be past {limit_service.ceiling_words(sector_limit)}.")
    return crossed


def _after_rows(
    after: risk_service.BookAfter | None,
    *,
    ticker: str,
    limits: limit_service.Limits | None = None,
) -> list[dict[str, str]]:
    """The drawn before-and-after rows (ADR 0137), each a fraction of the book in words,
    with the operator's own ceiling beside the row it caps where one is stated."""
    if after is None:
        return []
    stated = limits or limit_service.Limits()

    def share(value: Decimal) -> str:
        return risk_service.percent(value).lstrip("+")

    def ceiling(limit: BookLimit | None) -> str:
        return f"ceiling {limit_service.limit_percent(limit)}" if limit is not None else ""

    rows = [
        {
            "label": f"Weight in {ticker}",
            "before": share(after.weight_before.value),
            "after": share(after.weight_after.value),
            "ceiling": ceiling(stated.single_position),
        },
        {
            "label": "The five largest",
            "before": share(after.top_before.value) if after.top_before is not None else "—",
            "after": share(after.top_after.value),
            "ceiling": ceiling(stated.five_largest),
        },
    ]
    if after.sector is not None and after.sector_before is not None:
        rows.append(
            {
                "label": f"In {after.sector}",
                "before": share(after.sector_before.value),
                "after": share(after.sector_after.value) if after.sector_after else "—",
                "ceiling": ceiling(stated.for_sector(after.sector)),
            }
        )
    rows.append(
        {
            "label": "Cash",
            "before": share(after.cash_before.value),
            "after": share(after.cash_after.value),
            "ceiling": "",
        }
    )
    return rows


# What the check's what-if accepts: a percentage of the book, as typed. Bounded to the whole
# book because a position larger than it is not a weight this arithmetic can mean.
_WHOLE_BOOK: Final = Decimal(100)


def _typed_weight(raw: str) -> tuple[Decimal | None, str]:
    """The position after the trade as a fraction of the book, or why the typing is not one."""
    cleaned = raw.strip().rstrip("%").strip()
    if not cleaned:
        return None, ""
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None, _NOT_A_WEIGHT
    if not value.is_finite() or value < 0 or value > _WHOLE_BOOK:
        return None, _OUTSIDE_THE_BOOK
    return value / _WHOLE_BOOK, ""


_NOT_A_WEIGHT: Final = (
    "Type the position after the trade as a percentage of the book, such as 12.5."
)
_OUTSIDE_THE_BOOK: Final = (
    "A position after the trade is between none of the book and all of it: type 0 to 100."
)


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
