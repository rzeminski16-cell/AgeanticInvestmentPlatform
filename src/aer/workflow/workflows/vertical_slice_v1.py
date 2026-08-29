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

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from aer.agents.base import AgentContext
from aer.agents.peers import PROPOSED_BY as PEERS_PROPOSED_BY
from aer.agents.peers import PeerProposalAgent, PeerProposalInput
from aer.agents.plan_critic import PlanCriticAgent, PlanCriticInput, PlanCritique
from aer.agents.planner import PlannerAgent, PlannerInput, PriorResearch, salvaged_plan
from aer.agents.themes import PROPOSED_BY as THEMES_PROPOSED_BY
from aer.agents.themes import ThemeProposalAgent, ThemeProposalInput, ThemeSlate
from aer.agents.verdict import VerdictAgent, VerdictInput
from aer.agents.worker import ResearchTopic, WorkerExhaustedError, degraded_report
from aer.calc.basic import cagr
from aer.calc.comps import MultipleBasis, WithheldComps
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef, Unit, money
from aer.core.concepts import CANONICAL_CONCEPTS
from aer.core.disagreement import DisagreementKind
from aer.core.enums import Decision, FactBasis, GateKind, JobStatus, Provider, SourceTier
from aer.core.escalation import FiredTrigger
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.schemas.facts import RawFact
from aer.core.schemas.request import ResearchRequestRead
from aer.core.sectors import (
    ModelNotPermittedError,
    ValuationMandate,
    ValuationModel,
    mandate_for,
    model_for,
    profile_for,
    unclassified_mandate,
)
from aer.db.models import (
    Approval,
    Assumption,
    Calculation,
    Company,
    FinancialFact,
    Job,
    JobStep,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
    RevisionNote,
    SectionStatus,
    SourceDocument,
)
from aer.db.models.plan_skill_pin import PLANNED as PIN_PLANNED
from aer.db.models.plan_skill_pin import SKIPPED_NOT_APPLICABLE, PlanSkillPin
from aer.db.models.revision_note import DISPOSITION_REVISED as REVISION_DISPOSITION_REVISED
from aer.db.models.revision_note import DISPOSITION_STOOD as REVISION_DISPOSITION_STOOD
from aer.db.models.revision_note import SCOPE_PLAN as REVISION_SCOPE_PLAN
from aer.db.models.section_definition import BUILTIN, SKILL
from aer.errors import AerError, BudgetExceededError, ValidationError
from aer.extract import extract_bytes
from aer.fetch.policy import DEFAULT_POLICIES
from aer.providers.protocol import SpentButUnusableError
from aer.render.document import assemble_document
from aer.render.html import render_html
from aer.render.markdown import SectorNote, serialise_markdown
from aer.render.pdf import render_pdf
from aer.sections.deterministic import SectionStage, fill_deterministic_sections
from aer.sections.evidence import SectionExecution
from aer.sections.registry import create_report_sections, resolve_sections, sections_for_job
from aer.sections.writing import execute_builtin_section
from aer.services import calculations as calculation_service
from aer.services.acquisition import record_acquisition
from aer.services.analysis import analyse_company
from aer.services.artefacts import store_artefact
from aer.services.assumption_gate import assemble as assemble_assumptions
from aer.services.assumption_gate import gate_payload as gate_payload_for_assumptions
from aer.services.assumption_gate import gate_required, refreshed_payload
from aer.services.assumption_gate import valuation_model as assumptions_valuation_model
from aer.services.assumptions import assumptions_for_request
from aer.services.citations import review_evidence
from aer.services.comps import (
    PEER_SET_STEP,
    confirmed_peer_set,
    peer_set_payload,
    peer_set_required,
    propose_peers_from_sic,
)
from aer.services.comps_run import build_comps_table
from aer.services.consistency import check_report_consistency
from aer.services.disagreements import escalations_for_job
from aer.services.escalation import triggers_for_job
from aer.services.evaluations import evaluate_run
from aer.services.exhibits import exportable_charts_for
from aer.services.extractions import record_excerpts
from aer.services.facts import persist_facts, upsert_company
from aer.services.filings import acquire_filings
from aer.services.history import prior_digest_for
from aer.services.peer_discovery import DiscoveredPeers, discover_peers, merged_with
from aer.services.price_acquisition import acquire_prices
from aer.services.red_team import run_red_team
from aer.services.research import build_executors, run_worker
from aer.services.residual_income_run import value_the_bank
from aer.services.revision import revise_challenged_sections, revisions_for_job
from aer.services.sectors import (
    CLASSIFY_STEP,
    classification_payload,
    confirmed_classification,
    metric_disclosure,
    propose_from_sic,
    sector_gate_required,
)
from aer.services.segments import sweep_segment_facts
from aer.services.subject import subject_name
from aer.services.themes import (
    THEME_STEP,
    existing_vocabulary,
    normalised_slate,
    record_confirmed_themes,
    theme_set_payload,
    theme_set_required,
)
from aer.services.valuation_run import value_the_business
from aer.skills.execution import execute_custom_section
from aer.skills.resolution import (
    custom_definitions_for_pins,
    estimate_custom_section_cost,
    pinned_skills_for_job,
    pinned_skills_for_work_order,
    resolve_skills_for_plan,
)
from aer.sources.sec.companyfacts import UnmappedConcept, parse_company_facts
from aer.sources.sec.pit import select_point_in_time
from aer.verify.citations import verify_job_citations
from aer.workflow.engine import StepContext, StepPaused, StepResult, WorkflowStep

__all__ = [
    "ASSUMPTIONS_STEP",
    "COMPS_STEP",
    "FORECAST_YEARS",
    "PRICES_STEP",
    "VALUE_STEP",
    "WORKFLOW_VERSION",
    "assumptions_gate_payload",
    "assumptions_gate_refreshed",
    "assumptions_gate_required",
    "build_steps",
    "comps_note_for",
    "final_gate_payload",
    "gate_payload",
    "peer_gate_payload",
    "plan_gate_payload",
    "sector_gate_payload",
    "sector_key_of",
    "sector_note_for",
    "step_output",
    "theme_gate_payload",
    "unmapped_gate_payload",
    "unmapped_gate_required",
]

_log = structlog.get_logger("aer.workflow.vertical_slice")

WORKFLOW_VERSION: Final = "vertical_slice_v1"

# What the planner step is expected to cost. Used by the budget guard *before* the call, so
# it is necessarily an estimate; the real figure is metered afterwards. Every figure below
# was recalibrated against the first complete run (polish P8): an estimate sits at or a
# little above the observed cost, because the guard checks each step's projection before
# running it and a figure 2.4x low is 2.4x less protection at that step — while one 4x
# high pauses runs the budget could actually afford. The planner measured £0.171.
PLANNER_ESTIMATE_GBP: Final = Decimal("0.20")

# Per research worker (task 37): a bounded request/execute loop on the analysis route.
# Measured £0.083-£0.241 across the five workers; the estimate covers the dearest, plus
# the worker's bounded web searches (ADR 0092) — at most three per node at the verified
# $0.01 fee, carried by small routed calls whose results also enter later rounds as
# input.
WORKER_ESTIMATE_GBP: Final = Decimal("0.30")

# The validate step (task 39): at most a handful of capped advisory calls on the
# validator route, and frequently none at all — the run measured £0.00, and the
# deterministic rows cost nothing.
VALIDATOR_ESTIMATE_GBP: Final = Decimal("0.02")

# The red team (task 40): §1.8 budgeted the bear case at 90k in / 10k out on Opus via the
# batch path; the run measured £0.251. Zero-cost on the runs that skip it for want of
# claims.
RED_TEAM_ESTIMATE_GBP: Final = Decimal("0.35")

# The assumption proposals (gap B2c, ADR 0046): one Opus call at high effort returning two
# short justifications. The input carries the derived history and the run's findings, so
# it is not free — but it measured £0.065, a third of the original guess.
ASSUMPTIONS_ESTIMATE_GBP: Final = Decimal("0.10")

# The peer proposal (ADR 0059): one workhorse call at medium effort whose input is the
# company's identity and classification and whose output is at most eight short entries.
# The cheapest model call in the workflow — measured £0.014 — and it carries an estimate
# for the reason the draft eventually did: a step with no estimate is a step the budget
# guard waves through, and ADR 0052 makes that a test rather than a convention.
PEER_PROPOSAL_ESTIMATE_GBP: Final = Decimal("0.02")

# The theme proposer is the same shape of call as the peer proposer — one short slate
# from identity and classification — so it carries the same estimate.
THEME_PROPOSAL_ESTIMATE_GBP: Final = Decimal("0.02")

# The plan critic plus at most one planner revision (ADR 0091): the critic's input is the
# request and the plan alone — smaller than the planner's own — and the revision is the
# planner's measured call again. Priced as the pair, because the step spends both when a
# challenge clears the revision threshold.
CRITIQUE_PLAN_ESTIMATE_GBP: Final = Decimal("0.30")

# The revise pass (ADR 0091): at most MAX_REVISED_SECTIONS section redrafts, priced from
# the draft step's measurement — £4.84 across sixteen sections is roughly £0.30 each, and
# the margin covers the challenged sections skewing dear (they are the ones with claims).
REVISE_ESTIMATE_GBP: Final = Decimal("1.50")

# The review gate's authored half (ADR 0087): one sentence over a digest of outcomes, on
# the cheapest route the platform has. The margin over the expected fraction of a penny is
# the guard's, not the step's — a step with no estimate is a step the cap cannot pause.
VERDICT_ESTIMATE_GBP: Final = Decimal("0.10")

# The severity at which a plan challenge sends the plan back for a revision. Deliberately
# below the draft's material line (severity 4): a plan revision costs one planner call
# while a wrong plan costs the run, and gate 1 sees the critique either way.
PLAN_REVISION_SEVERITY: Final = 3

# The draft: one Opus call per model-written section, and by a wide margin the most
# expensive step in the workflow — a measured £5.17 on the first full live run.
#
# It carried no estimate at all until that run, which meant it carried no *guard*: both
# budget-check sites are written `if step.estimated_cost_gbp > 0`, so a step with no estimate
# is a step the cap silently waves through. The most expensive step in the run was the one
# ceiling could not pause, which is invariant 6's failure mode exactly — a cap that does not
# see the biggest spender is a cap that does not work.
#
# Recalibrated to the measurement: £4.84 on the complete run (the earlier, crashed run's
# £5.17 predates the length salvage that stopped paid drafts being discarded), with the
# margin above it. The guard is still only checked *before* the step, so
# this bounds when the draft may start, not what it may spend once running; per-section
# checking is a larger change than this one and is recorded as a gap rather than smuggled in.
DRAFT_ESTIMATE_GBP: Final = Decimal("5.00")

# How many sections draft at once when the run has a session factory (polish P10).
# Sections share the research fan-out's shape — each depends on the evidence pack and on
# nothing another section produces — but the bound is deliberately narrower than "all of
# them": the budget guard reads committed spend, so every section in flight checks the cap
# against the same total, and sixteen at once would widen that window in the phase that
# exists to make the cap trustworthy. Four keeps the window the size the research wave
# already established.
DRAFT_FAN_OUT: Final = 4

# How long the explicit forecast runs before the terminal value takes over. Five years is
# the convention, and the derived proposals are flat across it in any case — an operator who
# wants a fade enters `revenue_growth_y1` through `_y5` and the flat proposal steps aside.
# Not a request field, because a horizon somebody picks per run is a modelling choice this
# platform has no way to justify differently for each company.
FORECAST_YEARS: Final = 5

ASSUMPTIONS_STEP: Final = "propose_assumptions"
VALUE_STEP: Final = "value"
PRICES_STEP: Final = "acquire_prices"
COMPS_STEP: Final = "comps"

# What the gate shows as a runtime estimate. A constant for the slice, which does one
# fetch and one calculation; Phase 3 derives it from the plan.
_RUNTIME_ESTIMATE_SECONDS: Final = 120

# The concept this slice calculates. One, on purpose: the deliverable is the chain from
# filing to footnote, and a second concept would test the same chain twice.
SLICE_CONCEPT: Final = "revenue"

# How sure the platform is of a publication date it worked out rather than read. Below the
# certainty of a date printed on a filing, because it is an inference — a sound one, but an
# inference — and a reader comparing two documents' dates should be able to see which was
# stated and which was derived.
_DERIVED_FROM_CONTENTS: Final = 0.9


def build_steps() -> list[WorkflowStep]:
    """The workflow, in order."""
    return [
        WorkflowStep(key="plan", run=_plan, estimated_cost_gbp=PLANNER_ESTIMATE_GBP),
        # The plan's adversary, before the person (ADR 0091): scored challenges from a
        # separate context, one planner revision where a challenge clears the threshold,
        # and the critique inside the gate-1 hash so approving the plan approves it with
        # the critique in view.
        WorkflowStep(
            key="critique_plan",
            run=_critique_plan,
            estimated_cost_gbp=CRITIQUE_PLAN_ESTIMATE_GBP,
        ),
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
        WorkflowStep(
            key=PEER_SET_STEP,
            run=_propose_peers,
            estimated_cost_gbp=PEER_PROPOSAL_ESTIMATE_GBP,
        ),
        # Conditional: passes straight through when nothing comparable is in the database.
        # A run with no peers has no comparison to defend and should not wait at a gate to
        # confirm an empty list.
        WorkflowStep(
            key="gate_peer_set",
            run=_gate_peer_set,
            gate=GateKind.PEER_SET.value,
        ),
        # Themes after classification for the same reason peers are: what kind of business
        # this is shapes which larger stories it belongs to (K1, ADR 0065). A failed model
        # call proposes nothing and the run continues — there is no deterministic floor for
        # a judgement about the market, and no themes is a fact rather than a failure.
        WorkflowStep(
            key=THEME_STEP,
            run=_propose_themes,
            estimated_cost_gbp=THEME_PROPOSAL_ESTIMATE_GBP,
        ),
        # Conditional: passes straight through when nothing was proposed. A run with no
        # themes has no edges to defend and should not wait to confirm an empty list.
        WorkflowStep(
            key="gate_theme_set",
            run=_gate_theme_set,
            gate=GateKind.THEME_SET.value,
        ),
        # Prices (gap B3). After the peer gate because the comps table needs both, and
        # before the assumptions are proposed because the beta this regresses is one of
        # them — without it the operator types a beta by hand and the valuation waits.
        # Conditional on a subscription: no key, no prices, and the step says so.
        WorkflowStep(key=PRICES_STEP, run=_acquire_prices),
        WorkflowStep(key="extract", run=_extract),
        # Conditional: it passes straight through unless the extraction left tags the concept
        # map does not know. Declared unconditionally because a gate that only exists on the
        # runs that need it is a gate nobody can find when a run needs it.
        WorkflowStep(
            key="gate_unmapped_concepts",
            run=_gate_unmapped_concepts,
            gate=GateKind.UNMAPPED_CONCEPTS.value,
        ),
        # The first real fan-out (task 37): the calculation and the five research workers
        # are independent of each other and all of the financials gate, so they form one
        # wave — six nodes, inside the §2.5 bound of seven. Where the run has no session
        # factory (every savepoint-fixtured test) the engine takes them one at a time on
        # the caller's session, in this declared order.
        WorkflowStep(key="calculate", run=_calculate, needs=frozenset({"gate_unmapped_concepts"})),
        WorkflowStep(
            key="research_company",
            run=_research(ResearchTopic.COMPANY),
            needs=frozenset({"gate_unmapped_concepts"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_industry",
            run=_research(ResearchTopic.INDUSTRY),
            needs=frozenset({"gate_unmapped_concepts"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_macro",
            run=_research(ResearchTopic.MACRO),
            needs=frozenset({"gate_unmapped_concepts"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_recent_developments",
            run=_research(ResearchTopic.RECENT_DEVELOPMENTS),
            needs=frozenset({"gate_unmapped_concepts"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        WorkflowStep(
            key="research_technical_context",
            run=_research(ResearchTopic.TECHNICAL_CONTEXT),
            needs=frozenset({"gate_unmapped_concepts"}),
            estimated_cost_gbp=WORKER_ESTIMATE_GBP,
        ),
        # Comparables (gap B3). After the peer gate, which confirmed the set, and after
        # the prices, which supply the market capitalisation the enterprise-value multiples
        # need. **Before the assumptions gate rather than after it**: a comps table is a
        # relative judgement that waits on no forecast, and an operator deciding a terminal
        # growth rate is better off able to see what the market pays for the peers.
        WorkflowStep(key=COMPS_STEP, run=_comps, needs=frozenset({"calculate", PRICES_STEP})),
        # The assumptions a discounted cash flow rests on (gap B2c, ADR 0046). After the
        # analysis because six of them are derived from it, and after the research because
        # the two that are judgements are proposed against what the run found. Conditional
        # on the sector mandate: a bank is proposed nothing, because it is never going to be
        # given a forecast.
        WorkflowStep(
            key=ASSUMPTIONS_STEP,
            run=_propose_assumptions,
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
            estimated_cost_gbp=ASSUMPTIONS_ESTIMATE_GBP,
        ),
        # **The one gate that guards work which has not happened yet.** Every other gate
        # approves something produced; this approves the numbers a valuation is about to be
        # built on, some of them a model's. Skipped when the run has nothing to confirm.
        WorkflowStep(
            key="gate_assumptions",
            run=_gate_assumptions,
            gate=GateKind.ASSUMPTIONS.value,
        ),
        # The forecast itself, once a person has agreed the numbers behind it. Before the
        # draft, because the valuation and scenario sections have nothing to write from
        # otherwise — which is the state gap B2 described.
        WorkflowStep(key=VALUE_STEP, run=_value, needs=frozenset({"gate_assumptions"})),
        WorkflowStep(
            key="draft",
            run=_draft,
            needs=frozenset(
                {
                    "calculate",
                    COMPS_STEP,
                    "gate_assumptions",
                    VALUE_STEP,
                    "research_company",
                    "research_industry",
                    "research_macro",
                    "research_recent_developments",
                    "research_technical_context",
                }
            ),
            estimated_cost_gbp=DRAFT_ESTIMATE_GBP,
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
        # The writer's second attempt (ADR 0091): the sections the material challenges
        # attack are redrafted once, with the challenge in front of the writer, before a
        # person ever sees the draft. Seals the gate-2 hash, as the new last step that
        # can change what the operator is shown.
        WorkflowStep(key="revise", run=_revise, estimated_cost_gbp=REVISE_ESTIMATE_GBP),
        # The review gate's authored half (ADR 0087), written once the draft has frozen.
        # After revise deliberately: the subject must have stopped changing. It writes no
        # section, joins no payload and seals no hash — the gate-2 hash stays with revise,
        # because interpretation is never part of what the operator approves.
        WorkflowStep(key="verdict", run=_verdict, estimated_cost_gbp=VERDICT_ESTIMATE_GBP),
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

    # Prior approved research on this company, digested for the planner (K2, ADR 0064).
    # Hypothesis material bounded by the as-of date: a plan may ask what changed since the
    # last view; it may never inherit an answer. A first run — or a company the platform
    # has not resolved yet — feeds forward nothing, and the prompt says nothing about it.
    priors = await _prior_digests(context.session, request=request)

    try:
        draft = await agent.run(
            agent_context,
            PlannerInput(
                request=ResearchRequestRead.model_validate(request, from_attributes=True),
                available_section_keys=[definition.key for definition in definitions],
                prior_research=priors,
            ),
        )
    except SpentButUnusableError as unusable:
        # The planner is one call with no retry, so an over-full list — eleven risks
        # against a bound of ten, on a live run — killed the whole run at step one.
        # When cutting the lists to their bounds repairs the reply, the trimmed plan
        # proceeds to gate 1, where the operator sees exactly what will run (gap A42).
        rescued = salvaged_plan(unusable)
        if rescued is None:
            raise
        draft, trimmed = rescued
        _log.warning("planner.lists_trimmed", job_id=str(context.job.id), trimmed=trimmed)

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
    # One line the operator reads at gate 1: whether prior research was in front of the
    # planner, and how much. Inside the stored body, so it is inside the hash — a plan
    # informed by history and one planned blind are different proposals, and approving
    # one must not approve the other.
    payload["prior_research"] = _prior_research_note(priors)
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
    pins = await pinned_skills_for_work_order(context.session, work_order_id=plan.request_id)
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


async def _prior_digests(session: AsyncSession, *, request: ResearchRequest) -> list[PriorResearch]:
    """This company's prior approved conclusions, as the planner's typed input.

    The company is found by listing, exactly as the prior-run comparison finds it; a
    request the platform has never resolved has no company row and no history. The
    ``before`` bound is the request's as-of date, so a point-in-time run cannot be shaped
    by a view recorded in its future.
    """
    company = await session.scalar(
        select(Company).where(
            Company.ticker == request.ticker, Company.exchange == request.exchange
        )
    )
    if company is None:
        return []
    return [
        PriorResearch(
            report_id=str(digest.report_id),
            as_of_date=digest.as_of_date.isoformat(),
            rating=digest.rating,
            confidence=digest.confidence,
            valuation_range=digest.valuation_range,
            named_risks=list(digest.named_risks),
            catalyst_lines=list(digest.catalyst_lines),
        )
        for digest in await prior_digest_for(
            session, company_id=company.id, before=request.as_of_date
        )
    ]


def _prior_research_note(priors: Sequence[PriorResearch]) -> str:
    """The gate-1 sentence saying what the planner had in front of it. Empty for none."""
    if not priors:
        return ""
    newest = priors[0].as_of_date
    count = len(priors)
    plural = "s" if count != 1 else ""
    return (
        f"The planner was shown {count} prior approved report{plural} on this company "
        f"(newest as-of {newest}) as hypothesis material, labelled not-evidence. It may "
        "have shaped which questions this plan asks; it cannot support a claim, and the "
        "citation verifier rejects any attempt in code."
    )


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
        "prior_research": body.get("prior_research", ""),
        # The critic's challenges and whether the plan was revised for them (ADR 0091).
        # Inside the hash: a plan approved with a severity-4 challenge showing and one
        # approved clean are different approvals. Empty for a plan the critic never saw.
        "critique": dict(body.get("critique", {})),
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
    """Stop until a human approves the plan — with the critique in view (ADR 0091).

    The hash comes from the critique step, the last one that can change what the gate
    displays: it recomputed the payload after any revision and after the critique block
    joined the plan body.
    """
    return await _require_approval(context, gate=GateKind.PLAN, of_step="critique_plan")


async def _critique_plan(context: StepContext) -> StepResult:
    """Attack the plan from a separate context, revise it once if warranted, and re-seal.

    ADR 0091. The critic sees the request and the plan — there are no findings yet to
    leak — and a challenge at :data:`PLAN_REVISION_SEVERITY` or above sends the plan back
    to the planner exactly once, with the critique and its own previous proposal in front
    of it. The critique block then joins the plan body, so it sits inside the gate-1 hash
    and the reviewer approves the plan *with* its critique, never beside it.

    **The failure discipline is the peer proposer's.** A critic call that dies leaves the
    plan as proposed, says so, and re-seals the unchanged payload; a budget refusal is
    control flow the engine turns into a paused run, never absorbed.
    """
    request = await _request_for(context)
    plan = await context.session.get(ResearchPlan, context.job.plan_id)
    if plan is None:  # pragma: no cover -- written by the prior step
        message = "The plan step's row is missing."
        raise StepPaused(message, gate=None)

    body = dict(plan.plan or {})
    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )

    critique, consulted = await _critique_from_model(
        context, agent_context, request=request, body=body
    )
    actionable = [
        challenge
        for challenge in critique.challenges
        if challenge.severity >= PLAN_REVISION_SEVERITY
    ]

    revised = False
    if consulted and actionable:
        revised = await _revised_plan(
            context, agent_context, request=request, plan=plan, body=body, critique=critique
        )
        body = dict(plan.plan or {})

    body["critique"] = {
        "consulted": consulted,
        "revised": revised,
        "coverage_note": critique.coverage_note if consulted else "",
        "challenges": [
            {
                "aspect": challenge.aspect.value,
                "severity": challenge.severity,
                "statement": challenge.statement,
                "suggestion": challenge.suggestion,
            }
            for challenge in critique.challenges
        ],
    }
    plan.plan = body
    await context.session.flush()

    # A retried step re-decides from a fresh critique, so its record replaces the earlier
    # attempt's — this step is the only writer of the plan-scope notes, and duplicates
    # would double-count the run in `aer lessons`' note tally.
    await context.session.execute(
        delete(RevisionNote).where(
            RevisionNote.job_id == context.job.id,
            RevisionNote.scope == REVISION_SCOPE_PLAN,
        )
    )
    # One note per challenge (ADR 0091's memory half): what the loop did about each, so
    # `aer lessons` can count a class across runs whatever the loop decided this time.
    for challenge in critique.challenges:
        context.session.add(
            RevisionNote(
                job_id=context.job.id,
                scope=REVISION_SCOPE_PLAN,
                dimension=challenge.aspect.value,
                severity=challenge.severity,
                statement=challenge.statement,
                disposition=(
                    REVISION_DISPOSITION_REVISED
                    if revised and challenge.severity >= PLAN_REVISION_SEVERITY
                    else REVISION_DISPOSITION_STOOD
                ),
            )
        )
    await context.session.flush()

    # Refreshed before hashing, for the reason `_plan` refreshes: the hash must cover what
    # the database holds, and NUMERIC columns round what memory carried.
    await context.session.refresh(plan)
    pins = await pinned_skills_for_work_order(context.session, work_order_id=plan.request_id)
    payload_hash = sha256_hex(canonical_json(plan_gate_payload(plan, pins)))

    return StepResult(
        output={
            "payload_hash": payload_hash,
            "consulted": consulted,
            "challenges": len(critique.challenges),
            "actionable": len(actionable),
            "revised": revised,
        },
        cost_gbp=agent_context.spend_gbp,
    )


async def _critique_from_model(
    context: StepContext,
    agent_context: AgentContext,
    *,
    request: ResearchRequest,
    body: Mapping[str, Any],
) -> tuple[PlanCritique, bool]:
    """Ask the critic, or return an empty critique and say so.

    An outage or an unusable reply must not cost the run its gate — the plan as proposed
    is still a plan a person can judge — and a budget refusal is never absorbed.
    """
    try:
        critique = await PlanCriticAgent().run(
            agent_context,
            PlanCriticInput(
                company_name=request.company_name,
                ticker=request.ticker,
                exchange=request.exchange,
                as_of_date=request.as_of_date.isoformat(),
                point_in_time=request.point_in_time,
                analysis_mode=request.analysis_mode.value,
                investment_horizon_months=request.investment_horizon_months,
                focus_questions=list(request.focus_questions or []),
                summary=str(body.get("summary", "")),
                sections=list(body.get("sections", [])),
                planned_sources=list(body.get("planned_sources", [])),
                known_risks=list(body.get("known_risks", [])),
            ),
        )
    except BudgetExceededError:
        raise
    except (AerError, ValueError) as exc:
        _log.warning(
            "plan_critic.model_unavailable",
            job_id=str(context.job.id),
            error_code=getattr(exc, "code", ""),
        )
        return PlanCritique(challenges=[], coverage_note="unavailable"), False
    return critique, True


async def _revised_plan(
    context: StepContext,
    agent_context: AgentContext,
    *,
    request: ResearchRequest,
    plan: ResearchPlan,
    body: dict[str, Any],
    critique: PlanCritique,
) -> bool:
    """One planner revision against the critique. Returns whether the plan changed.

    Only the planner's own fields move — summary, per-section focus, sources, risks,
    confidence. The code-derived spine (`section_listing`), the prior-research note, the
    pins and the run's `report_sections` are all exactly as gate 1 resolved them: the
    critique challenges the proposal, never the platform's own assembly.

    A revision that fails leaves the original plan standing — the critique still reaches
    the gate, which is most of the value — and a budget refusal is never absorbed.
    """
    keys = [str(entry.get("key", "")) for entry in body.get("section_listing", [])]
    try:
        draft = await PlannerAgent().run(
            agent_context,
            PlannerInput(
                request=ResearchRequestRead.model_validate(request, from_attributes=True),
                available_section_keys=[key for key in keys if key],
                prior_research=await _prior_digests(context.session, request=request),
                previous_plan={
                    "summary": body.get("summary", ""),
                    "sections": body.get("sections", []),
                    "planned_sources": body.get("planned_sources", []),
                    "known_risks": body.get("known_risks", []),
                },
                critique=[
                    f"[{challenge.aspect.value}, severity {challenge.severity}/5] "
                    f"{challenge.statement} Suggestion: {challenge.suggestion}"
                    for challenge in critique.challenges
                    if challenge.severity >= PLAN_REVISION_SEVERITY
                ],
            ),
        )
    except BudgetExceededError:
        raise
    except SpentButUnusableError as unusable:
        rescued = salvaged_plan(unusable)
        if rescued is None:
            _log.warning("plan_critic.revision_unusable", job_id=str(context.job.id))
            return False
        draft, trimmed = rescued
        _log.warning("plan_critic.revision_trimmed", job_id=str(context.job.id), trimmed=trimmed)
    except (AerError, ValueError) as exc:
        _log.warning(
            "plan_critic.revision_failed",
            job_id=str(context.job.id),
            error_code=getattr(exc, "code", ""),
        )
        return False

    payload = draft.model_dump(mode="json")
    revised_body = dict(plan.plan or {})
    for key in ("summary", "sections", "planned_sources", "known_risks", "confidence"):
        revised_body[key] = payload[key]
    plan.plan = revised_body
    plan.planned_sources = payload["planned_sources"]
    plan.known_risks = payload["known_risks"]
    await context.session.flush()
    return True


# What an unmapped line is measured against, in order of preference. Revenue first because
# it is the figure a reader has in mind; total assets for a filer whose revenue line is
# itself an extension, which is rarer and exactly the case where the question matters most.
_SCALE_CONCEPTS: Final = ("revenue", "assets")


def _unmapped_rows(
    unmapped: Sequence[UnmappedConcept], *, chosen: Sequence[RawFact]
) -> list[dict[str, Any]]:
    """Each unmapped tag with the largest figure behind it, biggest share first.

    Gap R17. The gate asked "does this gap matter?" over a list of taxonomy element names —
    `us-gaap:SomeFilerExtension` and thirty-nine more — which is not a question anybody can
    answer from what was shown. One extension carrying a company's headline profit measure
    matters and forty carrying segment breakdowns do not, and the number is what tells them
    apart.

    **The largest absolute value, not the latest.** A tag's most recent observation can be a
    quarter, a restatement or a zero; what an operator is deciding is whether anything
    material hangs on this element, and the biggest figure it ever carried is the honest
    answer to that.
    """
    scale = _reference_figure(chosen)
    by_tag: dict[tuple[str, str], RawFact] = {}
    for fact in chosen:
        key = (fact.taxonomy, fact.raw_concept)
        held = by_tag.get(key)
        if held is None or abs(fact.value) > abs(held.value):
            by_tag[key] = fact

    rows: list[dict[str, Any]] = []
    for concept in unmapped:
        largest = by_tag.get((concept.taxonomy, concept.tag))
        share = (
            abs(largest.value) / scale
            if largest is not None and scale is not None and scale > 0
            else None
        )
        rows.append(
            {
                "tag": f"{concept.taxonomy}:{concept.tag}",
                "label": concept.label,
                "observations": concept.observations,
                "units": list(concept.units),
                "value": str(largest.value) if largest is not None else "",
                "unit": largest.unit if largest is not None else "",
                "period_end": largest.period_end.isoformat() if largest is not None else "",
                # A fraction, rendered as a percentage by the page. Empty where nothing
                # mapped to scale it against, which is a state worth showing rather than
                # papering over with a zero.
                "share": f"{share:.6f}" if share is not None else "",
            }
        )

    # Biggest share first, then anything unscaled, then alphabetically — so the one row
    # that decides the gate is the first row on the screen.
    rows.sort(key=lambda row: (-Decimal(row["share"] or 0), row["tag"]))
    return rows


def _mapped_rows(chosen: Sequence[RawFact]) -> list[dict[str, Any]]:
    """What the run *did* capture, one row per canonical concept and period.

    Shown beside the unmapped tags because "does this gap matter?" is a comparison, and an
    operator asked it over a list of element names alone was being asked to hold the
    statements in their head. Alongside them the question is usually answerable at a glance:
    a missing line of $40bn beside a revenue line of $331bn is one decision, and forty
    segment breakdowns beside the same revenue are a different one.

    Undimensioned figures only. A dimensioned fact is "revenue, Americas segment" rather
    than "revenue", and the two must never sit in one column competing for a period's
    number.
    """
    rows = [
        {
            "concept": fact.concept,
            "value": str(fact.value),
            "unit": fact.unit,
            "period_end": fact.period_end.isoformat(),
            "period_start": fact.period_start.isoformat() if fact.period_start else "",
        }
        for fact in chosen
        if fact.concept in CANONICAL_CONCEPTS and fact.dimension_axis is None
    ]
    rows.sort(key=lambda row: (row["concept"], row["period_end"]))
    return rows


def _reference_figure(chosen: Sequence[RawFact]) -> Decimal | None:
    """The mapped line an unmapped one is sized against, or ``None`` if none mapped."""
    for concept in _SCALE_CONCEPTS:
        values = [abs(fact.value) for fact in chosen if fact.concept == concept]
        if values:
            return max(values)
    return None


def unmapped_gate_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly what the unmapped-tags gate approves, as one structure.

    Built from the extract step's own output, so the tags an operator is shown are the tags
    the extractor actually could not place — not a re-derivation that might differ.
    """
    return {
        "exchange": str(produced.get("exchange", "")),
        "unmapped_tags": list(produced.get("unmapped_tags", [])),
        # Empty for a run recorded before 2026-08-25. The gate falls back to the tag list,
        # which is what it always showed, rather than rendering an absence as a hole.
        "unmapped_concepts": list(produced.get("unmapped_concepts", [])),
        "mapped_concepts": list(produced.get("mapped_concepts", [])),
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


async def _gate_unmapped_concepts(context: StepContext) -> StepResult:
    """Stop until a human confirms an extraction that left tags unmapped.

    **Skipped, not approved, when there is nothing to confirm.** A run whose every tag mapped
    records that the gate did not apply and continues; the approvals service already treats
    this gate as conditional, so an absent decision does not block the final gate.
    """
    produced = context.outputs.get("extract", {})
    if not unmapped_gate_required(produced):
        return StepResult(
            output={
                "gate": GateKind.UNMAPPED_CONCEPTS.value,
                "required": False,
                "unmapped_tags": [],
            }
        )

    return await _require_approval(context, gate=GateKind.UNMAPPED_CONCEPTS, of_step="extract")


def sector_key_of(outputs: Mapping[str, Mapping[str, Any]]) -> str:
    """The classification this run settled on, or ``""`` for an ordinary company.

    A named function rather than two lines inside the step, because what it returns decides
    whether a discounted cash flow is proposed at all — and reading the wrong step's output,
    or the wrong key of the right step's, would silently return ``""`` and hand a bank the
    standard model. An empty string is a real answer here and an error looks exactly like
    one, so the lookup is worth being able to test on its own.
    """
    return str(outputs.get(CLASSIFY_STEP, {}).get("sector_key", ""))


def assumptions_gate_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """What the assumptions step recorded, as the payload structure the gate hashes.

    The step's own record: what the run proposed and could not, at the moment it assembled.
    The gate itself no longer verifies against this — see :func:`assumptions_gate_refreshed`
    and gap A52: the rows are the valuation's real inputs and an operator can change them
    while the run waits, so the displayed and approved payload is re-read from the rows.
    This shape remains the record of what the step did, and the refreshed payload reproduces
    it byte for byte until a row actually changes.
    """
    return {
        "assumptions": list(produced.get("assumptions", [])),
        "outstanding": list(produced.get("outstanding", [])),
        "refused": list(produced.get("refused", [])),
        "skipped": list(produced.get("skipped", [])),
    }


def assumptions_gate_refreshed(
    rows: Sequence[Assumption], produced: dict[str, Any]
) -> dict[str, Any]:
    """The assumptions gate payload as the rows now stand (gap A52).

    The workflow's own forecast horizon applied to
    :func:`aer.services.assumption_gate.refreshed_payload`, so the gate page and the
    resuming workflow assemble — and hash — exactly the same structure. See
    :func:`_gate_assumptions` for why this gate verifies against the rows rather than the
    step's frozen record.
    """
    return refreshed_payload(rows, produced, years=FORECAST_YEARS)


def assumptions_gate_required(produced: Mapping[str, Any]) -> bool:
    """Whether this run has assumptions for a person to confirm.

    Delegates, so the rule has one definition. Restating it here is how the workflow and
    the service came to disagree in the first place — the condition grew a third clause in
    :func:`aer.services.assumption_gate.gate_required` and the copy kept the old two, which
    would have stopped runs at a gate they could not clear.
    """
    return gate_required(dict(produced))


async def _propose_assumptions(context: StepContext) -> StepResult:
    """Put a number against every assumption this run can, and name the rest.

    **Six from the filings, two from a model, three left for the operator.** The derived six
    come from :mod:`aer.services.assumption_proposals`; the terminal growth rate and the exit
    multiple from the ADR 0046 role, bounded in code; and the three the discount rate
    decomposes into — a risk-free rate, a beta and an equity risk premium — are named as
    outstanding with the reason, because this workflow acquires neither a macro series nor a
    price history and a beta invented here would be indistinguishable in the output from one
    somebody sourced.

    The analysis is **recomputed rather than re-read**. `_calculate` holds the
    :class:`~aer.services.analysis.AnalysisOutcome` only for the length of its own step, and
    the alternative — folding the proposals into that step — would put a model call inside
    the deterministic calculation step. The recomputation writes nothing: its ledger is
    never persisted, so the run's calculations are recorded exactly once.
    """
    request = await _request_for(context)
    acquired = context.output_of("acquire")
    sector_key = sector_key_of(context.outputs)

    analysis = await analyse_company(
        context.session,
        calculation_service.new_context(),
        company_id=_uuid(acquired["company_id"]),
        request=request,
        profile=profile_for(sector_key),
    )

    model = model_for(sector_key)
    agent_context: AgentContext | None = None
    if model is not None:
        agent_context = AgentContext(
            session=context.session,
            provider=context.service("provider"),
            router=context.service("router"),
            settings=context.service("settings"),
            store=context.service("store"),
            job_step=context.step,
        )

    outcome = await assemble_assumptions(
        context.session,
        agent_context,
        request=request,
        analysis=analysis,
        sector_key=sector_key,
        findings=_findings_for(context),
        years=FORECAST_YEARS,
        job_id=context.job.id,
    )

    rows = await assumptions_for_request(context.session, request.id)
    output: dict[str, Any] = {
        # Both, and neither is redundant. `valuation_model` is the authority — which of the
        # models this build implements will run — and `dcf_permitted` is the narrower fact
        # several surfaces still ask for on its own. A run recorded before the first key
        # existed is read back through the second, which is all those runs could have meant.
        "valuation_model": model.value if model is not None else "",
        "dcf_permitted": model is ValuationModel.DCF_FCFF,
        "sector_key": sector_key,
        **gate_payload_for_assumptions(rows, outcome),
        "model_consulted": outcome.model_consulted,
    }
    output["payload_hash"] = sha256_hex(canonical_json(assumptions_gate_payload(output)))
    return StepResult(
        output=output,
        cost_gbp=agent_context.spend_gbp if agent_context is not None else Decimal(0),
    )


def _findings_for(context: StepContext) -> tuple[str, ...]:
    """What the research workers concluded, as plain statements.

    Findings rather than documents. The proposing role holds no tools and reads no fetched
    text; a summary of what the run established is what it is entitled to see.
    """
    found: list[str] = []
    for key, produced in context.outputs.items():
        if not key.startswith("research_"):
            continue
        # Nested under "report": the research step stores the whole WorkerReport there.
        # This used to read a top-level "findings" that has never existed, so the
        # proposing role was shown an empty digest on every run and nobody noticed —
        # an empty tuple is also what a run with no findings legitimately produces.
        report = produced.get("report")
        if not isinstance(report, dict):
            continue
        for finding in report.get("findings", []) or []:
            statement = finding.get("statement") if isinstance(finding, dict) else None
            if statement:
                found.append(str(statement))
    return tuple(found)


async def _gate_assumptions(context: StepContext) -> StepResult:
    """Stop until a person has agreed the numbers the valuation will use.

    **Skipped, not approved, when there is nothing to agree.** A run whose sector blocks a
    discounted cash flow is never given a forecast and must not wait to approve one.

    **The hash is recomputed from the rows, not read from the step output** (gap A52).
    This gate approves inputs to work that has not happened yet, and the inputs are rows
    the operator can amend or add while the run waits — the valuation reads the rows, so
    an approval verified against the step's frozen record could cover figures the forecast
    will not use. The gate page displays the same refreshed payload, so what is shown,
    what is approved and what the valuation reads are one thing; unchanged rows reproduce
    the step's own hash, and a row changed after an approval pauses the run for a fresh
    decision rather than proceeding on an approval of something else.
    """
    produced = context.outputs.get(ASSUMPTIONS_STEP, {})
    if not assumptions_gate_required(produced):
        return StepResult(
            output={
                "gate": GateKind.ASSUMPTIONS.value,
                "required": False,
                "valuation_model": produced.get("valuation_model", ""),
                "dcf_permitted": produced.get("dcf_permitted", False),
            }
        )

    live = assumptions_gate_refreshed(
        await assumptions_for_request(context.session, context.job.work_order_id),
        dict(produced),
    )
    return await _require_approval(
        context,
        gate=GateKind.ASSUMPTIONS,
        of_step=ASSUMPTIONS_STEP,
        expected_hash=sha256_hex(canonical_json(live)),
    )


async def _value(context: StepContext) -> StepResult:
    """Build the forecast the operator agreed the inputs for, or say why there is none.

    **The first step in this platform's history to produce a discounted cash flow.** Every
    piece existed and nothing called them: `aer/calc/dcf.py`, `aer/calc/wacc.py` and
    `aer/services/valuation.py` were built with unit and property tests through Phase 3, and
    the valuation page has been empty since the first live run because no workflow step ever
    assembled their inputs.

    **A run without a valuation is an ordinary outcome, not a failure.** Most runs reach
    here with assumptions nobody has confirmed — the risk-free rate, the beta and the
    premium have no source in this workflow — so the step records why and the report says
    so. Stopping the run would throw away the analysis, the research and the draft over a
    number the operator has not typed yet.

    The mandate is rebuilt here rather than carried: `ValuationMandate` refuses to exist for
    a blocked model, so constructing one is the permission check, and doing it at the point
    of use means no earlier step can hand this one a permission it did not earn.

    **Which model runs is read back from the assumptions step, not re-derived** (ADR 0070).
    That step settled it from the confirmed classification and then collected exactly that
    model's inputs; deriving it again here would let a reclassification during the gate's
    wait produce a valuation nobody agreed the numbers for.
    """
    produced = context.outputs.get(ASSUMPTIONS_STEP, {})
    model = assumptions_valuation_model(produced)
    if model is None:
        return StepResult(
            output={
                "valued": False,
                "reason": (
                    "This company's sector has no valuation model this build implements, so "
                    "none was attempted."
                ),
            }
        )

    request = await _request_for(context)
    acquired = context.output_of("acquire")
    sector_key = sector_key_of(context.outputs)

    # Recomputed rather than re-read, for the reason `_propose_assumptions` gives: the
    # analysis object lives only inside `calculate`. This ledger is never persisted, so the
    # run's calculations are still recorded exactly once.
    analysis = await analyse_company(
        context.session,
        calculation_service.new_context(),
        company_id=_uuid(acquired["company_id"]),
        request=request,
        profile=profile_for(sector_key),
    )

    try:
        mandate = _mandate_for(request, sector_key=sector_key, model=model)
    except ModelNotPermittedError as refused:
        return StepResult(output={"valued": False, "reason": str(refused)})

    if model is ValuationModel.RESIDUAL_INCOME:
        bank = await value_the_bank(
            context.session,
            request=request,
            job_id=context.job.id,
            analysis=analysis,
            mandate=mandate,
            years=FORECAST_YEARS,
        )
        return StepResult(output=bank.as_dict())

    outcome = await value_the_business(
        context.session,
        request=request,
        job_id=context.job.id,
        analysis=analysis,
        mandate=mandate,
        years=FORECAST_YEARS,
    )
    return StepResult(output=outcome.as_dict())


def _mandate_for(
    request: ResearchRequest, *, sector_key: str, model: ValuationModel
) -> ValuationMandate:
    """Permission to run ``model`` on this company.

    An unclassified company takes :func:`aer.core.sectors.unclassified_mandate`, which is
    the permissive state and the right answer for most listed companies. A classified one
    goes through :func:`aer.core.sectors.mandate_for`, which raises for a blocked model —
    and the raise is the enforcement, because a mandate for a bank's discounted cash flow
    does not exist to be passed to :func:`aer.calc.dcf.discounted_cash_flow`.

    ``model`` comes from :func:`aer.core.sectors.model_for`, so the permission asked for is
    the one the gate collected inputs for. Asking for a model the profile blocks would raise
    here — correctly — but it would also mean the run had spent a gate gathering numbers
    nothing was ever going to read.
    """
    if not sector_key:
        return unclassified_mandate(model, subject=request.ticker)

    profile = profile_for(sector_key)
    if profile is None:
        message = (
            f"This run is classified as {sector_key!r}, which names no sector profile this "
            "build carries. An unrecognised classification is treated as a specialist one: "
            "no valuation is attempted."
        )
        raise ModelNotPermittedError(message, context={"sector_key": sector_key})

    return mandate_for(
        model,
        subject=request.ticker,
        profile=profile,
        # The sector gate is what confirmed the classification; reaching this step at all
        # means it was approved, and the mandate records the decision it rests on.
        confirmed_by=_SECTOR_GATE,
    )


# What a mandate records when the classification came through the sector gate. The gate's
# `approvals` row carries who and when; this names the decision the permission rests on.
_SECTOR_GATE: Final = "gate:SECTOR_SPECIALIST"


async def _comps(context: StepContext) -> StepResult:
    """Build the comparables table, with every peer this run had no data for named.

    **The comparables page has been empty since the first live run**, and not because the
    table was hard to build: `aer.services.comps.build` has always known how, and nothing
    called it.

    Each confirmed peer's multiples are computed from figures the platform already defends:
    the peer's own filed statements — a peer is proposed only because its facts are stored —
    and a price fetched, archived and licensed exactly as the subject's. A peer that cannot
    be priced or read is excluded by name with the reason, which is what stops a thin table
    reading as "this company has no comparables".
    """
    request = await _request_for(context)
    acquired = context.output_of("acquire")
    prices = context.outputs.get(PRICES_STEP, {})

    analysis = await analyse_company(
        context.session,
        calculation_service.new_context(),
        company_id=_uuid(acquired["company_id"]),
        request=request,
        profile=profile_for(sector_key_of(context.outputs)),
    )

    ledger = calculation_service.new_context()
    outcome = await build_comps_table(
        context.session,
        ledger,
        job=context.job,
        request=request,
        # The filer's own name on the subject's own row of the comparables table (A67).
        company_name=await subject_name(context.session, request),
        ticker=request.ticker,
        analysis=analysis,
        market_capitalisation=_market_capitalisation_from(prices, currency=request.base_currency),
        as_of=request.as_of_date,
        client=context.optional_service("eodhd_client"),
        store=context.service("store"),
    )

    if ledger.records:
        await calculation_service.persist_context(context.session, ledger, job_id=context.job.id)

    return StepResult(output=outcome.as_dict())


def _market_capitalisation_from(prices: Mapping[str, Any], *, currency: str) -> Quantity | None:
    """The market capitalisation the price step computed, read back as a quantity.

    Round-tripped through the step output rather than passed as an object, because that is
    what the engine carries between steps — and it is re-sourced to the calculation that
    produced it rather than left bare, since the unit system refuses an unsourced input and
    a figure with no lineage has no business in a multiple.
    """
    value = prices.get("market_capitalisation")
    if not value:
        return None
    return Quantity.of(
        Decimal(str(value)),
        Unit.currency(currency),
        source=SourceRef.calculation(
            prices.get("security_id", "market_capitalisation"),
            label="market capitalisation",
        ),
    )


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

    # Same concept, same period, two published values: caught here by arithmetic, as a
    # disagreements row the gate-2 banner shows — not left for the red team to notice
    # with a model call, which is how the live report's self-contradiction was found
    # (gap C6). Before the deterministic fill, so the disagreements the check records
    # are part of what the payload the operator approves already carries.
    conflicts = await check_report_consistency(context.session, job_id=context.job.id)

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
            "consistency_conflicts": conflicts,
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

    # Refilled *after* the challenges land (gap A41). The validate step wrote this section
    # a step earlier, when the disagreements table was still empty — so a live report said
    # "no disagreements recorded" above eight recorded challenges, two of them material.
    # The builders overwrite, so the refill is the same fill with the red team's rows now
    # in its denominator. The gate-2 hash is sealed by the revise step (ADR 0091), which
    # is now the last step that can change what the operator is shown.
    await fill_deterministic_sections(
        context.session, job=context.job, request=request, stage=SectionStage.VALIDATE
    )

    return StepResult(output=outcome.as_dict(), cost_gbp=agent_context.spend_gbp)


async def _revise(context: StepContext) -> StepResult:
    """Give the writer its second attempt, then seal what gate 2 approves.

    ADR 0091. The sections the material challenges attack are redrafted once with the
    challenge in front of the writer — same contract, same evidence policy, same
    validation as the first draft — before a person ever sees the draft. The challenge's
    own disagreement row is never auto-resolved: the revision happens beside it, both
    reach the gate, and the payload's ``revisions`` block puts what happened inside the
    hash the approval records.
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

    outcome = await revise_challenged_sections(
        agent_context,
        context.session,
        job=context.job,
        request=request,
        focus_by_key=await _focus_by_key(context),
    )

    if outcome.revised:
        # The deterministic sections describe the run's own record, and the record just
        # changed under them — the same reason the red team refills after its challenges
        # land.
        await fill_deterministic_sections(
            context.session, job=context.job, request=request, stage=SectionStage.VALIDATE
        )

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


async def _verdict(context: StepContext) -> StepResult:
    """Write the review gate's authored half, once, over the frozen draft (ADR 0087).

    The subject stopped changing when the revise step sealed the gate-2 hash; this step
    interprets it and stores the sentence as its own output, where the review page reads
    it back. **It joins no payload and moves no hash**: interpretation is never part of
    what the operator approves, no claim may name it, and a run that fails here still
    renders a complete composed verdict — which is why every failure short of a budget
    refusal degrades to ``written: False`` rather than costing the run its gate.
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

    payload = await final_gate_payload(context.session, job_id=context.job.id)
    try:
        authored = await VerdictAgent().run(
            agent_context, _verdict_subject(request, payload=payload)
        )
    except BudgetExceededError:
        raise
    except (AerError, ValueError) as exc:
        _log.warning(
            "verdict.model_unavailable",
            job_id=str(context.job.id),
            error_code=getattr(exc, "code", ""),
        )
        return StepResult(output={"written": False}, cost_gbp=agent_context.spend_gbp)

    return StepResult(
        output={
            "written": True,
            "sentence": authored.sentence,
            "tone": authored.tone.value,
        },
        cost_gbp=agent_context.spend_gbp,
    )


def _verdict_subject(request: ResearchRequest, *, payload: Mapping[str, Any]) -> VerdictInput:
    """The frozen record's shape, and deliberately not its prose.

    The one excerpt is the opening section's first lines, for register alone. Everything
    else is outcomes, challenges and flags — the digest a one-sentence interpretation
    actually rests on, at a fraction of the input cost of handing over the draft.
    """
    sections = [row for row in payload.get("sections", []) if isinstance(row, dict)]
    escalations = [row for row in payload.get("escalations", []) if isinstance(row, dict)]
    challenges = [
        row for row in escalations if row.get("kind") == DisagreementKind.THESIS_CONFLICT.value
    ]
    conflicts = len(escalations) - len(challenges)
    opening = next((str(row.get("content") or "") for row in sections if row.get("content")), "")

    return VerdictInput(
        company_name=request.company_name,
        ticker=request.ticker,
        sections=[
            {
                "key": str(row.get("key", "")),
                "status": str(row.get("status", "")),
                "words": len(str(row.get("content") or "").split()),
            }
            for row in sections
        ],
        not_generated=[str(row.get("key", "")) for row in sections if not row.get("content")],
        challenges=[
            {
                "material": bool(row.get("material")),
                "topic": str(row.get("topic") or "")[:200],
                "challenge": str(row.get("position_b") or "")[:400],
            }
            for row in challenges
        ],
        open_conflicts=conflicts,
        triggers=[
            str(row.get("kind", "")) for row in payload.get("triggers", []) if isinstance(row, dict)
        ],
        opening_excerpt=opening[:600],
    )


async def _focus_by_key(context: StepContext) -> dict[str, str]:
    """The planner's approved one-line brief per section, or nothing for none.

    Shared by the draft and the revise pass, so a revision writes to the same approved
    direction the first draft did.
    """
    if context.job.plan_id is None:
        return {}
    plan = await context.session.get(ResearchPlan, context.job.plan_id)
    if plan is None:
        return {}
    return {
        str(entry.get("key", "")): str(entry.get("focus", ""))
        for entry in (plan.plan or {}).get("sections", [])
        if isinstance(entry, dict)
    }


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
    return await _require_approval(context, gate=GateKind.FINAL, of_step="revise")


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
            Approval.work_order_id == context.job.work_order_id,
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


async def _require_approval(
    context: StepContext, *, gate: GateKind, of_step: str, expected_hash: str | None = None
) -> StepResult:
    """Continue only if an approval exists for exactly what this run produced.

    The approval's ``payload_hash`` is compared against the hash of what the step actually
    produced. An approval recorded against a different payload is not an approval of this
    one — that is the whole reason the hash is stored rather than just a timestamp and a
    user id.

    ``expected_hash`` overrides the step output's own hash, for the one gate whose payload
    is not frozen in a step output: the assumptions gate approves rows the operator can
    still change, so its caller recomputes the hash from them (gap A52).
    """
    produced = context.outputs.get(of_step, {})
    if expected_hash is None:
        expected_hash = str(produced.get("payload_hash", ""))

    approval = await context.session.scalar(
        select(Approval).where(
            Approval.work_order_id == context.job.work_order_id,
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
    """Fetch the company's facts and its filings from EDGAR, and record the provenance.

    **It used to fetch one document, and that was the whole of the run's evidence.** The
    XBRL aggregate: every figure the entity ever tagged, and not one sentence of prose. The
    original comment said a second document "would exercise the same chain again rather
    than exercising anything new", which was true of the chain and false of the research —
    the recent-developments worker finished a live run with five leads and no findings,
    because there was nothing recent in front of it to find.

    So the annual report and the recent current reports come too, dated by the day EDGAR
    accepted them and excerpted so they can be cited. See :mod:`aer.services.filings`.
    """
    request = await _request_for(context)
    client = context.service("sec_client")
    store = context.service("store")

    entity = await client.resolve_entity(request.ticker, exchange=request.exchange)

    # Upserted before anything is fetched, because every document recorded below has to say
    # which company it is about (ADR 0061) and the answer has to exist first. It used to be
    # created after the acquisition, which was fine while a request could only concern one
    # company and stopped being fine the moment peer acquisition existed.
    company = await upsert_company(
        context.session,
        entity=entity,
        ticker=request.ticker,
        exchange=request.exchange,
    )

    # Marks the request's identity as confirmed against a registry, and records *which*
    # company it was confirmed to be. Everything downstream can now tell a resolved company
    # from a string somebody typed, and can scope a fact query to the subject without
    # matching tickers back to strings.
    request.resolved = True
    request.company_id = company.id
    await context.session.flush()

    response = await client.fetch_company_facts(entity.identifier)

    acquisition = await record_acquisition(
        context.session,
        store,
        request=request,
        company_id=company.id,
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
        # An aggregate has no publication date of its own, and having none quarantined the
        # only source most runs held — so no claim could cite anything. The newest filing
        # it carries is the day this document could first have existed, which is a real
        # date rather than a convenience. See `CompanyFacts.latest_filed`.
        publication_date=response.data.latest_filed,
        publication_date_confidence=_DERIVED_FROM_CONTENTS,
    )

    filings = await acquire_filings(
        context.session,
        store,
        client=client,
        request=request,
        entity=entity,
        company=company,
        settings=context.service("settings"),
        job_id=context.job.id,
    )

    return StepResult(
        output={
            "company_id": str(company.id),
            "cik": entity.identifier,
            # The aggregate, named on its own because `extract` reads it by hash to build
            # the fact set. The filings below are prose, and are read by the workers and
            # the section writers rather than by the extractor.
            "source_document_id": str(acquisition.source_document.id),
            "artefact_sha256": acquisition.sha256,
            "quarantined": acquisition.quarantined,
            **filings.as_dict(),
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
    """Put forward comparable companies — the model's, resolved and acquired, then the floor.

    **A model names them and code decides whether they exist** (ADR 0059). The deterministic
    lookup underneath proposes only companies this database already holds, so on a first run
    it proposed nobody and no run ever had a comparison; the companies most comparable to a
    subject are precisely the ones this platform has not researched yet.

    Every ticker the model returns is resolved against EDGAR's own index — a hallucinated
    company appears in ``refused`` rather than at the gate — and **nothing is fetched for
    any of them** (ADR 0059 as amended): the set is recorded for the day a price feed
    makes a peer multiple computable, and until then a peer's only figures are whatever
    an earlier run of that company already banked.

    **The floor stays underneath.** A model call that fails leaves the run proposing what
    the database can support rather than dying, because the enrichment is not the step.
    """
    acquired = context.output_of("acquire")
    company = await context.session.get(Company, _uuid(acquired["company_id"]))
    if company is None:  # pragma: no cover -- written by the prior step
        message = "The acquire step's company row is missing."
        raise StepPaused(message, gate=None)

    request = await _request_for(context)
    floor = await propose_peers_from_sic(context.session, subject=company, as_of=request.as_of_date)

    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )
    discovered, consulted = await _peers_from_model(
        context,
        agent_context,
        request=request,
        company=company,
        sector_key=sector_key_of(context.outputs),
    )

    peers = merged_with(discovered.peers, floor)

    output: dict[str, Any] = {
        "subject": str(company.id),
        "subject_name": company.name,
        "subject_period_end": request.as_of_date.isoformat(),
        "basis": MultipleBasis.TRAILING_TWELVE_MONTHS.value,
        # Who actually contributed, rather than who was asked. A run whose model call
        # failed, or whose every suggestion was refused, is one whose peers came from the
        # lookup — and the gate should say so, because a reviewer weighs a rationale by
        # knowing what wrote it.
        "proposed_by": _proposer_names(
            from_model=len(discovered.peers), total=len(peers), consulted=consulted
        ),
        "peers": [peer.as_dict() for peer in peers],
        # Outside the gate payload on purpose: a refusal is context for the reviewer, not
        # a thing being approved, and putting it in the hash would make an approval depend
        # on what the model got wrong rather than on the set being confirmed.
        "refused": [item.as_dict() for item in discovered.refused],
    }
    output["payload_hash"] = sha256_hex(canonical_json(peer_gate_payload(output)))
    return StepResult(output=output, cost_gbp=agent_context.spend_gbp)


# What the deterministic proposal has always called itself in the step's output. Named here
# so the two proposers are written down in one place rather than as a literal in each branch.
_SIC_LOOKUP: Final = "sic_group_lookup"


def _proposer_names(*, from_model: int, total: int, consulted: bool) -> str:
    """Which proposers put a peer in this set, joined."""
    names = []
    if consulted and from_model:
        names.append(PEERS_PROPOSED_BY)
    if total > from_model:
        names.append(_SIC_LOOKUP)
    return "+".join(names) if names else _SIC_LOOKUP


async def _peers_from_model(
    context: StepContext,
    agent_context: AgentContext,
    *,
    request: ResearchRequest,
    company: Company,
    sector_key: str,
) -> tuple[DiscoveredPeers, bool]:
    """Ask for a slate and resolve it, or fall back to the floor alone and say so.

    The failure path is the reason this is a function. A provider outage or an unusable
    reply must not cost the run its peer step: the deterministic proposal is still there,
    still gated, still honest about who proposed it — which is what ``proposed_by`` carries.

    **A budget refusal is not one of those failures.** It is a control-flow signal the
    engine turns into a paused run awaiting a person's decision, and absorbing it here
    would spend past a cap and carry on — invariant 6's failure exactly, in the one place
    that looks like graceful degradation.
    """
    try:
        slate = await PeerProposalAgent().run(
            agent_context,
            PeerProposalInput(
                company_name=company.name,
                ticker=request.ticker,
                exchange=request.exchange,
                as_of_date=request.as_of_date.isoformat(),
                sic=company.sic or "",
                sic_description=company.sic_description or "",
                sector=sector_key,
            ),
        )
    except BudgetExceededError:
        raise
    except (AerError, ValueError) as exc:
        _log.warning(
            "peers.model_unavailable",
            job_id=str(context.job.id),
            error_code=getattr(exc, "code", ""),
        )
        return DiscoveredPeers(), False

    discovered = await discover_peers(
        context.session,
        client=context.service("sec_client"),
        request=request,
        subject=company,
        proposals=slate.peers,
    )
    return discovered, True


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


def theme_gate_payload(produced: Mapping[str, Any]) -> dict[str, Any]:
    """What the theme-set gate approves. Delegates, so one definition serves both halves."""
    return theme_set_payload(produced)


async def _propose_themes(context: StepContext) -> StepResult:
    """Ask which larger stories this company belongs to, and record the slate for the gate.

    **A model names them and code decides what they are** (ADR 0065): every key is slugged
    to one identity and matched against the ``themes`` table, so the reviewer sees which
    proposals join a tracked theme and which would found a new one. Membership is the
    subject alone — other companies join a theme through their own runs, each behind its
    own gate.

    **No floor, and a failed call is an empty slate.** Peers have a deterministic fallback
    because the database can name industry neighbours; there is no query that names a
    theme, so a provider outage simply means this run contributes none. That is a fact
    about the run, not a failure of it, and the step's output says which it was.
    """
    acquired = context.output_of("acquire")
    company = await context.session.get(Company, _uuid(acquired["company_id"]))
    if company is None:  # pragma: no cover -- written by the prior step
        message = "The acquire step's company row is missing."
        raise StepPaused(message, gate=None)
    request = await _request_for(context)

    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )
    slate, consulted = await _themes_from_model(
        context,
        agent_context,
        request=request,
        company=company,
        sector_key=sector_key_of(context.outputs),
    )

    # Slugging, dedupe and the existing-theme check live in the service, where a test
    # can reach them with a messy key; the step only maps the model's reply into it.
    themes = await normalised_slate(
        context.session,
        [(proposal.key, proposal.label, proposal.rationale) for proposal in slate.themes],
    )

    output: dict[str, Any] = {
        "subject": str(company.id),
        "subject_name": company.name,
        "themes": themes,
        "proposed_by": THEMES_PROPOSED_BY if consulted else "",
    }
    output["payload_hash"] = sha256_hex(canonical_json(theme_gate_payload(output)))
    return StepResult(output=output, cost_gbp=agent_context.spend_gbp)


async def _themes_from_model(
    context: StepContext,
    agent_context: AgentContext,
    *,
    request: ResearchRequest,
    company: Company,
    sector_key: str,
) -> tuple[ThemeSlate, bool]:
    """Ask for a slate, or return an empty one and say so.

    The same failure discipline as the peer proposer: an outage or an unusable reply must
    not cost the run its step, and a budget refusal is control flow the engine turns into
    a paused run — absorbing it here would spend past a cap and carry on.
    """
    try:
        slate = await ThemeProposalAgent().run(
            agent_context,
            ThemeProposalInput(
                company_name=company.name,
                ticker=request.ticker,
                exchange=request.exchange,
                as_of_date=request.as_of_date.isoformat(),
                sic=company.sic or "",
                sic_description=company.sic_description or "",
                sector=sector_key,
                existing=await existing_vocabulary(context.session),
            ),
        )
    except BudgetExceededError:
        raise
    except (AerError, ValueError) as exc:
        _log.warning(
            "themes.model_unavailable",
            job_id=str(context.job.id),
            error_code=getattr(exc, "code", ""),
        )
        return ThemeSlate(), False
    return slate, True


async def _gate_theme_set(context: StepContext) -> StepResult:
    """Stop until a person agrees which stories this company is filed under.

    **Skipped, not approved, when nothing was proposed.** A theme shapes how every later
    reader of the library weighs the company, so a slate that exists needs a person — and
    an empty one needs nobody, because there is nothing to defend.
    """
    produced = context.outputs.get(THEME_STEP, {})
    if not theme_set_required(produced):
        return StepResult(output={"gate": GateKind.THEME_SET.value, "required": False, "themes": 0})

    return await _require_approval(context, gate=GateKind.THEME_SET, of_step=THEME_STEP)


# ==========================================================================================
# 4. Extract
# ==========================================================================================


async def _acquire_prices(context: StepContext) -> StepResult:
    """Fetch the subject's price history and its market's, and propose a beta from them.

    **The step that makes the valuation reachable on an ordinary run.** `beta` is one of the
    three cost-of-capital inputs the assumptions gate needs, and until this existed the only
    way to supply it was to type it. The regression is proposed, never confirmed: the proxy
    is a judgement and the window changes the answer, so an operator agrees to it like any
    other number.

    Conditional on a subscription, and silent about its absence in the only way that
    matters — by saying so. A machine with no key runs every other step and the run reports
    which figures it could not compute.

    The calculations this writes — the market capitalisation, the beta and the adjustments
    beneath them — are persisted; the price series itself is stored as a hashed artefact
    with the licence note on its provenance row. Under ADR 0030 route 2 the series may not
    be exported, and the containment for that lives in the comps table and the exhibit
    layer rather than here.
    """
    request = await _request_for(context)
    acquired = context.output_of("acquire")
    company = await context.session.get(Company, _uuid(acquired["company_id"]))
    if company is None:  # pragma: no cover -- written by the prior step
        message = "The acquire step's company row is missing."
        raise StepPaused(message, gate=None)

    ledger = calculation_service.new_context()
    outcome = await acquire_prices(
        context.session,
        context.optional_service("eodhd_client"),
        context.service("store"),
        request=request,
        company=company,
        job_id=context.job.id,
        context=ledger,
        # Gap A47. `acquire_prices` has always preferred the filed count to the vendor's —
        # a fact with a hashed filing behind it beats a number in a JSON document — and
        # nothing ever passed one, so every run fell through to `/api/fundamentals`. That
        # endpoint is a ten-weight request, and it is a feed the operator's subscription
        # does not include, so the fallback could only ever fail: no market capitalisation,
        # and with it no enterprise-value multiple in the comps table.
        shares_outstanding=await _filed_share_count(
            context, company_id=company.id, request=request
        ),
    )

    if ledger.records:
        await calculation_service.persist_context(context.session, ledger, job_id=context.job.id)

    return StepResult(output=outcome.as_dict())


async def _filed_share_count(
    context: StepContext, *, company_id: uuid.UUID, request: ResearchRequest
) -> Quantity | None:
    """The most recent share count the filings carry, or nothing.

    The cover page of every annual report states the shares outstanding on the day it was
    signed (``dei:EntityCommonStockSharesOutstanding``), and the concept map already
    resolves it. So the newest observation wins here rather than the newest *fiscal year*:
    a market capitalisation wants the count as it stands, and the cover-page figure is
    both the freshest and — unlike the vendor's — a fact with an archived filing behind it.

    Deliberately not restricted to annual periods. A share count is an instant, and after
    gap A45 an instant no longer defines a period at all; that rule is about which years
    have statements, and this is a different question asked of the same rows.
    """
    statement = (
        select(FinancialFact)
        .where(
            FinancialFact.company_id == company_id,
            FinancialFact.concept == "shares_outstanding",
            FinancialFact.unit == "shares",
            # Consolidated only, for the reason `analysis` gives: a dimensioned row is one
            # class of stock, and a class is not the company.
            FinancialFact.dimension_axis.is_(None),
        )
        .order_by(FinancialFact.period_end.desc(), FinancialFact.filed_date.desc())
        .limit(1)
    )
    if request.point_in_time:
        statement = statement.where(FinancialFact.filed_date <= request.as_of_date)

    fact = await context.session.scalar(statement)
    if fact is None:
        return None
    return Quantity.of(
        fact.value,
        Unit.base("shares"),
        source=SourceRef.financial_fact(fact.id, label="shares outstanding"),
    )


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
    # The same tags with the numbers behind them (gap R17). A list of taxonomy element
    # names asks "does this gap matter?" and gives the operator nothing to answer it with;
    # what settles the question is how big the missing line is against a line that mapped.
    unmapped_detail = _unmapped_rows(parsed.unmapped, chosen=selection.chosen)

    # The aggregate holds only consolidated figures, so the segment breakdown comes from
    # the annual report itself — inline XBRL, already fetched and hashed by the acquire
    # step. Its output keys stay disjoint from `unmapped_tags`: the confirmation gate is
    # for statement lines hanging on filer extensions, not for a supplementary breakdown
    # the run can do without.
    segments = await sweep_segment_facts(
        context.session,
        store,
        company=company,
        filings=list(acquired.get("filings", [])),
    )

    output: dict[str, Any] = {
        "facts_written": written,
        "fact_extractions": len(fact_extractions),
        "facts_chosen": len(selection.chosen),
        "facts_rejected": len(selection.rejected),
        "rejected_for_look_ahead": len(selection.rejected_for_look_ahead),
        "exchange": request.exchange,
        "unmapped_tags": list(unmapped),
        "unmapped_concepts": unmapped_detail,
        "mapped_concepts": _mapped_rows(selection.chosen),
        "load_errors": [],
        **segments.as_dict(),
    }
    # The hash of exactly what the gate will display, on the same terms as the plan gate: an
    # approval recorded against a different set of tags is not an approval of this one.
    output["payload_hash"] = sha256_hex(canonical_json(unmapped_gate_payload(output)))
    return StepResult(output=output)


# ==========================================================================================
# 5. Calculate
# ==========================================================================================


async def _calculate(context: StepContext) -> StepResult:
    """The run's financial analysis, every figure traced to the facts behind it.

    **This step used to compute one number.** A revenue CAGR, which was the right scope for
    a vertical slice proving the chain and the wrong scope for a research platform: the
    statement assembler, the seventeen ratios and the earnings-quality signals were all
    built, tested and never called, so the balance-sheet and cash-flow sections had nothing
    to write about and the valuation page said the run had produced nothing.

    Both now happen, in one ledger and one transaction. The CAGR is kept rather than folded
    into the suite — it is the headline growth figure the summary reaches for, and it spans
    the whole filed history rather than sitting inside one period.
    """
    request = await _request_for(context)
    acquired = context.output_of("acquire")
    company_id = _uuid(acquired["company_id"])
    calc_context = calculation_service.new_context()

    analysis = await analyse_company(
        context.session,
        calc_context,
        company_id=company_id,
        request=request,
        # What this kind of business does not define, so the coverage the gate reads and
        # the ratios the report shows are both about a company of this kind (A64).
        profile=profile_for(sector_key_of(context.outputs)),
    )

    growth = await _revenue_growth(context, company_id=company_id, ledger=calc_context)

    if not calc_context.records:
        # Nothing derived at all: no annual facts, and fewer than two periods of revenue.
        # Not an error — a company with one filed year genuinely has no trend — but the
        # report must say so rather than showing empty tables with no explanation.
        _log.info("workflow.no_calculation_possible", periods=len(analysis.periods))
        return StepResult(
            output={
                "calculation_id": None,
                "reason": "no annual facts to analyse",
                **analysis.as_dict(),
            }
        )

    rows = await calculation_service.persist_context(
        context.session, calc_context, job_id=context.job.id
    )

    output: dict[str, Any] = {"calculation_id": str(rows[-1].id), **analysis.as_dict()}
    output["calculations"] = len(rows)
    if growth is not None:
        output.update(growth)
    return StepResult(output=output)


async def _revenue_growth(
    context: StepContext, *, company_id: uuid.UUID, ledger: CalculationContext
) -> dict[str, Any] | None:
    """The compound growth rate across the whole filed revenue history, or nothing.

    Kept separate from the period-by-period analysis because it is not a period figure: it
    spans the earliest filed year to the latest, and the executive summary wants exactly
    that. Returns ``None`` when there is only one year, which is a fact about the company
    rather than a failure of the run.
    """
    facts = list(
        await context.session.scalars(
            select(FinancialFact)
            .where(
                FinancialFact.company_id == company_id,
                FinancialFact.concept == SLICE_CONCEPT,
                FinancialFact.unit == "USD",
                # The consolidated line only: a segment's revenue as either endpoint
                # would put one slice's growth forward as the company's.
                FinancialFact.dimension_axis.is_(None),
            )
            .order_by(FinancialFact.period_end)
        )
    )

    minimum_for_a_growth_rate = 2
    if len(facts) < minimum_for_a_growth_rate:
        return None

    first, last = facts[0], facts[-1]
    result = cagr(
        ledger,
        start=money(first.value, "USD", source=SourceRef.financial_fact(first.id, label="revenue")),
        end=money(last.value, "USD", source=SourceRef.financial_fact(last.id, label="revenue")),
        years=last.period_end.year - first.period_end.year,
    )
    return {
        "concept": SLICE_CONCEPT,
        "value": str(result.value),
        "unit": result.unit.symbol,
        "from_period": first.period_end.isoformat(),
        "to_period": last.period_end.isoformat(),
        "source_document_id": str(first.source_document_id),
    }


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
        try:
            investigation = await run_worker(
                agent_context,
                context.session,
                topic=topic,
                request=request,
                # `fetch_known_url` appears on the worker's menu only where a fetcher was
                # bundled. The registry grants the capability either way; this decides
                # whether the run can act on it -- see `build_executors`.
                executors=build_executors(
                    context.session,
                    request=request,
                    fetcher=context.services.get("fetcher"),
                    store=context.service("store"),
                    settings=context.service("settings"),
                    job_id=context.job.id,
                    sec_client=context.services.get("sec_client"),
                    # Binds `web_search` (ADR 0092): the context carries the provider,
                    # the route and the step the search's costs are metered against.
                    agent_context=agent_context,
                ),
            )
        except (WorkerExhaustedError, ValidationError) as failed:
            # One dead topic must not abandon four finished ones and everything
            # downstream — a live run lost its draft, its validation and roughly a
            # pound of finished work to a single worker's final reply. The step
            # succeeds with a degraded product that says so everywhere the real one
            # would have spoken: an empty report whose coverage note is the failure,
            # the audit trail the error carried out, and the spend, which is real
            # whether or not the reply was usable. ValidationError is caught alongside
            # exhaustion because from `run_worker` it means exactly one thing — the
            # model's replies could not be read twice — which is the same class of
            # death with a different bound.
            _log.warning(
                "workflow.research_degraded",
                topic=topic.value,
                code=failed.code,
                error=failed.message,
            )
            return StepResult(
                output={
                    "topic": topic.value,
                    "report": degraded_report(topic, failed.message).model_dump(mode="json"),
                    "tool_calls": int(failed.context.get("tool_calls", 0) or 0),
                    "rounds": int(failed.context.get("rounds", 0) or 0),
                    "requests": list(failed.context.get("requests", []) or []),
                    "degraded": {"code": failed.code, "detail": failed.message},
                },
                cost_gbp=agent_context.spend_gbp,
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


@dataclass(frozen=True, slots=True)
class _SectionWork:
    """One model-written section as plain data, so it can cross a session boundary."""

    section_id: uuid.UUID
    custom: bool
    focus: str = ""


async def _draft_one(
    agent_context: AgentContext, *, request: ResearchRequest, work: _SectionWork
) -> SectionExecution:
    """Draft one section on whatever session the agent context carries.

    Loads the row itself rather than taking one, because the row must belong to the
    context's session — under the fan-out that is a session the caller has never seen.
    """
    session = agent_context.session
    section = await session.scalar(
        select(ReportSection)
        .where(ReportSection.id == work.section_id)
        .options(selectinload(ReportSection.definition))
    )
    if section is None:  # pragma: no cover -- the partitioning just read this row
        message = f"Section {work.section_id} vanished mid-draft."
        raise AerError(message, context={"section_id": str(work.section_id)})

    if work.custom:
        job = await session.get(Job, agent_context.job_step.job_id)
        assert job is not None
        pins = await pinned_skills_for_job(session, job=job)
        pin = next((p for p in pins if p.skill_id == section.definition.skill_id), None)
        if pin is None:  # pragma: no cover -- the partitioning refused pin-less sections
            message = "The skill pin this section was partitioned under has vanished."
            raise AerError(message, context={"section_id": str(work.section_id)})
        return await execute_custom_section(
            agent_context, section=section, pin=pin, request=request
        )

    return await execute_builtin_section(
        agent_context, section=section, request=request, focus=work.focus
    )


async def _draft_one_apart(
    factory: async_sessionmaker[AsyncSession],
    *,
    services: dict[str, Any],
    step_id: uuid.UUID,
    request_id: uuid.UUID,
    work: _SectionWork,
    bound: asyncio.Semaphore,
) -> tuple[dict[str, Any], bool, Decimal]:
    """One fanned-out section: own session, own commit — the engine's node rules, one
    level down.

    The step row this hangs its costs on was committed by ``_step_row`` before the step
    began, so a session opened here can reference it. Committing per section means a
    paid draft survives a sibling's failure — the same reason the length salvage exists —
    and what crosses back to the coordinator is plain data, never a row.
    """
    async with bound, factory() as session:
        step = await session.get(JobStep, step_id)
        request = await session.get(ResearchRequest, request_id)
        if step is None or request is None:  # pragma: no cover -- both committed
            message = "The draft step's own rows vanished mid-run."
            raise AerError(message, context={"step_id": str(step_id)})
        agent_context = AgentContext(
            session=session,
            provider=services["provider"],
            router=services["router"],
            settings=services["settings"],
            store=services["store"],
            job_step=step,
        )
        execution = await _draft_one(agent_context, request=request, work=work)
        generated = execution.status is SectionStatus.GENERATED
        outcome = execution.as_dict()
        await session.commit()
        return outcome, generated, agent_context.spend_gbp


def _partitioned_sections(
    sections: Sequence[ReportSection],
    *,
    pin_by_skill: Mapping[Any, Any],
    focus_by_key: Mapping[str, str],
) -> tuple[dict[uuid.UUID, dict[str, Any]], list[_SectionWork]]:
    """Split a run's sections into refusals and drafting work, in declared order.

    The pin-less custom refusal happens here, on the caller's session: it is a recorded
    state, not a model call, and spends nothing — so it never joins the fan-out.
    """
    refused: dict[uuid.UUID, dict[str, Any]] = {}
    pending: list[_SectionWork] = []
    for section in sections:
        definition = section.definition
        if definition.origin != SKILL and definition.token_budget == 0:
            # Deterministic: filled at this stage already, or by the stage that owns it.
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
                refused[section.id] = {
                    "section_key": section.section_key,
                    "status": section.status.value,
                }
                continue
            pending.append(_SectionWork(section_id=section.id, custom=True))
            continue
        pending.append(
            _SectionWork(
                section_id=section.id,
                custom=False,
                focus=focus_by_key.get(section.section_key, ""),
            )
        )
    return refused, pending


async def _drafted_in_place(
    context: StepContext, *, request: ResearchRequest, pending: Sequence[_SectionWork]
) -> tuple[dict[uuid.UUID, tuple[dict[str, Any], bool]], Decimal]:
    """The sequential path: one at a time on the caller's session, in declared order.

    Every savepoint-fixtured test comes through here — the engine's own no-factory
    fallback, one level down — so its behaviour is exactly the pre-P10 loop's.
    """
    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=context.service("settings"),
        store=context.service("store"),
        job_step=context.step,
    )
    executed: dict[uuid.UUID, tuple[dict[str, Any], bool]] = {}
    for work in pending:
        execution = await _draft_one(agent_context, request=request, work=work)
        executed[work.section_id] = (
            execution.as_dict(),
            execution.status is SectionStatus.GENERATED,
        )
    return executed, agent_context.spend_gbp


async def _drafted_apart(
    context: StepContext,
    *,
    factory: async_sessionmaker[AsyncSession],
    request: ResearchRequest,
    pending: Sequence[_SectionWork],
) -> tuple[dict[uuid.UUID, tuple[dict[str, Any], bool]], Decimal]:
    """The fan-out: a bounded wave of sections, each on a session of its own."""
    bound = asyncio.Semaphore(DRAFT_FAN_OUT)
    started = [
        asyncio.create_task(
            _draft_one_apart(
                factory,
                services=context.services,
                step_id=context.step.id,
                request_id=request.id,
                work=work,
                bound=bound,
            )
        )
        for work in pending
    ]
    # Drain, never abandon — the engine's wave rule: every started section runs to its
    # recorded, committed outcome before any failure surfaces. A failure then fails the
    # step, but the siblings' paid drafts are already in the database.
    settled = await asyncio.gather(*started, return_exceptions=True)
    executed: dict[uuid.UUID, tuple[dict[str, Any], bool]] = {}
    spent = Decimal(0)
    first_failure: BaseException | None = None
    for work, item in zip(pending, settled, strict=True):
        if isinstance(item, BaseException):
            first_failure = first_failure or item
            continue
        outcome, generated, spend = item
        executed[work.section_id] = (outcome, generated)
        spent += spend
    if first_failure is not None:
        raise first_failure
    return executed, spent


async def _draft(context: StepContext) -> StepResult:
    """Fill in every section this run has, whatever they are.

    **No section key appears here.** The step iterates ``report_sections`` and routes
    each by what it *is*: a built-in goes to the generic contract-filler, a custom
    section (``origin='skill'``) executes under the ``<user_skill>`` contract against
    its pinned composed policy (task 38, ADR 0037). A failed custom section is a
    recorded state the run continues past, never an absent section.

    **Sections draft concurrently where the run has a session factory** (polish P10).
    They share the research wave's shape — each depends on the evidence pack and on
    nothing another section produces — so they fan out under the same rules: a bounded
    wave, a session per section, and a drain-never-abandon gather. Without a factory —
    every savepoint-fixtured test — they run one at a time on the caller's session in
    declared order, exactly as the engine itself falls back.
    """
    request = await _request_for(context)

    # The planner's one-line brief per section, approved at gate 1. Keyed lookup rather
    # than trusting order: the planner proposes focus for the sections it chose to speak
    # about, and a section it named none for is written from its contract alone.
    focus_by_key = await _focus_by_key(context)

    pins = await pinned_skills_for_job(context.session, job=context.job)
    pin_by_skill = {pin.skill_id: pin for pin in pins}

    # The platform-filled sections first, so a zero-budget definition with no registered
    # builder fails here — while the seed is the last thing that changed — rather than
    # rendering as an inexplicable blank later.
    deterministic = await fill_deterministic_sections(
        context.session, job=context.job, request=request, stage=SectionStage.DRAFT
    )

    sections = await sections_for_job(context.session, context.job.id)
    refused, pending = _partitioned_sections(
        sections, pin_by_skill=pin_by_skill, focus_by_key=focus_by_key
    )

    factory: async_sessionmaker[AsyncSession] | None = context.optional_service("session_factory")
    if factory is None:
        executed, spent = await _drafted_in_place(context, request=request, pending=pending)
    else:
        executed, spent = await _drafted_apart(
            context, factory=factory, request=request, pending=pending
        )

    # Reassembled in declared order — the sections' own, never completion order — so the
    # stored output is identical however the fan-out happened to schedule.
    filled = 0
    custom_outcomes: list[dict[str, Any]] = []
    builtin_outcomes: list[dict[str, Any]] = []
    custom_ids = {work.section_id for work in pending if work.custom}
    for section in sections:
        if section.id in refused:
            custom_outcomes.append(refused[section.id])
            continue
        if section.id not in executed:
            continue
        outcome, generated = executed[section.id]
        (custom_outcomes if section.id in custom_ids else builtin_outcomes).append(outcome)
        if generated:
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
        cost_gbp=spent,
    )


async def step_output(session: AsyncSession, *, job_id: uuid.UUID, step_key: str) -> dict[str, Any]:
    """What a step recorded, as it recorded it.

    The latest attempt, because a retried step's earlier attempts are history rather than
    the answer. Empty for a step that has not run, which every caller here treats as "this
    gate does not apply yet" rather than as an error.
    """
    step = await session.scalar(
        select(JobStep)
        .where(JobStep.job_id == job_id, JobStep.step_key == step_key)
        .order_by(JobStep.sequence.desc())
        .limit(1)
    )
    return (step.output_ref or {}) if step is not None else {}


# Which step's frozen output each gate approves. Five of the seven gates read nothing else,
# which is why they can share one entry point at all.
_GATE_STEPS: Final[Mapping[str, str]] = {
    # The critique step, not the plan step (ADR 0091): the last one that can change what
    # gate 1 displays, since a revision and the critique block both land after `plan`.
    GateKind.PLAN.value: "critique_plan",
    GateKind.SECTOR_SPECIALIST.value: CLASSIFY_STEP,
    GateKind.PEER_SET.value: PEER_SET_STEP,
    GateKind.THEME_SET.value: THEME_STEP,
    GateKind.UNMAPPED_CONCEPTS.value: "extract",
    GateKind.ASSUMPTIONS.value: ASSUMPTIONS_STEP,
    # The revise step, not the red team (ADR 0091), on the same principle.
    GateKind.FINAL.value: "revise",
}


async def gate_payload(session: AsyncSession, *, job: Job, gate: str) -> dict[str, Any]:
    """Exactly what a gate approves, for any gate this workflow declares.

    One entry point so a caller does not have to know which of seven builders a gate uses,
    which is what lets the run console, the JSON API and a second tool's pages render a
    gate without importing this module.

    **This does not re-derive anything, and the distinction is the whole reason the
    consolidation is safe.** Five of the seven gates are built from the step's own frozen
    output — `unmapped_gate_payload` says why, that "the tags an operator is shown are the
    tags the extractor actually could not place, not a re-derivation that might differ" —
    and reading that record back out of `job_steps` is reading what the step wrote, not
    recomputing it. What would have broken the guarantee is a uniform signature that forced
    every gate to recompute from live rows. Two gates genuinely differ and keep their own
    branch rather than being bent into the common shape:

    * **plan** is assembled from the plan row and its pins, because the pins can be read
      back and the payload is a view over them rather than something the step alone knows.
    * **assumptions** assembles from the rows as they stand, deliberately and alone among
      the gates, because it approves work that has not happened yet and an operator can
      amend a value while the run waits (ADR 0046's amendment, gap A52). Its frozen output
      is still the record of what the step did; it is not what the gate approves.
    """
    produced = await step_output(session, job_id=job.id, step_key=_GATE_STEPS.get(gate, ""))

    if gate == GateKind.PLAN.value:
        plan = await session.get(ResearchPlan, job.plan_id) if job.plan_id else None
        if plan is None:
            return {}
        pins = await pinned_skills_for_work_order(session, work_order_id=job.work_order_id)
        return plan_gate_payload(plan, pins)

    if gate == GateKind.ASSUMPTIONS.value:
        rows = await assumptions_for_request(session, job.work_order_id)
        return assumptions_gate_refreshed(rows, dict(produced))

    if gate == GateKind.FINAL.value:
        return await final_gate_payload(session, job_id=job.id)

    builder = _STEP_OUTPUT_GATES.get(gate)
    if builder is None:
        return {}
    return builder(produced)


# The five that are a pure function of one step's output.
_STEP_OUTPUT_GATES: Final[Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    GateKind.SECTOR_SPECIALIST.value: sector_gate_payload,
    GateKind.PEER_SET.value: peer_gate_payload,
    GateKind.THEME_SET.value: theme_gate_payload,
    GateKind.UNMAPPED_CONCEPTS.value: unmapped_gate_payload,
}


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
        # What the revise pass did rides inside it too (ADR 0091): "approved with these
        # revisions in view" must be verifiable, and the notes are frozen once the revise
        # step — the only writer of them — has run.
        "revisions": await revisions_for_job(session, job_id),
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
                # A red-team challenge's structured record (gap R5). Inside the hash: the
                # statement and basis are part of what the operator approves over.
                "detail": row.detail,
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

    ``None`` when no comparison was performed — no peer set confirmed, or the comps step
    has not succeeded or built no table — because "no comparison was performed" and "a
    comparison whose figures you are not being shown" are different claims and only the
    second needs saying.

    **The counts are the comps step's own** (gap A53). This note used to re-align the
    confirmed peers by date and count the survivors, and the first live run showed what
    that does: the step's table held no peer — none could be priced — while the
    render-time alignment counted one, so the report disclosed a comparison against one
    peer that exists in no version anywhere. Date alignment is a necessary condition for
    comparison, not the comparison; one step built the table, and its stored outcome is
    the only honest source for what the table held.

    Returns a :class:`~aer.calc.comps.WithheldComps` and never a table. A rendered report is
    the shareable artefact, and every multiple in it would derive from market data licensed
    for internal use only — see `_comps_block` in :mod:`aer.render.markdown`.
    """
    confirmed = await confirmed_peer_set(session, job)
    if not confirmed:
        return None

    outcome = await _comps_outcome_for(session, job)
    if outcome is None or not outcome.get("comps"):
        return None

    peers = int(outcome.get("peers", 0))
    as_of_text = outcome.get("as_of")
    return WithheldComps(
        peer_count=peers,
        # Outcomes recorded before the step stored its exclusion count fall back to the
        # identity `build` maintains: every confirmed peer is in the table or excluded.
        excluded_count=int(outcome.get("excluded_count", len(confirmed) - peers)),
        as_of=date.fromisoformat(as_of_text) if as_of_text else request.as_of_date,
        licence_note=DEFAULT_POLICIES[Provider.EODHD].licence_note,
        # The reasons the step already grouped, so the report says why rather than "for
        # want of usable data" (gap R20). Deduplicated again here because the grouping is
        # by reason, and an older outcome that carries none simply says less.
        exclusion_reasons=tuple(
            dict.fromkeys(
                str(row.get("reason", "")).strip()
                for row in outcome.get("excluded", [])
                if str(row.get("reason", "")).strip()
            )
        ),
    )


async def _comps_outcome_for(session: AsyncSession, job: Job) -> dict[str, Any] | None:
    """The comps step's recorded output for this job, or ``None`` before it has succeeded."""
    return await session.scalar(
        select(JobStep.output_ref)
        .where(
            JobStep.job_id == job.id,
            JobStep.step_key == COMPS_STEP,
            JobStep.status == JobStatus.SUCCEEDED,
        )
        .order_by(JobStep.attempt.desc())
        .limit(1)
    )


async def _render(context: StepContext) -> StepResult:
    """One assembly, every stored notation: Markdown, HTML, and — behind an approval —
    the PDF, each a content-addressed artefact the report row links.

    The PDF is rendered from the archived HTML bytes, not from the document object, so
    what freezes is provably derived from the file a re-reader can fetch; and only an
    approved report gets one, because the PDF's every date is the approval's and an
    unapproved run has no approval to stamp (task 48).
    """
    request = await _request_for(context)
    acquired = context.output_of("acquire")
    store = context.service("store")

    company = await context.session.get(Company, _uuid(acquired["company_id"]))

    comps = await comps_note_for(context.session, job=context.job, request=request)
    document = await assemble_document(
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
        style=context.service("settings").house_style,
    )
    markdown = serialise_markdown(document)
    html = render_html(document)

    markdown_artefact = await store_artefact(
        context.session,
        store,
        data=markdown.encode("utf-8"),
        media_type="text/markdown",
    )
    html_artefact = await store_artefact(
        context.session,
        store,
        data=html.encode("utf-8"),
        media_type="text/html",
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
        content={"markdown": markdown, "sections": document.section_keys},
        content_hash=sha256_hex(markdown),
        markdown_artefact_id=markdown_artefact.artefact.id,
        html_artefact_id=html_artefact.artefact.id,
        approved_by=approval.actor_user_id if approval is not None else None,
        approved_at=approval.decided_at if approval is not None else None,
        # Frozen only because a human approved it. The check constraint enforces the same
        # rule, so an immutable report always has an approval behind it.
        immutable=approval is not None,
    )
    context.session.add(report)
    await context.session.flush()

    # The confirmed themes land in rows only now, pointed at this report, so the edge
    # stays inert until the report is immutable — the graph and the vault read
    # memberships through approved reports alone (K1, ADR 0065). Structurally this cannot
    # raise for an undecided slate: the theme gate sits earlier in the DAG, so reaching
    # here means it was approved or never required.
    recorded_themes = await record_confirmed_themes(context.session, job=context.job, report=report)

    pdf_sha256 = None
    if approval is not None and approval.decided_at is not None:
        stored_html = await store.read(html_artefact.sha256)
        pdf_bytes = render_pdf(
            stored_html.decode("utf-8"),
            report_id=str(report.id),
            content_hash=report.content_hash,
            approved_at=approval.decided_at,
        )
        pdf_artefact = await store_artefact(
            context.session,
            store,
            data=pdf_bytes,
            media_type="application/pdf",
        )
        report.pdf_artefact_id = pdf_artefact.artefact.id
        pdf_sha256 = pdf_artefact.sha256

    context.job.status = JobStatus.SUCCEEDED
    context.job.finished_at = datetime.now(UTC)
    await context.session.flush()

    return StepResult(
        output={
            "report_id": str(report.id),
            "markdown_sha256": markdown_artefact.sha256,
            "html_sha256": html_artefact.sha256,
            "pdf_sha256": pdf_sha256,
            "footnotes": document.footnote_count,
            "sections": document.section_keys,
            "characters": len(markdown),
            "themes_recorded": list(recorded_themes),
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
