"""Re-reading a run's archived section replies under today's rules, at no spend.

The confirmation run (`docs/users/the-confirmation-run.md`, 2026-09-05) lost three sections
to two rules — a numeral read without its sign (ADR 0097, amended) and a numeric claim
refused for citing no excerpt (ADR 0109) — and cost £10.82 to show it. Both fixes were made
from the run's record: every reply the writer gave is archived byte for byte beside the call
that produced it (``agent_runs.response_payload_ref``), and the rules that refused it are
deterministic Python over the run's own rows. So whether a fix takes is a question the
record can answer. Read each archived reply back, hold it to the rules as they stand now,
and say what they make of it — no fetch, no model call, nothing billed. The same posture
as :mod:`aer.services.run_replay`, one level down: that module asks whether the run still
reproduces; this one asks what the run's replies would meet today.

**What is replayed is the reply, not the run.** The evidence pack a section is held to is
rebuilt from the run's rows under the section's standing policy, which is what the next
draft is dealt; a retry that ran under a cut word budget (gap A51a) is replayed under the
standing one. The block beside a platform-filled section is rebuilt the same way, and its
commentary check runs as it would at draft. Everything the rules read is therefore the
record as it stands, and a pass here is the pass the next live attempt would get from
:func:`aer.sections.evidence.validate_draft` — not a promise about what the model writes
next time.

**The agreement metric runs beside the rules.** The live run's seven reported sections
failed at ``validate``, not ``draft``: ``cited_figure_agreement`` reads the drafted claim
against the calculation it names, and a refusal there costs the run after the section was
paid for. It is measured here over the replayed claims, so a reply that drafts but would be
reported is shown as both.

**The salvage runs too.** A refusal is not a lost section: the draft step's last resort
narrows the last reply it could read — malformed claims dropped, unsourced-numeral sentences
removed, surplus gap remarks reduced to one, length trimmed — and keeps it when what remains
conforms (ADR 0057). The same pass runs here on every readable reply, so ``refused`` splits
into *repaired* and *lost*, and the sections are rolled up the way the attempt loop reads
them: the first reply that passes, else the last readable one if the salvage repairs it,
else lost. A readout that stopped at the refusal overstated the loss: the confirmation run's
replay showed eighteen refusals, and what the operator needed to know was how many of them
would have cost a section. The roll-up is what the record can say for nothing, not a
prediction of the next run — a retry was written against the refusal it was sent, not
today's.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import structlog
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.contract_schema import draft_model_for
from aer.agents.custom_section import CustomSectionAgent, CustomSectionDraft
from aer.agents.section_writer import SectionDraft, SectionWriterAgent
from aer.config import Settings
from aer.db.models import (
    AgentRun,
    Artefact,
    Calculation,
    Job,
    JobStep,
    PlanSkillPin,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
)
from aer.db.models.section_definition import SKILL
from aer.errors import AerError
from aer.eval.observations import CitedFigureObservation
from aer.eval.runtime import cited_figure_agreement
from aer.sections.deterministic import AUGMENTERS, SectionAugmenter, model_facing_contract
from aer.sections.evidence import Evidence, SectionPolicy, gather_evidence, validate_draft
from aer.sections.registry import sections_for_job
from aer.sections.writing import ALL_CATEGORIES, policy_of_definition, salvaged
from aer.skills.execution import policy_of_pin
from aer.skills.resolution import pinned_skills_for_job
from aer.storage.protocol import ArtefactStore

__all__ = [
    "DraftReplay",
    "ReplayedReply",
    "ReplayedSection",
    "ReplyVerdict",
    "SectionOutcome",
    "replay_drafts",
]

_log = structlog.get_logger("aer.services.draft_replay")

# The roles whose replies are section drafts. Anything else the run archived — the planner,
# the red team, the verdict — answers a different schema and is not a draft to replay.
WRITER_ROLES: Final[frozenset[str]] = frozenset({SectionWriterAgent.role, CustomSectionAgent.role})

# The line both writers open their user turn with: "Write the section 'Cash Flow Analysis'
# (cash_flow_analysis) for ..." from the built-in writer, "Draft the section ..." from a
# custom one. The key in brackets is the only thing here that names the section — an
# `agent_runs` row records the step it ran in, not the section it wrote — and it is read
# from the archived request rather than inferred from ordering, which a fan-out does not
# preserve. `!r` quotes the title with single quotes unless the title holds one.
_TURN_HEADER: Final[re.Pattern[str]] = re.compile(
    r"the section (?:'[^\n]*?'|\"[^\n]*?\") \((?P<key>[^()\s]+)\) for "
)

# The sentence both writers open a retry's instruction with. `agent_runs.created_at` is the
# database's transaction time, and a section's two attempts run in one transaction, so they
# share it: the retry is told apart by the refusal it carries, which only a retry is sent.
_RETRY_MARKER: Final = "Your previous draft was refused for these reasons; fix them:"

_FIELD_PROBLEMS_SHOWN: Final = 5

# The step whose replies decide whether a section exists. A revision's reply (ADR 0091) is
# read and shown like any other, but a refused revision leaves the approved draft standing
# (ADR 0098), so it never costs a section and never counts in the roll-up.
DRAFT_STEP: Final = "draft"


class ReplyVerdict(StrEnum):
    """What today's rules make of one archived reply."""

    PASSES = "passes"
    REPAIRED = "repaired"
    REFUSED = "refused"
    UNREADABLE = "unreadable"
    UNIDENTIFIED = "unidentified"
    UNARCHIVED = "unarchived"


@dataclass(frozen=True, slots=True)
class ReplayedReply:
    """One archived writer reply, read back under the rules as they stand.

    ``recorded_stop_reason`` is what the run wrote down at the time: ``schema_rejected``
    for a reply that could not be read then, the API's own stop reason otherwise. It is
    carried so the readout can say "refused then, passes now", which is the whole point of
    the exercise. ``disagreements`` are the agreement metric's, kept apart from
    ``problems`` because they fail a different step of the run.

    ``REPAIRED`` is a reply the rules refuse as written and the salvage pass narrows to a
    conforming draft — the draft step's last resort, run here on every readable reply so
    that a refusal it repairs is told apart from one that costs the section. ``problems``
    are then what it was refused for and ``repairs`` the edits that kept it, in the words
    the section row would record; ``claims`` counts what the kept draft carries.
    """

    section_key: str | None
    step_key: str
    ordinal: int
    of: int
    model: str
    output_tokens: int | None
    recorded_stop_reason: str | None
    verdict: ReplyVerdict
    problems: tuple[str, ...] = ()
    disagreements: tuple[str, ...] = ()
    claims: int = 0
    repairs: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """Drafts under today's rules as written, and every cited figure agrees."""
        return self.verdict is ReplyVerdict.PASSES and not self.disagreements

    @property
    def kept(self) -> bool:
        """Yields a section under today's rules: as written, or after the salvage's edits."""
        return self.verdict in {ReplyVerdict.PASSES, ReplyVerdict.REPAIRED}


class SectionOutcome(StrEnum):
    """What one section's draft-step replies come to under today's rules."""

    DRAFTS = "drafts"
    REPAIRED = "repaired"
    LOST = "lost"


@dataclass(frozen=True, slots=True)
class ReplayedSection:
    """One section's draft-step replies, read the way the attempt loop reads them.

    The loop keeps the first reply that passes; failing that, it salvages the last reply it
    could read; failing that, the section is lost. ``at_reply`` is the ordinal of the reply
    the outcome rests on, and ``problems`` and ``repairs`` are that reply's.
    """

    section_key: str
    outcome: SectionOutcome
    at_reply: int
    of: int
    problems: tuple[str, ...] = ()
    repairs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftReplay:
    """Every archived section reply of one run, replayed."""

    job_id: uuid.UUID
    section_key: str | None
    replies: tuple[ReplayedReply, ...]

    @property
    def clean(self) -> int:
        return sum(1 for reply in self.replies if reply.clean)

    def counted(self, verdict: ReplyVerdict) -> int:
        return sum(1 for reply in self.replies if reply.verdict is verdict)

    @property
    def reported(self) -> int:
        """Replies that draft but the agreement metric would report."""
        return sum(
            1
            for reply in self.replies
            if reply.verdict is ReplyVerdict.PASSES and reply.disagreements
        )

    @property
    def sections(self) -> tuple[ReplayedSection, ...]:
        """Every section with a draft-step reply, in the order the run first wrote to it."""
        by_section: dict[str, list[ReplayedReply]] = {}
        for reply in self.replies:
            if reply.section_key is not None and reply.step_key == DRAFT_STEP:
                by_section.setdefault(reply.section_key, []).append(reply)
        return tuple(_section_outcome(key, replies) for key, replies in by_section.items())

    def sections_counted(self, outcome: SectionOutcome) -> int:
        return sum(1 for section in self.sections if section.outcome is outcome)


def _section_outcome(section_key: str, replies: Sequence[ReplayedReply]) -> ReplayedSection:
    """The attempt loop's reading of one section's replies, from their verdicts alone.

    Mirrors :func:`aer.sections.writing.execute_builtin_section`: the first passing draft
    ends the loop, and only when none passes is the last candidate — the last reply that
    parsed, whatever came after it — handed to the salvage.
    """
    passing = next((r for r in replies if r.verdict is ReplyVerdict.PASSES), None)
    if passing is not None:
        return ReplayedSection(
            section_key, SectionOutcome.DRAFTS, at_reply=passing.ordinal, of=passing.of
        )
    readable = {ReplyVerdict.REPAIRED, ReplyVerdict.REFUSED}
    last = next((r for r in reversed(replies) if r.verdict in readable), replies[-1])
    outcome = (
        SectionOutcome.REPAIRED if last.verdict is ReplyVerdict.REPAIRED else SectionOutcome.LOST
    )
    return ReplayedSection(
        section_key,
        outcome,
        at_reply=last.ordinal,
        of=last.of,
        problems=last.problems,
        repairs=last.repairs,
    )


@dataclass(frozen=True, slots=True)
class _StandingRules:
    """What one section is held to now: built once per section, from the run's rows."""

    contract: dict[str, Any]
    policy: SectionPolicy
    evidence: Evidence
    narrowed: type[BaseModel]
    declared: type[CustomSectionDraft]
    augmenter: SectionAugmenter | None
    block: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Exchange:
    run: AgentRun
    step_key: str
    request: dict[str, Any] | None
    response: dict[str, Any] | None
    section_key: str | None
    retry: bool


async def replay_drafts(
    session: AsyncSession,
    store: ArtefactStore,
    settings: Settings,
    *,
    job_id: uuid.UUID,
    section_key: str | None = None,
) -> DraftReplay:
    """Read every archived section reply of a run back under today's rules.

    Raises:
        AerError: No such run, or the run has no section by the key asked for.
    """
    job = await session.get(Job, job_id)
    if job is None:
        message = f"No run {job_id}."
        raise AerError(message, context={"job_id": str(job_id)})
    request = await _request_of(session, job)
    if request is None:
        message = f"Run {job_id} has no research request to replay under."
        raise AerError(message, context={"job_id": str(job_id)})

    sections = {row.section_key: row for row in await sections_for_job(session, job.id)}
    if section_key is not None and section_key not in sections:
        message = f"Run {job_id} has no section {section_key!r}."
        raise AerError(message, context={"job_id": str(job_id), "section_key": section_key})

    exchanges = [
        exchange
        for exchange in await _writer_exchanges(session, store, job_id=job.id)
        if section_key is None or exchange.section_key == section_key
    ]
    evidence_job_id = await _evidence_job_of(session, job)
    calculations = {
        str(row.id): row
        for row in await session.scalars(
            select(Calculation).where(Calculation.job_id == evidence_job_id)
        )
    }
    pins = await pinned_skills_for_job(session, job=job)

    counts: dict[str | None, int] = {}
    for exchange in exchanges:
        counts[exchange.section_key] = counts.get(exchange.section_key, 0) + 1

    # A section's rules are built once and kept, as an `AerError` where they cannot be
    # rebuilt — a skill-origin section whose pin the plan no longer holds — so that one
    # such section costs its own replies a verdict and not the other twenty theirs.
    rules: dict[str, _StandingRules | AerError] = {}
    ordinals: dict[str | None, int] = {}
    replies: list[ReplayedReply] = []
    for exchange in exchanges:
        ordinals[exchange.section_key] = ordinals.get(exchange.section_key, 0) + 1
        section = sections.get(exchange.section_key) if exchange.section_key else None
        standing: _StandingRules | AerError | None = None
        if section is not None:
            if section.section_key not in rules:
                try:
                    rules[section.section_key] = await _standing_rules(
                        session,
                        settings,
                        request=request,
                        section=section,
                        pins=pins,
                        evidence_job_id=evidence_job_id,
                    )
                except AerError as unbuildable:
                    rules[section.section_key] = unbuildable
            standing = rules[section.section_key]
        replies.append(
            _replayed(
                exchange,
                ordinal=ordinals[exchange.section_key],
                of=counts[exchange.section_key],
                standing=standing,
                calculations=calculations,
            )
        )

    _log.info(
        "draft_replay.completed",
        job_id=str(job_id),
        section_key=section_key,
        replies=len(replies),
        clean=sum(1 for reply in replies if reply.clean),
    )
    return DraftReplay(job_id=job_id, section_key=section_key, replies=tuple(replies))


async def _request_of(session: AsyncSession, job: Job) -> ResearchRequest | None:
    """The mandate a run's replies were written for.

    A run's own work order carries it. A rehearsal's does not — a section rehearsal or a
    skill dry run gets a work order of its own with no mandate under it, and its plan's
    ``request_id`` names what it was a rehearsal *of* — so the plan is read second, which
    is what lets ``aer replay-draft <rehearsal job>`` read a rehearsal's replies back.
    """
    request = await session.get(ResearchRequest, job.work_order_id)
    if request is not None or job.plan_id is None:
        return request
    plan = await session.get(ResearchPlan, job.plan_id)
    return await session.get(ResearchRequest, plan.request_id) if plan is not None else None


async def _evidence_job_of(session: AsyncSession, job: Job) -> uuid.UUID:
    """Whose calculations and platform-filled blocks the replies were held to.

    A run's own. A rehearsal's plan records the run it borrowed its evidence from, and
    its replies must be held to that evidence again or every figure they cite reads as
    one the section "does not hold".
    """
    if job.plan_id is None:
        return job.id
    plan = await session.get(ResearchPlan, job.plan_id)
    recorded = (plan.plan or {}).get("evidence_job_id") if plan is not None else None
    try:
        return uuid.UUID(str(recorded)) if recorded else job.id
    except ValueError:
        return job.id


# -- Reading the archive ---------------------------------------------------------------------


async def _writer_exchanges(
    session: AsyncSession, store: ArtefactStore, *, job_id: uuid.UUID
) -> list[_Exchange]:
    """Every writer call the run archived, oldest first, with both payloads read back.

    Oldest first by the database's clock, and within one transaction's worth of calls — a
    section's first attempt and its retry — the retry second, read from the refusal only a
    retry carries. The id breaks what is left, which is nothing the record can order.
    """
    rows = await session.execute(
        select(AgentRun, JobStep.step_key)
        .join(JobStep, JobStep.id == AgentRun.job_step_id)
        .where(JobStep.job_id == job_id, AgentRun.agent_role.in_(WRITER_ROLES))
        .order_by(AgentRun.created_at, AgentRun.id)
    )
    exchanges: list[_Exchange] = []
    for run, step_key in rows:
        request = await _archived_json(session, store, run.request_payload_ref)
        response = await _archived_json(session, store, run.response_payload_ref)
        user_text = _user_text(request) if request is not None else ""
        found = _TURN_HEADER.search(user_text)
        exchanges.append(
            _Exchange(
                run=run,
                step_key=step_key,
                request=request,
                response=response,
                section_key=found.group("key") if found else None,
                retry=_RETRY_MARKER in user_text,
            )
        )
    exchanges.sort(key=lambda e: (e.run.created_at, e.retry, str(e.run.id)))
    return exchanges


async def _archived_json(
    session: AsyncSession, store: ArtefactStore, ref: uuid.UUID | None
) -> dict[str, Any] | None:
    """An archived payload as a mapping, or ``None`` for one that cannot be read back.

    ``None`` rather than a raise: one garbage-collected artefact is one reply nobody can
    account for (:mod:`aer.services.run_replay` fails the run for it), not a reason to
    tell the operator nothing about the other twenty.
    """
    if ref is None:
        return None
    artefact = await session.get(Artefact, ref)
    if artefact is None:
        return None
    try:
        payload = json.loads(await store.read(artefact.sha256))
    except (AerError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _user_text(request: dict[str, Any]) -> str:
    """Every user-turn string in an archived request, whichever provider archived it.

    The live provider stores the wire shape — ``content`` a string, or a list of text
    blocks when the turn carried a cache prefix — and the fake stores the turn's own
    fields. Both are read, because a run replayed in a test is archived by the fake.
    """
    parts: list[str] = []
    messages = request.get("messages")
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        prefix = message.get("cache_prefix")
        if isinstance(prefix, str):
            parts.append(prefix)
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return "\n".join(parts)


def _reply_text(response: dict[str, Any]) -> tuple[str | None, str | None]:
    """The JSON the reply carried and the stop reason it carried, either possibly absent.

    The live provider archives the SDK's dump of the response — text blocks under
    ``content``, thinking blocks beside them — and the fake archives the value it was
    scripted under ``parsed``. The text is what the rules read; a reply with none is not
    a draft, whatever else it holds.
    """
    stop_reason = response.get("stop_reason")
    stop = str(stop_reason) if stop_reason else None
    parsed = response.get("parsed")
    if isinstance(parsed, dict):
        return json.dumps(parsed), stop
    blocks = response.get("content")
    text = (
        "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if isinstance(blocks, list)
        else ""
    )
    return (text if text.strip() else None), stop


# -- Holding a reply to the rules -----------------------------------------------------------


async def _standing_rules(
    session: AsyncSession,
    settings: Settings,
    *,
    request: ResearchRequest,
    section: ReportSection,
    pins: Sequence[PlanSkillPin],
    evidence_job_id: uuid.UUID,
) -> _StandingRules:
    """What this section is held to now, built the way its draft step builds it.

    Mirrors :func:`aer.sections.writing.execute_builtin_section` and
    :func:`aer.skills.execution.execute_custom_section` line for line where they decide
    the contract, the policy and the evidence, because a replay held to anything else
    would answer a question nobody asked.
    """
    definition = section.definition
    if definition.origin == SKILL:
        pin = next((p for p in pins if p.skill_id == definition.skill_id), None)
        if pin is None:
            message = (
                f"Section {section.section_key!r} was written under a skill pin this run's "
                "plan no longer holds, so its standing policy cannot be rebuilt."
            )
            raise AerError(message, context={"section_key": section.section_key})
        contract: dict[str, Any] = definition.output_contract or {}
        policy = policy_of_pin(pin, settings=settings)
        categories = frozenset(pin.granted_tools or [])
        declared: type[CustomSectionDraft] = CustomSectionDraft
        augmenter = None
    else:
        contract = model_facing_contract(definition.output_contract or {})
        policy = policy_of_definition(definition).scaled(request.analysis_mode)
        categories = ALL_CATEGORIES
        declared = SectionDraft
        augmenter = AUGMENTERS.get(section.section_key)

    evidence = await gather_evidence(
        session,
        request=request,
        evidence_job_id=evidence_job_id,
        policy=policy,
        categories=categories,
    )
    block = (
        await augmenter.build(session, job_id=evidence_job_id, request=request)
        if augmenter is not None
        else {}
    )
    return _StandingRules(
        contract=contract,
        policy=policy,
        evidence=evidence,
        narrowed=draft_model_for(declared, contract, name=section.section_key),
        declared=declared,
        augmenter=augmenter,
        block=block,
    )


def _replayed(
    exchange: _Exchange,
    *,
    ordinal: int,
    of: int,
    standing: _StandingRules | AerError | None,
    calculations: dict[str, Calculation],
) -> ReplayedReply:
    run = exchange.run

    def reply(
        verdict: ReplyVerdict,
        problems: Sequence[str] = (),
        disagreements: Sequence[str] = (),
        claims: int = 0,
        repairs: Sequence[str] = (),
    ) -> ReplayedReply:
        return ReplayedReply(
            section_key=exchange.section_key,
            step_key=exchange.step_key,
            ordinal=ordinal,
            of=of,
            model=run.model,
            output_tokens=run.output_tokens,
            recorded_stop_reason=run.stop_reason,
            verdict=verdict,
            problems=tuple(problems),
            disagreements=tuple(disagreements),
            claims=claims,
            repairs=tuple(repairs),
        )

    if exchange.request is None or exchange.response is None:
        missing = "request" if exchange.request is None else "response"
        return reply(
            ReplyVerdict.UNARCHIVED, [f"The archived {missing} payload cannot be read back."]
        )
    if standing is None:
        return reply(
            ReplyVerdict.UNIDENTIFIED, ["The archived request names no section this run holds."]
        )
    if isinstance(standing, AerError):
        return reply(ReplyVerdict.UNIDENTIFIED, [str(standing)])

    text, stop_reason = _reply_text(exchange.response)
    unreadable = _unreadable(text, stop_reason)
    if unreadable is not None:
        return reply(ReplyVerdict.UNREADABLE, [unreadable])
    assert text is not None

    try:
        narrowed = standing.narrowed.model_validate_json(text)
    except PydanticValidationError as rejected:
        return reply(ReplyVerdict.UNREADABLE, _field_problems(rejected))
    # Back to the role's declared envelope, as `Agent._as_declared` brings a live reply:
    # the validators below are written against it, and a narrowed instance's content is a
    # nested model where they expect a mapping.
    draft = standing.declared.model_validate(
        narrowed.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    verdict, problems, kept, repairs = _held_to_the_rules(draft, standing)
    return reply(
        verdict,
        problems,
        _disagreements(kept, section_key=str(exchange.section_key), calculations=calculations),
        claims=len(kept.claims),
        repairs=repairs,
    )


def _held_to_the_rules(
    draft: CustomSectionDraft, standing: _StandingRules
) -> tuple[ReplyVerdict, list[str], CustomSectionDraft, tuple[str, ...]]:
    """The verdict on a parsed draft, with the draft that verdict keeps and how.

    Refused as written, the draft is handed to the salvage — the draft step's last resort,
    run on every readable reply rather than only the last attempt's, because what the
    readout owes the operator is whether *this* refusal would have cost the section or an
    edit. Built-in sections only: that is the one path that salvages
    (`execute_custom_section` does not), and the replay answers for the path.
    """
    problems = validate_draft(
        draft, contract=standing.contract, evidence=standing.evidence, policy=standing.policy
    )
    if standing.augmenter is not None:
        problems.extend(standing.augmenter.check(draft.content, standing.block))
    if not problems:
        return ReplyVerdict.PASSES, problems, draft, ()
    repair = (
        salvaged(
            draft,
            contract=standing.contract,
            evidence=standing.evidence,
            policy=standing.policy,
            augmenter=standing.augmenter,
            block=standing.block,
        )
        if isinstance(draft, SectionDraft)
        else None
    )
    if repair is None:
        return ReplyVerdict.REFUSED, problems, draft, ()
    return ReplyVerdict.REPAIRED, problems, repair.draft, repair.notes


def _unreadable(text: str | None, stop_reason: str | None) -> str | None:
    """Why there is no draft to read, or ``None`` when there is one."""
    if stop_reason == "max_tokens":
        return "The reply ran out of room at the token ceiling; there is no draft to read."
    if stop_reason == "refusal":
        return "The model declined to answer; there is no draft to read."
    if text is None:
        return "The reply carried no text; there is no draft to read."
    return None


def _field_problems(rejected: PydanticValidationError) -> list[str]:
    """The reply's schema failures, without its values — see the provider's own summary."""
    if any(error["type"] == "json_invalid" for error in rejected.errors()):
        return ["The reply's JSON stops mid-object: it was cut off before it was complete."]
    return [
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error.get('msg', '')}"
        for error in rejected.errors()[:_FIELD_PROBLEMS_SHOWN]
    ]


def _disagreements(
    draft: CustomSectionDraft, *, section_key: str, calculations: dict[str, Calculation]
) -> tuple[str, ...]:
    """What `cited_figure_agreement` would report over this draft's claims.

    The same observation the evaluation step builds from the recorded claims
    (:func:`aer.services.evaluations._cited_figures`), taken here from the reply before
    anything is recorded. A claim naming a calculation the run does not hold is already a
    refusal above and contributes nothing here.
    """
    observations = [
        CitedFigureObservation(
            name=f"{section_key}/{calculation.name}#{calculation.sequence}",
            text=claim.statement,
            calculation=calculation.name,
            value=calculation.output_value,
            unit=calculation.output_unit,
        )
        for claim in draft.claims
        if claim.kind == "numeric"
        and claim.calculation_id is not None
        and (calculation := calculations.get(claim.calculation_id)) is not None
    ]
    if not observations:
        return ()
    return cited_figure_agreement(observations).failures
