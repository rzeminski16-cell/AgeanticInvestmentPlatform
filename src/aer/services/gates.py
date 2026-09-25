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
* **`remeasure_checks` runs the checks again** (ADR 0123): the `validate` step and every
  step after it are put back to be executed, as attempt *n+1* on the same rows, so a
  corrected figure is re-checked for the cost of those steps rather than the whole run.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.core.enums import GateKind, JobStatus
from aer.db.models import Approval, AuditEvent, Job, JobStep, ReportSection, RevisionNote, User
from aer.db.models.revision_note import DISPOSITION_REQUESTED, SCOPE_FINAL_GATE
from aer.db.models.section_definition import SKILL
from aer.errors import ConflictError, ValidationError
from aer.services import approvals as approval_service
from aer.services.approvals import payload_hash_for
from aer.services.revision import OPERATOR_DIMENSION
from aer.workflow.pauses import LIVE_PAYLOAD_GATES
from aer.workflow.registry import resolve_workflow
from aer.workflow.workflows.vertical_slice_v1 import seal_step_for

__all__ = [
    "MEASURE_STEP",
    "REDRAFT_EVENT",
    "REDRAFT_STEP",
    "Redraft",
    "Remeasure",
    "Reseal",
    "refuse_settling_after_decision",
    "remeasure_checks",
    "request_redraft",
    "reseal_final_gate",
    "reseal_gate",
]

_log = structlog.get_logger("aer.services.gates")

RESEALED_EVENT = "gate.resealed"
REMEASURE_EVENT = "run.remeasure_requested"
REDRAFT_EVENT = "run.redraft_requested"

# The step that writes the run's evaluation rows. Re-measuring starts here and takes every
# step after it with it; `tests/test_gate_remedies.py` pins it to the workflow's own order.
MEASURE_STEP = "validate"

# The step that answers an operator's redraft, measures the checks again and seals the gate
# over the result. A redraft starts here, after the checks' first reading, because the
# critique loop and the red team need not run again for one section.
REDRAFT_STEP = "revise"


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


async def reseal_gate(
    session: AsyncSession, *, job: Job, actor: User, reason: str, gate: GateKind = GateKind.FINAL
) -> Reseal:
    """Move a gate's seal to the payload as the run's record now stands.

    The final gate is the one whose seal moves in practice — settling a disagreement is the
    operator action that moves it — but every gate sealed by a step can drift the same way,
    and the engine names the case for each; so the console's control re-seals whichever
    gate the run is waiting at (ADR 0123 part 3).

    Raises:
        ConflictError: The run is executing, or has finished. A worker mid-step may be
            about to write the seal itself; a finished run has nothing left to gate.
        ValidationError: The run has not sealed this gate yet — there is nothing to move —
            or the gate's approval is verified against the live payload, so it has no seal.
    """
    if job.status is JobStatus.RUNNING:
        message = "This run is executing now; wait for it to stop before re-sealing."
        raise ConflictError(message, context={"job_id": str(job.id)})
    if job.status.is_terminal:
        message = f"This run has already {job.status.value.lower()}; there is no gate to re-seal."
        raise ConflictError(message, context={"job_id": str(job.id)})
    if gate in LIVE_PAYLOAD_GATES:
        message = (
            f"The {gate.value} gate is decided on the page as it renders now, not on a seal, "
            "so there is nothing to re-seal; open it and decide again."
        )
        raise ValidationError(message, context={"job_id": str(job.id), "gate": gate.value})

    step_key = seal_step_for(gate.value)
    row = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job.id, JobStep.step_key == step_key)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    if row is None or row.status is not JobStatus.SUCCEEDED:
        which = "its final gate" if gate is GateKind.FINAL else f"the {gate.value} gate"
        message = f"This run has not sealed {which} yet, so there is nothing to re-seal."
        raise ValidationError(message, context={"job_id": str(job.id), "step": step_key})

    builder = resolve_workflow(job.workflow_version).gate_payload()
    current = payload_hash_for(await builder(session, job=job, gate=gate.value))
    previous = str((row.output_ref or {}).get("payload_hash", ""))
    approval = await approval_service.current_decision(session, job.id, gate)
    matches = None if approval is None else approval.payload_hash == current

    if previous == current:
        return Reseal(
            gate=gate, previous_hash=previous, current_hash=current, approval_matches=matches
        )

    # Reassigned rather than mutated in place: the column is JSON, and SQLAlchemy sees a
    # new value, not a changed key.
    row.output_ref = {**(row.output_ref or {}), "payload_hash": current}
    await _append_event(
        session,
        actor=actor,
        job=job,
        event_type=RESEALED_EVENT,
        payload={
            "job_id": str(job.id),
            "gate": gate.value,
            "from": previous,
            "to": current,
            "reason": reason,
        },
    )
    _log.info(
        "gate.resealed",
        job_id=str(job.id),
        gate=gate.value,
        previous=previous[:12],
        current=current[:12],
        actor=actor.email,
    )
    return Reseal(gate=gate, previous_hash=previous, current_hash=current, approval_matches=matches)


async def reseal_final_gate(session: AsyncSession, *, job: Job, actor: User, reason: str) -> Reseal:
    """:func:`reseal_gate` for the final gate — the terminal command's and the driver's call."""
    return await reseal_gate(session, job=job, actor=actor, reason=reason, gate=GateKind.FINAL)


@dataclass(frozen=True, slots=True)
class Remeasure:
    """Which steps re-measuring put back to be executed, in workflow order."""

    invalidated: tuple[str, ...]


async def remeasure_checks(
    session: AsyncSession, *, job: Job, actor: User, reason: str
) -> Remeasure:
    """Put the checks — and everything sealed over them — back to be executed (ADR 0123).

    The rows stay; their status stops being ``SUCCEEDED``, so the engine runs each as
    attempt *n+1* the next time the run executes. The caller records the resume and
    re-enqueues; this function owns the record of what was invalidated and why.

    Raises:
        ConflictError: The run is executing, or has finished. A worker mid-step may be
            about to write the very rows; a finished report is superseded (ADR 0116), not
            re-measured.
        ValidationError: The run has not measured its checks yet.
    """
    if job.status is JobStatus.RUNNING:
        message = "This run is executing now; wait for it to stop before re-measuring."
        raise ConflictError(message, context={"job_id": str(job.id)})
    if job.status.is_terminal:
        message = (
            f"This run has already {job.status.value.lower()}. A finished report is not "
            "re-measured; it is superseded, with the reason recorded."
        )
        raise ConflictError(message, context={"job_id": str(job.id)})

    rows = list(
        await session.scalars(
            select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.sequence)
        )
    )
    measured = next((row for row in rows if row.step_key == MEASURE_STEP), None)
    if measured is None or measured.status is not JobStatus.SUCCEEDED:
        message = "This run has not measured its checks yet, so there is nothing to re-measure."
        raise ValidationError(message, context={"job_id": str(job.id), "step": MEASURE_STEP})

    invalidated: list[str] = []
    for row in rows:
        if row.sequence >= measured.sequence and row.status is not JobStatus.QUEUED:
            row.status = JobStatus.QUEUED
            row.finished_at = None
            row.error = None
            invalidated.append(row.step_key)

    await _append_event(
        session,
        actor=actor,
        job=job,
        event_type=REMEASURE_EVENT,
        payload={"job_id": str(job.id), "reason": reason, "invalidated": ", ".join(invalidated)},
    )
    _log.info(
        "run.remeasure_requested",
        job_id=str(job.id),
        invalidated=invalidated,
        actor=actor.email,
    )
    return Remeasure(invalidated=tuple(invalidated))


@dataclass(frozen=True, slots=True)
class Redraft:
    """The request recorded, and which steps it put back to be executed."""

    section_key: str
    invalidated: tuple[str, ...]


async def request_redraft(
    session: AsyncSession, *, job: Job, section_key: str, reason: str, actor: User
) -> Redraft:
    """Ask for one section to be redrafted at the final gate (roadmap §3.19 item 75).

    A check that refuses a sentence at the final gate used to leave two ways on: approve
    against the check, which the round's own definition counts as a rescue, or reject the
    run and pay for another. This is the third. The request is recorded as a revision note
    with the operator's reason, and the revise step and every step after it go back to be
    executed as their next attempt: the revise step answers the request instead of running
    the critique loop again, the checks are measured on the redrafted text, the gate is
    sealed over it, and the run comes back to this gate. The cost is one section's draft
    and the steps after it, never the run.

    Raises:
        ConflictError: The run is executing, has finished, is not waiting at its final
            gate, or has a final decision already — a decided gate is superseded (ADR 0123),
            not redrafted under.
        ValidationError: The reason is empty, the section is not this run's, or it is a
            custom section, which executes under the policy pinned when gate 1 showed it
            and is never redrafted by the platform (ADR 0037).
    """
    reason = " ".join(reason.split())
    if not reason:
        message = (
            "Say what the redraft should put right, in your own words — the refusal it "
            "answers, or what reads wrong. The writer is given exactly this."
        )
        raise ValidationError(message, context={"job_id": str(job.id)})
    if job.status is not JobStatus.AWAITING_APPROVAL:
        message = (
            "A section is redrafted while the run waits at its final gate, and this one is not."
        )
        raise ConflictError(message, context={"job_id": str(job.id), "status": job.status.value})
    if await approval_service.pending_gate(session, job) is not GateKind.FINAL:
        message = "This run is waiting at an earlier gate; the draft does not exist yet."
        raise ConflictError(message, context={"job_id": str(job.id)})
    if await _final_approval(session, job) is not None:
        message = (
            "The final gate has a decision already. A decided gate is decided again on what "
            "it shows, not redrafted under."
        )
        raise ConflictError(message, context={"job_id": str(job.id)})

    section = await session.scalar(
        select(ReportSection)
        .where(ReportSection.job_id == job.id, ReportSection.section_key == section_key)
        .options(selectinload(ReportSection.definition))
    )
    if section is None:
        message = "That section is not part of this run."
        raise ValidationError(message, context={"job_id": str(job.id), "section": section_key})
    if section.definition.origin == SKILL:
        message = (
            f"{section.definition.title} is a custom section, and it runs under the policy "
            "its skill had when the plan was approved; the platform never redrafts one. "
            "Change the skill and start a new run, or approve with the section as it is."
        )
        raise ValidationError(message, context={"job_id": str(job.id), "section": section_key})

    session.add(
        RevisionNote(
            job_id=job.id,
            scope=SCOPE_FINAL_GATE,
            section_key=section_key,
            dimension=OPERATOR_DIMENSION,
            severity=5,
            statement=reason[:4000],
            disposition=DISPOSITION_REQUESTED,
        )
    )

    rows = list(
        await session.scalars(
            select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.sequence)
        )
    )
    revised = next((row for row in rows if row.step_key == REDRAFT_STEP), None)
    if revised is None or revised.status is not JobStatus.SUCCEEDED:
        message = "This run has not reached its revise step, so there is no draft to redraft."
        raise ConflictError(message, context={"job_id": str(job.id), "step": REDRAFT_STEP})
    invalidated: list[str] = []
    for row in rows:
        if row.sequence >= revised.sequence and row.status is not JobStatus.QUEUED:
            row.status = JobStatus.QUEUED
            row.finished_at = None
            row.error = None
            invalidated.append(row.step_key)

    await _append_event(
        session,
        actor=actor,
        job=job,
        event_type=REDRAFT_EVENT,
        payload={
            "job_id": str(job.id),
            "section": section_key,
            "reason": reason[:4000],
            "invalidated": ", ".join(invalidated),
        },
    )
    _log.info(
        "run.redraft_requested",
        job_id=str(job.id),
        section=section_key,
        invalidated=invalidated,
        actor=actor.email,
    )
    return Redraft(section_key=section_key, invalidated=tuple(invalidated))


async def _final_approval(session: AsyncSession, job: Job) -> Approval | None:
    return await approval_service.current_decision(session, job.id, GateKind.FINAL)


async def _append_event(
    session: AsyncSession,
    *,
    actor: User,
    job: Job,
    event_type: str,
    payload: dict[str, str],
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor.email,
            event_type=event_type,
            payload=dict(payload),
            previous=previous,
            request_id=job.work_order_id,
            job_id=job.id,
        )
    )
    await session.flush()
