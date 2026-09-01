"""Loading one run's recorded rows into the §2.4 trigger engine's shapes.

The engine in :mod:`aer.core.escalation` is pure — rows in, fired triggers out — and this
module is everything impure about the question: which tables hold the rows, and how each
becomes the engine's shape. Nothing here decides anything. A threshold applied during
loading would be a rule the pure engine's tests cannot see.

**The triggers are computed fresh on every read, never stored.** They are derived data
over rows that are themselves already stored, and the determinism the gate hash depends
on comes from those rows being frozen once the red-team step has run — not from caching a
verdict. A stored trigger row could survive the evidence changing under it, which is the
one failure mode a banner must not have.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.disagreement import ResolvedBy
from aer.core.escalation import (
    ConflictScene,
    CostScene,
    EvidenceTally,
    FiredTrigger,
    MetricScore,
    PolicyClamp,
    SectionScene,
    SourceScene,
    fire_triggers,
)
from aer.db.models import (
    Cost,
    Evaluation,
    Job,
    JobStep,
    ResearchPlan,
    ResearchRequest,
    SourceDocument,
)
from aer.db.models.plan_skill_pin import PLANNED
from aer.db.models.section_definition import SKILL
from aer.sections.registry import section_outcomes, sections_for_job
from aer.services.disagreements import disagreements_for_job
from aer.services.evaluations import evaluations_for_job, section_coverage_for_job
from aer.skills.resolution import pinned_skills_for_job

__all__ = ["cost_scene_for_job", "triggers_for_job"]


async def triggers_for_job(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    cost: CostScene | None = None,
) -> tuple[FiredTrigger, ...]:
    """Every §2.4 trigger that holds for this run, from its recorded rows alone.

    ``cost`` replaces the live spend and cap, and the gate-2 payload always supplies one:
    money is the only input here that is not frozen when the payload is sealed. See
    :func:`cost_scene_for_job`.
    """
    return fire_triggers(
        point_in_time=request.work_order.point_in_time,
        metrics=await _metric_scores(session, job=job),
        sections=await _section_scenes(session, job=job, request=request),
        conflicts=await _conflict_scenes(session, job=job),
        clamps=await _policy_clamps(session, job=job),
        sources=await _source_scenes(session, request=request),
        cost=cost
        if cost is not None
        else await cost_scene_for_job(session, job=job, request=request),
    )


async def cost_scene_for_job(
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    through_step: str | None = None,
) -> CostScene:
    """The run's cap, its approved estimate, and what the cost rows actually sum to.

    The actual figure is the same sum the budget guard enforces against — the ``costs``
    table, nothing derived — so the banner and the hard cap cannot disagree about what
    was spent.

    **``through_step`` bounds the sum to the steps at or before that one, and the gate-2
    payload is built with it.** Spend is the only input to the §2.4 triggers that is not
    frozen when the revise step seals the payload: the verdict step runs after the seal
    and writes a cost row of its own. With a live sum, the cost trigger's evidence carried
    a total that had already moved by the time the review page rendered — so on any run
    above 80% of its cap the sealed hash and the page's hash differed by construction, and
    the gate refused every approval of a payload nobody could ever match. It was correct
    to refuse; the payload should not have moved. Bounding the sum makes the step that
    seals and every reader after it compute one figure from one set of rows.

    A step that has not run yet bounds nothing — the sum stays live — because "as at a
    step that has not happened" has no meaning, and silently returning zero would put a
    run below its own alert threshold.
    """
    query = select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.job_id == job.id)
    if through_step is not None:
        sealed_at = await session.scalar(
            select(JobStep.sequence).where(
                JobStep.job_id == job.id, JobStep.step_key == through_step
            )
        )
        if sealed_at is not None:
            # Every cost row carries the step that incurred it, so the bound is the
            # workflow's own order rather than a timestamp comparison — which would make
            # the gate turn on which of two writes landed first.
            query = query.where(
                Cost.job_step_id.in_(
                    select(JobStep.id).where(
                        JobStep.job_id == job.id, JobStep.sequence <= sealed_at
                    )
                )
            )
    actual = await session.scalar(query)
    estimated: Decimal | None = None
    if job.plan_id is not None:
        plan = await session.get(ResearchPlan, job.plan_id)
        if plan is not None:
            estimated = Decimal(str(plan.estimated_cost_gbp))
    # `Decimal(str(...))` because a freshly written row's attribute can still hold what
    # the writer assigned — a string, in several test seeds — until it round-trips the
    # NUMERIC column. The engine does arithmetic on the cap, and "2.50" * Decimal raises.
    return CostScene(
        cap_gbp=Decimal(str(request.work_order.max_cost_gbp)),
        estimated_gbp=estimated,
        actual_gbp=Decimal(str(actual if actual is not None else 0)),
    )


async def _metric_scores(session: AsyncSession, *, job: Job) -> tuple[MetricScore, ...]:
    return tuple(
        MetricScore(
            metric=row.metric,
            passed=row.passed,
            value=row.value,
            threshold=row.threshold,
            failures=tuple(str(item) for item in row.details.get("failures", [])),
            disputes=_disputes(row),
        )
        for row in await evaluations_for_job(session, job.id)
    )


def _disputes(row: Evaluation) -> tuple[str, ...]:
    """The advisories on this row that contradict its deterministic verdict.

    Advice never changes a verdict (ADR 0038) — but advice that *disagrees* with one is
    §2.4's "validator disagreement", and the uncertainty trigger exists to put exactly
    that in front of a person. An excerpt-location assist only runs against citations the
    verifier failed, so "found" is a disagreement by construction; a proposed date is one
    because the platform holds the source undated.
    """
    found: list[str] = []
    for advisory in row.details.get("advisories", []):
        source = advisory.get("source_document_id", "unknown source")
        if advisory.get("kind") == "excerpt_location" and advisory.get("found"):
            found.append(
                "an advisory validator locates a candidate excerpt for a citation the "
                f"deterministic verifier failed (source {source})"
            )
        elif advisory.get("kind") == "date_adjudication" and advisory.get("proposed_date"):
            found.append(
                f"an advisory validator proposes {advisory['proposed_date']} for a "
                f"source the platform holds undated (source {source})"
            )
    return tuple(found)


async def _section_scenes(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> tuple[SectionScene, ...]:
    coverage = {
        row.name: row for row in await section_coverage_for_job(session, job=job, request=request)
    }
    # The draft step's own record of what each section was dealt and why it refused. The
    # same reader the review page's per-section table uses, so the banner at the top of the
    # gate and the table below it cannot tell different stories about one section.
    outcomes = await section_outcomes(session, job_id=job.id)
    scenes: list[SectionScene] = []
    for section in await sections_for_job(session, job.id):
        covered = coverage.get(section.section_key)
        scenes.append(
            SectionScene(
                key=section.section_key,
                status=section.status.value,
                required=section.definition.required,
                custom=section.definition.origin == SKILL,
                has_primary=covered.has_primary if covered is not None else False,
                covered=covered.covered if covered is not None else False,
                shortfall=covered.shortfall if covered is not None else "no coverage row",
                confidence=section.confidence,
                requires_primary=covered.requires_primary if covered is not None else True,
                **_recorded(outcomes.get(section.section_key)),
            )
        )
    return tuple(scenes)


def _recorded(outcome: Mapping[str, Any] | None) -> dict[str, Any]:
    """The draft step's record of one section, in the scene's own fields.

    ``dealt`` stays ``None`` when the step recorded no tally, which is not the same as a
    tally of nothing: a section that never reached the writer was not dealt an empty pack,
    it was never dealt anything at all, and the trigger says so differently.
    """
    if not outcome:
        return {}
    recorded = outcome.get("evidence_dealt")
    dealt: EvidenceTally | None = None
    if isinstance(recorded, Mapping):
        dealt = EvidenceTally(
            facts=int(recorded.get("facts", 0) or 0),
            calculations=int(recorded.get("calculations", 0) or 0),
            excerpts=int(recorded.get("excerpts", 0) or 0),
        )
    causes = outcome.get("refusal_causes")
    return {
        "dealt": dealt,
        "attempts": int(outcome.get("attempts", 0) or 0),
        "refusal_causes": tuple(causes) if isinstance(causes, Mapping) else (),
    }


async def _conflict_scenes(session: AsyncSession, *, job: Job) -> tuple[ConflictScene, ...]:
    return tuple(
        ConflictScene(
            topic=row.topic,
            kind=row.kind,
            material=row.material,
            settled_by_human=row.resolved_by is ResolvedBy.HUMAN,
        )
        for row in await disagreements_for_job(session, job.id)
    )


async def _policy_clamps(session: AsyncSession, *, job: Job) -> tuple[PolicyClamp, ...]:
    clamps: list[PolicyClamp] = []
    for pin in await pinned_skills_for_job(session, job=job):
        if pin.status != PLANNED:
            # A skipped skill did not run; a clamp on a policy nobody executed is not a
            # difference between what was written and what happened.
            continue
        for clamp in pin.clamps or []:
            clamps.append(_clamp(pin.skill.key, clamp))
    return tuple(clamps)


def _clamp(skill_key: str, clamp: dict[str, Any]) -> PolicyClamp:
    return PolicyClamp(
        skill_key=skill_key,
        field=str(clamp.get("field", "")),
        requested=str(clamp.get("requested", "")),
        effective=str(clamp.get("effective", "")),
        reason=str(clamp.get("reason", "")),
    )


async def _source_scenes(
    session: AsyncSession, *, request: ResearchRequest
) -> tuple[SourceScene, ...]:
    rows = await session.scalars(
        select(SourceDocument)
        .where(SourceDocument.work_order_id == request.id)
        .order_by(SourceDocument.retrieved_at, SourceDocument.id)
    )
    scenes: list[SourceScene] = []
    for row in rows:
        latest = row.publication_date_latest or row.publication_date
        scenes.append(
            SourceScene(
                name=row.title or row.url,
                post_dated=latest is not None and latest > request.work_order.as_of_date,
                admissible=row.is_admissible,
                injection_flagged=row.injection_flagged,
            )
        )
    return tuple(scenes)
