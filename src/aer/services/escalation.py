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

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.disagreement import ResolvedBy
from aer.core.escalation import (
    ConflictScene,
    CostScene,
    FiredTrigger,
    MetricScore,
    PolicyClamp,
    SectionScene,
    SourceScene,
    fire_triggers,
)
from aer.db.models import Cost, Evaluation, Job, ResearchPlan, ResearchRequest, SourceDocument
from aer.db.models.plan_skill_pin import PLANNED
from aer.db.models.section_definition import SKILL
from aer.sections.registry import sections_for_job
from aer.services.disagreements import disagreements_for_job
from aer.services.evaluations import evaluations_for_job, section_coverage_for_job
from aer.skills.resolution import pinned_skills_for_job

__all__ = ["cost_scene_for_job", "triggers_for_job"]


async def triggers_for_job(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> tuple[FiredTrigger, ...]:
    """Every §2.4 trigger that holds for this run, from its recorded rows alone."""
    return fire_triggers(
        point_in_time=request.point_in_time,
        metrics=await _metric_scores(session, job=job),
        sections=await _section_scenes(session, job=job, request=request),
        conflicts=await _conflict_scenes(session, job=job),
        clamps=await _policy_clamps(session, job=job),
        sources=await _source_scenes(session, request=request),
        cost=await cost_scene_for_job(session, job=job, request=request),
    )


async def cost_scene_for_job(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> CostScene:
    """The run's cap, its approved estimate, and what the cost rows actually sum to.

    The actual figure is the same sum the budget guard enforces against — the ``costs``
    table, nothing derived — so the banner and the hard cap cannot disagree about what
    was spent.
    """
    actual = await session.scalar(
        select(func.coalesce(func.sum(Cost.amount_gbp), 0)).where(Cost.job_id == job.id)
    )
    estimated: Decimal | None = None
    if job.plan_id is not None:
        plan = await session.get(ResearchPlan, job.plan_id)
        if plan is not None:
            estimated = Decimal(str(plan.estimated_cost_gbp))
    # `Decimal(str(...))` because a freshly written row's attribute can still hold what
    # the writer assigned — a string, in several test seeds — until it round-trips the
    # NUMERIC column. The engine does arithmetic on the cap, and "2.50" * Decimal raises.
    return CostScene(
        cap_gbp=Decimal(str(request.max_cost_gbp)),
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
            )
        )
    return tuple(scenes)


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
        .where(SourceDocument.request_id == request.id)
        .order_by(SourceDocument.retrieved_at, SourceDocument.id)
    )
    scenes: list[SourceScene] = []
    for row in rows:
        latest = row.publication_date_latest or row.publication_date
        scenes.append(
            SourceScene(
                name=row.title or row.url,
                post_dated=latest is not None and latest > request.as_of_date,
                admissible=row.is_admissible,
                injection_flagged=row.injection_flagged,
            )
        )
    return tuple(scenes)
