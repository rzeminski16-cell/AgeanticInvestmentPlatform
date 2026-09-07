"""Drafting one built-in section against a finished run's evidence, under today's prompts.

The built-in twin of :mod:`aer.services.skill_dry_run`. That module exists because an
author cannot afford a full run per iteration of a skill; this one exists because the
platform's own writer cannot either. The confirmation run of 2026-09-05 cost £10.82 to
learn that the writer overran its word budget on eleven first attempts, and the fix —
stating the budget as the limit — is a bet about what the model writes next time, which no
zero-spend replay can settle. A rehearsal settles it for about thirty pence: one section,
the real writer under the prompts as they stand now, the real validator and salvage, the
real claim and citation services, against the evidence a run already paid to acquire.

The same three properties as the dry run, for the same reasons:

**It is the real execution path.** :func:`aer.sections.writing.execute_builtin_section`,
handed the source run's calculations and platform-filled blocks through ``evidence_job_id``
exactly as the custom path is. The plan's approved focus for the section and the run's
pinned operator guidance are carried too, so the rehearsal writes to the same brief the
run did. A rehearsal down a simplified path would be a preview of the simplified path.

**It cannot write into the run it reads.** Its own work order, job, plan, step and section
row; the source run is named in the plan's summary and the step's input hash, and nothing
the rehearsal does carries the source job's id. The source run's rows are read — facts and
sources by request, calculations by the source job's id — and never touched.

**It spends real money, so it is metered and capped like everything else.** The budget
guard runs before the call against the source request's own cap and the month's; the cost
rows are written by the same meter, under the rehearsal's job. And because the writer's
replies are archived beside their calls like any run's, ``aer replay-draft <rehearsal
job>`` can read them back under whatever rule changes next.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.core.enums import JobStatus
from aer.core.hashing import sha256_hex
from aer.core.section_output import prose_word_count
from aer.db.models import (
    Company,
    Job,
    JobStep,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    WorkOrder,
)
from aer.db.models.section_definition import SKILL
from aer.errors import AerError, ValidationError
from aer.render.markdown import render_markdown
from aer.sections.evidence import word_ceiling
from aer.sections.registry import applies_to, create_report_sections, resolve_sections
from aer.sections.writing import execute_builtin_section, policy_of_definition
from aer.services.mandate import mandate_of
from aer.skills.resolution import guidance_from_pins, pinned_skills_for_job
from aer.version import git_sha
from aer.workflow.engine import BudgetGuard, spend_so_far

if TYPE_CHECKING:
    from aer.config import Settings
    from aer.providers.protocol import LLMProvider
    from aer.providers.router import Router
    from aer.storage.protocol import ArtefactStore

__all__ = [
    "REHEARSAL_ESTIMATE_GBP",
    "REHEARSAL_STEP",
    "REHEARSAL_WORKFLOW",
    "RehearsalOutcome",
    "RehearsalRefusedError",
    "rehearse_section",
]

_log = structlog.get_logger("aer.services.section_rehearsal")

# What a rehearsal's job and plan are stamped with — a marked workflow version, for the
# reason the dry run's is: a run nobody can tell from a real one is eventually mistaken
# for one.
REHEARSAL_WORKFLOW: Final = "section_rehearsal_v1"
REHEARSAL_STEP: Final = "rehearse_section"

# The ceiling the guard is asked to clear before the call, not the expected cost. The
# confirmation run's draft step came to £6.34 over 37 writer replies — about 17p a reply —
# and a section is allowed two attempts; the estimate covers two dear ones.
REHEARSAL_ESTIMATE_GBP: Final = Decimal("0.60")


class RehearsalRefusedError(AerError):
    """The rehearsal could not be attempted at all — no such section, wrong kind, no budget."""

    code = "section_rehearsal_refused"
    http_status = 422


@dataclass(slots=True)
class RehearsalOutcome:
    """What one rehearsal produced, and the numbers the operator came for.

    ``words`` is counted over the stored section the way the validator counts a draft, so
    it reads against ``word_budget`` — what the writer was asked for — and
    ``word_ceiling`` — where the validator refuses. ``edits`` is what the salvage or the
    evidence shortfall recorded on the row, in the reader's words, or nothing.
    """

    job_id: uuid.UUID
    source_job_id: uuid.UUID
    section_key: str
    title: str
    status: SectionStatus
    attempts: int
    claims_recorded: int
    words: int
    word_budget: int
    word_ceiling: int
    problems: list[str]
    refusal_causes: dict[str, int]
    edits: str | None
    # The row's own figure (ADR 0099): a float column, carried as stored.
    confidence: float | None
    insufficient_evidence: bool
    evidence_truncated: bool
    evidence_dealt: dict[str, int] | None
    markdown: str
    footnote_count: int
    cost_gbp: Decimal
    estimated_cost_gbp: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": str(self.job_id),
            "source_job_id": str(self.source_job_id),
            "section_key": self.section_key,
            "title": self.title,
            "status": self.status.value,
            "attempts": self.attempts,
            "claims": self.claims_recorded,
            "words": self.words,
            "word_budget": self.word_budget,
            "word_ceiling": self.word_ceiling,
            "problems": list(self.problems),
            "refusal_causes": dict(self.refusal_causes),
            "edits": self.edits,
            "confidence": str(self.confidence) if self.confidence is not None else None,
            "insufficient_evidence": self.insufficient_evidence,
            "evidence_truncated": self.evidence_truncated,
            "evidence_dealt": self.evidence_dealt,
            "footnote_count": self.footnote_count,
            "cost_gbp": str(self.cost_gbp),
            "estimated_cost_gbp": str(self.estimated_cost_gbp),
        }


async def rehearse_section(
    session: AsyncSession,
    *,
    section_key: str,
    source_job: Job,
    settings: Settings,
    provider: LLMProvider,
    router: Router,
    store: ArtefactStore,
) -> RehearsalOutcome:
    """Draft one built-in section against a finished run's evidence, on its own job.

    Args:
        source_job: The run whose stored evidence the section may cite. Read only — the
            rehearsal writes exclusively against the job it creates.

    Raises:
        RehearsalRefusedError: No such section applies to the source run, the section is
            a skill's (the dry run is its rehearsal) or the platform's (nothing to draft),
            or the projected cost would take the request past its cap.
    """
    request = await mandate_of(session, source_job)
    if request is None:  # pragma: no cover -- a job cannot exist without its request
        message = "The chosen run has no research request."
        raise ValidationError(message, context={"job_id": str(source_job.id)})

    definition = await _rehearsable_definition(session, section_key=section_key, request=request)

    # Its own cost cap, checked before the call: the guard the workflow engine uses,
    # against the same request ceiling, so a rehearsal cannot spend what a run would have
    # been refused.
    guard = BudgetGuard(
        per_run_cap_gbp=Decimal(str(request.work_order.max_cost_gbp)),
        monthly_cap_gbp=settings.monthly_budget_gbp,
    )
    work_order = WorkOrder(
        user_id=request.work_order.user_id,
        tool="research",
        subject_kind="company",
        subject_id=request.company_id,
        as_of_date=request.work_order.as_of_date,
        point_in_time=request.work_order.point_in_time,
        max_cost_gbp=request.work_order.max_cost_gbp,
        status=request.work_order.status,
    )
    session.add(work_order)
    await session.flush()
    job = Job(
        work_order_id=work_order.id,
        workflow_version=REHEARSAL_WORKFLOW,
        code_version=git_sha() or "unknown",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    try:
        await guard.check(session, job=job, projected_gbp=REHEARSAL_ESTIMATE_GBP)
    except AerError as refused:
        job.status = JobStatus.BUDGET_EXCEEDED
        job.finished_at = datetime.now(UTC)
        await session.flush()
        message = (
            f"A rehearsal of {section_key!r} is projected to cost up to "
            f"£{REHEARSAL_ESTIMATE_GBP}, which this request's cap will not carry. {refused}"
        )
        raise RehearsalRefusedError(
            message,
            context={"section_key": section_key, "estimated_gbp": str(REHEARSAL_ESTIMATE_GBP)},
        ) from refused

    step, section = await _stage(
        session, job=job, source_job=source_job, request=request, definition=definition
    )
    focus, guidance = await _brief(session, source_job=source_job, section_key=section_key)
    context = AgentContext(
        session=session,
        provider=provider,
        router=router,
        settings=settings,
        store=store,
        job_step=step,
    )
    execution = await execute_builtin_section(
        context,
        section=section,
        request=request,
        focus=focus,
        guidance=guidance,
        # The evidence is the source run's: its calculations, and the platform-filled
        # blocks rendered from them. Facts and sources belong to the request.
        evidence_job_id=source_job.id,
    )
    markdown, footnotes = await _render(session, job=job, request=request)

    # The ledger's figure, not the context's running total: what the meter wrote is what
    # `aer diagnose` and the spend page will show, and the two must not differ by a
    # rounding.
    cost = await spend_so_far(session, job_id=job.id)
    step.status = JobStatus.SUCCEEDED
    step.finished_at = datetime.now(UTC)
    step.cost_gbp = cost
    step.output_ref = execution.as_dict()
    job.status = JobStatus.SUCCEEDED
    job.finished_at = datetime.now(UTC)
    job.total_cost_gbp = cost
    await session.flush()

    policy = policy_of_definition(definition).scaled(request.analysis_mode)
    outcome = RehearsalOutcome(
        job_id=job.id,
        source_job_id=source_job.id,
        section_key=section.section_key,
        title=definition.title,
        status=execution.status,
        attempts=execution.attempts,
        claims_recorded=execution.claims_recorded,
        words=prose_word_count(section.content or {}),
        word_budget=policy.word_budget,
        word_ceiling=word_ceiling(policy.word_budget) if policy.word_budget > 0 else 0,
        problems=list(execution.problems),
        refusal_causes=dict(execution.refusal_causes),
        edits=section.low_confidence_reason,
        confidence=section.confidence,
        insufficient_evidence=execution.insufficient_evidence,
        evidence_truncated=execution.evidence_truncated,
        evidence_dealt=execution.dealt.as_dict() if execution.dealt is not None else None,
        markdown=markdown,
        footnote_count=footnotes,
        cost_gbp=cost,
        estimated_cost_gbp=REHEARSAL_ESTIMATE_GBP,
    )
    _log.info(
        "section.rehearsed",
        section_key=section_key,
        job_id=str(job.id),
        source_job_id=str(source_job.id),
        status=execution.status.value,
        attempts=execution.attempts,
        words=outcome.words,
        word_budget=outcome.word_budget,
        claims=execution.claims_recorded,
        cost_gbp=str(cost),
    )
    return outcome


async def _rehearsable_definition(
    session: AsyncSession, *, section_key: str, request: ResearchRequest
) -> SectionDefinition:
    """The built-in definition this key names, if a writer would draft it for this run."""
    definitions = {d.key: d for d in await resolve_sections(session, request=request)}
    definition = definitions.get(section_key)
    if definition is None:
        known = ", ".join(sorted(definitions))
        message = f"No section is keyed {section_key!r}. The sections are: {known}."
        raise RehearsalRefusedError(message, context={"section_key": section_key})
    if definition.origin == SKILL:
        message = (
            f"{section_key!r} is a skill's section: rehearse it with the skill's own dry "
            "run, from the skill editor, which executes it under its composed policy."
        )
        raise RehearsalRefusedError(message, context={"section_key": section_key})
    if definition.token_budget == 0:
        message = (
            f"{section_key!r} is filled by the platform from the run's records; no writer "
            "is called for it, so there is nothing to rehearse."
        )
        raise RehearsalRefusedError(message, context={"section_key": section_key})
    if not applies_to(definition, request):
        message = f"{section_key!r} does not apply to this run's subject, so no run would draft it."
        raise RehearsalRefusedError(message, context={"section_key": section_key})
    return definition


async def _stage(
    session: AsyncSession,
    *,
    job: Job,
    source_job: Job,
    request: ResearchRequest,
    definition: SectionDefinition,
) -> tuple[JobStep, ReportSection]:
    """Everything the rehearsal needs to look like a run: a plan, a step, a section row.

    All of it on the rehearsal's own job. The plan's ``request_id`` is the mandate's, as
    the dry run's is (`research_plans` hangs off `research_requests`), and its summary
    names the source run, which is where a reader looks to answer "what was this a
    rehearsal *of*?".
    """
    plan = ResearchPlan(
        request_id=request.id,
        workflow_version=REHEARSAL_WORKFLOW,
        plan={
            "summary": f"Rehearsal of {definition.key} against run {source_job.id}",
            "sections": [],
            # Machine-readable as well as in the summary: `aer replay-draft` holds the
            # rehearsal's replies to the evidence they were written against.
            "evidence_job_id": str(source_job.id),
        },
        planned_sources=[],
        known_risks=[],
        estimated_cost_gbp=REHEARSAL_ESTIMATE_GBP,
        estimated_runtime_seconds=0,
    )
    session.add(plan)
    await session.flush()
    job.plan_id = plan.id

    step = JobStep(
        job_id=job.id,
        step_key=REHEARSAL_STEP,
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:{REHEARSAL_STEP}",
        input_hash=sha256_hex(f"{definition.id}:{source_job.id}"),
        started_at=datetime.now(UTC),
    )
    session.add(step)
    await session.flush()
    [section] = await create_report_sections(session, job_id=job.id, definitions=[definition])
    return step, section


async def _brief(
    session: AsyncSession, *, source_job: Job, section_key: str
) -> tuple[str, list[Any]]:
    """The source run's approved focus for this section, and its pinned guidance.

    What the draft step hands the writer beyond the contract (ADR 0091's focus, ADR 0108's
    guidance), read from the source run so the rehearsal writes to the brief the run did.
    A source run with no plan — one that never reached gate 1 — gives an empty brief.
    """
    focus = ""
    if source_job.plan_id is not None:
        plan = await session.get(ResearchPlan, source_job.plan_id)
        if plan is not None:
            focus = next(
                (
                    str(entry.get("focus", ""))
                    for entry in (plan.plan or {}).get("sections", [])
                    if isinstance(entry, dict) and entry.get("key") == section_key
                ),
                "",
            )
    pins = await pinned_skills_for_job(session, job=source_job)
    return focus, guidance_from_pins(pins)


async def _render(session: AsyncSession, *, job: Job, request: ResearchRequest) -> tuple[str, int]:
    """The rehearsal job's one section, through the report renderer — as the document
    would carry it, footnotes and all."""
    company = await session.scalar(
        select(Company).where(
            Company.ticker == request.ticker, Company.exchange == request.exchange
        )
    )
    rendered = await render_markdown(session, job=job, request=request, company=company)
    return rendered.markdown, rendered.footnote_count
