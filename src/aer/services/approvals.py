"""Approval gates: what a human agreed to, and in what order.

**An approval records a hash of exactly what was displayed.** Not a timestamp and a user
id — those say somebody clicked something. The hash says *what*. A plan that changed after
approval is a plan nobody approved, and without the hash there is no way to tell the
difference, which makes the gate theatre.

**The order is enforced, not assumed.** Gate 2 cannot be approved before gate 1. A
sufficiently determined operator could otherwise approve the final report of a run whose
plan was never agreed, which is precisely the outcome the gates exist to prevent.

**Approving twice is refused, unless the page moved.** An approval is a decision, not a
state to be re-asserted, so a second decision over the same content is refused as a
duplicate. A second decision over content that has changed under the first is the change
of mind ADR 0123 gives its vocabulary: a new row that names the decision it supersedes,
recorded as ``AMENDED`` when it approves what the page shows now and ``REJECTED`` when it
does not. The superseded row is never touched; the workflow reads the newest row nothing
supersedes.

**A rejection ends the run.** Recording ``REJECTED`` at a run gate asks the cancellation
service to stop the run, with the rejection as the reason — which is what the button has
always said it would do, and what lets the request be started again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Decision, GateKind, JobStatus
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import Approval, AuditEvent, Job, JobStep, User
from aer.errors import ConflictError, ValidationError
from aer.services import cancellation as cancellation_service

__all__ = [
    "GATE_ORDER",
    "PASSING_DECISIONS",
    "ApprovalRecorded",
    "approvals_for_job",
    "current_decision",
    "current_decisions",
    "payload_hash_for",
    "record_decision",
]

_log = structlog.get_logger("aer.services.approvals")

# The order gates must be passed in. A gate cannot be decided until every gate before it
# has been approved.
GATE_ORDER: Final[tuple[GateKind, ...]] = (
    GateKind.PLAN,
    GateKind.UNMAPPED_CONCEPTS,
    GateKind.PEER_SET,
    GateKind.SECTOR_SPECIALIST,
    GateKind.THEME_SET,
    GateKind.ASSUMPTIONS,
    GateKind.BUDGET,
    GateKind.FINAL,
)

# Gates that only fire under particular conditions. Absent ones do not block a later gate,
# because a run that never needed a peer set should not be stuck waiting to approve one.
_CONDITIONAL: Final[frozenset[GateKind]] = frozenset(
    {
        GateKind.UNMAPPED_CONCEPTS,
        GateKind.PEER_SET,
        GateKind.SECTOR_SPECIALIST,
        # Conditional because a run whose proposer named no themes — or whose model call
        # failed — has no edges to defend, and must not wait to confirm an empty list.
        GateKind.THEME_SET,
        # Conditional because a run whose sector mandate blocks a discounted cash flow has
        # no assumptions to confirm, and a bank must not sit waiting to approve a forecast
        # it is never going to be given.
        GateKind.ASSUMPTIONS,
        GateKind.BUDGET,
    }
)

# The decisions a gate is passed on. An amended decision is an approval taken after the
# content changed under an earlier one (ADR 0123); every reader of "is this gate approved?"
# reads this rather than testing for APPROVED alone.
PASSING_DECISIONS: Final[frozenset[Decision]] = frozenset({Decision.APPROVED, Decision.AMENDED})

# The gate names a paused step may carry. A membership test rather than a try/except around
# `GateKind(...)`, so a step whose recorded detail is from an older workflow version falls
# back to the gate order instead of raising in a read path.
_GATE_VALUES: Final[frozenset[str]] = frozenset(gate.value for gate in GateKind)


@dataclass(frozen=True, slots=True)
class ApprovalRecorded:
    """The decision, and the audit event that witnessed it."""

    approval: Approval
    audit_event: AuditEvent


def payload_hash_for(payload: Any) -> str:
    """Hash exactly what a gate displayed.

    Canonical JSON, so the same content hashes the same however it was assembled. This is
    the value the workflow compares against before continuing past a gate.
    """
    return sha256_hex(canonical_json(payload))


async def record_decision(
    session: AsyncSession,
    *,
    job: Job,
    gate: GateKind,
    decision: Decision,
    actor: User,
    payload_hash: str,
    notes: str | None = None,
    supersedes: Approval | None = None,
) -> ApprovalRecorded:
    """Record a decision at a gate.

    Args:
        payload_hash: A hash of exactly what the operator was shown. Produced by
            :func:`payload_hash_for` from the same object the template rendered.
        supersedes: The gate's current decision, when this one replaces it because the
            page moved (ADR 0123). Callers pass ``APPROVED`` or ``REJECTED``; a superseding
            approval is recorded as ``AMENDED`` here, so the vocabulary is written in one
            place.

    Raises:
        ValidationError: If this gate has already been decided over the content it still
            shows, if an earlier gate has not been approved yet, or if ``supersedes`` is
            not the gate's current decision.
    """
    if not payload_hash:
        message = (
            "An approval must record a hash of what was displayed. Without it, an approval "
            "of a plan that has since changed is indistinguishable from one that has not."
        )
        raise ValidationError(message, context={"gate": gate.value})
    if gate not in GATE_ORDER:
        # The thesis gate (ADR 0103). It is decided on a finding rather than on a run step,
        # a monitor pass may open it more than once, and the order below is a research
        # run's — so the monitor service writes its own row, and this refuses to.
        message = (
            f"The {gate.spoken} gate is not part of a run's gate order. It is decided on a "
            "monitor finding, through the monitor service, and never here."
        )
        raise ValidationError(message, context={"gate": gate.value})

    if decision is Decision.AMENDED and supersedes is None:
        message = (
            "An amended decision is one that supersedes another. Decide APPROVED over what the "
            "page shows now and name the decision it replaces; the record then says amended."
        )
        raise ValidationError(message, context={"gate": gate.value})

    current = await current_decision(session, job.id, gate)
    if supersedes is None:
        _refuse_if_already_decided(current, gate=gate)
    else:
        _refuse_unless_superseding_a_stale_decision(
            current, supersedes=supersedes, gate=gate, payload_hash=payload_hash
        )
    await _refuse_if_out_of_order(session, job=job, gate=gate)

    recorded = (
        Decision.AMENDED if supersedes is not None and decision in PASSING_DECISIONS else decision
    )
    approval = Approval(
        work_order_id=job.work_order_id,
        job_id=job.id,
        gate=gate,
        decision=recorded,
        actor_user_id=actor.id,
        notes=notes,
        payload_hash=payload_hash,
        supersedes_id=supersedes.id if supersedes is not None else None,
    )
    session.add(approval)
    await session.flush()

    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    event = AuditEvent.create_linked(
        actor=actor.email,
        event_type=f"approval.{recorded.value.lower()}",
        payload={
            "gate": gate.value,
            "decision": recorded.value,
            "payload_hash": payload_hash,
            "approval_id": str(approval.id),
            "supersedes": str(supersedes.id) if supersedes is not None else None,
        },
        previous=previous,
        request_id=job.work_order_id,
        job_id=job.id,
    )
    session.add(event)
    await session.flush()

    _log.info(
        "approval.recorded",
        job_id=str(job.id),
        gate=gate.value,
        decision=recorded.value,
        supersedes=str(supersedes.id) if supersedes is not None else None,
        actor=actor.email,
    )

    # A rejection ends the run (ADR 0123). The cancellation service makes a run that is
    # waiting terminal at once and lets a moving one stop at its next boundary; a run that
    # has already ended is left as it ended, because the decision changes nothing about it.
    if recorded is Decision.REJECTED and job.status not in cancellation_service.TERMINAL_STATUSES:
        reason = f"Rejected at the {gate.spoken} gate."
        if notes:
            reason = f"{reason} {notes}"
        await cancellation_service.request_cancellation(
            session, job=job, actor=actor, reason=reason[:4000]
        )
    return ApprovalRecorded(approval=approval, audit_event=event)


def _refuse_if_already_decided(existing: Approval | None, *, gate: GateKind) -> None:
    if existing is None:
        return

    message = (
        f"The {gate.spoken} gate was already {existing.decision.value.lower()} at "
        f"{existing.decided_at.isoformat()}. An approval is a decision, not a state to be "
        "re-asserted. If the page has moved since, decide again on what it shows now and "
        "the new decision supersedes this one; if it has not, there is nothing to decide."
    )
    raise ValidationError(
        message,
        context={
            "gate": gate.value,
            "existing_decision": existing.decision.value,
            "approval_id": str(existing.id),
        },
    )


def _refuse_unless_superseding_a_stale_decision(
    current: Approval | None, *, supersedes: Approval, gate: GateKind, payload_hash: str
) -> None:
    """The two conditions a superseding decision must meet (ADR 0123).

    It names the gate's *current* decision — not one already superseded, not another
    gate's — and the page has moved since that decision: the hash it carries differs from
    the one being recorded. A second decision over unchanged content is the re-assertion
    the third rule refuses, whatever it names.
    """
    if current is None or current.id != supersedes.id or supersedes.gate is not gate:
        message = (
            f"The decision named as superseded is not the {gate.spoken} gate's current one. "
            "Open the gate again and decide on what it shows now."
        )
        raise ConflictError(message, context={"gate": gate.value, "supersedes": str(supersedes.id)})
    if supersedes.payload_hash == payload_hash:
        message = (
            f"The {gate.spoken} gate was already {supersedes.decision.value.lower()} over "
            "exactly this content. A decision is not a state to be re-asserted; superseding "
            "one is for when the page has moved under it, and it has not."
        )
        raise ValidationError(
            message,
            context={
                "gate": gate.value,
                "existing_decision": supersedes.decision.value,
                "approval_id": str(supersedes.id),
            },
        )


async def _refuse_if_out_of_order(session: AsyncSession, *, job: Job, gate: GateKind) -> None:
    """Refuse a gate whose predecessors have not been approved.

    Conditional gates that never fired are skipped rather than treated as blocking — a run
    that needed no peer-set decision should not be unable to reach its final gate.
    """
    decided = await current_decisions(session, job.id)

    for earlier in GATE_ORDER[: GATE_ORDER.index(gate)]:
        if earlier in _CONDITIONAL and earlier not in decided:
            continue
        approval = decided.get(earlier)
        if approval is None or approval.decision not in PASSING_DECISIONS:
            state = "not been reached" if approval is None else approval.decision.value.lower()
            message = (
                f"The {gate.spoken} gate cannot be decided while the {earlier.spoken} gate has "
                f"{state}. Gates are passed in order, so that nothing is approved on the "
                "strength of a step nobody agreed to."
            )
            raise ValidationError(
                message,
                context={"gate": gate.value, "blocked_by": earlier.value},
            )


async def approvals_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[Approval]:
    """Every decision recorded for a run, oldest first — superseded ones included."""
    rows = await session.scalars(
        select(Approval).where(Approval.job_id == job_id).order_by(Approval.decided_at)
    )
    return list(rows)


async def current_decisions(session: AsyncSession, job_id: uuid.UUID) -> dict[GateKind, Approval]:
    """The decision that stands at each gate: the newest row nothing supersedes (ADR 0123).

    The one answer to "what was decided here?", so that the gate check, the order check,
    the confirmed-slate readers and the pages cannot disagree about it.
    """
    rows = await approvals_for_job(session, job_id)
    superseded = {row.supersedes_id for row in rows if row.supersedes_id is not None}
    current: dict[GateKind, Approval] = {}
    for row in rows:
        if row.id not in superseded:
            current[row.gate] = row
    return current


async def current_decision(
    session: AsyncSession, job_id: uuid.UUID, gate: GateKind
) -> Approval | None:
    """The decision that stands at one gate, or ``None`` when it has not been decided."""
    return (await current_decisions(session, job_id)).get(gate)


async def pending_gate(session: AsyncSession, job: Job) -> GateKind | None:
    """The gate this run is waiting at, if it is waiting at one.

    **Asked of the run, not of the gate order.** A conditional gate fires only on the runs
    that need it, so no ordering over :data:`GATE_ORDER` can say whether *this* run stopped
    at one — and a console that answered from the order alone sent an operator stuck at the
    financials gate to the draft page, which had nothing to approve. The paused step records
    which gate it paused at, so that is what is read.

    Falls back to the order for a run that is not paused at a step, which is what a caller
    asking "what is next?" of a queued run wants.
    """
    paused = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job.id, JobStep.status == JobStatus.AWAITING_APPROVAL)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    if paused is not None:
        named = (paused.error or {}).get("context", {}).get("gate")
        if named in _GATE_VALUES:
            return GateKind(named)

    decided = {
        row.gate for row in await session.scalars(select(Approval).where(Approval.job_id == job.id))
    }
    for gate in GATE_ORDER:
        if gate in _CONDITIONAL:
            continue
        if gate not in decided:
            return gate
    return None


def utc_now() -> datetime:
    return datetime.now(UTC)
