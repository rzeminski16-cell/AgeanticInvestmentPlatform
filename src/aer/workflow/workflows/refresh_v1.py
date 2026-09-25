"""The refresh workflow: a second run on the same request that pays only for what moved (F4).

ADR 0131 §3. Nineteen steps under the vertical slice's own keys wherever a reader expects
them, so the value step, the comps step and the render step read the outputs they already
read by name::

    carry_forward -> acquire -> acquire_macro -> classify* -> propose_peers* ->
    propose_themes* -> extract -> [unmapped gate] -> acquire_prices -> calculate -> comps ->
    propose_assumptions* -> value -> diff -> draft -> validate -> revise -> [final gate] ->
    render

The four starred steps carry the prior job's frozen output under the same key and spend
nothing. The free steps are the slice's own functions, imported by their private names on
purpose: a second copy of `_acquire` would be a second acquisition path to keep honest, and
the slice is the one that ships. There is no planner, no critic, no research worker, no red
team, no verdict and no challenge briefs.

**Nothing new costs nothing.** When the acquisition read no filing the record did not hold
and the aggregate's digest is the prior run's, the diff is empty, the change summary says so
from its rows, no section is drafted, no document is rendered, and the prior report stays
current. The draft step's ceiling (``settings.refresh_budget_gbp``) is checked before every
section it would pay for; a section the ceiling will not carry is carried instead, marked
stale, and the summary says which.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aer.agents.base import AgentContext
from aer.core.enums import GateKind, JobStatus
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import (
    Claim,
    Job,
    OperatorPeer,
    OperatorTheme,
    Report,
    ReportChange,
    ReportSection,
    ResearchRequest,
    SectionDefinition,
    SectionStatus,
    User,
)
from aer.db.models.section_definition import BUILTIN, SKILL
from aer.sections.deterministic import (
    CHANGE_SUMMARY_KEY,
    SectionStage,
    fill_deterministic_sections,
    price_drafted_cases,
)
from aer.sections.registry import create_report_sections, sections_for_job
from aer.services import approvals as approval_service
from aer.services import refresh as refresh_service
from aer.services.citations import record_citation, record_claim
from aer.services.comps import PEER_SET_STEP
from aer.services.refresh import DIFF_STEP
from aer.services.sectors import CLASSIFY_STEP
from aer.services.themes import THEME_STEP
from aer.skills.resolution import guidance_from_pins, pinned_skills_for_job
from aer.workflow.engine import StepContext, StepPaused, StepResult, WorkflowStep, spend_so_far
from aer.workflow.pauses import PauseReason

# The slice's private step functions and seams, imported deliberately (see the module
# docstring): the refresh runs the slice's code, it does not restate it.
from aer.workflow.workflows.vertical_slice_v1 import (
    _COST_SCENE,
    ASSUMPTIONS_STEP,
    COMPS_STEP,
    MACRO_STEP,
    PRICES_STEP,
    VALIDATOR_ESTIMATE_GBP,
    VALUE_STEP,
    _acquire,
    _acquire_macro,
    _acquire_prices,
    _calculate,
    _comps,
    _cost_record,
    _draft_one,
    _extract,
    _focus_by_key,
    _gate_final,
    _gate_unmapped_concepts,
    _render,
    _SectionWork,
    _validate,
    _value,
    final_gate_payload,
    sealed_cost_scene,
    step_output,
    unmapped_gate_payload,
)

__all__ = [
    "CARRY_STEP",
    "DIFF_STEP",
    "DRAFT_STEP",
    "WORKFLOW_VERSION",
    "build_steps",
]

_log = structlog.get_logger("aer.workflow.refresh")

WORKFLOW_VERSION: Final = refresh_service.WORKFLOW_VERSION
CARRY_STEP: Final = "carry_forward"
DRAFT_STEP: Final = "draft"
_SEAL_STEP: Final = "revise"

# What one re-drafted section is projected at, and what the step is: the mechanism's six
# at the slice's per-section figure. The ceiling in the settings bounds the spend; this is
# what the budget guard compares before the step starts.
PER_SECTION_ESTIMATE_GBP: Final = (
    refresh_service.DRAFT_ESTIMATE_GBP / refresh_service.SPINE_SECTIONS
)
DRAFT_ESTIMATE_GBP: Final = PER_SECTION_ESTIMATE_GBP * refresh_service.EXPECTED_SECTIONS

# The output keys later steps and the summary read from the diff and the draft.
_NOTHING_NEW: Final = "nothing_new"
_TO_REDRAFT: Final = "sections_to_redraft"
_TOO_MANY: Final = "too_many"


def build_steps() -> list[WorkflowStep]:
    """The refresh, in order (ADR 0131 §3)."""
    return [
        WorkflowStep(key=CARRY_STEP, run=_carry_forward),
        WorkflowStep(key="acquire", run=_acquire),
        WorkflowStep(key=MACRO_STEP, run=_acquire_macro),
        WorkflowStep(key=CLASSIFY_STEP, run=_carried(CLASSIFY_STEP)),
        WorkflowStep(key=PEER_SET_STEP, run=_carried(PEER_SET_STEP)),
        WorkflowStep(key=THEME_STEP, run=_carried(THEME_STEP)),
        WorkflowStep(key="extract", run=_extract),
        # Conditional, and the one gate a refresh may stop at (ADR 0131 §2): a tag the
        # extractor cannot place stops a refresh as it stops a run. A decision the prior
        # run took over exactly these tags is carried rather than asked again.
        WorkflowStep(
            key="gate_unmapped_concepts",
            run=_gate_unmapped_concepts_or_carry,
            gate=GateKind.UNMAPPED_CONCEPTS.value,
        ),
        WorkflowStep(key=PRICES_STEP, run=_acquire_prices),
        WorkflowStep(key="calculate", run=_calculate),
        WorkflowStep(key=COMPS_STEP, run=_comps),
        WorkflowStep(key=ASSUMPTIONS_STEP, run=_carried(ASSUMPTIONS_STEP)),
        WorkflowStep(key=VALUE_STEP, run=_value),
        WorkflowStep(key=DIFF_STEP, run=_diff),
        WorkflowStep(key=DRAFT_STEP, run=_draft, estimated_cost_gbp=DRAFT_ESTIMATE_GBP),
        # The slice's validators, whose assists are the one model spend a quiet quarter
        # would otherwise pay: skipped when nothing is new, since there is no document.
        WorkflowStep(
            key="validate",
            run=_validate_unless_nothing_new,
            estimated_cost_gbp=VALIDATOR_ESTIMATE_GBP,
        ),
        # Seals and nothing else: the cost scene and the final gate's payload hash, with no
        # revise pass — there was no adversary to answer.
        WorkflowStep(key=_SEAL_STEP, run=_seal),
        WorkflowStep(
            key="gate_final", run=_gate_final_unless_nothing_new, gate=GateKind.FINAL.value
        ),
        WorkflowStep(key="render", run=_render_unless_nothing_new),
    ]


# ==========================================================================================
# 1. Carry forward
# ==========================================================================================


async def _carry_forward(context: StepContext) -> StepResult:
    """Name the run being refreshed, and give this run the sections it will fill.

    The sections are the prior run's own definitions — the versions its content was written
    to, so a carried section's content still fits its contract — plus the change summary,
    which no full run resolves (its applicability names a property every request answers
    ``false``) and which this step alone creates. The operator's peer and theme additions
    are copied to this job too: the re-asserted approvals hash the whole slate, and the
    slate is read per job.
    """
    prior_job, report = await _prior(context)
    prior_sections = await sections_for_job(context.session, prior_job.id)
    definitions = [section.definition for section in prior_sections]
    summary = await _summary_definition(context)
    if summary is not None and all(d.key != summary.key for d in definitions):
        definitions.append(summary)
    created = await create_report_sections(
        context.session, job_id=context.job.id, definitions=definitions
    )
    peers, themes = await _copy_operator_slate(context, prior_job_id=prior_job.id)
    return StepResult(
        output={
            "prior_job_id": str(prior_job.id),
            "prior_report_id": str(report.id),
            "prior_as_of_date": report.as_of_date.isoformat(),
            "carried_plan_id": str(prior_job.plan_id) if prior_job.plan_id else None,
            "section_keys": [definition.key for definition in definitions],
            "sections_created": [row.section_key for row in created],
            "operator_peers_carried": peers,
            "operator_themes_carried": themes,
        }
    )


async def _prior(context: StepContext) -> tuple[Job, Report]:
    """The report this job refreshes and the run that produced it, or a pause naming why not."""
    report = (
        await context.session.get(Report, context.job.refreshes_report_id)
        if context.job.refreshes_report_id is not None
        else None
    )
    prior = await context.session.get(Job, report.job_id) if report is not None else None
    if report is None or prior is None:
        message = (
            "This run is recorded as a refresh, and the report it refreshes cannot be found. "
            "Commission the refresh again from the report's own page."
        )
        raise StepPaused(message, gate=None, reason=PauseReason.ROW_MISSING)
    return prior, report


async def _summary_definition(context: StepContext) -> SectionDefinition | None:
    found: SectionDefinition | None = await context.session.scalar(
        select(SectionDefinition)
        .where(SectionDefinition.key == CHANGE_SUMMARY_KEY, SectionDefinition.origin == BUILTIN)
        .order_by(SectionDefinition.version.desc())
        .limit(1)
    )
    return found


async def _copy_operator_slate(context: StepContext, *, prior_job_id: uuid.UUID) -> tuple[int, int]:
    peers = list(
        await context.session.scalars(
            select(OperatorPeer).where(OperatorPeer.job_id == prior_job_id)
        )
    )
    themes = list(
        await context.session.scalars(
            select(OperatorTheme).where(OperatorTheme.job_id == prior_job_id)
        )
    )
    existing_peers = {
        row.company_id
        for row in await context.session.scalars(
            select(OperatorPeer).where(OperatorPeer.job_id == context.job.id)
        )
    }
    existing_themes = {
        row.key
        for row in await context.session.scalars(
            select(OperatorTheme).where(OperatorTheme.job_id == context.job.id)
        )
    }
    for peer in peers:
        if peer.company_id in existing_peers:
            continue
        context.session.add(
            OperatorPeer(
                job_id=context.job.id,
                company_id=peer.company_id,
                rationale=peer.rationale,
                added_by=peer.added_by,
            )
        )
    for theme in themes:
        if theme.key in existing_themes:
            continue
        context.session.add(
            OperatorTheme(
                job_id=context.job.id,
                key=theme.key,
                label=theme.label,
                rationale=theme.rationale,
                added_by=theme.added_by,
            )
        )
    await context.session.flush()
    return len(peers), len(themes)


# ==========================================================================================
# 2. The carried steps and the carried gate
# ==========================================================================================


def _carried(step_key: str) -> Callable[[StepContext], Any]:
    """A step that records the prior job's frozen output under the same key, for nothing."""

    async def run(context: StepContext) -> StepResult:
        carried = context.output_of(CARRY_STEP)
        prior_job_id = uuid.UUID(str(carried["prior_job_id"]))
        produced = await step_output(context.session, job_id=prior_job_id, step_key=step_key)
        return StepResult(output={**produced, "carried_from_job_id": str(prior_job_id)})

    run.__name__ = f"_carried_{step_key}"
    return run


async def _gate_unmapped_concepts_or_carry(context: StepContext) -> StepResult:
    """The slice's gate, unless the prior run decided over exactly these tags.

    The gate approves the extract step's frozen payload by hash. When the refresh's
    extraction produced the same payload the prior run's operator already decided on, that
    decision is re-asserted on this job with its hash and a note (ADR 0131 §2), and the
    slice's own gate then finds it. A different payload — a tag the extractor could not
    place last time either, beside one it could not place now — is a new question and
    stops the refresh exactly as it stops a run.
    """
    produced = context.outputs.get("extract", {})
    if context.job.refreshes_report_id is not None and produced:
        carried = context.output_of(CARRY_STEP)
        prior_job_id = uuid.UUID(str(carried["prior_job_id"]))
        decided = await approval_service.current_decision(
            context.session, prior_job_id, GateKind.UNMAPPED_CONCEPTS
        )
        already = await approval_service.current_decision(
            context.session, context.job.id, GateKind.UNMAPPED_CONCEPTS
        )
        expected = sha256_hex(canonical_json(unmapped_gate_payload(produced)))
        if (
            already is None
            and decided is not None
            and decided.decision in approval_service.PASSING_DECISIONS
            and decided.payload_hash == expected
        ):
            actor = await context.session.get(User, decided.actor_user_id)
            if actor is not None:
                await approval_service.record_decision(
                    context.session,
                    job=context.job,
                    gate=GateKind.UNMAPPED_CONCEPTS,
                    decision=decided.decision,
                    actor=actor,
                    payload_hash=decided.payload_hash,
                    notes=(
                        "Carried by the refresh: the extraction left the same tags unplaced "
                        "as the run this decision was taken on."
                    ),
                )
    return await _gate_unmapped_concepts(context)


# ==========================================================================================
# 3. The diff
# ==========================================================================================


async def _diff(context: StepContext) -> StepResult:
    """Compare this run's figures with the prior run's, and decide what to re-draft.

    Pure in the middle (:mod:`aer.calc.changes`), rows at the edge (``report_changes``), and
    one judgement here that is a lookup rather than a judgement: a section is re-drafted
    when a material row names a figure among its prior claims (ADR 0131 §6). *Nothing new*
    is decided from the acquisition alone — no filing read for the first time and the
    aggregate's digest unchanged — so the answer does not depend on which calculations a
    later code version happens to strike.
    """
    request = await _request_for(context)
    prior_job, report = await _prior(context)
    outcome = await refresh_service.diff_runs(
        context.session, job=context.job, prior=prior_job, request=request, report=report
    )

    acquired = context.output_of("acquire")
    prior_acquired = await step_output(context.session, job_id=prior_job.id, step_key="acquire")
    new_filings = list(acquired.get("filings", []) or [])
    same_aggregate = bool(acquired.get("artefact_sha256")) and acquired.get(
        "artefact_sha256"
    ) == prior_acquired.get("artefact_sha256")
    nothing_new = not new_filings and same_aggregate and not outcome.material

    named = await refresh_service.figures_named_by_sections(context.session, job_id=prior_job.id)
    flagged = sorted(refresh_service.sections_moved(outcome.material, named))
    too_many = len(flagged) > refresh_service.MAX_SECTIONS_FOR_A_REFRESH

    return StepResult(
        output={
            **outcome.as_dict(),
            _NOTHING_NEW: nothing_new,
            "same_aggregate": same_aggregate,
            "filings_read_first": len(new_filings),
            _TO_REDRAFT: flagged,
            _TOO_MANY: too_many,
        }
    )


# ==========================================================================================
# 4. The draft: re-draft what moved, carry the rest
# ==========================================================================================


async def _draft(context: StepContext) -> StepResult:  # noqa: PLR0912, PLR0915 -- one pass, in order
    """Fill this run's sections: re-draft the flagged ones, carry the others (ADR 0131 §6).

    In position order, one section at a time on the caller's session. The change summary
    is written first because it is the refresh's headline and, on a quiet quarter, its
    whole result — the augmenter answers from the rows and no writer is called. A flagged
    section is re-drafted in place under the ceiling; a carried one gets the prior
    content, its claims re-pointed at this ledger where the figure resolves, and its
    citations copied unverified, to be verified at the final gate with everything else's.
    """
    request = await _request_for(context)
    prior_job, _report = await _prior(context)
    diff = context.output_of(DIFF_STEP)
    settings = context.service("settings")
    nothing_new = bool(diff.get(_NOTHING_NEW))
    too_many = bool(diff.get(_TOO_MANY))
    flagged = set(diff.get(_TO_REDRAFT, []) or [])

    deterministic = await fill_deterministic_sections(
        context.session, job=context.job, request=request, stage=SectionStage.DRAFT
    )

    sections = await sections_for_job(context.session, context.job.id)
    prior_by_key = {
        section.section_key: section
        for section in await sections_for_job(context.session, prior_job.id)
    }
    remap = await refresh_service.calculation_remap(
        context.session, prior_job_id=prior_job.id, job_id=context.job.id
    )
    focus_by_key = await _focus_by_key(context)
    pins = await pinned_skills_for_job(context.session, job=context.job)
    guidance = guidance_from_pins(pins)

    agent_context = AgentContext(
        session=context.session,
        provider=context.service("provider"),
        router=context.service("router"),
        settings=settings,
        store=context.service("store"),
        job_step=context.step,
    )
    spent_before = await spend_so_far(context.session, job_id=context.job.id)
    ceiling: Decimal = settings.refresh_budget_gbp

    redrafted: list[str] = []
    filled_from_record: list[str] = []
    carried: list[str] = []
    stale: list[str] = []
    over_ceiling: list[str] = []
    kept: list[str] = []
    outcomes: list[dict[str, Any]] = []

    ordered = sorted(sections, key=lambda s: (s.section_key != CHANGE_SUMMARY_KEY, s.position))
    for section in ordered:
        definition = section.definition
        if definition.origin != SKILL and definition.token_budget == 0:
            continue
        key = section.section_key
        if section.status is SectionStatus.GENERATED:
            # Re-entrant, as the slice's draft is: an earlier attempt already wrote it.
            kept.append(key)
            continue
        prior = prior_by_key.get(key)
        # A section is carried only into the version of its definition that wrote it. Carried
        # by key alone, a report written to the thesis contract lands in the cases' contract
        # (ADR 0135) and renders as a heading with nothing under it: a changed contract is a
        # reason to draft, whatever else moved.
        contract_moved = (
            prior is not None and prior.section_definition_id != section.section_definition_id
        )
        has_prior = (
            prior is not None and prior.status is SectionStatus.GENERATED and not contract_moved
        )

        if key == CHANGE_SUMMARY_KEY:
            # Free when nothing moved (the augmenter answers from the rows); one writer call
            # otherwise, and the first thing the ceiling is asked to carry.
            if not nothing_new and _over(spent_before, agent_context.spend_gbp, ceiling):
                over_ceiling.append(key)
                _left_unwritten(section, ceiling=ceiling)
                outcomes.append(_outcome(section, "over_ceiling"))
                continue
            execution = await _draft_one(
                agent_context,
                request=request,
                work=_SectionWork(section_id=section.id, custom=False, focus="", guidance=()),
            )
            # Written by the augmenter from the rows alone (no attempt was made) or by the
            # writer over them: the record says which, so the summary can too.
            from_record = execution.attempts == 0
            outcomes.append(
                {
                    **execution.as_dict(),
                    "disposition": "from_record" if from_record else "redrafted",
                }
            )
            if execution.status is SectionStatus.GENERATED:
                (filled_from_record if from_record else redrafted).append(key)
            continue

        wants_redraft = key in flagged or not has_prior
        if wants_redraft and (contract_moved or (not too_many and not nothing_new)):
            if _over(spent_before, agent_context.spend_gbp, ceiling):
                over_ceiling.append(key)
                if has_prior and prior is not None:
                    await _carry(context, section=section, prior=prior, remap=remap, stale=True)
                    stale.append(key)
                    outcomes.append(_outcome(section, "stale"))
                else:
                    _left_unwritten(section, ceiling=ceiling)
                    outcomes.append(_outcome(section, "over_ceiling"))
                continue
            execution = await _draft_one(
                agent_context,
                request=request,
                work=_SectionWork(
                    section_id=section.id,
                    custom=definition.origin == SKILL,
                    focus=focus_by_key.get(key, ""),
                    guidance=tuple(guidance),
                ),
            )
            if execution.status is SectionStatus.GENERATED:
                redrafted.append(key)
                outcomes.append({**execution.as_dict(), "disposition": "redrafted"})
                continue
            if has_prior and prior is not None:
                # A re-draft that fails carries the prior text, marked stale (ADR 0131 §6).
                await _carry(context, section=section, prior=prior, remap=remap, stale=True)
                stale.append(key)
                outcomes.append({**execution.as_dict(), "disposition": "stale"})
                continue
            outcomes.append({**execution.as_dict(), "disposition": "failed"})
            continue

        if has_prior and prior is not None:
            is_stale = wants_redraft  # flagged, but too many moved to re-draft any
            await _carry(context, section=section, prior=prior, remap=remap, stale=is_stale)
            (stale if is_stale else carried).append(key)
            outcomes.append(_outcome(section, "stale" if is_stale else "carried"))
            continue

        # No prior text and nothing to draft from: a section the prior run never wrote.
        section.status = SectionStatus.FAILED
        section.low_confidence_reason = (
            "The run this refresh updates never wrote this section, and a refresh drafts only "
            "what moved. A full run writes it."
        )
        outcomes.append(_outcome(section, "failed"))

    await context.session.flush()
    # Carried or redrafted, the cases' figures are struck on this run's own base case: the
    # argument is prose and carries, the figures are records (ADR 0135, ADR 0131 §2).
    priced = await price_drafted_cases(context.session, job=context.job, force=True)
    filled = sum(
        1
        for s in await sections_for_job(context.session, context.job.id)
        if s.status is SectionStatus.GENERATED
    )
    return StepResult(
        output={
            "sections_drafted": filled,
            "deterministic_sections": deterministic,
            "builtin_sections": outcomes,
            "custom_sections": [],
            "redrafted": redrafted,
            "from_record": filled_from_record,
            "carried": carried,
            "stale": stale,
            "over_ceiling": over_ceiling,
            "kept": kept,
            "cases_priced": priced,
            _NOTHING_NEW: nothing_new,
            _TOO_MANY: too_many,
            "ceiling_gbp": str(ceiling),
        },
        cost_gbp=agent_context.spend_gbp,
    )


def _over(spent_before: Decimal, spent_here: Decimal, ceiling: Decimal) -> bool:
    """Whether one more section at the per-section estimate would breach the ceiling."""
    return spent_before + spent_here + PER_SECTION_ESTIMATE_GBP > ceiling


def _left_unwritten(section: ReportSection, *, ceiling: Decimal) -> None:
    section.status = SectionStatus.FAILED
    section.low_confidence_reason = (
        f"Not drafted: the refresh's ceiling of £{ceiling:.2f} was reached before this section "
        "could be paid for."
    )


def _outcome(section: ReportSection, disposition: str) -> dict[str, Any]:
    return {
        "section_key": section.section_key,
        "status": section.status.value,
        "attempts": 0,
        "disposition": disposition,
    }


async def _carry(
    context: StepContext,
    *,
    section: ReportSection,
    prior: ReportSection,
    remap: Mapping[str, str],
    stale: bool,
) -> None:
    """Copy the prior section here: content, claims re-pointed, citations unverified."""
    section.content = _remapped(prior.content, remap) if prior.content is not None else None
    section.status = SectionStatus.GENERATED
    section.confidence = prior.confidence
    prior_note = (prior.low_confidence_reason or "").strip()
    if stale:
        as_of = context.output_of(CARRY_STEP).get("prior_as_of_date", "an earlier date")
        note = (
            f"Carried from the report of {as_of} although a figure it rests on moved: the "
            "refresh's ceiling, or the number of sections that moved, left it un-drafted."
        )
        section.low_confidence_reason = f"{prior_note} {note}".strip()
    else:
        section.low_confidence_reason = prior_note or None

    claims = list(
        await context.session.scalars(
            select(Claim)
            .where(Claim.report_section_id == prior.id)
            .options(selectinload(Claim.citations))
            .order_by(Claim.created_at, Claim.id)
        )
    )
    for claim in claims:
        calculation_id = claim.calculation_id
        if calculation_id is not None:
            calculation_id = uuid.UUID(remap.get(str(calculation_id), str(calculation_id)))
        copied = await record_claim(
            context.session,
            section=section,
            kind=claim.kind,
            text=claim.text,
            financial_fact_id=claim.financial_fact_id,
            calculation_id=calculation_id,
            attestation_id=claim.attestation_id,
        )
        for citation in claim.citations:
            await record_citation(
                context.session,
                claim=copied,
                source_document_id=citation.source_document_id,
                extraction_id=citation.extraction_id,
            )
    await context.session.flush()


def _remapped(value: Any, remap: Mapping[str, str]) -> Any:
    """The prior content with every calculation id it names re-pointed at this ledger's."""
    if isinstance(value, dict):
        return {key: _remapped(item, remap) for key, item in value.items()}
    if isinstance(value, list):
        return [_remapped(item, remap) for item in value]
    if isinstance(value, str) and value in remap:
        return remap[value]
    return value


# ==========================================================================================
# 5. Seal, gate, render
# ==========================================================================================


async def _seal(context: StepContext) -> StepResult:
    """Record the cost scene and the final gate's payload hash. No revise pass."""
    request = await _request_for(context)
    cost = await sealed_cost_scene(context.session, job=context.job, request=request)
    payload = await final_gate_payload(context.session, job_id=context.job.id, cost=cost)
    draft = context.outputs.get(DRAFT_STEP, {})
    return StepResult(
        output={
            "revised": [],
            "stood": [],
            _COST_SCENE: _cost_record(cost),
            "payload_hash": sha256_hex(canonical_json(payload)),
            _NOTHING_NEW: bool(draft.get(_NOTHING_NEW)),
        }
    )


async def _validate_unless_nothing_new(context: StepContext) -> StepResult:
    if _nothing_new(context):
        return StepResult(
            output={
                "skipped": True,
                "reason": "Nothing new was read, so there is no refreshed document to validate.",
                "metrics": {},
                "failed": [],
                "not_exercised": [],
            }
        )
    return await _validate(context)


async def _gate_final_unless_nothing_new(context: StepContext) -> StepResult:
    if _nothing_new(context):
        return StepResult(
            output={
                "gate": GateKind.FINAL.value,
                "required": False,
                "reason": "Nothing new was read, so there is no refreshed document to approve.",
            }
        )
    return await _gate_final(context)


async def _render_unless_nothing_new(context: StepContext) -> StepResult:
    """Render as the slice does, then point this run's change rows at the report.

    On a quiet quarter nothing is rendered: the prior report stays current, the change
    summary stands as the run's result, and the run finishes at no spend (ADR 0131 §4).
    """
    if _nothing_new(context):
        context.job.status = JobStatus.SUCCEEDED
        context.job.finished_at = datetime.now(UTC)
        await context.session.flush()
        return StepResult(
            output={
                "report_id": None,
                _NOTHING_NEW: True,
                "prior_report_id": context.output_of(CARRY_STEP).get("prior_report_id"),
            }
        )
    result = await _render(context)
    report_id = result.output.get("report_id")
    if report_id:
        rows = await context.session.scalars(
            select(ReportChange).where(ReportChange.job_id == context.job.id)
        )
        for row in rows:
            row.report_id = uuid.UUID(str(report_id))
        await context.session.flush()
    return result


def _nothing_new(context: StepContext) -> bool:
    return bool(context.outputs.get(DIFF_STEP, {}).get(_NOTHING_NEW))


async def _request_for(context: StepContext) -> ResearchRequest:
    request = await context.session.get(ResearchRequest, context.job.work_order_id)
    if request is None:  # pragma: no cover -- a job cannot exist without its request
        message = "The job's research request is missing."
        raise StepPaused(message, gate=None, reason=PauseReason.ROW_MISSING)
    return request
