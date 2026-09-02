"""Writing a decision down before the outcome is known, and every later act upon it.

Roadmap §3.7, under ADR 0104. A decision is a judgement — a named person, at a time, on a
stated basis — seen from its consequence: what they decided to do about a thesis. This
module is the only writer of ``decisions`` and of the one column a trade carries about one,
and every write appends to the audit chain with the decision as its subject.

**What it refuses is the point.** It never edits a decision: a revision is a new row that
supersedes the old, and the old is withdrawn with that reason, so what was decided when is
a fact about that time. It never stores a size as a number — the size is a sentence, and
the schema has no column a calculation could read (ADR 0074). And it never decides
anything: an action from a closed list is what the operator chose, and the platform's
whole contribution is to have written it down before the trade rather than after.

**A trade carries out a decision by pointing at it.** :func:`carry_out` is the only place
``transactions.decision_id`` is set, and it refuses a pairing that cannot be what it
claims — a sale carrying out a buy, a trade in one security carrying out a decision about
another.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.core.enums import DecisionAction, JudgementKind, TransactionKind
from aer.db.models import (
    AuditEvent,
    Decision,
    Judgement,
    Portfolio,
    Security,
    Thesis,
    Transaction,
    User,
)
from aer.errors import ConflictError, ValidationError

__all__ = [
    "ACTION_WORDS",
    "SUBJECT_DECISION",
    "carry_out",
    "decision_of",
    "decisions_for",
    "decisions_of_thesis",
    "open_for_the_book",
    "record_decision",
    "reviews_due",
    "revise_decision",
    "withdraw_decision",
]

_log = structlog.get_logger("aer.services.decisions")

SUBJECT_DECISION: Final = "decision"

# What each action is called in a sentence — on the journal, on the trade form, in the
# reason a revision writes on the row it supersedes.
ACTION_WORDS: Final[dict[DecisionAction, str]] = {
    DecisionAction.BUY: "open a position",
    DecisionAction.ADD: "add to the position",
    DecisionAction.TRIM: "trim the position",
    DecisionAction.SELL: "close the position",
    DecisionAction.HOLD: "keep holding",
    DecisionAction.PASS: "not act on this",
}

# Which trades can carry out which decisions. A sale cannot carry out a buy, and a dividend
# carries out nothing; the pairing is refused rather than recorded and left for a reviewer
# to puzzle over.
_CARRIED_OUT_BY: Final[dict[DecisionAction, frozenset[TransactionKind]]] = {
    DecisionAction.BUY: frozenset({TransactionKind.BUY}),
    DecisionAction.ADD: frozenset({TransactionKind.BUY}),
    DecisionAction.TRIM: frozenset({TransactionKind.SELL}),
    DecisionAction.SELL: frozenset({TransactionKind.SELL}),
    DecisionAction.HOLD: frozenset(),
    DecisionAction.PASS: frozenset(),
}


async def record_decision(
    session: AsyncSession,
    *,
    actor: User,
    thesis: Thesis,
    action: DecisionAction,
    statement: str,
    basis: str,
    security: Security | None = None,
    portfolio: Portfolio | None = None,
    size_statement: str | None = None,
    horizon_months: int | None = None,
    exit_plan: str | None = None,
    review_by: date | None = None,
    decided_at: datetime | None = None,
    supersedes: Decision | None = None,
) -> Decision:
    """Write one decision down, before the outcome.

    Raises:
        ConflictError: If the thesis is retired, or belongs to somebody else. A retired
            thesis is a record of what was believed; a decision about it now would be a
            decision about a view nobody holds.
        ValidationError: If the statement or the basis is blank, or the horizon is not a
            positive number of months.
    """
    if thesis.is_retired:
        message = (
            f"The thesis {thesis.title!r} was retired on {thesis.retired_at:%Y-%m-%d}. A "
            "decision is about a view somebody holds; write a new thesis first."
        )
        raise ConflictError(message, context={"thesis_id": str(thesis.id)})
    if thesis.user_id != actor.id:
        message = "A decision is recorded by the person whose thesis it acts on."
        raise ConflictError(message, context={"thesis_id": str(thesis.id)})
    if not statement.strip():
        message = "A decision states what was decided; this one states nothing."
        raise ValidationError(message, context={"field": "statement"})
    if not basis.strip():
        message = (
            "A decision needs a stated basis — what you read, saw or reasoned that led to "
            "it. A decision with no grounds is the entry the post-trade review cannot score."
        )
        raise ValidationError(message, context={"field": "basis"})
    if horizon_months is not None and horizon_months <= 0:
        message = "An intended holding period is a positive number of months."
        raise ValidationError(message, context={"field": "horizon_months"})

    judgement = Judgement(
        kind=JudgementKind.DECISION,
        held_by=actor.email,
        held_at=decided_at or datetime.now(UTC),
        basis=basis.strip(),
        supersedes_id=supersedes.judgement_id if supersedes is not None else None,
    )
    session.add(judgement)
    await session.flush()

    decision = Decision(
        judgement_id=judgement.id,
        thesis_id=thesis.id,
        portfolio_id=portfolio.id if portfolio is not None else None,
        security_id=security.id if security is not None else None,
        action=action,
        statement=statement.strip(),
        size_statement=_blank_to_none(size_statement),
        horizon_months=horizon_months,
        exit_plan=_blank_to_none(exit_plan),
        review_by=review_by,
    )
    session.add(decision)
    await session.flush()
    # Read back through the loaders, for the reason `theses.add_premise` gives: the
    # judgement arrives by the joined load, and assigning the relationship would fire the
    # one-to-one backref's load on an async session.
    await session.refresh(judgement)
    loaded = await session.scalar(
        select(Decision)
        .options(selectinload(Decision.transactions), selectinload(Decision.security))
        .where(Decision.judgement_id == judgement.id)
    )
    assert loaded is not None
    decision = loaded

    await _record(
        session,
        actor=actor.email,
        event_type="decision.recorded",
        decision_id=decision.judgement_id,
        payload={
            "decision_id": str(decision.judgement_id),
            "thesis_id": str(thesis.id),
            "action": action.value,
            "statement": decision.statement,
            "basis": judgement.basis,
            "decided_at": judgement.held_at.isoformat(),
            "security_id": str(security.id) if security is not None else None,
            "portfolio_id": str(portfolio.id) if portfolio is not None else None,
            "size_statement": decision.size_statement,
            "horizon_months": decision.horizon_months,
            "exit_plan": decision.exit_plan,
            "review_by": review_by.isoformat() if review_by else None,
            "supersedes_id": str(supersedes.judgement_id) if supersedes is not None else None,
        },
    )
    _log.info(
        "decision.recorded",
        decision_id=str(decision.judgement_id),
        thesis_id=str(thesis.id),
        action=action.value,
    )
    return decision


async def withdraw_decision(
    session: AsyncSession, *, decision: Decision, actor: User, reason: str
) -> Decision:
    """The person no longer stands by this, and says why. The row stays.

    Raises:
        ConflictError: If it is already withdrawn.
        ValidationError: If the reason is blank.
    """
    judgement = decision.judgement
    if judgement.is_withdrawn:
        message = "This decision was already withdrawn, and the reason given then stands."
        raise ConflictError(message, context={"decision_id": str(decision.judgement_id)})
    if not reason.strip():
        message = (
            "Withdrawing a decision needs a reason. A decision given up without saying why "
            "is the least reviewable row the journal could hold."
        )
        raise ValidationError(message, context={"field": "reason"})

    judgement.withdrawn_at = datetime.now(UTC)
    judgement.withdrawn_reason = reason.strip()
    await session.flush()

    await _record(
        session,
        actor=actor.email,
        event_type="decision.withdrawn",
        decision_id=decision.judgement_id,
        payload={
            "decision_id": str(decision.judgement_id),
            "thesis_id": str(decision.thesis_id),
            "reason": judgement.withdrawn_reason,
        },
    )
    return decision


async def revise_decision(
    session: AsyncSession,
    *,
    decision: Decision,
    actor: User,
    thesis: Thesis,
    action: DecisionAction,
    statement: str,
    basis: str,
    security: Security | None = None,
    portfolio: Portfolio | None = None,
    size_statement: str | None = None,
    horizon_months: int | None = None,
    exit_plan: str | None = None,
    review_by: date | None = None,
) -> Decision:
    """A new decision that supersedes this one, which is withdrawn as superseded.

    Never an edit. The earlier row keeps what was decided then; the new row says what is
    decided now and points at what it replaced, once, through the judgement's own
    supersession link.

    Raises:
        ConflictError: If the decision is already withdrawn or already superseded.
    """
    already = await session.scalar(
        select(Judgement.id).where(Judgement.supersedes_id == decision.judgement_id)
    )
    if already is not None:
        message = "This decision was already superseded once, and the history cannot fork."
        raise ConflictError(message, context={"decision_id": str(decision.judgement_id)})
    if decision.judgement.is_withdrawn:
        message = "A withdrawn decision is not revised; record a new one."
        raise ConflictError(message, context={"decision_id": str(decision.judgement_id)})

    revised = await record_decision(
        session,
        actor=actor,
        thesis=thesis,
        action=action,
        statement=statement,
        basis=basis,
        security=security,
        portfolio=portfolio,
        size_statement=size_statement,
        horizon_months=horizon_months,
        exit_plan=exit_plan,
        review_by=review_by,
        supersedes=decision,
    )
    await withdraw_decision(
        session,
        decision=decision,
        actor=actor,
        reason=f"Superseded by a later decision: {revised.statement}",
    )
    return revised


async def carry_out(
    session: AsyncSession, *, transaction: Transaction, decision: Decision, actor: User
) -> Transaction:
    """The trade says which decision it carried out (ADR 0104 §2).

    Raises:
        ValidationError: If the trade is not one that could carry this decision out — the
            wrong kind for the action, a different security, a withdrawn decision, or a
            trade already attributed to another decision.
    """
    if transaction.decision_id is not None and transaction.decision_id != decision.judgement_id:
        message = "This trade already carries out a different decision."
        raise ValidationError(message, context={"transaction_id": str(transaction.attestation_id)})
    if decision.judgement.is_withdrawn:
        message = "A withdrawn decision is not carried out. Record the decision that was."
        raise ValidationError(message, context={"decision_id": str(decision.judgement_id)})
    permitted = _CARRIED_OUT_BY[decision.action]
    if transaction.kind not in permitted:
        message = (
            f"A {transaction.kind.value} cannot carry out a decision to "
            f"{ACTION_WORDS[decision.action]}."
        )
        raise ValidationError(
            message,
            context={"kind": transaction.kind.value, "action": decision.action.value},
        )
    if (
        decision.security_id is not None
        and transaction.security_id is not None
        and decision.security_id != transaction.security_id
    ):
        message = "This trade is in a different security from the one the decision names."
        raise ValidationError(message, context={"decision_id": str(decision.judgement_id)})

    transaction.decision_id = decision.judgement_id
    await session.flush()
    # The decision's own list of trades was loaded before this one joined it, and a loader
    # does not overwrite a collection already in the identity map; reload it so the caller
    # holding the decision sees the trade it just attributed.
    await session.refresh(decision, attribute_names=["transactions"])
    await _record(
        session,
        actor=actor.email,
        event_type="decision.carried_out",
        decision_id=decision.judgement_id,
        payload={
            "decision_id": str(decision.judgement_id),
            "transaction_id": str(transaction.attestation_id),
            "kind": transaction.kind.value,
            "trade_date": transaction.trade_date.isoformat(),
        },
    )
    return transaction


# -- Reading ---------------------------------------------------------------------------------


def _loaded() -> Any:
    return (
        selectinload(Decision.thesis),
        selectinload(Decision.security),
        selectinload(Decision.transactions),
    )


async def decisions_for(
    session: AsyncSession, *, user_id: uuid.UUID, withdrawn: bool = False
) -> list[Decision]:
    """This person's decisions, newest first."""
    condition = (
        Judgement.withdrawn_at.is_not(None) if withdrawn else Judgement.withdrawn_at.is_(None)
    )
    rows = await session.scalars(
        select(Decision)
        .join(Judgement, Judgement.id == Decision.judgement_id)
        .join(Thesis, Thesis.id == Decision.thesis_id)
        .options(*_loaded())
        .where(Thesis.user_id == user_id, condition)
        .order_by(Judgement.held_at.desc())
    )
    return list(rows)


async def decision_of(
    session: AsyncSession, decision_id: uuid.UUID, *, user_id: uuid.UUID
) -> Decision | None:
    """One decision of this person's, or ``None`` for both "no such" and "not yours"."""
    found: Decision | None = await session.scalar(
        select(Decision)
        .join(Thesis, Thesis.id == Decision.thesis_id)
        .options(*_loaded())
        .where(Decision.judgement_id == decision_id, Thesis.user_id == user_id)
    )
    return found


async def decisions_of_thesis(session: AsyncSession, thesis: Thesis) -> list[Decision]:
    """Every decision taken on one thesis, newest first, withdrawn ones included."""
    rows = await session.scalars(
        select(Decision)
        .join(Judgement, Judgement.id == Decision.judgement_id)
        .options(*_loaded())
        .where(Decision.thesis_id == thesis.id)
        .order_by(Judgement.held_at.desc())
    )
    return list(rows)


async def open_for_the_book(session: AsyncSession, *, user_id: uuid.UUID) -> list[Decision]:
    """The decisions a trade could carry out: held, and of a kind that moves the book."""
    return [
        row for row in await decisions_for(session, user_id=user_id) if row.action.moves_the_book
    ]


async def reviews_due(session: AsyncSession, *, user_id: uuid.UUID, today: date) -> list[Decision]:
    """Held decisions a person said they would look at again by a date that has passed."""
    return [
        row
        for row in await decisions_for(session, user_id=user_id)
        if row.review_by is not None and row.review_by <= today
    ]


def _blank_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


# -- The chain -------------------------------------------------------------------------------


async def _record(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    decision_id: uuid.UUID,
) -> None:
    """One link on the chain, correlated to the decision (ADR 0072's subject columns)."""
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous=previous,
            subject_kind=SUBJECT_DECISION,
            subject_id=decision_id,
        )
    )
    await session.flush()
