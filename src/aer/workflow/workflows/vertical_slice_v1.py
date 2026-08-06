"""The Phase 1 workflow: request in, cited report out.

Eight steps, two of which are human decisions::

    plan -> [gate 1] -> acquire -> extract -> calculate -> draft -> [gate 2] -> render

**The point of this workflow is to be end-to-end, not to be deep.** It fetches one
document, extracts a handful of facts, performs one calculation and writes two sections.
Every one of those is a placeholder for something much larger in Phase 3 — but the chain
from a filing's bytes to a footnote in a report is complete, and that chain is what the
rest of the platform gets built inside.

**No step contains a section key.** ``draft`` iterates ``report_sections`` and asks the
generic renderer for each; adding a third section makes this workflow produce a third
section with no change here. That property is tested.

**The gates are steps, not decorations.** Reaching one raises :class:`StepPaused`, the
engine records the job as awaiting approval, and the run stops. Resuming re-enters at the
gate, finds an approval, and continues — which is why approving twice has to be refused
somewhere, and that somewhere is :mod:`aer.services.approvals`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.planner import PlannerAgent, PlannerInput
from aer.agents.worker import ResearchTopic
from aer.calc.basic import cagr
from aer.calc.comps import MultipleBasis, WithheldComps, align_peers
from aer.calc.units import SourceRef, money
from aer.core.enums import Decision, FactBasis, GateKind, JobStatus, Provider, SourceTier
from aer.core.escalation import FiredTrigger
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.schemas.request import ResearchRequestRead
from aer.db.models import (
    Approval,
    Calculation,
    Company,
    FinancialFact,
    Job,
    Report,
    ResearchPlan,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
)
from aer.db.models.plan_skill_pin import PLANNED as PIN_PLANNED
from aer.db.models.plan_skill_pin import SKIPPED_NOT_APPLICABLE, PlanSkillPin
from aer.db.models.section_definition import BUILTIN, SKILL
from aer.extract import extract_bytes
from aer.fetch.policy import DEFAULT_POLICIES
from aer.render.markdown import SectorNote, render_markdown
from aer.sections.deterministic import SectionStage, fill_deterministic_sections
from aer.sections.registry import create_report_sections, resolve_sections, sections_for_job
from aer.sections.writing import execute_builtin_section
from aer.services import calculations as calculation_service
from aer.services.acquisition import record_acquisition
from aer.services.artefacts import store_artefact
from aer.services.citations import review_evidence
from aer.services.comps import (
    PEER_SET_STEP,
    confirmed_peer_set,
    peer_set_payload,
    peer_set_required,
    propose_peers_from_sic,
)
from aer.services.disagreements import escalations_for_job
from aer.services.escalation import triggers_for_job
from aer.services.evaluations import evaluate_run
from aer.services.exhibits import exportable_charts_for
from aer.services.extractions import record_excerpts
from aer.services.facts import persist_facts, upsert_company
from aer.services.red_team import run_red_team
from aer.services.research import run_worker
from aer.services.sectors import (
    CLASSIFY_STEP,
    classification_payload,
    confirmed_classification,
    metric_disclosure,
    propose_from_sic,
    sector_gate_required,
)
from aer.skills.execution import execute_custom_section
from aer.skills.resolution import (
    custom_definitions_for_pins,
    estimate_custom_section_cost,
    pinned_skills_for_job,
    pinned_skills_for_plan,
    resolve_skills_for_plan,
)
from aer.sources.sec.companyfacts import parse_company_facts
from aer.sources.sec.pit import select_point_in_time
from aer.verify.citations import verify_job_citations
from aer.workflow.engine import StepContext, StepPaused, StepResult, WorkflowStep

__all__ = [
    "WORKFLOW_VERSION",
    "build_steps",
    "comps_note_for",
    "final_gate_payload",
    "peer_gate_payload",
    "plan_gate_payload",
    "sector_gate_payload",
    "sector_note_for",
    "unmapped_gate_payload",
    "unmapped_gate_required",
]

_log = structlog.get_logger("aer.workflow.vertical_slice")

WORKFLOW_VERSION: Final = "vertical_slice_v1"

# What the planner step is expected to cost. Used by the budget guard *before* the call, so
# it is necessarily an estimate; the real figure is metered afterwards. Deliberately
# generous — a guard that underestimates lets a run through it should have paused.
PLANNER_ESTIMATE_GBP: Final = Decimal("0.15")

# Per research worker (task 37): a bounded request/execute loop on the analysis route.
# Generous for the same reason as the planner's — an estimate that understates lets a run
# through the guard that it should have paused.
WORKER_ESTIMATE_GBP: Final = Decimal("0.10")

# The validate step (task 39): at most a handful of capped advisory calls on the
# validator route, and frequently none at all — the deterministic rows cost nothing.
VALIDATOR_ESTIMATE_GBP: Final = Decimal("0.05")

# The red team (task 40): §1.8 budgets the bear case at 90k in / 10k out on Opus via the
# batch path (~£0.85 at current rates). Generous for the same reason as every estimate
# here, and zero-cost on the runs that skip it for want of claims.
RED_TEAM_ESTIMATE_GBP: Final = Decimal("1.00")

# What the gate shows as a runtime estimate. A constant for the slice, which does one
# fetch and one calculation; Phase 3 derives it from the plan.
_RUNTIME_ESTIMATE_SECONDS: Final = 120

# The concept this slice calculates. One, on purpose: the deliverable is the chain from
# filing to footnote, and a second concept would test the same chain twice.
SLICE_CONCEPT: Final = "revenue"


def build_steps() -> list[WorkflowStep]:
    """The workflow, in order."""
    return [
        WorkflowStep(key="plan", run=_plan, estimated_cost_gbp=PLANNER_ESTIMATE_GBP),
        WorkflowStep(key="gate_plan", run=_gate_plan, gate=GateKind.PLAN.value),
        WorkflowStep(key="acquire", run=_acquire),
        # Classification before extraction, because what kind of business this is decides
        # which valuation models may run, and a run that discovers that after computing a
        # discounted cash flow has already computed it.
        WorkflowStep(key=CLASSIFY_STEP, run=_classify),
        # Conditional: passes straight through for an ordinary company. A specialist
        # proposal stops here, because it is the proposal that blocks a model and a
        # classification nobody reviewed is a model deciding which models may run.
        WorkflowStep(
            key="gate_sector_specialist",
            run=_gate_sector_specialist,
            gate=GateKind.SECTOR_SPECIALIST.value,
        ),
        # Peers after classification, because what kind of business this is decides which
        # companies are comparable with it. **The gate order in `aer.services.approvals` lists
        # PEER_SET before SECTOR_SPECIALIST, and that is about approval precedence rather than
        # about step order** — both are conditional, so an undecided one never blocks the
        # other, and the workflow is free to propose in the sequence that makes sense.
        WorkflowStep(key=PEER_SET_STEP, run=_propose_peers),
        # Conditional: passes straight through when nothing comparable is in the database.
        # A run with no peers has no comparison to defend and should not wait at a gate to
        # confirm an empty list.
        WorkflowStep(
            key="gate_peer_set",
            run=_gate_peer_set,
            gate=GateKind.PEER_SET.value,
        ),
        WorkflowStep(key="extract", run=_extract),
        # Conditional: it passes straight through unless the extraction left tags the concept
        # map does not know. Declared unconditionally because a gate that only exists on the
        # runs that need it is a gate nobody can find when a run needs it.
        WorkflowStep(
            key="gate_uk_financials",
            run=_gate_uk_financials,
            gate=GateKind.UK_FINANCIALS.value,
        ),
        # The first real fan-out (task 37): the calculation and the five research workers
        # are independent of each other and all of the financials gate, so they form one
        # wave — six nodes, inside the §2.5 bound of seven. Where the run has no session
        # factory (every savepoint-fixtured test) the engine takes them one at a time on
        # the caller's session, in this declared order.
        WorkflowStep(key="calculate", run=_calculate, needs=frozenset({"gate_uk_financials"})),
        WorkflowStep(
            key="research_company",
            run=_research(ResearchTopic.COMPANY),
            needs=frozenset({"gate_uk_financials"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_industry",
            run=_research(ResearchTopic.INDUSTRY),
            needs=frozenset({"gate_uk_financials"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_macro",
            run=_research(ResearchTopic.MACRO),
            needs=frozenset({"gate_uk_financials"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_recent_developments",
            run=_research(ResearchTopic.RECENT_DEVELOPMENTS),
            needs=frozenset({"gate_uk_financials"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_technical_context",
            run=_research(ResearchTopic.TECHNICAL_CONTEXT),
            needs=frozenset({"gate_uk_financials"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="draft",
            run=_draft,
            needs=frozenset(
                {
                    "calculate",
                    "research_company",
                    "research_industry",
                    "research_macro",
                    "research_recent_developments",
                    "research_technical_context",
                }
            ),
        ),
        # Validation before the gate (task 39): the eight §2.10 run-time rows are written
        # here, so gate 2 shows scores rather than promising them. The step never pauses
        # the run itself — a failed metric is a recorded state the gate displays and the
        # task 41 escalation engine will act on.
        WorkflowStep(key="validate", run=_validate, estimated_cost_gbp=VALIDATOR_ESTIMATE_GBP),
        # The adversary last before the gate (task 40): its challenges join the payload
        # as escalations, so the hash gate 2 verifies is computed here — by the final
        # step that can change what the operator will be shown.
        WorkflowStep(key="red_team", run=_red_team, estimated_cost_gbp=RED_TEAM_ESTIMATE_GBP),
        WorkflowStep(key="gate_final", run=_gate_final, gate=GateKind.FINAL.value),
        WorkflowStep(key="render", run=_render),
    ]


# ==========================================================================================
# 1. Plan
# ==========================================================================================


async def _plan(context: StepContext) -> StepResult:
    """Ask the planner what this run should do, and store the proposal.

    The proposal is stored as a ``research_plans`` row *and* hashed, because gate 1
    approves exactly what was displayed and the approval records that hash. An approval of
    a plan that has since changed is not an approval.
    """
    request = await _request_for(context)
    # Built-ins only here: the planner proposes over the platform's own sections, and a
    # custom section is the operator's instruction, not the planner's to plan. This run's
    # custom sections join below, from the pins — never from a blanket query, which would
    # sweep in projections belonging to other plans.
    definitions = [
        definition
        for definition in await resolve_sections(context.session, request=request)
        if definition.origin == BUILTIN
    ]

    agent = PlannerAgent()
    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )

    draft = await agent.run(
        agent_context,
        PlannerInput(
            request=ResearchRequestRead.model_validate(request, from_attributes=True),
            available_section_keys=[definition.key for definition in definitions],
        ),
    )

    payload = draft.model_dump(mode="json")
    # The spine as data, alongside the model's proposal: which sections this run owes, in
    # position order, each with its budget. Written by code from the resolved definitions
    # — the planner proposes focus, never the section list — and stored on the plan row so
    # every reader of the gate-1 payload (the hash here, the API, the review page) gets
    # the same listing without re-resolving definitions that may have gained versions
    # since. A listing re-resolved at read time would change the hash and refuse every
    # approval of the plan it drifted from.
    # Each model-written section is estimated at its budgeted evidence tokens against the
    # writer's routed model (task 45): the spine spends for real now, and a cost the gate
    # does not show is a cost nobody agreed to. Deterministic sections estimate at zero
    # because zero is what they spend.
    writer_model = context.service("router").resolve("report_writer").model
    usd_to_gbp = context.service("settings").usd_to_gbp
    spine_estimates = {
        definition.key: (
            Decimal(0)
            if definition.token_budget == 0
            else estimate_custom_section_cost(
                model=writer_model,
                token_budget=definition.token_budget,
                usd_to_gbp=usd_to_gbp,
            )
        )
        for definition in definitions
    }
    payload["section_listing"] = [
        {
            "key": definition.key,
            "title": definition.title,
            "position": str(definition.position),
            "required": definition.required,
            "token_budget": definition.token_budget,
            "deterministic": definition.token_budget == 0,
            "estimated_cost_gbp": str(spine_estimates[definition.key]),
        }
        for definition in definitions
    ]
    plan = ResearchPlan(
        request_id=request.id,
        workflow_version=WORKFLOW_VERSION,
        plan=payload,
        planned_sources=payload["planned_sources"],
        known_risks=payload["known_risks"],
        estimated_cost_gbp=agent_context.spend_gbp + sum(spine_estimates.values(), Decimal(0)),
        estimated_runtime_seconds=_RUNTIME_ESTIMATE_SECONDS,
    )
    context.session.add(plan)
    await context.session.flush()

    # The job records which plan it ran under. The column existed from Phase 1; this is
    # the first thing that needs it answered — "which skill versions shaped this run?"
    # resolves job -> plan -> pins.
    context.job.plan_id = plan.id

    # Every enabled skill, pinned to this plan — planned with its composed policy, or
    # skipped with its reason. The pinned sections' budgets join the estimate the
    # operator approves against, because a cost the gate does not show is a cost nobody
    # agreed to.
    resolved = await resolve_skills_for_plan(
        context.session,
        request=request,
        plan=plan,
        settings=context.service("settings"),
        router=context.service("router"),
    )
    plan.estimated_cost_gbp = plan.estimated_cost_gbp + resolved.estimated_cost_gbp
    await context.session.flush()

    # Refreshed before hashing, so the hash covers what the *database* holds rather than
    # what is in memory. `estimated_cost_gbp` is NUMERIC(12,6): a Decimal that arrived with
    # more places comes back rounded, and a gate page reading the row would otherwise
    # compute a different hash from the one recorded here and reject every approval.
    await context.session.refresh(plan)

    # The hash of exactly what gate 1 will display -- the same function the page renders
    # from. Recorded on the approval, so an approval of one plan cannot be reused for a
    # different one; see `_require_approval`. The pins are inside the payload, so
    # approving one set of skills is not approving another.
    pins = await pinned_skills_for_plan(context.session, plan_id=plan.id)
    payload_hash = sha256_hex(canonical_json(plan_gate_payload(plan, pins)))

    # The run's sections are the built-ins plus this plan's pinned custom sections
    # (task 38). Derived from the pins rather than taken from `resolved.definitions`, so
    # a retried plan step — whose resolution returns the existing pins and no fresh
    # projections — creates exactly the same rows.
    definitions = definitions + await custom_definitions_for_pins(context.session, pins)
    await create_report_sections(context.session, job_id=context.job.id, definitions=definitions)

    return StepResult(
        output={
            "plan_id": str(plan.id),
            "payload_hash": payload_hash,
            "section_keys": [definition.key for definition in definitions],
            "planned_sources": len(draft.planned_sources),
            "skills_planned": [pin.skill.key for pin in pins if pin.status == PIN_PLANNED],
            "skills_skipped": {
                pin.skill.key: pin.reason for pin in pins if pin.status == SKIPPED_NOT_APPLICABLE
            },
        },
        cost_gbp=agent_context.spend_gbp,
    )


# ==========================================================================================
# 2 and 7. The gates
# ==========================================================================================


def plan_gate_payload(plan: ResearchPlan, pins: Sequence[PlanSkillPin] = ()) -> dict[str, Any]:
    """Exactly what gate 1 approves, as one structure.

    Built here and used by the plan step, the JSON API and the review page alike, so "what
    the run hashed", "what the API reports" and "what the operator was shown" are the same
    object by construction rather than by three functions agreeing.

    Costs are strings because they are ``Decimal``; a JSON number would round them, and a
    hash over a rounded figure is a hash over something nobody displayed.

    ``pins`` carries the plan's skill pins (task 36) — the exact versions, the composed
    policies and every clamp — because approving a plan is approving *those*, and a pin
    outside the hash would be a skill the operator never signed off.
    """
    body = dict(plan.plan or {})
    return {
        "plan_id": str(plan.id),
        "workflow_version": plan.workflow_version,
        "summary": body.get("summary", ""),
        "sections": body.get("sections", []),
        "section_listing": list(body.get("section_listing", [])),
        "planned_sources": list(plan.planned_sources or []),
        "known_risks": list(plan.known_risks or []),
        "estimated_cost_gbp": str(plan.estimated_cost_gbp),
        "estimated_runtime_seconds": plan.estimated_runtime_seconds,
        "skills": [
            {
                "key": pin.skill.key,
                "kind": pin.skill.kind,
                "title": pin.skill_version.title,
                "version": pin.skill_version.version,
                "content_hash": pin.skill_version.content_hash,
                "status": pin.status,
                "reason": pin.reason,
                "token_budget": pin.token_budget,
                "granted_tools": list(pin.granted_tools or []),
                "clamps": list(pin.clamps or []),
                "estimated_cost_gbp": str(pin.estimated_cost_gbp),
            }
            for pin in pins
        ],
    }


async def _gate_plan(context: StepContext) -> StepResult:
    """Stop until a human approves the plan."""
    return await _require_approval(context, gate=GateKind.PLAN, of_step="plan")


def unmapped_gate_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly what the unmapped-tags gate approves, as one structure.

    Built from the extract step's own output, so the tags an operator is shown are the tags
    the extractor actually could not place — not a re-derivation that might differ.
    """
    return {
        "exchange": str(produced.get("exchange", "")),
        "unmapped_tags": list(produced.get("unmapped_tags", [])),
        "facts_written": produced.get("facts_written", 0),
        "load_errors": list(produced.get("load_errors", [])),
    }


def unmapped_gate_required(produced: Mapping[str, Any]) -> bool:
    """Whether this run's extraction needs a person to look at it before it is used.

    On unmapped **tags**, not on a count or a proportion. One extension element carrying a
    company's headline profit measure matters and forty carrying segment breakdowns nobody
    asked for do not, and only a person can tell which — see
    :attr:`aer.extract.ixbrl.IxbrlExtraction.needs_confirmation`, which this mirrors.
    """
    return bool(produced.get("unmapped_tags"))


async def _gate_uk_financials(context: StepContext) -> StepResult:
    """Stop until a human confirms an extraction that left tags unmapped.

    **Skipped, not approved, when there is nothing to confirm.** A run whose every tag mapped
    records that the gate did not apply and continues; the approvals service already treats
    this gate as conditional, so an absent decision does not block the final gate.
    """
    produced = context.outputs.get("extract", {})
    if not unmapped_gate_required(produced):
        return StepResult(
            output={"gate": GateKind.UK_FINANCIALS.value, "required": False, "unmapped_tags": []}
        )

    return await _require_approval(context, gate=GateKind.UK_FINANCIALS, of_step="extract")


async def _validate(context: StepContext) -> StepResult:
    """Write the run's eight §2.10 evaluation rows — scores, never a pause.

    The deterministic verifier runs inside :func:`evaluate_run`, first and
    authoritatively; the LLM assists advise on what it could not settle and their advice
    lands in the rows' details. A failed metric here is a recorded state for gate 2 to
    display and the escalation engine (task 41) to act on — the *evidence* rule that can
    stop a run still lives in the final gate, unchanged.
    """
    request = await _request_for(context)
    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )
    rows = await evaluate_run(agent_context, job=context.job, request=request)

    # After the metric rows, before the red team: the validation record becomes a report
    # section here, so the payload the red team seals — and the preview the operator
    # approves — already carries it. The metrics above were measured before this fill, so
    # a section recording the validators never sits in its own denominator.
    deterministic = await fill_deterministic_sections(
        context.session, job=context.job, request=request, stage=SectionStage.VALIDATE
    )

    return StepResult(
        output={
            "deterministic_sections": deterministic,
            "metrics": {
                row.metric: {
                    "value": str(row.value) if row.value is not None else None,
                    "passed": row.passed,
                }
                for row in rows
            },
            "failed": [row.metric for row in rows if row.passed is False],
            "not_exercised": [row.metric for row in rows if row.passed is None],
        },
        cost_gbp=agent_context.spend_gbp,
    )


async def _red_team(context: StepContext) -> StepResult:
    """Attack the draft's recorded claims from a separate context, and seal the payload.

    The adversary sees the claims and the evidence index — the input type cannot carry
    the drafting context (ADR 0039) — and every surviving challenge lands as an escalated
    ``disagreements`` row gate 2 displays. This step computes the payload hash the final
    gate verifies, because its challenges are the last thing that can change what the
    operator will be shown.
    """
    request = await _request_for(context)
    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )
    outcome = await run_red_team(agent_context, context.session, job=context.job, request=request)

    payload = await final_gate_payload(context.session, job_id=context.job.id)
    return StepResult(
        output={
            **outcome.as_dict(),
            # Gate 2 approves exactly this. The hash is what the approval records, so an
            # approval of an earlier payload cannot be reused for a later one.
            "payload_hash": sha256_hex(canonical_json(payload)),
        },
        cost_gbp=agent_context.spend_gbp,
    )


async def _gate_final(context: StepContext) -> StepResult:
    """Check the evidence, then stop until a human approves the draft.

    **The evidence check comes first, and it is not part of the approval.** An operator must
    not be shown a draft to approve while the platform still has unverified citations in it —
    approving would then mean "I accept this" without the platform having said which parts of
    it it could not stand behind. So the run pauses on the evidence before it pauses on the
    person, with a different message.
    """
    await _refuse_unsupported_evidence(context)
    await _pause_naming_triggers(context)
    return await _require_approval(context, gate=GateKind.FINAL, of_step="red_team")


async def _pause_naming_triggers(context: StepContext) -> None:
    """Pause an undecided run with the fired §2.4 triggers in the message.

    §2.4 says any fired trigger "pauses the run and raises a banner at Gate 2". The run
    pauses at gate 2 regardless — the final gate always needs a person — so what a fired
    trigger changes is what the pause *says*: the message names the conditions, and the
    console shows them before anyone opens the review page. A clean run falls through to
    :func:`_require_approval`'s ordinary message; a decided run falls through so the
    decision, not the banner, determines what happens next.

    Raises:
        StepPaused: The gate is undecided and at least one trigger fired.
    """
    approval = await context.session.scalar(
        select(Approval).where(
            Approval.request_id == context.job.request_id,
            Approval.gate == GateKind.FINAL,
            Approval.job_id == context.job.id,
        )
    )
    if approval is not None:
        return

    request = await _request_for(context)
    fired = await triggers_for_job(context.session, job=context.job, request=request)
    if not fired:
        return

    names = ", ".join(trigger.kind.value for trigger in fired)
    plural = "s" if len(fired) != 1 else ""
    message = (
        f"This run is waiting for the final gate, and {len(fired)} escalation "
        f"trigger{plural} raised the banner: {names}. Nothing further happens, and "
        "nothing further is spent, until somebody approves or rejects it with the "
        "banner in view."
    )
    raise StepPaused(
        message,
        gate=GateKind.FINAL.value,
        context={
            "job_id": str(context.job.id),
            "triggers": [trigger.kind.value for trigger in fired],
        },
    )


async def _refuse_unsupported_evidence(context: StepContext) -> None:
    """Verify every citation this run produced, and pause if any claim is left unsupported.

    Verification runs **here rather than when a citation is written**, because a citation is a
    proposal and the point at which it must become a confirmation is the point at which
    something rests on it. Re-checking at the gate also means a document that changed after the
    citation was recorded is caught, which a one-off check at write time would miss.

    Raises:
        StepPaused: Something is unsupported. Not a failure — the run is recoverable by fixing
            the evidence or by overriding a citation with a reason, and a failed run would
            throw away everything already paid for.
    """
    await verify_job_citations(
        context.session,
        context.service("store"),
        job_id=context.job.id,
        settings=context.service("settings"),
    )
    review = await review_evidence(context.session, job_id=context.job.id)

    if review.is_admissible:
        return

    raise StepPaused(
        review.as_message(),
        gate=GateKind.FINAL.value,
        context={
            "job_id": str(context.job.id),
            "claims": review.claims,
            "citations": review.citations,
            "verified": review.verified,
            "unsupported_claims": len(review.unsupported),
            "unverified_citations": len(review.unverified),
        },
    )


async def _require_approval(context: StepContext, *, gate: GateKind, of_step: str) -> StepResult:
    """Continue only if an approval exists for exactly what this run produced.

    The approval's ``payload_hash`` is compared against the hash of what the step actually
    produced. An approval recorded against a different payload is not an approval of this
    one — that is the whole reason the hash is stored rather than just a timestamp and a
    user id.
    """
    produced = context.outputs.get(of_step, {})
    expected_hash = str(produced.get("payload_hash", ""))

    approval = await context.session.scalar(
        select(Approval).where(
            Approval.request_id == context.job.request_id,
            Approval.gate == gate,
            Approval.job_id == context.job.id,
        )
    )

    if approval is None:
        message = (
            f"This run is waiting for the {gate.value} gate. Nothing further happens, and "
            "nothing further is spent, until somebody approves or rejects it."
        )
        raise StepPaused(message, gate=gate.value, context={"job_id": str(context.job.id)})

    if approval.decision is not Decision.APPROVED:
        message = (
            f"The {gate.value} gate was {approval.decision.value.lower()}. The run stops here."
        )
        raise StepPaused(message, gate=gate.value, context={"decision": approval.decision.value})

    if expected_hash and approval.payload_hash != expected_hash:
        message = (
            f"The {gate.value} approval was recorded against different content from what "
            "this run produced. An approval of something else is not an approval of this, "
            "so the run stops rather than proceeding on it."
        )
        raise StepPaused(
            message,
            gate=gate.value,
            context={"approved_hash": approval.payload_hash, "actual_hash": expected_hash},
        )

    return StepResult(output={"approval_id": str(approval.id), "gate": gate.value})


# ==========================================================================================
# 3. Acquire
# ==========================================================================================


async def _acquire(context: StepContext) -> StepResult:
    """Fetch the company's facts from EDGAR and record the provenance.

    One document. The slice's purpose is the chain, and a second document would exercise
    the same chain again rather than exercising anything new.
    """
    request = await _request_for(context)
    client = context.service("sec_client")
    store = context.service("store")

    entity = await client.resolve_entity(request.ticker, exchange=request.exchange)
    response = await client.fetch_company_facts(entity.identifier)

    acquisition = await record_acquisition(
        context.session,
        store,
        request=request,
        # Which run fetched it. Optional on the service because a document can be supplied
        # by hand or gathered while planning — but a run that omits it produces provenance
        # attributable to a request and to no particular run, and the sources table, which
        # asks "what did *this* run acquire?", would be empty for every real run.
        job_id=context.job.id,
        result=response.fetch,
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        title=f"{entity.name} XBRL company facts",
        publisher="US Securities and Exchange Commission",
    )

    company = await upsert_company(
        context.session,
        entity=entity,
        ticker=request.ticker,
        exchange=request.exchange,
    )

    # Marks the request's identity as confirmed against a registry. Everything downstream
    # can now tell a resolved company from a string somebody typed.
    request.resolved = True
    await context.session.flush()

    return StepResult(
        output={
            "company_id": str(company.id),
            "cik": entity.identifier,
            "source_document_id": str(acquisition.source_document.id),
            "artefact_sha256": acquisition.sha256,
            "quarantined": acquisition.quarantined,
        }
    )


# ==========================================================================================
# 3b. Classify
# ==========================================================================================


def sector_gate_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """What the sector gate approves. Delegates, so one definition serves both halves."""
    return classification_payload(produced)


async def _classify(context: StepContext) -> StepResult:
    """Propose what kind of business this is, from the registry's own classification.

    **Deterministic in this slice, and that is a deliberate floor rather than the finished
    article.** The proposal comes from the filer's SIC code, which is free, reproducible and
    already retrieved — so the gate and the enforcement it feeds are exercised on every run
    without a model call. Phase 4's classifier agent replaces the proposal and nothing else:
    the confirmation, the mandate and the block are indifferent to who proposed.

    A SIC code matching no specialist profile produces an empty proposal, no gate, and the
    standard model — which is the right answer for most listed companies.

    **`Company.sic` is populated by the adapters that parse it**, which today means Companies
    House and the SEC *submissions* endpoint. This slice acquires *companyfacts*, which does
    not carry a SIC code, so a run through this path classifies nothing and takes the standard
    model. That is safe rather than merely convenient — an absent classification is the
    permissive state and it is reached here by the data genuinely not being present, not by a
    lookup failing quietly — but it does mean the block is exercised by runs that resolve a
    SIC and not yet by this one. The mechanism, the gate and the refusal are tested
    independently of where the proposal came from.
    """
    acquired = context.output_of("acquire")
    company = await context.session.get(Company, _uuid(acquired["company_id"]))
    if company is None:  # pragma: no cover -- written by the prior step
        message = "The acquire step's company row is missing."
        raise StepPaused(message, gate=None)

    proposal = propose_from_sic(company.sic or "")
    profile = proposal.profile

    output: dict[str, Any] = {
        "sector_key": proposal.sector_key,
        "sector_label": profile.label if profile is not None else "",
        "rationale": proposal.rationale,
        "proposed_by": proposal.proposed_by,
        "confidence": proposal.confidence,
        "sic_code": proposal.sic_code,
        "sic_candidates": list(proposal.sic_candidates),
        "allowed_models": [m.value for m in profile.allowed_models] if profile else [],
        "blocked_models": [m.value for m in profile.blocked_models] if profile else [],
        "required_metrics": list(profile.required_metrics) if profile else [],
        "warnings": list(profile.warnings) if profile else [],
    }
    output["payload_hash"] = sha256_hex(canonical_json(sector_gate_payload(output)))
    return StepResult(output=output)


async def _gate_sector_specialist(context: StepContext) -> StepResult:
    """Stop until a person agrees what kind of business this is.

    **Skipped, not approved, when nothing specialist was proposed.** An ordinary company does
    not need a human to confirm that it is ordinary, and a gate that fired on every run would
    be one an operator learns to click through.
    """
    produced = context.outputs.get(CLASSIFY_STEP, {})
    if not sector_gate_required(produced):
        return StepResult(
            output={
                "gate": GateKind.SECTOR_SPECIALIST.value,
                "required": False,
                "sector_key": "",
            }
        )

    return await _require_approval(context, gate=GateKind.SECTOR_SPECIALIST, of_step=CLASSIFY_STEP)


def peer_gate_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """What the peer-set gate approves. Delegates, so one definition serves both halves."""
    return peer_set_payload(produced)


async def _propose_peers(context: StepContext) -> StepResult:
    """Put forward comparable companies, from what this database already holds.

    **Deterministic in this slice, and a floor rather than the finished article** — the same
    shape as `_classify`. Companies sharing the subject's SIC group are proposed with the
    reason stated; a model writing a real rationale per peer replaces the proposal and nothing
    else, because the confirmation and the refusal are indifferent to who proposed.

    A database holding no comparable company proposes nobody, no gate fires, and the report
    says no comparison was performed. That is the honest answer for the first company anybody
    researches.
    """
    acquired = context.output_of("acquire")
    company = await context.session.get(Company, _uuid(acquired["company_id"]))
    if company is None:  # pragma: no cover -- written by the prior step
        message = "The acquire step's company row is missing."
        raise StepPaused(message, gate=None)

    request = await _request_for(context)
    proposals = await propose_peers_from_sic(
        context.session, subject=company, as_of=request.as_of_date
    )

    output: dict[str, Any] = {
        "subject": str(company.id),
        "subject_name": company.name,
        "subject_period_end": request.as_of_date.isoformat(),
        "basis": MultipleBasis.TRAILING_TWELVE_MONTHS.value,
        "proposed_by": "sic_group_lookup",
        "peers": [peer.as_dict() for peer in proposals],
    }
    output["payload_hash"] = sha256_hex(canonical_json(peer_gate_payload(output)))
    return StepResult(output=output)


async def _gate_peer_set(context: StepContext) -> StepResult:
    """Stop until a person agrees which companies this one is comparable with.

    **Skipped, not approved, when nothing was proposed.** A badly chosen peer moves a median
    more than most modelling choices do and does it invisibly, so a set that exists needs a
    person — and a set that is empty needs nobody, because there is no comparison to defend.
    """
    produced = context.outputs.get(PEER_SET_STEP, {})
    if not peer_set_required(produced):
        return StepResult(output={"gate": GateKind.PEER_SET.value, "required": False, "peers": 0})

    return await _require_approval(context, gate=GateKind.PEER_SET, of_step=PEER_SET_STEP)


# ==========================================================================================
# 4. Extract
# ==========================================================================================


async def _extract(context: StepContext) -> StepResult:
    """Parse the archived document and persist the point-in-time facts.

    Parsed from the **artefact**, not from a response held in memory. The artefact is the
    authoritative copy, and if the two could differ then the facts and the evidence a
    citation verifies against would be different documents.
    """
    request = await _request_for(context)
    acquired = context.output_of("acquire")
    store = context.service("store")

    payload = await store.read(acquired["artefact_sha256"])
    parsed = parse_company_facts(payload)

    selection = select_point_in_time(parsed.facts, as_of_date=request.as_of_date)

    company = await context.session.get(Company, _uuid(acquired["company_id"]))
    document = await context.session.get(SourceDocument, _uuid(acquired["source_document_id"]))
    if company is None or document is None:  # pragma: no cover -- written by the prior step
        message = "The acquire step's rows are missing."
        raise StepPaused(message, gate=None)

    written = await persist_facts(
        context.session,
        company=company,
        source_document=document,
        facts=selection.chosen,
        basis=FactBasis.AS_REPORTED,
    )

    # Each persisted fact's value, located in the archived document and recorded as an
    # extraction (task 45): this is what lets a numeric claim naming the fact carry a
    # citation the deterministic verifier can re-read and confirm. A value the locator
    # cannot find is skipped, not invented — the fact still traces to its document.
    extracted = await extract_bytes(payload, extractor="json", settings=context.service("settings"))
    excerpts = []
    for fact in selection.chosen:
        found = extracted.locate(f'"val":{fact.value}') or extracted.locate(str(fact.value))
        if found is not None:
            excerpts.append(found)
    fact_extractions = await record_excerpts(
        context.session,
        source_document_id=document.id,
        extracted=extracted.text,
        excerpts=excerpts,
    )

    # Tags that produced facts and reached no canonical concept. **Kept, not dropped**: the
    # concept map is deliberately the top sixty rather than the whole taxonomy, so a filing
    # falling outside it is expected — and a run that silently ignored the overflow would be
    # a run whose statements are missing lines nobody was told about.
    unmapped = tuple(sorted({f"{c.taxonomy}:{c.tag}" for c in parsed.unmapped}))

    output: dict[str, Any] = {
        "facts_written": written,
        "fact_extractions": len(fact_extractions),
        "facts_chosen": len(selection.chosen),
        "facts_rejected": len(selection.rejected),
        "rejected_for_look_ahead": len(selection.rejected_for_look_ahead),
        "exchange": request.exchange,
        "unmapped_tags": list(unmapped),
        "load_errors": [],
    }
    # The hash of exactly what the gate will display, on the same terms as the plan gate: an
    # approval recorded against a different set of tags is not an approval of this one.
    output["payload_hash"] = sha256_hex(canonical_json(unmapped_gate_payload(output)))
    return StepResult(output=output)


# ==========================================================================================
# 5. Calculate
# ==========================================================================================


async def _calculate(context: StepContext) -> StepResult:
    """Compute the slice's one figure, traced to the facts it came from."""
    acquired = context.output_of("acquire")
    company_id = _uuid(acquired["company_id"])

    facts = list(
        await context.session.scalars(
            select(FinancialFact)
            .where(
                FinancialFact.company_id == company_id,
                FinancialFact.concept == SLICE_CONCEPT,
                FinancialFact.unit == "USD",
            )
            .order_by(FinancialFact.period_end)
        )
    )

    minimum_for_a_growth_rate = 2
    if len(facts) < minimum_for_a_growth_rate:
        # Not an error: a company with one year of filed revenue genuinely has no growth
        # rate. The report says so rather than the run failing.
        _log.info("workflow.no_calculation_possible", facts=len(facts))
        return StepResult(output={"calculation_id": None, "reason": "fewer than two periods"})

    first, last = facts[0], facts[-1]
    calc_context = calculation_service.new_context()

    result = cagr(
        calc_context,
        start=money(first.value, "USD", source=SourceRef.fact(first.id, label="revenue")),
        end=money(last.value, "USD", source=SourceRef.fact(last.id, label="revenue")),
        years=last.period_end.year - first.period_end.year,
    )

    rows = await calculation_service.persist_context(
        context.session, calc_context, job_id=context.job.id
    )

    return StepResult(
        output={
            "calculation_id": str(rows[-1].id),
            "concept": SLICE_CONCEPT,
            "value": str(result.value),
            "unit": result.unit.symbol,
            "from_period": first.period_end.isoformat(),
            "to_period": last.period_end.isoformat(),
            "source_document_id": str(first.source_document_id),
        }
    )


# ==========================================================================================
# 6. Draft
# ==========================================================================================


def _research(topic: ResearchTopic) -> Any:
    """One research worker as a workflow node, closed over its topic.

    The findings live on the step's own output row: they are the node's product, they are
    already JSON, and the drafting layer reads its inputs from step outputs. The audit
    trail of every tool request — executed and refused alike — travels with them.
    """

    async def run(context: StepContext) -> StepResult:
        request = await _request_for(context)
        agent_context = AgentContext(
            session=context.session,
            provider=context.service("provider"),
            router=context.service("router"),
            settings=context.service("settings"),
            store=context.service("store"),
            job_step=context.step,
        )
        investigation = await run_worker(
            agent_context, context.session, topic=topic, request=request
        )
        return StepResult(
            output={
                "topic": topic.value,
                "report": investigation.report.model_dump(mode="json"),
                "tool_calls": investigation.tool_calls,
                "rounds": investigation.rounds,
                "requests": [item.as_dict() for item in investigation.executed],
            },
            cost_gbp=agent_context.spend_gbp,
        )

    return run


async def _draft(context: StepContext) -> StepResult:
    """Fill in every section this run has, whatever they are.

    **No section key appears here.** The step iterates ``report_sections`` and routes
    each by what it *is*: a built-in goes to the generic contract-filler, a custom
    section (``origin='skill'``) executes under the ``<user_skill>`` contract against
    its pinned composed policy (task 38, ADR 0037). A failed custom section is a
    recorded state the run continues past, never an absent section.
    """
    request = await _request_for(context)

    # The planner's one-line brief per section, approved at gate 1. Keyed lookup rather
    # than trusting order: the planner proposes focus for the sections it chose to speak
    # about, and a section it named none for is written from its contract alone.
    focus_by_key: dict[str, str] = {}
    if context.job.plan_id is not None:
        plan = await context.session.get(ResearchPlan, context.job.plan_id)
        if plan is not None:
            focus_by_key = {
                str(entry.get("key", "")): str(entry.get("focus", ""))
                for entry in (plan.plan or {}).get("sections", [])
                if isinstance(entry, dict)
            }

    pins = await pinned_skills_for_job(context.session, job=context.job)
    pin_by_skill = {pin.skill_id: pin for pin in pins}
    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )

    # The platform-filled sections first, so a zero-budget definition with no registered
    # builder fails here — while the seed is the last thing that changed — rather than
    # rendering as an inexplicable blank later.
    deterministic = await fill_deterministic_sections(
        context.session, job=context.job, request=request, stage=SectionStage.DRAFT
    )

    sections = await sections_for_job(context.session, context.job.id)
    filled = 0
    custom_outcomes: list[dict[str, Any]] = []
    builtin_outcomes: list[dict[str, Any]] = []

    for section in sections:
        definition = section.definition
        if definition.origin != SKILL and definition.token_budget == 0:
            # Deterministic: filled above at this stage, or by the stage that owns it.
            continue
        if definition.origin == SKILL:
            pin = pin_by_skill.get(definition.skill_id) if definition.skill_id is not None else None
            if pin is None:
                # A skill-origin section this plan never pinned has no approved policy
                # to run under, and running it anyway would execute something gate 1
                # never displayed.
                section.status = SectionStatus.FAILED
                section.low_confidence_reason = (
                    "No skill pin exists for this section on this run's plan, so there "
                    "is no approved composed policy to execute it under."
                )
                custom_outcomes.append(
                    {"section_key": section.section_key, "status": section.status.value}
                )
                continue
            execution = await execute_custom_section(
                agent_context, section=section, pin=pin, request=request
            )
            custom_outcomes.append(execution.as_dict())
            if execution.status is SectionStatus.GENERATED:
                filled += 1
            continue

        execution = await execute_builtin_section(
            agent_context,
            section=section,
            request=request,
            focus=focus_by_key.get(section.section_key, ""),
        )
        builtin_outcomes.append(execution.as_dict())
        if execution.status is SectionStatus.GENERATED:
            filled += 1

    await context.session.flush()

    return StepResult(
        output={
            "sections_drafted": filled,
            "deterministic_sections": deterministic,
            "builtin_sections": builtin_outcomes,
            "custom_sections": custom_outcomes,
            # No payload hash here since task 40: the red team's challenges join the
            # gate-2 payload after drafting, so the hash the gate verifies is computed
            # by the red_team step — the last one that can change it.
        },
        cost_gbp=agent_context.spend_gbp,
    )


async def final_gate_payload(session: AsyncSession, *, job_id: uuid.UUID) -> dict[str, Any]:
    """Exactly what gate 2 approves, as one structure.

    Built here and used both by the draft step and by the review page, so "what the run
    hashed" and "what the operator was shown" are the same object by construction. Two
    functions producing the same shape would be two functions that eventually do not, and
    the symptom would be a gate that refuses every approval for reasons nobody can see.

    **Escalated disagreements are part of the payload, not a decoration beside it.** That
    means they are inside the hash the approval records, so "approved with these three
    conflicts outstanding" is a verifiable statement afterwards rather than a claim about
    what a page happened to render. It also means settling one invalidates a stale approval
    of the older draft, which is correct: the evidence changed.

    **The §2.4 triggers ride inside the hash on the same argument** (task 41). "Approved
    with the look-ahead banner showing" must be verifiable, and a trigger outside the hash
    could fire after the approval without invalidating it. The trigger engine is pure over
    rows that are frozen once the red-team step has run, so the hash sealed there and the
    hash the review page computes live agree — a property the tests hold, not assume.
    """
    sections = await sections_for_job(session, job_id)
    escalations = await escalations_for_job(session, job_id)
    triggers: tuple[FiredTrigger, ...] = ()
    job = await session.get(Job, job_id)
    if job is not None:
        request = await session.get(ResearchRequest, job.request_id)
        if request is not None:
            triggers = await triggers_for_job(session, job=job, request=request)
    return {
        # Status and the degradation note ride inside the hash: a failed custom section
        # and an insufficiency banner are part of what the operator approves, and a
        # payload without them would let "approved" mean "approved, unaware".
        "sections": [
            {
                "key": s.section_key,
                "content": s.content,
                "status": s.status.value,
                "note": s.low_confidence_reason,
            }
            for s in sections
        ],
        "escalations": [
            {
                "id": str(row.id),
                "topic": row.topic,
                "kind": row.kind.value,
                "rule": row.rule.value,
                "rationale": row.resolution_rationale,
                "material": row.material,
                "position_a": row.position_a,
                "position_b": row.position_b,
            }
            for row in escalations
        ],
        "triggers": [trigger.as_record() for trigger in triggers],
    }


# ==========================================================================================
# 8. Render
# ==========================================================================================


async def sector_note_for(session: AsyncSession, *, job: Job) -> SectorNote | None:
    """What this run's sector obliges its report to say, or ``None`` for an ordinary company.

    Read from the *confirmed* classification rather than from the proposal, so a report can
    only carry limitations somebody agreed applied. A run that reaches here with an
    unconfirmed specialist proposal has already been stopped by the gate.

    Public since task 46: the document preview pages assemble with exactly what the
    render step assembles with, by calling exactly what it calls.
    """
    profile, _ = await confirmed_classification(session, job)
    if profile is None:
        return None

    computed = {
        row.name
        for row in await session.scalars(select(Calculation).where(Calculation.job_id == job.id))
    }
    disclosure = metric_disclosure(profile, computed=computed)

    return SectorNote(
        label=profile.label,
        warnings=profile.warnings,
        blocked_models=tuple(model.value for model in profile.blocked_models),
        metric_disclosure=disclosure.as_paragraph(),
    )


async def comps_note_for(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> WithheldComps | None:
    """What this run's comparables work obliges its report to say, or ``None``.

    ``None`` when no peer set was confirmed, because "no comparison was performed" and "a
    comparison whose figures you are not being shown" are different claims and only the
    second needs saying.

    Returns a :class:`~aer.calc.comps.WithheldComps` and never a table. A rendered report is
    the shareable artefact, and every multiple in it would derive from market data licensed
    for internal use only — see `_comps_block` in :mod:`aer.render.markdown`.
    """
    confirmed = await confirmed_peer_set(session, job)
    if not confirmed:
        return None

    aligned, excluded = align_peers(
        [(peer.identifier, peer.name, peer.period_end) for peer in confirmed],
        subject_period_end=request.as_of_date,
    )
    return WithheldComps(
        peer_count=len(aligned),
        excluded_count=len(excluded),
        as_of=request.as_of_date,
        licence_note=DEFAULT_POLICIES[Provider.EODHD].licence_note,
    )


async def _render(context: StepContext) -> StepResult:
    """Render the Markdown, archive it, and freeze the report."""
    request = await _request_for(context)
    acquired = context.output_of("acquire")
    store = context.service("store")

    company = await context.session.get(Company, _uuid(acquired["company_id"]))

    comps = await comps_note_for(context.session, job=context.job, request=request)
    rendered = await render_markdown(
        context.session,
        job=context.job,
        request=request,
        company=company,
        comps=comps,
        sector=await sector_note_for(context.session, job=context.job),
        charts=await exportable_charts_for(
            context.session,
            job=context.job,
            request=request,
            licence_note=comps.licence_note if comps else "",
        ),
    )

    artefact = await store_artefact(
        context.session,
        store,
        data=rendered.markdown.encode("utf-8"),
        media_type="text/markdown",
    )

    approval = await context.session.scalar(
        select(Approval).where(Approval.job_id == context.job.id, Approval.gate == GateKind.FINAL)
    )

    report = Report(
        job_id=context.job.id,
        request_id=request.id,
        company_id=company.id if company is not None else None,
        as_of_date=request.as_of_date,
        rating=None,
        confidence=None,
        content={"markdown": rendered.markdown, "sections": rendered.section_keys},
        content_hash=sha256_hex(rendered.markdown),
        markdown_artefact_id=artefact.artefact.id,
        approved_by=approval.actor_user_id if approval is not None else None,
        approved_at=approval.decided_at if approval is not None else None,
        # Frozen only because a human approved it. The check constraint enforces the same
        # rule, so an immutable report always has an approval behind it.
        immutable=approval is not None,
    )
    context.session.add(report)

    context.job.status = JobStatus.SUCCEEDED
    context.job.finished_at = datetime.now(UTC)
    await context.session.flush()

    return StepResult(
        output={
            "report_id": str(report.id),
            "markdown_sha256": artefact.sha256,
            "footnotes": rendered.footnote_count,
            "sections": rendered.section_keys,
            "characters": len(rendered.markdown),
        }
    )


# ==========================================================================================
# Helpers
# ==========================================================================================


async def _request_for(context: StepContext) -> ResearchRequest:
    request = await context.session.get(ResearchRequest, context.job.request_id)
    if request is None:  # pragma: no cover -- a job cannot exist without its request
        message = "The job's research request is missing."
        raise StepPaused(message, gate=None)
    return request


def _uuid(value: Any) -> uuid.UUID:
    """Parse an identifier that travelled through a step's JSON output."""
    return uuid.UUID(str(value))
