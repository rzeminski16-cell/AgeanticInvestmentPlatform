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

from dataclasses import dataclass
from typing import Any, Final

import structlog

from aer.agents.base import AgentContext, TokenCapExceededError, schema_problems
from aer.agents.section_writer import SectionDraft, SectionWriterAgent, SectionWriterInput
from aer.core.enums import SourceTier
from aer.core.section_output import trimmed_to_word_count, without_unsourced_numeral_sentences
from aer.db.models import ReportSection, ResearchRequest, SectionDefinition, SectionStatus
from aer.errors import ValidationError
from aer.sections.evidence import (
    MAX_GENERATION_ATTEMPTS,
    Evidence,
    SectionExecution,
    SectionPolicy,
    confidence_of,
    content_source_ids,
    degradation_note,
    gather_evidence,
    policy_shortfalls,
    record_draft_claims,
    validate_draft,
    word_ceiling,
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
        word_budget=_word_budget(stated.get("word_budget")),
    )


def _word_budget(value: object) -> int:
    """A declared word budget, or zero — unbounded — for absent and unusable values.

    The same falling-back posture as the other preferences: a mistyped budget costs the
    budget, never the section.
    """
    try:
        stated = int(str(value))
    except (TypeError, ValueError):
        return 0
    return stated if stated > 0 else 0


def _writer_route(definition: SectionDefinition) -> str | None:
    """The cheaper route this definition row asks its writer to bill at, or ``None``."""
    stated = (definition.evidence_policy or {}).get("writer_role")
    return stated if isinstance(stated, str) and stated else None


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
    # Scaled to the request's depth: the definition states the standard budgets, and
    # quick/full move them in code rather than in a prompt (gap O5).
    policy = policy_of_definition(definition).scaled(request.analysis_mode)

    evidence = await gather_evidence(
        context.session,
        request=request,
        evidence_job_id=context.job_step.job_id,
        policy=policy,
        categories=_ALL_CATEGORIES,
    )

    # The row may name a cheaper configured route (gap O1). Honoured only when the
    # router actually configures it — the usual falling-back posture: a mistyped route
    # name costs the saving, never the section.
    requested = _writer_route(definition)
    if requested is not None and requested not in context.router.roles:
        _log.warning(
            "section_writer.unrouted_writer_role", section=section.section_key, route=requested
        )
        requested = None
    agent = SectionWriterAgent(route_role=requested)
    problems: list[str] = []
    draft: SectionDraft | None = None
    last_candidate: SectionDraft | None = None

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

        last_candidate = candidate
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

    salvage_notes: tuple[str, ...] = ()
    if draft is None and last_candidate is not None:
        salvage = _salvaged(last_candidate, contract=contract, evidence=evidence, policy=policy)
        if salvage is not None:
            # Recorded on the section, not just in a log: a reader of the run console
            # should see that the platform edited the draft, and which way.
            draft, salvage_notes = salvage.draft, salvage.notes
            _log.info(
                "section_writer.draft_salvaged",
                section=section.section_key,
                attempts=attempts,
                problems=problems,
                repairs=len(salvage_notes),
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
    # A section the platform edited is a degraded section, and the notes travel the same
    # channel every other degradation does — into the confidence and onto the row.
    shortfalls.extend(salvage_notes)

    section.content = draft.content
    section.status = SectionStatus.GENERATED
    section.confidence = confidence_of(draft.content, degraded=bool(shortfalls))
    section.low_confidence_reason = degradation_note(shortfalls)
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


@dataclass(frozen=True, slots=True)
class _Salvage:
    """A repaired draft and the edits that repaired it, for the record."""

    draft: SectionDraft
    notes: tuple[str, ...]


# What a salvaged section says about itself. Recorded on the row rather than only logged:
# a reader of the console should see that the platform edited the draft, and which way.
_NUMERAL_NOTE: Final = (
    "One or more sentences carrying figures no claim resolved were removed from this "
    "section rather than discarding the draft (ADR 0057)."
)
_LENGTH_NOTE: Final = (
    "This section ran past its word budget and was shortened by dropping trailing "
    "sentences rather than discarding the draft (ADR 0057). The analysis is the model's; "
    "the cut is the platform's."
)


def _salvaged(
    candidate: SectionDraft,
    *,
    contract: dict[str, Any],
    evidence: Evidence,
    policy: SectionPolicy,
) -> _Salvage | None:
    """The candidate narrowed until it conforms, if narrowing is the repair.

    The section-writer's version of the plan salvage (gap A42): code narrowing model
    output from the billed reply, never adding to it. Two repairs, applied in order and
    either sufficient on its own:

    * **Unsourced-numeral sentences removed.** Both sections the first live report lost
      died over a single flagged token each — a whole paid-for draft discarded for one
      clause the rule had a quarrel with.
    * **Length trimmed to the ceiling.** Nine of the next report's sixteen sections
      overran their budget, several for *nothing else*: complete, fully cited drafts
      thrown away for being long, which is the worst trade in the pipeline.

    Order matters and is deliberate: removing unsourced sentences also removes words, so
    the numeral repair runs first and the trim only takes what is still over.

    The salvage declines unless the narrowed draft passes **full** revalidation, so it can
    only ever turn a refused draft into a conforming one, and a draft failing for any
    other reason still fails (ADR 0057).
    """
    content = candidate.content
    notes: list[str] = []

    covered = [claim.statement for claim in candidate.claims if claim.kind == "numeric"]
    narrowed = without_unsourced_numeral_sentences(content, covered)
    if narrowed is not None:
        content = narrowed
        notes.append(_NUMERAL_NOTE)

    if policy.word_budget > 0:
        shortened = trimmed_to_word_count(content, word_ceiling(policy.word_budget))
        if shortened is not None:
            content = shortened
            notes.append(_LENGTH_NOTE)

    if not notes:
        return None

    repaired = candidate.model_copy(update={"content": content})
    if validate_draft(repaired, contract=contract, evidence=evidence, policy=policy):
        return None
    return _Salvage(draft=repaired, notes=tuple(notes))
