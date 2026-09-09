"""One run, written out whole, for somebody else to read.

The operator asked for this directly after the first acceptance pass: *"in the background,
each run has a highly detailed and comprehensive log of what is happening and the results
with any useful info. This is so that I can then take that log and give it to Claude to
analyse and help identify causes and issues quicker."*

They already had two things that were nearly it and were not it. `aer diagnose` prints a
per-step readout — everything a person needs to see what happened, and nothing a reader
who was not there can use to answer *why*. `just diagnose-run` writes a JSON export, but it
is a hand-written SQL script scoped to drafting, it needs Docker, and it was built for one
investigation. Neither answers "the comps step returned nothing — what did it read?"

**Everything here is a read.** No fetch, no model call, no spend, and nothing is computed
that the run did not already record. Running it on a finished run a month later produces
the same bytes, because every field is a stored one.

**What it deliberately leaves out**, and why each would be a defect rather than a feature:

* **Credentials.** Nothing here reads settings. `aer.logging` redacts by name and by shape
  as a backstop; not putting them in the document in the first place is the actual control.
* **Licensed vendor series.** Price bars and corporate actions may not be republished
  (ADR 0030 as amended), and a document the operator pastes into a chat is a republication.
  Derived figures — a market capitalisation, a beta — are the operator's to publish and are
  here. `aer purge-licensed` exists because this line matters.
* **Archived request and response bodies.** Their content hashes are here, which is the
  address `aer.storage` reads them back from. A single drafting response is megabytes and
  twenty of them would make a document nobody can open, let alone paste.
* **The report's prose.** The rendered document is its own artefact and the operator has
  it. What is here is the *record around* the prose — what each section was refused for,
  what it was dealt, what it cost — which is the half a reader cannot reconstruct.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import ClaimKind
from aer.db.models import (
    Approval,
    Calculation,
    Citation,
    Claim,
    Disagreement,
    Evaluation,
    FinancialFact,
    Job,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
    SourceDocument,
)
from aer.services.step_diagnostic import RunDiagnostic, run_diagnostic
from aer.version import version

__all__ = ["EXPORT_SCHEMA", "RunExport", "export_run"]

# Bumped when a reader would be wrong to assume the previous shape. A document with no
# version is a document whose age nobody can tell.
EXPORT_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class RunExport:
    """The document, and the one-line summary the command prints over it."""

    document: dict[str, Any]

    @property
    def job_id(self) -> str:
        return str(self.document["run"]["job_id"])

    @property
    def summary(self) -> str:
        run = self.document["run"]
        counts = self.document["counts"]
        return (
            f"{run['job_id']} — {run['status']} — £{run['spend_gbp']} — "
            f"{counts['steps']} step(s), {counts['model_calls']} model call(s), "
            f"{counts['sections']} section(s), {counts['claims']} claim(s)"
        )


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _exchanges(diagnostic: RunDiagnostic) -> list[dict[str, Any]]:
    return [
        {
            "step": step.key,
            "attempt": step.attempt,
            "role": exchange.agent_role,
            "model": exchange.model,
            "effort": exchange.effort,
            "input_tokens": exchange.input_tokens,
            "output_tokens": exchange.output_tokens,
            # The half a reader most often wants and the diagnostic prints in passing:
            # `schema_rejected` is a paid call that produced nothing, and the first
            # acceptance pass had one nobody could see without reading the worker's log.
            "stop_reason": exchange.stop_reason,
            "latency_ms": exchange.latency_ms,
            "request_sha256": exchange.request_sha256,
            "response_sha256": exchange.response_sha256,
        }
        for step in diagnostic.steps
        for exchange in step.exchanges
    ]


def _steps(diagnostic: RunDiagnostic) -> list[dict[str, Any]]:
    return [
        {
            "key": step.key,
            "sequence": step.sequence,
            "status": step.status.value,
            "attempt": step.attempt,
            "cost_gbp": _decimal(step.cost_gbp),
            "elapsed_seconds": step.elapsed_seconds,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "finished_at": step.finished_at.isoformat() if step.finished_at else None,
            "error": step.error,
            # The step's own recorded output, whole. This is the field that answers most
            # "why did it do that?" questions — the peer set as approved, what
            # `acquire_prices` stored, the estimate's own arithmetic — and it is already
            # bounded, because a step that wanted to record megabytes records a hash.
            "output": step.output,
        }
        for step in diagnostic.steps
    ]


async def _sections(session: AsyncSession, *, job_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(ReportSection)
        .where(ReportSection.job_id == job_id)
        .order_by(ReportSection.position, ReportSection.section_key)
    )
    return [
        {
            "key": row.section_key,
            "status": row.status.value,
            "confidence": float(row.confidence) if row.confidence is not None else None,
            # Why a section is thin or absent, in the words the platform used at the time.
            "note": row.low_confidence_reason,
        }
        for row in rows
    ]


async def _evaluations(session: AsyncSession, *, job_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Evaluation).where(Evaluation.job_id == job_id).order_by(Evaluation.metric)
    )
    return [
        {
            "metric": row.metric,
            "value": _decimal(row.value),
            "threshold": _decimal(row.threshold),
            # Tri-state as the table stores it: `None` is "not exercised", which is never a
            # pass and never a failure.
            "passed": row.passed,
            "details": row.details,
        }
        for row in rows
    ]


async def _approvals(session: AsyncSession, *, job_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Approval).where(Approval.job_id == job_id).order_by(Approval.decided_at)
    )
    return [
        {
            "gate": row.gate.value,
            "decision": row.decision.value,
            "actor_user_id": str(row.actor_user_id),
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            # What was on screen when it was approved. A reader comparing this against the
            # payload's hash today can tell whether the approval still covers the run.
            "payload_hash": row.payload_hash,
            "notes": row.notes,
        }
        for row in rows
    ]


async def _disagreements(session: AsyncSession, *, job_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Disagreement).where(Disagreement.job_id == job_id).order_by(Disagreement.created_at)
    )
    return [
        {
            "topic": row.topic,
            "kind": row.kind.value,
            "rule": row.rule.value,
            "resolution": row.resolution.value if row.resolution else None,
            "resolved_by": row.resolved_by.value if row.resolved_by else None,
            "material": row.material,
            "rationale": row.resolution_rationale,
            "detail": row.detail,
        }
        for row in rows
    ]


async def _calculations(session: AsyncSession, *, job_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(Calculation)
        .where(Calculation.job_id == job_id)
        .order_by(Calculation.created_at, Calculation.id)
    )
    return [
        {
            "name": row.name,
            "formula": row.formula,
            "function_ref": row.function_ref,
            "code_version": row.code_version,
            "output_value": _decimal(row.output_value),
            "output_unit": row.output_unit,
            "period": row.period_label,
            # Every input with its source, which is what makes a figure walkable back to a
            # filing without the reader holding a database open.
            "inputs": row.inputs,
        }
        for row in rows
    ]


async def _counts(
    session: AsyncSession, *, job_id: uuid.UUID, request_id: uuid.UUID
) -> dict[str, int]:
    section_ids = select(ReportSection.id).where(ReportSection.job_id == job_id).scalar_subquery()
    claims = await session.scalar(
        select(func.count()).select_from(Claim).where(Claim.report_section_id.in_(section_ids))
    )
    numeric = await session.scalar(
        select(func.count())
        .select_from(Claim)
        .where(Claim.report_section_id.in_(section_ids), Claim.kind == ClaimKind.NUMERIC)
    )
    citations = await session.scalar(
        select(func.count())
        .select_from(Citation)
        .where(
            Citation.claim_id.in_(select(Claim.id).where(Claim.report_section_id.in_(section_ids)))
        )
    )
    verified = await session.scalar(
        select(func.count())
        .select_from(Citation)
        .where(
            Citation.claim_id.in_(select(Claim.id).where(Claim.report_section_id.in_(section_ids))),
            Citation.excerpt_verified.is_(True),
        )
    )
    documents = await session.scalar(
        select(func.count())
        .select_from(SourceDocument)
        .where(SourceDocument.work_order_id == request_id)
    )
    quarantined = await session.scalar(
        select(func.count())
        .select_from(SourceDocument)
        .where(
            SourceDocument.work_order_id == request_id,
            SourceDocument.quarantined.is_(True),
        )
    )
    # By company rather than by job: a fact is the company's and outlives the run that
    # fetched it, which is exactly why `_filed_share_count` could read one an earlier run
    # had written.
    facts = await session.scalar(
        select(func.count())
        .select_from(FinancialFact)
        .where(
            FinancialFact.source_document_id.in_(
                select(SourceDocument.id).where(SourceDocument.work_order_id == request_id)
            )
        )
    )
    return {
        "claims": claims or 0,
        "numeric_claims": numeric or 0,
        "citations": citations or 0,
        "citations_verified": verified or 0,
        "source_documents": documents or 0,
        "source_documents_quarantined": quarantined or 0,
        "financial_facts": facts or 0,
    }


async def export_run(session: AsyncSession, *, job_id: uuid.UUID) -> RunExport:
    """Everything this run recorded, as one JSON-serialisable document.

    Raises:
        ValueError: If there is no such run. A missing run is the caller's mistake, and an
            empty document would look like a run that did nothing.
    """
    job = await session.get(Job, job_id)
    if job is None:
        message = f"No run {job_id}."
        raise ValueError(message)

    diagnostic = await run_diagnostic(session, job_id=job_id)
    request = await session.get(ResearchRequest, job.work_order_id)
    plan = (
        await session.scalar(select(ResearchPlan).where(ResearchPlan.id == job.plan_id))
        if job.plan_id
        else None
    )

    steps = _steps(diagnostic)
    exchanges = _exchanges(diagnostic)
    sections = await _sections(session, job_id=job_id)
    counts = await _counts(session, job_id=job_id, request_id=job.work_order_id)

    document: dict[str, Any] = {
        "schema": EXPORT_SCHEMA,
        "exported_by": version(),
        "run": {
            "job_id": str(job.id),
            "work_order_id": str(job.work_order_id),
            "status": diagnostic.status.value,
            "workflow_version": diagnostic.workflow_version,
            "code_version": diagnostic.code_version,
            "step_mode": diagnostic.step_mode,
            "spend_gbp": _decimal(diagnostic.spend_gbp),
            "next_step": diagnostic.next_step,
            "not_reached": list(diagnostic.not_reached),
        },
        "subject": None
        if request is None
        else {
            "company_name": request.company_name,
            "ticker": request.ticker,
            "exchange": request.exchange,
            "as_of_date": request.work_order.as_of_date.isoformat(),
            "point_in_time": request.work_order.point_in_time,
            "analysis_mode": request.analysis_mode.value,
            "max_cost_gbp": _decimal(request.work_order.max_cost_gbp),
        },
        "plan": None
        if plan is None
        else {
            "estimated_cost_gbp": _decimal(plan.estimated_cost_gbp),
            "estimated_runtime_seconds": plan.estimated_runtime_seconds,
            "planned_sources": plan.planned_sources,
            "known_risks": plan.known_risks,
            "plan": plan.plan,
        },
        "steps": steps,
        "model_calls": exchanges,
        "sections": sections,
        "evaluations": await _evaluations(session, job_id=job_id),
        "approvals": await _approvals(session, job_id=job_id),
        "disagreements": await _disagreements(session, job_id=job_id),
        "calculations": await _calculations(session, job_id=job_id),
        "counts": {
            "steps": len(steps),
            "model_calls": len(exchanges),
            "sections": len(sections),
            **counts,
        },
        "omitted": {
            "credentials": "never read by this export",
            "licensed_series": (
                "price bars and corporate actions are not republishable (ADR 0030); "
                "figures derived from them are here"
            ),
            "archived_payloads": "addressed by content hash; read them with the store",
            "report_prose": "the rendered document is its own artefact",
        },
    }
    return RunExport(document=document)
