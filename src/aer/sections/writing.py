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

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

import structlog

from aer.agents.base import AgentContext, TokenCapExceededError, schema_problems
from aer.agents.section_writer import SectionDraft, SectionWriterAgent, SectionWriterInput
from aer.core.enums import SourceTier
from aer.core.section_output import (
    CLAIM_EDIT_NOTE,
    GAP_EDIT_NOTE,
    LENGTH_EDIT_NOTE,
    NUMERAL_EDIT_NOTE,
    confidence_ceiling,
    trimmed_to_word_count,
    without_surplus_gap_sentences,
    without_unsourced_numeral_sentences,
)
from aer.core.skill_guidance import OperatorGuidance
from aer.db.models import ReportSection, ResearchRequest, SectionDefinition, SectionStatus
from aer.errors import ValidationError
from aer.sections.deterministic import AUGMENTERS, SectionAugmenter, model_facing_contract
from aer.sections.evidence import (
    MAX_GENERATION_ATTEMPTS,
    Evidence,
    EvidenceDealt,
    SectionExecution,
    SectionPolicy,
    classify_refusals,
    confidence_of,
    content_source_ids,
    covered_figures,
    degradation_note,
    gather_evidence,
    policy_shortfalls,
    record_draft_claims,
    validate_draft,
    word_ceiling,
)
from aer.services.subject import subject_name

__all__ = ["ALL_CATEGORIES", "execute_builtin_section", "policy_of_definition"]

_log = structlog.get_logger("aer.sections.writing")

# Every built-in section is assembled the full pack; its budget, not a grant, is what
# bounds it. The writer role itself holds no tools (ADR 0042) — these are evidence
# categories code assembles, not capabilities the model can exercise.
ALL_CATEGORIES = frozenset({"search_facts", "search_sources"})

# The output ceiling a retry runs at after the first attempt was truncated at the role's
# registered one (polish P6). Double the writer's 16,384, because `max_tokens` bounds
# thinking and visible output together and the truncated attempt has already shown the
# pair does not fit — and no higher, because 32,768 is the smallest output limit any
# routed model imposes, and a retry the API refuses outright would turn a truncation
# into a failed call. Applied per instance, for the retry alone: the standing ceiling
# stays where it genuinely binds, which is what keeps it a ceiling.
TRUNCATION_RETRY_CEILING: Final = 32_768


def _routed_writer(
    definition: SectionDefinition, *, section: ReportSection, router: Any
) -> SectionWriterAgent:
    """The writer, billed at the row's cheaper configured route where one exists (gap O1).

    Honoured only when the router actually configures it — the usual falling-back
    posture: a mistyped route name costs the saving, never the section.
    """
    requested = _writer_route(definition)
    if requested is not None and requested not in router.roles:
        _log.warning(
            "section_writer.unrouted_writer_role", section=section.section_key, route=requested
        )
        requested = None
    return SectionWriterAgent(route_role=requested)


def _counted(causes: dict[str, int], problems: list[str]) -> dict[str, int]:
    """Fold one attempt's refusals into the section's running cause counter (P6)."""
    for cause, count in classify_refusals(problems).items():
        causes[cause] = causes.get(cause, 0) + count
    return causes


def _refused_reply(
    unparsable: ValidationError, *, agent: SectionWriterAgent, causes: dict[str, int]
) -> list[str]:
    """An unusable reply turned into what the retry needs, with its causes counted.

    The field-level detail, not the count. A retry told only that "22 field(s) broke a
    constraint" has nothing to act on and makes the same mistake again, which is how
    three sections of one live report died at two attempts each.

    A reply that stopped at the role's output ceiling additionally raises the ceiling for
    the retry: an identical retry is a known failure at full price — a live run paid for
    the same truncation twice (polish P6). The first attempt *is* the measurement that
    the ceiling bound; the retry runs with the headroom that measurement asks for, and
    the standing ceiling stays where it binds for everything else.
    """
    problems = schema_problems(unparsable)
    _counted(causes, problems)
    if unparsable.context.get("stop_reason") == "max_tokens":
        agent.output_ceiling_tokens = TRUNCATION_RETRY_CEILING
    return problems


def _after_refused_reply(
    unparsable: ValidationError,
    *,
    agent: SectionWriterAgent,
    causes: dict[str, int],
    policy: SectionPolicy,
) -> tuple[SectionPolicy, list[str]]:
    """What an unusable reply leaves the retry with: its problems, and its policy.

    An ordinary refusal keeps the policy; a truncation cuts the word budget (gap A51a)
    on top of the ceiling `_refused_reply` raises, and the appended note states the cut
    so the retry writes to it rather than merely being told to be brief.
    """
    problems = _refused_reply(unparsable, agent=agent, causes=causes)
    if unparsable.context.get("stop_reason") == "max_tokens":
        policy, cut_note = _cut_for_retry(policy)
        if cut_note:
            problems.append(cut_note)
    return policy, problems


def _cut_for_retry(policy: SectionPolicy) -> tuple[SectionPolicy, str]:
    """The policy a truncated attempt retries under: half the word budget (gap A51a).

    Raising the output ceiling (polish P6) gives the retry room; this gives it a smaller
    ask. The live run's Balance Sheet section hit the ceiling on both attempts, because
    the retry said "say it in fewer words" while demanding the same content — the word
    budget is what states the demand, so the budget is what moves, and the validator
    enforces the cut one. An unbounded section keeps relying on the raised ceiling alone.
    """
    if policy.word_budget <= 0:
        return policy, ""
    cut = max(1, policy.word_budget // 2)
    note = (
        "Your reply ran out of room before it was complete. The word budget for this "
        f"retry is {cut} words — half the original — so the whole section fits: cover "
        "the essentials and leave the rest out."
    )
    return replace(policy, word_budget=cut), note


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


def _with_salvage(
    draft: SectionDraft | None,
    last_candidate: SectionDraft | None,
    *,
    section: ReportSection,
    contract: dict[str, Any],
    evidence: Evidence,
    policy: SectionPolicy,
    augmenter: SectionAugmenter | None,
    block: dict[str, Any] | None,
    attempts: int,
    problems: list[str],
) -> tuple[SectionDraft | None, tuple[str, ...]]:
    """The last refused draft after the salvage pass, and what the pass changed.

    Returns the draft untouched when there was one, and ``(None, ())`` when there is
    nothing to repair or the salvage declined — so the caller's next line is the same
    "no draft means failed" it always was.
    """
    if draft is not None or last_candidate is None:
        return draft, ()

    salvage = _salvaged(
        last_candidate,
        contract=contract,
        evidence=evidence,
        policy=policy,
        augmenter=augmenter,
        block=block,
    )
    if salvage is None:
        return None, ()

    # Recorded on the section, not just in a log: a reader of the run console should see
    # that the platform edited the draft, and which way.
    _log.info(
        "section_writer.draft_salvaged",
        section=section.section_key,
        attempts=attempts,
        problems=problems,
        repairs=len(salvage.notes),
    )
    return salvage.draft, salvage.notes


async def execute_builtin_section(
    context: AgentContext,
    *,
    section: ReportSection,
    request: ResearchRequest,
    focus: str = "",
    challenges: Sequence[str] = (),
    guidance: Sequence[OperatorGuidance] = (),
) -> SectionExecution:
    """Write one built-in section to a recorded outcome. Never raises for a bad draft.

    Mirrors :func:`aer.skills.execution.execute_custom_section` deliberately: the section
    row is mutated in place, claims and citations are recorded through the same services,
    and every failure mode is a visible state on the row rather than an absent section.

    ``challenges`` is the revise pass's input (ADR 0091): material red-team challenges
    against this section's previous draft, composed into the instruction block as
    direction to address. Everything else — the contract, the evidence policy, the claim
    rules, validation — is exactly the first draft's, which is what stops a revision
    being a second way to publish an unsupported sentence.

    ``guidance`` is the run's pinned prompt-kind skills (ADR 0108), passed whole: the
    writer composes only the kinds its role reads, last in the user turn.
    """
    definition = section.definition
    # The model is bound by the contract minus any platform-filled fields (ADR 0063):
    # those are rendered from the run's records by the section's augmenter and merged
    # into the content after the draft passes, at the positions the stored contract
    # declares. The model cannot write them — its schema forbids unknown fields.
    contract = model_facing_contract(definition.output_contract or {})
    augmenter, block, standalone = await _augmentation(context, section=section, request=request)
    if standalone:
        # The augmenter answered before the model was asked (gap A51c): the rendered
        # record is the section's whole truthful content, so the platform stores it and
        # spends nothing.
        return await _filled_from_record(context, section=section, block=block, reason=standalone)
    # Scaled to the request's depth: the definition states the standard budgets, and
    # quick/full move them in code rather than in a prompt (gap O5).
    policy = policy_of_definition(definition).scaled(request.analysis_mode)
    # Stated once, from the policy as declared: a truncation retry cuts `policy`'s word
    # budget (gap A51a), and the cached evidence-policy block must stay byte-identical —
    # the cut budget travels in the user message, which changes every retry anyway.
    stated_policy = policy.as_prompt_payload()

    evidence = await gather_evidence(
        context.session,
        request=request,
        evidence_job_id=context.job_step.job_id,
        policy=policy,
        categories=ALL_CATEGORIES,
    )

    agent = _routed_writer(definition, section=section, router=context.router)
    problems: list[str] = []
    draft: SectionDraft | None = None
    last_candidate: SectionDraft | None = None
    # Every attempt's refusals, counted by cause (polish P6). Accumulated across the
    # retries so the run record says what each section struggled with, whether or not a
    # later attempt recovered.
    causes: dict[str, int] = {}

    # The filer's own name, not the one typed into the form (gap A67). Resolved once
    # rather than per attempt: it cannot change between retries, and the stable prompt
    # context has to stay byte-identical across them.
    subject = await subject_name(context.session, request)

    attempts = 0
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        attempts = attempt
        payload = SectionWriterInput(
            section_key=section.section_key,
            title=definition.title,
            company_name=subject,
            ticker=request.ticker,
            as_of_date=request.work_order.as_of_date.isoformat(),
            point_in_time=request.work_order.point_in_time,
            output_contract=contract,
            evidence_policy=stated_policy,
            internal_evidence=evidence.internal,
            untrusted_evidence=evidence.untrusted,
            focus=focus,
            challenges=list(challenges),
            guidance=list(guidance),
            problems=problems,
            evidence_truncated=evidence.truncated,
            # The budget with its consequence, from the numbers the validator reads
            # (gap A50) — and the cut budget on a truncation retry (gap A51a).
            word_budget=policy.word_budget,
            word_ceiling=word_ceiling(policy.word_budget) if policy.word_budget > 0 else 0,
            # The other half of the augmenter's check: what the block beside this section
            # carries, so the writer can keep to it rather than be refused for guessing.
            platform_note=(
                augmenter.note(block) if augmenter is not None and augmenter.note else ""
            ),
        )
        try:
            candidate = await agent.run(context, payload)
        except TokenCapExceededError as refused:
            # Deterministic in the composition: a retry would compose the same call and
            # be refused the same way, so the section fails now, visibly and for free.
            failed_problems = [str(refused)]
            return _failed(
                section,
                attempts=attempt,
                problems=failed_problems,
                truncated=evidence.truncated,
                dealt=evidence.dealt,
                block=block,
                causes=_counted(causes, failed_problems),
            )
        except ValidationError as unparsable:
            policy, problems = _after_refused_reply(
                unparsable, agent=agent, causes=causes, policy=policy
            )
            continue

        last_candidate = candidate
        problems = validate_draft(candidate, contract=contract, evidence=evidence, policy=policy)
        if augmenter is not None:
            # The deterministic edge of what the model may say beside the rendered block
            # (ADR 0063): a commentary describing method inputs the record does not hold
            # is a refusal like any other, and the problem text tells the retry what to
            # remove rather than what to invent.
            problems.extend(augmenter.check(candidate.content, block))
        if not problems:
            draft = candidate
            break
        _counted(causes, problems)
        _log.info(
            "section_writer.draft_refused",
            section=section.section_key,
            attempt=attempt,
            problems=problems,
        )

    draft, salvage_notes = _with_salvage(
        draft,
        last_candidate,
        section=section,
        contract=contract,
        evidence=evidence,
        policy=policy,
        augmenter=augmenter,
        block=block,
        attempts=attempts,
        problems=problems,
    )

    if draft is None:
        return _failed(
            section,
            attempts=attempts,
            problems=problems,
            truncated=evidence.truncated,
            dealt=evidence.dealt,
            block=block,
            causes=causes,
        )

    recorded, cited_source_ids = await record_draft_claims(
        context.session, section=section, draft=draft, evidence=evidence
    )
    # Content-cited sources count towards the floor exactly as the coverage metric
    # counts them: a figure row citing through content is evidence, not decoration.
    cited_source_ids |= content_source_ids(draft.content)
    shortfalls = policy_shortfalls(cited_source_ids, evidence=evidence, policy=policy)
    # A section the platform edited is a degraded section — the confidence cap applies as
    # it does to any other degradation — but an edit is not an evidence shortfall, and the
    # two must never share the "Insufficient evidence" label (gap R2). The edit sentences
    # ride on the row *after* the evidence banner, unlabelled, in the shared vocabulary the
    # renderer uses to keep them out of the inline banner and in the appendix (gap R1).
    notes = [note for note in (degradation_note(shortfalls), *salvage_notes) if note]

    # The platform-filled fields join the model's, at the positions the stored contract
    # declares (ADR 0063). The merge is one-way: the model's schema cannot carry these
    # keys, so nothing of the draft is overwritten.
    section.content = {**draft.content, **block} if block else draft.content
    section.status = SectionStatus.GENERATED
    # Three degradations, three ceilings (ADR 0099). A section shortened to fit is not a
    # section whose evidence fell short, and the number a reader sees has to say which.
    section.confidence = confidence_of(
        draft.content,
        ceiling=confidence_ceiling(insufficient_evidence=bool(shortfalls), edits=salvage_notes),
    )
    section.low_confidence_reason = " ".join(notes) or None
    await context.session.flush()

    _log.info(
        "section_writer.generated",
        section=section.section_key,
        attempts=attempts,
        claims=recorded,
        insufficient_evidence=bool(shortfalls),
        evidence_truncated=evidence.truncated,
        evidence_dealt=evidence.dealt.as_dict(),
    )
    return SectionExecution(
        section=section,
        status=SectionStatus.GENERATED,
        attempts=attempts,
        claims_recorded=recorded,
        insufficient_evidence=bool(shortfalls),
        evidence_truncated=evidence.truncated,
        dealt=evidence.dealt,
        # The full record for the step output and the console: the evidence shortfalls
        # and the edits, distinguishable because the edit sentences are the shared
        # constants rather than free prose.
        problems=[*shortfalls, *salvage_notes],
        refusal_causes=causes,
    )


async def _filled_from_record(
    context: AgentContext,
    *,
    section: ReportSection,
    block: dict[str, Any],
    reason: str,
) -> SectionExecution:
    """The rendered record stored as the whole section, with no writer call (gap A51c).

    Generated, not failed: the block is true, complete for the state the run is in, and
    rendered from rows a reader can audit. The reason rides on the row the way every
    other degradation note does, so the console and the report say why there is no
    commentary instead of leaving a reader to infer it from an absence.
    """
    section.content = block
    section.status = SectionStatus.GENERATED
    section.confidence = None
    section.low_confidence_reason = reason
    await context.session.flush()
    _log.info(
        "section_writer.filled_from_record",
        section=section.section_key,
        reason=reason,
    )
    return SectionExecution(
        section=section,
        status=SectionStatus.GENERATED,
        attempts=0,
    )


def _failed(
    section: ReportSection,
    *,
    attempts: int,
    problems: list[str],
    truncated: bool = False,
    dealt: EvidenceDealt | None = None,
    block: dict[str, Any] | None = None,
    causes: dict[str, int] | None = None,
) -> SectionExecution:
    """Mark the section failed with its reasons on the row. The run continues.

    A section with platform-filled fields keeps them even when the model's part failed:
    the rendered record is true whatever the commentary did, and a reader is better served
    by the method tables under a failure banner than by a blank section (ADR 0063).

    **The failure says what the section was dealt** (gap A63). It used to record the
    problems alone, so the sections whose evidence supply most needed explaining were the
    only ones whose record omitted it — five sections of a live run died on having nothing
    citable, and answering "were they starved?" meant reading a worker log for a line
    that is only written when a section *succeeds*.
    """
    section.status = SectionStatus.FAILED
    section.content = block or None
    section.confidence = None
    section.low_confidence_reason = " ".join(problems)[:2000] or None
    _log.warning(
        "section_writer.failed",
        section=section.section_key,
        attempts=attempts,
        problems=problems,
        evidence_truncated=truncated,
        evidence_dealt=dealt.as_dict() if dealt is not None else None,
    )
    return SectionExecution(
        section=section,
        status=SectionStatus.FAILED,
        attempts=attempts,
        evidence_truncated=truncated,
        dealt=dealt,
        problems=problems,
        refusal_causes=causes if causes is not None else classify_refusals(problems),
    )


@dataclass(frozen=True, slots=True)
class _Salvage:
    """A repaired draft and the edits that repaired it, for the record."""

    draft: SectionDraft
    notes: tuple[str, ...]


# What a salvaged section says about itself — the shared reader-register sentences from
# `aer.core.section_output` (gaps R1/R2). Recorded on the row rather than only logged, so
# the console and the report's appendix both see that the draft was edited, and which way;
# the mechanism (ADR 0057's trim-not-discard trade) stays in the ADR and the structured
# log, never in a rendered document.
_NUMERAL_NOTE: Final = NUMERAL_EDIT_NOTE
_CLAIM_NOTE: Final = CLAIM_EDIT_NOTE
_GAP_NOTE: Final = GAP_EDIT_NOTE
_LENGTH_NOTE: Final = LENGTH_EDIT_NOTE


async def _augmentation(
    context: AgentContext,
    *,
    section: ReportSection,
    request: ResearchRequest,
) -> tuple[SectionAugmenter | None, dict[str, Any], str]:
    """This section's platform-filled fields, rendered before the model is called.

    Built once, ahead of the attempt loop: the block depends only on the run's records,
    and every retry validates the model's commentary against the same rendered record.

    The third element is the augmenter's standalone reason (gap A51c) — why the block is
    the section's *whole* truthful content and no writer call should be made — or an
    empty string for the ordinary path. The live run reached the valuation section with
    no valuation, paid for two writer attempts, and had both refused for describing
    method inputs no calculation produced; the guard was right and the calls were
    pointless.
    """
    augmenter = AUGMENTERS.get(section.section_key)
    if augmenter is None:
        return None, {}, ""
    block = await augmenter.build(context.session, job_id=context.job_step.job_id, request=request)
    standalone = augmenter.standalone(block) if augmenter.standalone is not None else ""
    return augmenter, block, standalone


def _salvaged(
    candidate: SectionDraft,
    *,
    contract: dict[str, Any],
    evidence: Evidence,
    policy: SectionPolicy,
    augmenter: SectionAugmenter | None = None,
    block: dict[str, Any] | None = None,
) -> _Salvage | None:
    """The candidate narrowed until it conforms, if narrowing is the repair.

    The section-writer's version of the plan salvage (gap A42): code narrowing model
    output from the billed reply, never adding to it. Two repairs, applied in order and
    either sufficient on its own:

    * **Malformed claims dropped.** A claim that does not stand on what its kind requires
      — a numeric one naming no figure, or naming one and citing nothing — is set aside.
      Four of the eight sections the MSFT run lost died on this (roadmap §2.1), each
      taking a dozen sound claims and a finished draft with it.
    * **Unsourced-numeral sentences removed.** Both sections the first live report lost
      died over a single flagged token each — a whole paid-for draft discarded for one
      clause the rule had a quarrel with.
    * **Repeated remarks about missing evidence reduced to one.** The gap budget is right
      — a live report spent a third of its prose describing absent disclosure — but
      refusing the draft for it threw away the other two thirds, which were about the
      company and fully cited. The first remark stays, which is what the rule asks for.
    * **Length trimmed to the ceiling.** Nine of the next report's sixteen sections
      overran their budget, several for *nothing else*: complete, fully cited drafts
      thrown away for being long, which is the worst trade in the pipeline.

    Order matters and is deliberate. Dropping a claim removes the cover its statement and
    its named figure gave a numeral, so the claim repair runs before the numeral one and
    the sentences that rested on a dropped claim go with it — the section keeps nothing it
    can no longer support. The gap repair runs after the numeral one, which may already
    have removed a gap sentence that carried an unsourced figure and so cost nothing.
    Removing sentences also removes words, so the trim runs last and takes only what is
    still over.

    What counts as covered comes from :func:`aer.sections.evidence.covered_figures`, the
    same call the validator makes: the salvage that removes a sentence and the rule that
    refuses it must agree exactly about which numerals are accounted for, or the salvage
    hands back a draft the revalidation below rejects for the sentence it just kept.

    The salvage declines unless the narrowed draft passes **full** revalidation, so it can
    only ever turn a refused draft into a conforming one, and a draft failing for any
    other reason still fails (ADR 0057).
    """
    content = candidate.content
    notes: list[str] = []

    claims = [claim for claim in candidate.claims if claim.malformed_reason is None]
    if len(claims) != len(candidate.claims):
        candidate = candidate.model_copy(update={"claims": claims})
        notes.append(_CLAIM_NOTE)

    covered, figures = covered_figures(candidate.claims, evidence=evidence)
    narrowed = without_unsourced_numeral_sentences(content, covered, figures)
    if narrowed is not None:
        content = narrowed
        notes.append(_NUMERAL_NOTE)

    reduced = without_surplus_gap_sentences(content)
    if reduced is not None:
        content = reduced
        notes.append(_GAP_NOTE)

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
    if augmenter is not None and augmenter.check(repaired.content, block or {}):
        # Full revalidation includes the augmenter's edge (ADR 0063): a trim that happened
        # to keep an offending method claim must not smuggle it through.
        return None
    return _Salvage(draft=repaired, notes=tuple(notes))
