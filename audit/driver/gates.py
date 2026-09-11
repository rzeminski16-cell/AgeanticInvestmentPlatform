"""Clearing a gate the way the web route does, with the policy deciding.

The payload is the platform's own (`gate_payload`), the hash is the platform's own
(`payload_hash_for`), and the row is written by the platform's own `record_decision`. The
driver adds two checks the page cannot make for itself: that the hash the run sealed is the
hash the payload builder reproduces now (seal drift), and that the policy's decision is
written down with its reasons.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Decision, GateKind, JobStatus
from aer.db.models import Approval, Job, JobStep, User
from aer.services import approvals as approval_service
from aer.services.assumptions import assumptions_for_request, confirm, propose
from aer.services.evaluations import evaluations_for_job
from aer.services.gates import reseal_final_gate
from aer.workflow.registry import resolve_workflow
from aer.workflow.workflows.vertical_slice_v1 import seal_step_for, step_output
from audit.driver.policy import (
    FinalGateFacts,
    GateVerdict,
    decide_assumptions,
    decide_final,
    decide_peer_set,
    decide_plan,
    decide_sector,
    decide_theme_set,
    decide_unmapped,
)
from audit.driver.recorder import Recorder
from audit.subjects import Subject

__all__ = ["GateOutcome", "clear_pending_gate"]

_STEP_OUTPUT_GATES = frozenset(
    {
        GateKind.SECTOR_SPECIALIST,
        GateKind.PEER_SET,
        GateKind.THEME_SET,
        GateKind.UNMAPPED_CONCEPTS,
    }
)
_OPERATOR = "operator:audit"


@dataclass(frozen=True, slots=True)
class GateOutcome:
    gate: str
    approved: bool
    rationale: str
    stop_reason: str | None = None
    resealed: bool = False


async def _paused_step(session: AsyncSession, job_id: uuid.UUID) -> JobStep | None:
    row: JobStep | None = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.status == JobStatus.AWAITING_APPROVAL)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    return row


async def _already_decided(
    session: AsyncSession, job_id: uuid.UUID, gate: GateKind
) -> Approval | None:
    row: Approval | None = await session.scalar(
        select(Approval).where(Approval.job_id == job_id, Approval.gate == gate)
    )
    return row


async def clear_pending_gate(
    session: AsyncSession,
    *,
    job: Job,
    subject: Subject,
    actor: User,
    cap_gbp: Decimal,
    recorder: Recorder,
) -> GateOutcome:
    """Decide the gate the run is waiting at, as the policy says, and write it down."""
    gate = await approval_service.pending_gate(session, job)
    if gate is None:
        return GateOutcome(
            gate="",
            approved=False,
            rationale="waiting with no pending gate",
            stop_reason="no pending gate",
        )

    paused = await _paused_step(session, job.id)
    context = ((paused.error or {}).get("context", {}) if paused is not None else {}) or {}

    # A second pause at the final gate after it was approved: the seal drifted, or the
    # evidence check refused. Neither is a decision to record again.
    prior = await _already_decided(session, job.id, gate)
    if prior is not None:
        if (
            gate is GateKind.FINAL
            and context.get("live_hash")
            and context.get("live_hash") == context.get("approved_hash")
        ):
            reseal = await reseal_final_gate(
                session,
                job=job,
                actor=actor,
                reason="audit driver: the live payload matches the approval; moving the seal",
            )
            await session.commit()
            recorder.event("gate.resealed", gate=gate.value, detail=str(reseal)[:300])
            return GateOutcome(
                gate=gate.value,
                approved=True,
                rationale="resealed to the approved payload",
                resealed=True,
            )
        recorder.event("gate.blocked_after_approval", gate=gate.value, context=context)
        return GateOutcome(
            gate=gate.value,
            approved=False,
            rationale="the gate paused again after its approval",
            stop_reason=f"post-approval pause at {gate.value}: {str(context)[:300]}",
        )

    builder = resolve_workflow(job.workflow_version).gate_payload()
    payload = await builder(session, job=job, gate=gate.value)
    digest = approval_service.payload_hash_for(payload)
    recorder.write_json(
        f"gate-{gate.value}.json", {"payload": payload, "hash": digest, "pause_context": context}
    )

    if gate in _STEP_OUTPUT_GATES or gate is GateKind.PLAN:
        produced = await step_output(session, job_id=job.id, step_key=seal_step_for(gate.value))
        recorded = str(produced.get("payload_hash", "") or "")
        if recorded and recorded != digest:
            recorder.event("gate.seal_drift", gate=gate.value, recorded=recorded, rebuilt=digest)
            return GateOutcome(
                gate=gate.value,
                approved=False,
                rationale="seal drift",
                stop_reason="the sealed hash does not match the rebuilt payload",
            )

    verdict = await _decide(
        session, gate=gate, payload=payload, subject=subject, job=job, cap_gbp=cap_gbp
    )

    if gate is GateKind.ASSUMPTIONS and verdict.approve:
        for row in (*verdict.supply, *verdict.amend):
            await propose(
                session,
                request_id=job.work_order_id,
                name=row.name,
                value=row.value,
                unit=row.unit,
                justification=row.justification,
                proposed_by=_OPERATOR,
                by_human=True,
                job_id=job.id,
            )
        for assumption in await assumptions_for_request(session, job.work_order_id):
            if not assumption.approved:
                await confirm(session, assumption=assumption, actor=actor)
        await session.flush()
        payload = await builder(session, job=job, gate=gate.value)
        digest = approval_service.payload_hash_for(payload)
        recorder.write_json(
            f"gate-{gate.value}-confirmed.json", {"payload": payload, "hash": digest}
        )

    recorder.event(
        "gate.decided",
        gate=gate.value,
        approved=verdict.approve,
        rationale=verdict.rationale,
        findings=list(verdict.findings),
        stop_reason=verdict.stop_reason,
        payload_hash=digest,
    )
    if not verdict.approve:
        return GateOutcome(
            gate=gate.value,
            approved=False,
            rationale=verdict.rationale,
            stop_reason=verdict.stop_reason,
        )

    await approval_service.record_decision(
        session,
        job=job,
        gate=gate,
        decision=Decision.APPROVED,
        actor=actor,
        payload_hash=digest,
        notes=f"{verdict.rationale} Findings: {'; '.join(verdict.findings) or 'none'}"[:4000],
    )
    await session.commit()
    return GateOutcome(gate=gate.value, approved=True, rationale=verdict.rationale)


async def _decide(
    session: AsyncSession,
    *,
    gate: GateKind,
    payload: Mapping[str, Any],
    subject: Subject,
    job: Job,
    cap_gbp: Decimal,
) -> GateVerdict:
    if gate is GateKind.PLAN:
        return decide_plan(payload, subject, cap_gbp=cap_gbp)
    if gate is GateKind.SECTOR_SPECIALIST:
        return decide_sector(payload, subject)
    if gate is GateKind.PEER_SET:
        return decide_peer_set(payload, subject)
    if gate is GateKind.THEME_SET:
        return decide_theme_set(payload, subject)
    if gate is GateKind.UNMAPPED_CONCEPTS:
        extract = await step_output(session, job_id=job.id, step_key="extract")
        return decide_unmapped(
            payload, subject, facts_chosen=int(extract.get("facts_chosen", 0) or 0)
        )
    if gate is GateKind.ASSUMPTIONS:
        return decide_assumptions(payload, subject)
    if gate is GateKind.FINAL:
        rows = await evaluations_for_job(session, job.id)
        failed = tuple(str(row.metric) for row in rows if row.passed is False)
        facts = FinalGateFacts(
            sections=tuple(payload.get("sections", [])),
            triggers=tuple(payload.get("triggers", [])),
            escalations=tuple(payload.get("escalations", [])),
            revisions=tuple(payload.get("revisions", [])),
            failed_metrics=failed,
        )
        return decide_final(facts)
    return GateVerdict(
        approve=False, rationale=f"no policy for gate {gate.value}", stop_reason="unknown gate"
    )
