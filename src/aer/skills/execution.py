"""Custom-section execution: the pinned policy, applied through the shared discipline.

The deterministic half of ADR 0037. The agent proposes one draft; everything around it —
what evidence it sees, whether its content satisfies the pinned contract, whether its
claims resolve, what happens when they do not — is the shared machinery of
:mod:`aer.sections.evidence`, driven here by the **pin's snapshot** rather than a
recomposition, because a floor that moved between approval and execution must not
silently change what runs. Task 45 moved the machinery there so built-in sections draft
under the identical rules; this module keeps what is custom about custom sections: the
pinned policy, the pinned tool grant deciding which evidence categories are assembled,
the operator's text in the ``<user_skill>`` block, and the reserved-field refusal at the
last boundary before a model call.

Every failure mode is a visible state on the section row, never an absent section:

* a second validation failure marks the section ``failed`` with the reasons recorded;
* evidence short of the composed policy generates with an insufficiency note and low
  confidence — never fabricated prose;
* evidence over the token budget is truncated at whole-item boundaries and flagged;
* a projected contract carrying a reserved field is refused before any money is spent —
  task 35 makes that unrepresentable upstream, and this boundary does not rely on it.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from aer.agents.base import AgentContext, TokenCapExceededError, schema_problems
from aer.agents.custom_section import CustomSectionAgent, CustomSectionDraft, CustomSectionInput
from aer.core.section_output import reserved_fields_in
from aer.db.models import PlanSkillPin, ReportSection, ResearchRequest, SectionStatus
from aer.errors import ValidationError
from aer.sections.evidence import (
    EVIDENCE_ITEM_CAP,
    MAX_GENERATION_ATTEMPTS,
    SectionExecution,
    SectionPolicy,
    confidence_of,
    content_source_ids,
    degradation_note,
    gather_evidence,
    policy_shortfalls,
    record_draft_claims,
    validate_draft,
)

__all__ = [
    "EVIDENCE_ITEM_CAP",
    "MAX_GENERATION_ATTEMPTS",
    "SectionExecution",
    "execute_custom_section",
]

_log = structlog.get_logger("aer.skills.execution")


async def execute_custom_section(
    context: AgentContext,
    *,
    section: ReportSection,
    pin: PlanSkillPin,
    request: ResearchRequest,
    evidence_job_id: uuid.UUID | None = None,
) -> SectionExecution:
    """Run one custom section to a recorded outcome. Never raises for a bad draft.

    The section row is mutated in place — content, status, confidence, and the
    low-confidence reason that carries the insufficiency and truncation flags — and the
    claims and citations are recorded through the same services built-in sections use.
    What comes back is the audit summary the workflow step stores.

    Args:
        evidence_job_id: Whose recorded calculations count as this section's evidence.
            Defaults to the executing run's own job, which is the only answer a real run
            has. A dry run (task 43) executes in its own job against a finished run's
            evidence, and passes that run's id here — an explicit argument rather than a
            silent widening of the query, because "which run's figures may this section
            cite?" is exactly the question a reader of a citation needs answered.
    """
    contract: dict[str, Any] = section.definition.output_contract or {}

    # Defence in depth at the last boundary before a model call. Task 35 refuses these
    # names at authoring and task 36 projects only validated contracts, so reaching this
    # means a row was written around the service layer — and the answer to that is a
    # refusal here, not trust there.
    reserved = reserved_fields_in(contract)
    if reserved:
        message = (
            f"The projected contract declares the reserved field(s) {sorted(reserved)}. "
            "Ratings, recommendations and valuation ranges are owned by built-in "
            "sections; this section is refused unrun."
        )
        return _failed(section, attempts=0, problems=[message])

    policy = _policy_of(pin, context)
    evidence = await gather_evidence(
        context.session,
        request=request,
        evidence_job_id=evidence_job_id or context.job_step.job_id,
        policy=policy,
        # The pinned grant doubles as the evidence-category gate: a section granted no
        # `search_sources` is assembled no source listings, and so cannot cite them.
        categories=frozenset(pin.granted_tools or []),
    )

    agent = CustomSectionAgent()
    body = pin.skill_version.body
    problems: list[str] = []
    draft: CustomSectionDraft | None = None

    attempts = 0
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        attempts = attempt
        payload = CustomSectionInput(
            section_key=section.section_key,
            title=section.definition.title,
            company_name=request.company_name,
            ticker=request.ticker,
            as_of_date=request.as_of_date.isoformat(),
            output_contract=contract,
            evidence_policy=policy.as_prompt_payload(),
            internal_evidence=evidence.internal,
            untrusted_evidence=evidence.untrusted,
            skill_body=body,
            problems=problems,
            evidence_truncated=evidence.truncated,
        )
        try:
            candidate = await agent.run(context, payload)
        except TokenCapExceededError as refused:
            # Deterministic in the composition: a retry would compose the same call and
            # be refused the same way, so the section fails now, visibly and for free.
            return _failed(
                section,
                attempts=attempt,
                problems=[str(refused)],
                truncated=evidence.truncated,
            )
        except ValidationError as unparsable:
            # As in `aer.sections.writing`: the fields, not the count, or the retry has
            # nothing to act on.
            problems = schema_problems(unparsable)
            continue

        problems = validate_draft(candidate, contract=contract, evidence=evidence, policy=policy)
        if not problems:
            draft = candidate
            break
        _log.info(
            "custom_section.draft_refused",
            section=section.section_key,
            attempt=attempt,
            problems=problems,
        )

    if draft is None:
        return _failed(section, attempts=attempts, problems=problems, truncated=evidence.truncated)

    recorded, cited_source_ids = await record_draft_claims(
        context.session, section=section, draft=draft, evidence=evidence
    )
    # Content-cited sources count towards the floor exactly as the coverage metric
    # counts them: a figure row citing through content is evidence, not decoration.
    cited_source_ids |= content_source_ids(draft.content)
    shortfalls = policy_shortfalls(cited_source_ids, evidence=evidence, policy=policy)

    section.content = draft.content
    section.status = SectionStatus.GENERATED
    section.confidence = confidence_of(draft.content, degraded=bool(shortfalls))
    section.low_confidence_reason = degradation_note(shortfalls, truncated=evidence.truncated)
    await context.session.flush()

    _log.info(
        "custom_section.generated",
        section=section.section_key,
        attempts=attempts,
        claims=recorded,
        insufficient_evidence=bool(shortfalls),
        evidence_truncated=evidence.truncated,
    )
    return SectionExecution(
        section=section,
        status=SectionStatus.GENERATED,
        attempts=attempts,
        claims_recorded=recorded,
        insufficient_evidence=bool(shortfalls),
        evidence_truncated=evidence.truncated,
        problems=shortfalls,
    )


def _failed(
    section: ReportSection,
    *,
    attempts: int,
    problems: list[str],
    truncated: bool = False,
) -> SectionExecution:
    """Mark the section failed with its reasons on the row. The run continues."""
    section.status = SectionStatus.FAILED
    section.content = None
    section.confidence = None
    section.low_confidence_reason = " ".join(problems)[:2000] or None
    _log.warning(
        "custom_section.failed",
        section=section.section_key,
        attempts=attempts,
        problems=problems,
    )
    return SectionExecution(
        section=section,
        status=SectionStatus.FAILED,
        attempts=attempts,
        evidence_truncated=truncated,
        problems=problems,
    )


def _policy_of(pin: PlanSkillPin, context: AgentContext) -> SectionPolicy:
    """The pin's snapshot as one policy — what gate 1 approved, not a recomposition."""
    return SectionPolicy(
        min_sources=pin.min_sources if pin.min_sources is not None else 1,
        requires_primary=bool(pin.requires_primary),
        max_tier_rank=pin.max_tier if pin.max_tier is not None else 5,
        allow_forward_looking=(
            pin.allow_forward_looking if pin.allow_forward_looking is not None else True
        ),
        token_budget=(
            pin.token_budget
            if pin.token_budget is not None
            else context.settings.custom_section_token_ceiling
        ),
    )
