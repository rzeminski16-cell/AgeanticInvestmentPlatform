"""Built-in section execution: the definition row's policy, applied by the shared rules.

Task 45, ADR 0042. What the custom boundary (:mod:`aer.skills.execution`) does under a
pin, this does under the seeded ``section_definitions`` row: the same evidence assembly
inside the same budgets, the same one-call-one-retry ladder, the same deterministic
validation, the same recording services. The differences are exactly the ones the ADR
names — no operator text in the composition, no tool grant to intersect (every built-in
sees the full evidence pack its budget admits), and the planner's approved *focus* line
as the only steer beyond the contract.
"""

from __future__ import annotations

import structlog

from aer.agents.base import AgentContext, TokenCapExceededError, schema_problems
from aer.agents.section_writer import SectionDraft, SectionWriterAgent, SectionWriterInput
from aer.core.enums import SourceTier
from aer.db.models import ReportSection, ResearchRequest, SectionDefinition, SectionStatus
from aer.errors import ValidationError
from aer.sections.evidence import (
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

__all__ = ["execute_builtin_section", "policy_of_definition"]

_log = structlog.get_logger("aer.sections.writing")

# Every built-in section is assembled the full pack; its budget, not a grant, is what
# bounds it. The writer role itself holds no tools (ADR 0042) — these are evidence
# categories code assembles, not capabilities the model can exercise.
_ALL_CATEGORIES = frozenset({"search_facts", "search_sources"})


def policy_of_definition(definition: SectionDefinition) -> SectionPolicy:
    """The definition row's floor and budget as one policy.

    ``max_tier`` is seeded as a tier name (``"T4_LICENSED_MARKET"``); a bare rank is
    accepted for robustness, and an absent or unknown value falls back to tier 5 — the
    same ceiling an ungoverned custom section defaults to, and the loosest this platform
    admits as citable evidence.
    """
    stated = definition.evidence_policy or {}
    return SectionPolicy(
        min_sources=int(stated.get("min_sources", 1)),
        requires_primary=bool(stated.get("requires_primary", True)),
        max_tier_rank=_tier_rank(stated.get("max_tier")),
        allow_forward_looking=bool(stated.get("allow_forward_looking", False)),
        token_budget=definition.token_budget,
        concept_priority=_names(stated.get("concept_priority")),
        excerpt_keywords=_names(stated.get("excerpt_keywords")),
        fact_basis=_basis(stated.get("fact_basis")),
    )


def _basis(value: object) -> str:
    """A declared fact basis, or "any" for absent and for anything unrecognised.

    Falling back rather than raising, for the same reason max_tier does: a definition
    row with a mistyped preference should cost that preference, not the section.
    """
    if isinstance(value, str) and value in {"annual", "interim", "any"}:
        return value
    return "any"


def _names(value: object) -> tuple[str, ...]:
    """A policy's list of names as a tuple, and anything else as none declared."""
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _tier_rank(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return SourceTier(value).rank
        except ValueError:
            return 5
    return 5


async def execute_builtin_section(
    context: AgentContext,
    *,
    section: ReportSection,
    request: ResearchRequest,
    focus: str = "",
) -> SectionExecution:
    """Write one built-in section to a recorded outcome. Never raises for a bad draft.

    Mirrors :func:`aer.skills.execution.execute_custom_section` deliberately: the section
    row is mutated in place, claims and citations are recorded through the same services,
    and every failure mode is a visible state on the row rather than an absent section.
    """
    definition = section.definition
    contract = definition.output_contract or {}
    policy = policy_of_definition(definition)

    evidence = await gather_evidence(
        context.session,
        request=request,
        evidence_job_id=context.job_step.job_id,
        policy=policy,
        categories=_ALL_CATEGORIES,
    )

    agent = SectionWriterAgent()
    problems: list[str] = []
    draft: SectionDraft | None = None

    attempts = 0
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        attempts = attempt
        payload = SectionWriterInput(
            section_key=section.section_key,
            title=definition.title,
            company_name=request.company_name,
            ticker=request.ticker,
            as_of_date=request.as_of_date.isoformat(),
            point_in_time=request.point_in_time,
            output_contract=contract,
            evidence_policy=policy.as_prompt_payload(),
            internal_evidence=evidence.internal,
            untrusted_evidence=evidence.untrusted,
            focus=focus,
            problems=problems,
            evidence_truncated=evidence.truncated,
        )
        try:
            candidate = await agent.run(context, payload)
        except TokenCapExceededError as refused:
            # Deterministic in the composition: a retry would compose the same call and
            # be refused the same way, so the section fails now, visibly and for free.
            return _failed(
                section, attempts=attempt, problems=[str(refused)], truncated=evidence.truncated
            )
        except ValidationError as unparsable:
            # The field-level detail, not the count. A retry told only that "22 field(s)
            # broke a constraint" has nothing to act on and makes the same mistake again,
            # which is how three sections of one live report died at two attempts each.
            problems = schema_problems(unparsable)
            continue

        problems = validate_draft(candidate, contract=contract, evidence=evidence, policy=policy)
        if not problems:
            draft = candidate
            break
        _log.info(
            "section_writer.draft_refused",
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
        "section_writer.generated",
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
        "section_writer.failed",
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
