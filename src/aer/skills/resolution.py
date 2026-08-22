"""Plan-time skill resolution: select, pin, compose, project, and price.

§2.12 step 1, and the property everything downstream leans on: **pinning happens here,
once, to exact immutable versions**. Editing a skill after a plan is approved changes
nothing about that run, because the run holds ``skill_versions`` ids, not keys. A re-run
plans afresh and picks up the new version — deliberately, visibly, behind gate 1.

**The composed policy is part of what gate 1 approves**, so it is computed here — against
today's floor, today's role allowlist and today's ceiling — and snapshotted onto the pin
with every clamp. The gate shows the effective policy and its receipts; the approval hash
covers them; execution (task 38) reads the snapshot rather than recomposing, because a
floor that moved between approval and execution must not silently change what runs.

**Custom sections are projected into ``section_definitions``** — the data-driven content
model Phase 1 built for exactly this — as ``origin='skill'`` rows, versioned and never
edited, with a fresh definition version whenever the projection differs from the latest
one. Execution under the ``<user_skill>`` contract lives in :mod:`aer.skills.execution`
(ADR 0037), which reads the pin's snapshot rather than recomposing anything here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.core.schemas.skill import EvidencePolicyRequest
from aer.core.sectors import suggested_profiles
from aer.core.skill_applicability import market_of, skill_applies
from aer.core.skill_policy import ComposedSectionPolicy, compose_policy
from aer.db.models import (
    Company,
    Job,
    PlanSkillPin,
    ResearchPlan,
    ResearchRequest,
    Skill,
    SkillVersion,
)
from aer.db.models.plan_skill_pin import PLANNED, SKIPPED_NOT_APPLICABLE
from aer.db.models.section_definition import SKILL, SectionDefinition
from aer.providers.costs import DEFAULT_PRICES, unknown_model_prices

if TYPE_CHECKING:
    from aer.config import Settings
    from aer.providers.router import Router

__all__ = [
    "CUSTOM_SECTION_OUTPUT_TOKENS",
    "PLANNED_CUSTOM_SECTION_TOOLS",
    "compose_for_version",
    "contract_schema",
    "custom_definitions_for_pins",
    "estimate_custom_section_cost",
    "pinned_skills_for_job",
    "pinned_skills_for_work_order",
    "project_custom_section",
    "resolve_skills_for_plan",
]

_log = structlog.get_logger("aer.skills.resolution")

# What a custom section may ask for, per §2.12: search over what the run already holds,
# plus fetching a specific known URL through the deterministic fetch layer. The registry's
# `custom_section` role (ADR 0037) holds the same set, and a test pins the two to each
# other so they cannot drift — the list a skill is composed against is the list the role
# actually holds.
PLANNED_CUSTOM_SECTION_TOOLS: Final[frozenset[str]] = frozenset(
    {"search_facts", "search_sources", "fetch_known_url"}
)

# §1.8's cost table: a custom section is budgeted at up to 12k in and ~3k out. The output
# side of the estimate, since the input side is the composed token budget.
CUSTOM_SECTION_OUTPUT_TOKENS: Final = 3_000


def estimate_custom_section_cost(*, model: str, token_budget: int, usd_to_gbp: Decimal) -> Decimal:
    """What one custom section is projected to cost, in pounds.

    Deliberately the ceiling, not the average: the budget is a cap the operator approves
    against, and an estimate that assumed less than the cap would make the cap itself
    unapproved spending.
    """
    prices = DEFAULT_PRICES.get(model) or unknown_model_prices(model)
    million = Decimal(1_000_000)
    usd = (
        prices.input_usd * Decimal(token_budget)
        + prices.output_usd * Decimal(CUSTOM_SECTION_OUTPUT_TOKENS)
    ) / million
    return (usd * usd_to_gbp).quantize(Decimal("0.0001"))


@dataclass(slots=True)
class ResolvedSkills:
    """What resolution produced: every pin, and the projected section definitions."""

    pins: list[PlanSkillPin]
    definitions: list[SectionDefinition]

    @property
    def planned(self) -> list[PlanSkillPin]:
        return [pin for pin in self.pins if pin.status == PLANNED]

    @property
    def estimated_cost_gbp(self) -> Decimal:
        return sum((pin.estimated_cost_gbp for pin in self.planned), Decimal(0))


async def resolve_skills_for_plan(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    plan: ResearchPlan,
    settings: Settings,
    router: Router,
) -> ResolvedSkills:
    """Pin every enabled skill to this plan — as planned, or as skipped with its reason."""
    skills = list(await session.scalars(select(Skill).where(Skill.enabled).order_by(Skill.key)))

    existing = await pinned_skills_for_work_order(session, work_order_id=plan.request_id)
    if existing and await _pins_are_current(session, existing, skills=skills):
        # A retried plan step that already flushed its pins, and nothing has moved under it.
        # Re-deciding here could disagree with what a gate page has already displayed.
        return ResolvedSkills(pins=existing, definitions=[])

    if existing:
        # A genuine re-plan on this run — a superseded job runs the plan step again — after
        # the enabled skills or their versions changed. ADR 0068 moved pins from the plan to
        # the run root, which is what lets a tool with no plan pin anything; the cost it
        # named was that a re-planned run can no longer say which of two sets a job ran
        # under. Reusing a stale set unconditionally would be a worse cost than that one and
        # a silent one: an operator who fixes a skill and restarts a failed run would get
        # the version they just replaced, with the pin still asserting it was deliberate.
        #
        # So the set is kept current rather than kept forever. A retry reuses; a re-plan
        # over changed skills replaces.
        for pin in existing:
            await session.delete(pin)
        await session.flush()

    if not skills:
        return ResolvedSkills(pins=[], definitions=[])

    sector_keys = await _known_sector_keys(session, request=request)
    market = market_of(request.exchange)
    model = router.resolve("custom_section").model

    pins: list[PlanSkillPin] = []
    definitions: list[SectionDefinition] = []
    for skill in skills:
        version = await _latest_version(session, skill_id=skill.id)
        if version is None:  # pragma: no cover -- the service writes v1 with the skill
            continue

        decision = skill_applies(
            scope=version.scope,
            markets=tuple((version.applicability or {}).get("markets", ["US", "UK"])),
            analysis_modes=tuple((version.applicability or {}).get("analysis_modes", [])),
            exclude_sectors=tuple((version.applicability or {}).get("exclude_sectors", [])),
            ticker=request.ticker,
            market=market,
            analysis_mode=request.analysis_mode.value,
            sector_profile_keys=sector_keys,
        )

        if not decision.applicable:
            pins.append(
                PlanSkillPin(
                    work_order_id=plan.request_id,
                    skill_id=skill.id,
                    skill_version_id=version.id,
                    status=SKIPPED_NOT_APPLICABLE,
                    reason=decision.reason,
                    estimated_cost_gbp=Decimal(0),
                )
            )
            continue

        pin = PlanSkillPin(
            work_order_id=plan.request_id,
            skill_id=skill.id,
            skill_version_id=version.id,
            status=PLANNED,
            reason="",
            estimated_cost_gbp=Decimal(0),
        )
        if skill.kind == "custom_section":
            composed = compose_for_version(version, settings=settings)
            pin.min_sources = composed.evidence.min_sources
            pin.requires_primary = composed.evidence.requires_primary
            pin.max_tier = composed.evidence.max_tier
            pin.allow_forward_looking = composed.evidence.allow_forward_looking
            pin.token_budget = composed.token_budget
            pin.granted_tools = sorted(composed.allowed_tools)
            pin.clamps = [
                {
                    "field": clamp.field,
                    "requested": clamp.requested,
                    "effective": clamp.effective,
                    "reason": clamp.reason,
                }
                for clamp in composed.clamps
            ]
            pin.estimated_cost_gbp = estimate_custom_section_cost(
                model=model,
                token_budget=composed.token_budget,
                usd_to_gbp=settings.usd_to_gbp,
            )
            definitions.append(
                await project_custom_section(
                    session, skill=skill, version=version, composed=composed
                )
            )
        pins.append(pin)

    session.add_all(pins)
    await session.flush()

    by_id = {skill.id: skill.key for skill in skills}
    _log.info(
        "skills.resolved",
        plan_id=str(plan.id),
        planned=[by_id[p.skill_id] for p in pins if p.status == PLANNED],
        skipped={by_id[p.skill_id]: p.reason for p in pins if p.status == SKIPPED_NOT_APPLICABLE},
    )
    return ResolvedSkills(pins=pins, definitions=definitions)


def compose_for_version(version: SkillVersion, *, settings: Settings) -> ComposedSectionPolicy:
    """The composed policy for a stored version — what a plan would pin for it.

    Public because the authoring surface previews with it (task 43): a preview
    computed by a second function would eventually disagree with what a run
    composes, and the whole point of the preview is that it does not.
    """
    requested = EvidencePolicyRequest(
        min_sources=version.min_sources or 0,
        requires_primary=bool(version.requires_primary),
        max_tier=version.max_tier if version.max_tier is not None else 5,
        allow_forward_looking=(
            version.allow_forward_looking if version.allow_forward_looking is not None else True
        ),
    )
    return compose_policy(
        requested=requested,
        requested_tools=list(version.allowed_tools or []),
        requested_budget=version.token_budget or settings.custom_section_token_ceiling,
        role_allowlist=PLANNED_CUSTOM_SECTION_TOOLS,
        budget_ceiling=settings.custom_section_token_ceiling,
    )


async def project_custom_section(
    session: AsyncSession,
    *,
    skill: Skill,
    version: SkillVersion,
    composed: ComposedSectionPolicy,
) -> SectionDefinition:
    """The skill as a ``section_definitions`` row, versioned like every other definition.

    Idempotent: projecting the same skill version twice returns the existing definition,
    and a *changed* projection — new contract, new title, new composed policy — becomes a
    new definition version, because definitions are never edited (the model's own rule).
    """
    key = f"custom.{skill.key}"
    projection: dict[str, Any] = {
        "title": version.title,
        "output_contract": contract_schema(version.output_contract or {}),
        "evidence_policy": {
            "min_sources": composed.evidence.min_sources,
            "requires_primary": composed.evidence.requires_primary,
            "max_tier": composed.evidence.max_tier,
            "allow_forward_looking": composed.evidence.allow_forward_looking,
        },
        "token_budget": composed.token_budget,
        "allowed_tools": sorted(composed.allowed_tools),
        "required": version.required,
    }

    latest = await session.scalar(
        select(SectionDefinition)
        .where(SectionDefinition.key == key)
        .order_by(SectionDefinition.version.desc())
        .limit(1)
    )
    if latest is not None and _projection_of(latest) == projection:
        return latest

    definition = SectionDefinition(
        key=key,
        version=(latest.version if latest else 0) + 1,
        origin=SKILL,
        skill_id=skill.id,
        title=version.title,
        position=await _position_for(session, declared=version.position, existing=latest),
        required=version.required,
        output_contract=projection["output_contract"],
        evidence_policy=projection["evidence_policy"],
        token_budget=composed.token_budget,
        allowed_tools=sorted(composed.allowed_tools),
        applicability={},
    )
    session.add(definition)
    await session.flush()
    return definition


def _projection_of(definition: SectionDefinition) -> dict[str, Any]:
    return {
        "title": definition.title,
        "output_contract": definition.output_contract,
        "evidence_policy": dict(definition.evidence_policy or {}),
        "token_budget": definition.token_budget,
        "allowed_tools": list(definition.allowed_tools or []),
        "required": definition.required,
    }


def contract_schema(declared: dict[str, Any]) -> dict[str, Any]:
    """The author's field spec as a minimal JSON Schema.

    Scalar types map; anything structured is carried as-is under a permissive schema for
    now — the faithful generation of nested contracts is task 38's work, alongside the
    executor that must satisfy them. Field order is preserved (the column is ``json``),
    because the renderer takes its order from this document.
    """
    scalars = {"string": {"type": "string"}, "number": {"type": "number"}}
    properties = {
        name: scalars.get(spec, {}) if isinstance(spec, str) else {}
        for name, spec in declared.items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(declared),
    }


async def _position_for(
    session: AsyncSession, *, declared: str | None, existing: SectionDefinition | None
) -> Decimal:
    """Where the section sits: beside its anchor, at its number, or at the end.

    A re-projection keeps its predecessor's position — the author placed it once, and a
    contract edit is not a request to move it.
    """
    if existing is not None:
        return existing.position
    if declared and declared.isdigit():
        return Decimal(declared)
    if declared and (declared.startswith(("after:", "before:"))):
        relation, _, anchor_key = declared.partition(":")
        anchor = await session.scalar(
            select(SectionDefinition)
            .where(SectionDefinition.key == anchor_key)
            .order_by(SectionDefinition.version.desc())
            .limit(1)
        )
        if anchor is not None:
            offset = Decimal(1) if relation == "after" else Decimal(-1)
            return anchor.position + offset

    top = await session.scalar(
        select(SectionDefinition.position).order_by(SectionDefinition.position.desc()).limit(1)
    )
    return (top if top is not None else Decimal(0)) + Decimal(100)


async def _known_sector_keys(session: AsyncSession, *, request: ResearchRequest) -> frozenset[str]:
    """The specialist profiles this company's stored classification suggests.

    Empty for a first-time company — the SIC arrives with acquisition, after the plan.
    Task 38 re-checks applicability at execution, once the classification is confirmed.
    """
    company = await session.scalar(select(Company).where(Company.ticker == request.ticker))
    if company is None or not company.sic:
        return frozenset()
    return frozenset(profile.key for profile in suggested_profiles(company.sic))


async def _latest_version(session: AsyncSession, *, skill_id: Any) -> SkillVersion | None:
    found: SkillVersion | None = await session.scalar(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.version.desc())
        .limit(1)
    )
    return found


async def _pins_are_current(
    session: AsyncSession, pins: list[PlanSkillPin], *, skills: list[Skill]
) -> bool:
    """Whether a pin set still names the enabled skills at their current versions.

    Compared on ``(skill_id, skill_version_id)`` because both can move: a skill can be
    enabled or disabled between runs, and an edited one gets a new immutable version. Either
    change makes the stored set a description of a run nobody is about to make.
    """
    pinned = {(pin.skill_id, pin.skill_version_id) for pin in pins}
    current = set()
    for skill in skills:
        version = await _latest_version(session, skill_id=skill.id)
        if version is not None:
            current.add((skill.id, version.id))
    return pinned == current


async def pinned_skills_for_work_order(
    session: AsyncSession, *, work_order_id: Any
) -> list[PlanSkillPin]:
    """Every pin on a run, planned first, then alphabetically — the gate's order.

    Pins moved from the plan to the run root in ADR 0068, which is what lets a tool with no
    research plan pin a skill at all. The cost is recorded there: a request may hold several
    plans, so these are one set per run rather than one per plan.

    Relationships are eagerly loaded because every reader — the payload builder, the gate
    page, the report — names the skill and its version, and a lazy load off an async
    session raises rather than loading.
    """
    pins = list(
        await session.scalars(
            select(PlanSkillPin)
            .where(PlanSkillPin.work_order_id == work_order_id)
            .options(
                selectinload(PlanSkillPin.skill),
                selectinload(PlanSkillPin.skill_version),
            )
        )
    )
    pins.sort(key=lambda pin: (pin.status != PLANNED, pin.skill.key))
    return pins


async def custom_definitions_for_pins(
    session: AsyncSession, pins: Sequence[PlanSkillPin]
) -> list[SectionDefinition]:
    """The projected definition behind each *planned* custom-section pin.

    Looked up by skill rather than carried in memory, so the plan step's retry — which
    finds its pins already flushed and receives no fresh projections — reaches the same
    definitions the first attempt projected. The latest version per skill is correct
    within a run for the same reason projection is idempotent: nothing else writes a
    newer one between the pin and this read.
    """
    definitions: list[SectionDefinition] = []
    for pin in pins:
        if pin.status != PLANNED or pin.skill.kind != "custom_section":
            continue
        found = await session.scalar(
            select(SectionDefinition)
            .where(SectionDefinition.skill_id == pin.skill_id)
            .order_by(SectionDefinition.version.desc())
            .limit(1)
        )
        if found is not None:
            definitions.append(found)
    return definitions


async def pinned_skills_for_job(session: AsyncSession, *, job: Job) -> list[PlanSkillPin]:
    """The skill versions a run actually ran under — the report's provenance question.

    Empty for a job predating its plan step, and for the runs of Phases 1-3, which
    carried no skills to pin.
    """
    if job.plan_id is None:
        return []
    return await pinned_skills_for_work_order(session, work_order_id=job.work_order_id)
