"""Approval gates: what a human agreed to, and in what order.

**An approval records a hash of exactly what was displayed.** Not a timestamp and a user
id — those say somebody clicked something. The hash says *what*. A plan that changed after
approval is a plan nobody approved, and without the hash there is no way to tell the
difference, which makes the gate theatre.

**The order is enforced, not assumed.** Gate 2 cannot be approved before gate 1. A
sufficiently determined operator could otherwise approve the final report of a run whose
plan was never agreed, which is precisely the outcome the gates exist to prevent.

**Approving twice is refused.** An approval is a decision, not a state to be re-asserted.
A second one would either be a duplicate — noise in the audit trail — or a change of mind,
which is a different thing and needs its own vocabulary before it needs an implementation.
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
from aer.errors import ValidationError

__all__ = [
    "GATE_ORDER",
    "ApprovalRecorded",
    "approvals_for_job",
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
) -> ApprovalRecorded:
    """Record a decision at a gate.

    Args:
        payload_hash: A hash of exactly what the operator was shown. Produced by
            :func:`payload_hash_for` from the same object the template rendered.

    Raises:
        ValidationError: If this gate has already been decided, or if an earlier gate has
            not been approved yet.
    """
    if not payload_hash:
        message = (
            "An approval must record a hash of what was displayed. Without it, an approval "
            "of a plan that has since changed is indistinguishable from one that has not."
        )
        raise ValidationError(message, context={"gate": gate.value})

    await _refuse_if_already_decided(session, job=job, gate=gate)
    await _refuse_if_out_of_order(session, job=job, gate=gate)

    approval = Approval(
        work_order_id=job.work_order_id,
        request_id=job.request_id,
        job_id=job.id,
        gate=gate,
        decision=decision,
        actor_user_id=actor.id,
        notes=notes,
        payload_hash=payload_hash,
    )
    session.add(approval)
    await session.flush()

    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    event = AuditEvent.create_linked(
        actor=actor.email,
        event_type=f"approval.{decision.value.lower()}",
        payload={
            "gate": gate.value,
            "decision": decision.value,
            "payload_hash": payload_hash,
            "approval_id": str(approval.id),
        },
        previous=previous,
        request_id=job.request_id,
        job_id=job.id,
    )
    session.add(event)
    await session.flush()

    _log.info(
        "approval.recorded",
        job_id=str(job.id),
        gate=gate.value,
        decision=decision.value,
        actor=actor.email,
    )
    return ApprovalRecorded(approval=approval, audit_event=event)


async def _refuse_if_already_decided(session: AsyncSession, *, job: Job, gate: GateKind) -> None:
    existing = await session.scalar(
        select(Approval).where(Approval.job_id == job.id, Approval.gate == gate)
    )
    if existing is None:
        return

    message = (
        f"The {gate.value} gate was already {existing.decision.value.lower()} at "
        f"{existing.decided_at.isoformat()}. An approval is a decision, not a state to be "
        "re-asserted; changing one needs a new run, not a second approval of the old."
    )
    raise ValidationError(
        message,
        context={
            "gate": gate.value,
            "existing_decision": existing.decision.value,
            "approval_id": str(existing.id),
        },
    )


async def _refuse_if_out_of_order(session: AsyncSession, *, job: Job, gate: GateKind) -> None:
    """Refuse a gate whose predecessors have not been approved.

    Conditional gates that never fired are skipped rather than treated as blocking — a run
    that needed no peer-set decision should not be unable to reach its final gate.
    """
    if gate not in GATE_ORDER:  # pragma: no cover -- GateKind is closed
        return

    decided = {
        row.gate: row
        for row in await session.scalars(select(Approval).where(Approval.job_id == job.id))
    }

    for earlier in GATE_ORDER[: GATE_ORDER.index(gate)]:
        if earlier in _CONDITIONAL and earlier not in decided:
            continue
        approval = decided.get(earlier)
        if approval is None or approval.decision is not Decision.APPROVED:
            state = "not been reached" if approval is None else approval.decision.value.lower()
            message = (
                f"The {gate.value} gate cannot be decided while the {earlier.value} gate has "
                f"{state}. Gates are passed in order, so that nothing is approved on the "
                "strength of a step nobody agreed to."
            )
            raise ValidationError(
                message,
                context={"gate": gate.value, "blocked_by": earlier.value},
            )


async def approvals_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[Approval]:
    """Every decision recorded for a run, oldest first."""
    rows = await session.scalars(
        select(Approval).where(Approval.job_id == job_id).order_by(Approval.decided_at)
    )
    return list(rows)


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
