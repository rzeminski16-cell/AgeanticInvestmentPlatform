"""Trying one skill against a finished run's evidence, without re-running the research.

Task 43. The authoring loop §2.12 describes — write a section, see it appear with its own
cited evidence — is worthless at a full run per iteration. A dry run executes **one**
section against evidence a previous run already acquired, so the loop is a minute and a
few pence rather than an afternoon and a pound.

Three properties make it a rehearsal rather than a shortcut:

**It is the real execution path.** :func:`~aer.skills.execution.execute_custom_section`,
the real composer, the real projection, the real claim and citation services, the real
renderer. A dry run that used a simplified path would be a preview of the simplified
path, and the section an author signed off would not be the section a run produces.

**It cannot write into the run it reads.** Isolation is structural rather than careful:
the dry run gets its **own job**, its own plan, its own pin and its own section row, and
nothing it does has the source job's id on it. The source run's evidence is read —
facts and sources by request, calculations by the source job's id, passed explicitly —
and nothing is read that a real run would not have been allowed to read.

**It spends real money, so it is metered and capped like everything else.** The model
call is a real call: the budget guard runs before it against the request's own cap, the
cost rows are written by the same meter, and the spend counts towards the request. A
rehearsal whose cost was invisible would be the one way to get under a cap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.core.enums import JobStatus, SkillKind
from aer.core.hashing import sha256_hex
from aer.db.models import (
    Company,
    Job,
    JobStep,
    PlanSkillPin,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    Skill,
    SkillVersion,
    WorkOrder,
)
from aer.db.models.plan_skill_pin import PLANNED
from aer.errors import AerError, ValidationError
from aer.render.markdown import render_markdown
from aer.services.skills import current_version
from aer.skills.execution import execute_custom_section
from aer.skills.resolution import (
    compose_for_version,
    estimate_custom_section_cost,
    project_custom_section,
)
from aer.version import git_sha
from aer.workflow.engine import BudgetGuard

if TYPE_CHECKING:
    from aer.config import Settings
    from aer.providers.protocol import LLMProvider
    from aer.providers.router import Router
    from aer.storage.protocol import ArtefactStore

__all__ = ["DRY_RUN_STEP", "DRY_RUN_WORKFLOW", "DryRunOutcome", "dry_run_skill"]

_log = structlog.get_logger("aer.services.skill_dry_run")

# What a dry run's job and plan are stamped with. A marked workflow version rather than a
# boolean column: every surface that groups runs by workflow already exists, and a run
# nobody can tell from a real one is a run that will eventually be mistaken for one.
DRY_RUN_WORKFLOW = "skill_dry_run_v1"
DRY_RUN_STEP = "dry_run_section"


class DryRunRefusedError(AerError):
    """The dry run could not be attempted at all — no evidence, wrong kind, no budget."""

    code = "skill_dry_run_refused"
    http_status = 422


@dataclass(slots=True)
class DryRunOutcome:
    """What one rehearsal produced: the section as it would have appeared, and its receipts."""

    job_id: uuid.UUID
    source_job_id: uuid.UUID
    skill_key: str
    skill_version: int
    section_key: str
    status: SectionStatus
    markdown: str
    footnote_count: int
    attempts: int
    claims_recorded: int
    insufficient_evidence: bool
    evidence_truncated: bool
    problems: list[str]
    cost_gbp: Decimal
    estimated_cost_gbp: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": str(self.job_id),
            "source_job_id": str(self.source_job_id),
            "skill_key": self.skill_key,
            "skill_version": self.skill_version,
            "section_key": self.section_key,
            "status": self.status.value,
            "markdown": self.markdown,
            "footnote_count": self.footnote_count,
            "attempts": self.attempts,
            "claims": self.claims_recorded,
            "insufficient_evidence": self.insufficient_evidence,
            "evidence_truncated": self.evidence_truncated,
            "problems": list(self.problems),
            "cost_gbp": str(self.cost_gbp),
            "estimated_cost_gbp": str(self.estimated_cost_gbp),
        }


async def dry_run_skill(
    session: AsyncSession,
    *,
    key: str,
    source_job: Job,
    settings: Settings,
    provider: LLMProvider,
    router: Router,
    store: ArtefactStore,
) -> DryRunOutcome:
    """Execute one skill's section against a finished run's evidence.

    Args:
        source_job: The run whose stored evidence the section may cite. Read only — the
            dry run writes exclusively against the job it creates.

    Raises:
        ValidationError: The key is unknown, or names a skill that produces no section.
        DryRunRefusedError: The projected cost would take the request past its cap.
    """
    skill = await session.scalar(select(Skill).where(Skill.key == key))
    version = await current_version(session, key=key)
    if skill is None or version is None:
        message = f"No skill is named {key!r}, so there is nothing to try."
        raise ValidationError(message, context={"key": key})

    if skill.kind != SkillKind.CUSTOM_SECTION.value:
        message = (
            f"{key!r} is a {skill.kind} skill: it composes into an existing agent's "
            "prompt rather than producing a section of its own, so there is no section "
            "for a dry run to render."
        )
        raise ValidationError(message, context={"key": key, "kind": skill.kind})

    request = await session.get(ResearchRequest, source_job.request_id)
    if request is None:  # pragma: no cover -- a job cannot exist without its request
        message = "The chosen run has no research request."
        raise ValidationError(message, context={"job_id": str(source_job.id)})

    composed = compose_for_version(version, settings=settings)
    estimate = estimate_custom_section_cost(
        model=router.resolve("custom_section").model,
        token_budget=composed.token_budget,
        usd_to_gbp=settings.usd_to_gbp,
    )

    # Its own cost cap, checked before the call rather than after: the guard the workflow
    # engine uses, against the same request ceiling, so a rehearsal cannot spend what a
    # run would have been refused.
    guard = BudgetGuard(
        per_run_cap_gbp=Decimal(str(request.max_cost_gbp)),
        monthly_cap_gbp=settings.monthly_budget_gbp,
    )

    definition = await project_custom_section(
        session, skill=skill, version=version, composed=composed
    )

    # A rehearsal is its own unit of budgeted work: its own job, its own step, its own
    # spend. Since ADR 0068 that means its own work order, and it has to be its own for a
    # concrete reason as well as a definitional one — pins are unique per (work order,
    # skill), so a dry run sharing the source run's work order could not rehearse a skill
    # that run had already pinned, which is every skill worth rehearsing.
    work_order = WorkOrder(
        user_id=request.user_id,
        tool="research",
        subject_kind="company",
        subject_id=request.company_id,
        as_of_date=request.as_of_date,
        point_in_time=request.point_in_time,
        max_cost_gbp=request.max_cost_gbp,
        status=request.status,
    )
    session.add(work_order)
    await session.flush()

    job = Job(
        work_order_id=work_order.id,
        request_id=request.id,
        workflow_version=DRY_RUN_WORKFLOW,
        code_version=git_sha() or "unknown",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()

    try:
        await guard.check(session, job=job, projected_gbp=estimate)
    except AerError as refused:
        job.status = JobStatus.BUDGET_EXCEEDED
        job.finished_at = datetime.now(UTC)
        await session.flush()
        message = (
            f"A dry run of {key!r} is projected to cost £{estimate}, which this "
            f"request's cap will not carry. {refused}"
        )
        raise DryRunRefusedError(
            message, context={"key": key, "estimated_gbp": str(estimate)}
        ) from refused

    pin, step, section = await _stage(
        session,
        job=job,
        source_job=source_job,
        skill=skill,
        version=version,
        definition=definition,
        composed=composed,
        estimate=estimate,
    )

    context = AgentContext(
        session=session,
        provider=provider,
        router=router,
        settings=settings,
        store=store,
        job_step=step,
    )
    execution = await execute_custom_section(
        context,
        section=section,
        pin=pin,
        request=request,
        # The evidence is the source run's. Facts and sources belong to the request and
        # are visible either way; calculations belong to a job, and without this the
        # rehearsal would silently have no figures to cite.
        evidence_job_id=source_job.id,
    )

    rendered = await _render(session, job=job, request=request)

    step.status = JobStatus.SUCCEEDED
    step.finished_at = datetime.now(UTC)
    step.cost_gbp = context.spend_gbp
    step.output_ref = execution.as_dict()
    job.status = JobStatus.SUCCEEDED
    job.finished_at = datetime.now(UTC)
    job.total_cost_gbp = context.spend_gbp
    await session.flush()

    _log.info(
        "skill.dry_run",
        key=key,
        version=version.version,
        job_id=str(job.id),
        source_job_id=str(source_job.id),
        status=execution.status.value,
        claims=execution.claims_recorded,
        cost_gbp=str(context.spend_gbp),
    )

    return DryRunOutcome(
        job_id=job.id,
        source_job_id=source_job.id,
        skill_key=key,
        skill_version=version.version,
        section_key=section.section_key,
        status=execution.status,
        markdown=rendered[0],
        footnote_count=rendered[1],
        attempts=execution.attempts,
        claims_recorded=execution.claims_recorded,
        insufficient_evidence=execution.insufficient_evidence,
        evidence_truncated=execution.evidence_truncated,
        problems=list(execution.problems),
        cost_gbp=context.spend_gbp,
        estimated_cost_gbp=estimate,
    )


async def _stage(
    session: AsyncSession,
    *,
    job: Job,
    source_job: Job,
    skill: Skill,
    version: SkillVersion,
    definition: SectionDefinition,
    composed: Any,
    estimate: Decimal,
) -> tuple[PlanSkillPin, JobStep, ReportSection]:
    """Everything the rehearsal needs to look like a run: a plan, a pin, a step, a section.

    All of it on the dry run's own job. The source run is named only in the plan's summary
    and the step's input hash, which is where a reader looks to answer "what was this a
    rehearsal *of*?".
    """
    plan = ResearchPlan(
        request_id=job.request_id,
        workflow_version=DRY_RUN_WORKFLOW,
        plan={"summary": f"Dry run of {skill.key} against run {source_job.id}", "sections": []},
        planned_sources=[],
        known_risks=[],
        estimated_cost_gbp=estimate,
        estimated_runtime_seconds=0,
    )
    session.add(plan)
    await session.flush()
    job.plan_id = plan.id

    pin = _pin_for(
        work_order_id=job.work_order_id,
        skill=skill,
        version=version,
        composed=composed,
        estimate=estimate,
    )
    step = JobStep(
        job_id=job.id,
        step_key=DRY_RUN_STEP,
        sequence=0,
        status=JobStatus.RUNNING,
        attempt=0,
        idempotency_key=f"{job.id}:{DRY_RUN_STEP}",
        input_hash=sha256_hex(f"{version.content_hash}:{source_job.id}"),
        started_at=datetime.now(UTC),
    )
    section = ReportSection(
        job_id=job.id,
        section_definition_id=definition.id,
        section_key=definition.key,
        position=definition.position,
        status=SectionStatus.PENDING,
    )
    session.add_all([pin, step, section])
    await session.flush()
    return pin, step, section


def _pin_for(
    *,
    work_order_id: uuid.UUID,
    skill: Skill,
    version: SkillVersion,
    composed: Any,
    estimate: Decimal,
) -> PlanSkillPin:
    """The pin the dry run executes under — the same snapshot a plan would have stored.

    A real pin on a real (dry-run) plan rather than an object held in memory: the
    execution path reads its policy from the pin, and a rehearsal running against
    something a plan could not have produced would be rehearsing a different thing.
    """
    return PlanSkillPin(
        work_order_id=work_order_id,
        skill_id=skill.id,
        skill_version_id=version.id,
        status=PLANNED,
        reason="",
        min_sources=composed.evidence.min_sources,
        requires_primary=composed.evidence.requires_primary,
        max_tier=composed.evidence.max_tier,
        allow_forward_looking=composed.evidence.allow_forward_looking,
        token_budget=composed.token_budget,
        granted_tools=sorted(composed.allowed_tools),
        clamps=[
            {
                "field": clamp.field,
                "requested": clamp.requested,
                "effective": clamp.effective,
                "reason": clamp.reason,
            }
            for clamp in composed.clamps
        ],
        estimated_cost_gbp=estimate,
    )


async def _render(session: AsyncSession, *, job: Job, request: ResearchRequest) -> tuple[str, int]:
    """The dry-run job's one section, through the report renderer.

    The same renderer the report uses, over a job that holds exactly this section — so
    what an author reads is the section as the document would carry it, footnotes and
    all, rather than a preview of the content dictionary.
    """
    company = await session.scalar(
        select(Company).where(
            Company.ticker == request.ticker, Company.exchange == request.exchange
        )
    )
    rendered = await render_markdown(session, job=job, request=request, company=company)
    return rendered.markdown, rendered.footnote_count
