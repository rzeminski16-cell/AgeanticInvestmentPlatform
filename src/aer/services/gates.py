"""What a gate sealed, kept equal to what the operator is shown.

Gate 2 approves a payload the revise step seals: sections, the disagreements still open,
the triggers. The review page recomputes the same payload live and the approval carries
that hash; the engine then compares the approval against the seal. **One operator action
changes the payload after the seal** — settling a disagreement on the review page moves it
out of the open list — and until this module existed nothing re-sealed. The first live run
of the confirmation runbook settled a challenge, approved what the page showed, and stopped
with "what this run sealed and what the review page shows have drifted apart": the approval
matched the page and the seal matched neither, and no decision could release it.

Two rules follow, and a recovery for a run already caught:

* **Settling before the gate is decided re-seals.** The step's recorded hash moves to the
  payload as it now stands, so an approval taken *before* the settle no longer matches —
  which is what `final_gate_payload` always intended, "settling one invalidates a stale
  approval" — and one taken *after* does.
* **Settling after the gate is decided is refused.** The approval was of the payload with
  that conflict open; changing the record underneath a recorded decision would make the
  approval an approval of something else, and a second decision is refused by design.
* **`reseal_final_gate` re-derives the seal from the record**, for a run stopped on the
  drift. It adds nothing: the payload is what the run's own rows say, and the audit chain
  records the move. Whether the recorded approval then matches is reported, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import GateKind, JobStatus
from aer.db.models import Approval, AuditEvent, Job, JobStep, User
from aer.errors import ConflictError, ValidationError
from aer.services.approvals import payload_hash_for
from aer.workflow.registry import resolve_workflow
from aer.workflow.workflows.vertical_slice_v1 import seal_step_for

__all__ = ["Reseal", "refuse_settling_after_decision", "reseal_final_gate"]

_log = structlog.get_logger("aer.services.gates")

RESEALED_EVENT = "gate.resealed"


@dataclass(frozen=True, slots=True)
class Reseal:
    """What re-sealing found and did."""

    gate: GateKind
    previous_hash: str
    current_hash: str
    # ``None`` when nothing has been decided at this gate yet.
    approval_matches: bool | None

    @property
    def changed(self) -> bool:
        return self.previous_hash != self.current_hash


async def refuse_settling_after_decision(session: AsyncSession, *, job: Job) -> None:
    """Refuse to change gate 2's payload once gate 2 has been decided.

    Raises:
        ValidationError: The final gate has a recorded decision. The message says why the
            order matters and what the operator can still do.
    """
    approval = await _final_approval(session, job)
    if approval is None:
        return
    message = (
        f"The final gate was already {approval.decision.value.lower()} at "
        f"{approval.decided_at.isoformat()}, over this conflict as an open one. Settling it "
        "now would change what that decision was taken on, and a decision is not "
        "re-asserted: settle before you decide, or record the resolution in the review "
        "the report gets afterwards."
    )
    raise ValidationError(message, context={"job_id": str(job.id), "gate": GateKind.FINAL.value})


async def reseal_final_gate(session: AsyncSession, *, job: Job, actor: User, reason: str) -> Reseal:
    """Move the final gate's seal to the payload as the run's record now stands.

    Raises:
        ConflictError: The run is executing, or has finished. A worker mid-step may be
            about to write the seal itself; a finished run has nothing left to gate.
        ValidationError: The run has not sealed its final gate yet — there is nothing to
            move.
    """
    if job.status is JobStatus.RUNNING:
        message = "This run is executing now; wait for it to stop before re-sealing."
        raise ConflictError(message, context={"job_id": str(job.id)})
    if job.status.is_terminal:
        message = f"This run has already {job.status.value.lower()}; there is no gate to re-seal."
        raise ConflictError(message, context={"job_id": str(job.id)})

    step_key = seal_step_for(GateKind.FINAL.value)
    row = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job.id, JobStep.step_key == step_key)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    if row is None or row.status is not JobStatus.SUCCEEDED:
        message = "This run has not sealed its final gate yet, so there is nothing to re-seal."
        raise ValidationError(message, context={"job_id": str(job.id), "step": step_key})

    builder = resolve_workflow(job.workflow_version).gate_payload()
    current = payload_hash_for(await builder(session, job=job, gate=GateKind.FINAL.value))
    previous = str((row.output_ref or {}).get("payload_hash", ""))
    approval = await _final_approval(session, job)
    matches = None if approval is None else approval.payload_hash == current

    if previous == current:
        return Reseal(
            gate=GateKind.FINAL,
            previous_hash=previous,
            current_hash=current,
            approval_matches=matches,
        )

    # Reassigned rather than mutated in place: the column is JSON, and SQLAlchemy sees a
    # new value, not a changed key.
    row.output_ref = {**(row.output_ref or {}), "payload_hash": current}
    await _append_event(
        session,
        actor=actor,
        job=job,
        payload={
            "job_id": str(job.id),
            "gate": GateKind.FINAL.value,
            "from": previous,
            "to": current,
            "reason": reason,
        },
    )
    _log.info(
        "gate.resealed",
        job_id=str(job.id),
        gate=GateKind.FINAL.value,
        previous=previous[:12],
        current=current[:12],
        actor=actor.email,
    )
    return Reseal(
        gate=GateKind.FINAL,
        previous_hash=previous,
        current_hash=current,
        approval_matches=matches,
    )


async def _final_approval(session: AsyncSession, job: Job) -> Approval | None:
    approval: Approval | None = await session.scalar(
        select(Approval).where(Approval.job_id == job.id, Approval.gate == GateKind.FINAL)
    )
    return approval


async def _append_event(
    session: AsyncSession, *, actor: User, job: Job, payload: dict[str, str]
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor.email,
            event_type=RESEALED_EVENT,
            payload=dict(payload),
            previous=previous,
            request_id=job.work_order_id,
            job_id=job.id,
        )
    )
    await session.flush()
