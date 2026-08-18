"""The research workers: per-topic investigation under one contract and hard bounds.

`docs/PLAN.md` §2.5's parallel workers — company, industry, macro, recent developments,
technical context — one agent class, parameterised by topic, sharing a single typed
contract, the ``analysis`` role's allowlist and its token caps.

**The model never calls a tool. It asks, in a schema; code decides.** Each turn the worker
returns either tool *requests* or its final report — never both, never neither. Every
request is authorised against the role's registry allowlist before anything executes, and
executed deterministically by :mod:`aer.services.research`. This is the request/execute
protocol ADR 0036 records: there is no tool-use surface in the provider at all, so an
instruction smuggled into a fetched document has nothing to invoke — the strongest form of
"assert at the registry, not the prompt" available, because the assertion is that the
capability does not exist.

**The bounds are code.** Twelve executed tool calls per worker (§2.5), counted here; a
thirteenth request is refused with the budget named, and refusals of unlisted tools cost
nothing — a poisoned document must not be able to burn a worker's budget by asking for
capabilities it will never get. Rounds are bounded separately, so a worker that never
produces a report fails visibly instead of orbiting.

**Findings reference evidence; they do not contain numbers.** A finding cites source
documents and fact ids, and the validator refuses one that cites nothing — a hunch wearing
a label. Anything numeric belongs to the deterministic layer as a stored fact or a
recorded calculation; the worker's job is to say *which* evidence matters and what should
be investigated next, never to assert a figure of its own.

**A rejected reply is a conversation, not a death.** Both ways a report can be turned away
— the validator refusing it on the platform's tables, or the contract itself refusing to
read it — come back to the model as problems to fix on the next turn. The second used to
propagate out and kill the node, which is a strange way to treat the more trivially
fixable of the two: a coverage note forty characters too long is one sentence of feedback
away from being a good report. See :func:`investigate` and :data:`_LIMITS`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Final, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from aer.agents.base import Agent, AgentContext, ToolNotPermittedError, schema_problems
from aer.agents.untrusted import UntrustedSource
from aer.errors import AerError, ValidationError

__all__ = [
    "MAX_ROUNDS",
    "MAX_TOOL_CALLS",
    "MAX_UNREADABLE_REPLIES",
    "ExecutedTool",
    "Investigation",
    "ResearchTopic",
    "ResearchWorker",
    "ToolRequest",
    "WorkerExhaustedError",
    "WorkerFinding",
    "WorkerInput",
    "WorkerLead",
    "WorkerReport",
    "WorkerTurn",
    "degraded_report",
    "investigate",
]

_log = structlog.get_logger("aer.agents.worker")

# §2.5: max 12 tool calls each. Executed calls, counted in code; the thirteenth is refused
# with the budget named.
MAX_TOOL_CALLS: Final = 12

# Turns of the request/execute loop. A separate bound from the tool budget: a worker that
# spends nothing and produces nothing must still terminate, visibly.
MAX_ROUNDS: Final = 5

# Replies the schema could not read, before the worker gives up. Deliberately much smaller
# than MAX_ROUNDS, because it is the bound that actually limits the retrying: an unreadable
# reply is retried within the same round rather than consuming one (see :func:`investigate`),
# so this is the whole of the allowance. Naming the field the model overran fixes it on the
# next attempt or it does not get fixed at all — a third identical failure would be a third
# identical bill.
MAX_UNREADABLE_REPLIES: Final = 2


class WorkerExhaustedError(AerError):
    """A worker reached its bounds without producing a report.

    A deliberate failure rather than a shrug: it says which bound was hit and carries the
    audit trail of what the worker did do. What a caller makes of it is the caller's
    policy — the vertical slice records the dead topic as a visible degradation
    (:func:`degraded_report`) rather than abandoning the four sibling topics and every
    step downstream of them, which is what re-raising through the engine used to cost.
    """

    code = "worker_exhausted"


class ResearchTopic(StrEnum):
    """The five investigations §2.5 fans out."""

    COMPANY = "company"
    INDUSTRY = "industry"
    MACRO = "macro"
    RECENT_DEVELOPMENTS = "recent_developments"
    TECHNICAL_CONTEXT = "technical_context"


_TOPIC_BRIEFS: Final[dict[ResearchTopic, str]] = {
    ResearchTopic.COMPANY: (
        "The company itself: business model, segments, management, disclosed risks, and "
        "what the filings actually say against what is commonly assumed."
    ),
    ResearchTopic.INDUSTRY: (
        "The industry around it: structure, competition, pricing power, and where this "
        "company sits relative to the peers the evidence names."
    ),
    ResearchTopic.MACRO: (
        "The macro backdrop that bears on this company specifically: rates, currency, "
        "demand drivers — only where the evidence connects them to this business."
    ),
    ResearchTopic.RECENT_DEVELOPMENTS: (
        "What has changed recently: filings, announcements and events inside the run's "
        "evidence window, and what they alter about the picture."
    ),
    ResearchTopic.TECHNICAL_CONTEXT: (
        "Market context for the listing: liquidity, listing venue particulars, index "
        "membership — context for the valuation, never a trading signal."
    ),
}


class ToolRequest(BaseModel):
    """One thing the worker asks code to do."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=500)
    why: str = Field(min_length=1, max_length=300)


class WorkerLead(BaseModel):
    """A question worth pursuing that this worker could not settle from the evidence."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=400)
    why_it_matters: str = Field(min_length=1, max_length=400)


class WorkerFinding(BaseModel):
    """One structured finding, standing on named evidence."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=600)
    kind: Literal["factual", "forward_looking", "risk", "context"]
    source_document_ids: list[str] = Field(default_factory=list, max_length=8)
    fact_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _stands_on_evidence(self) -> WorkerFinding:
        if not self.source_document_ids and not self.fact_ids:
            message = (
                "A finding must cite at least one source document or fact id. A finding "
                "without evidence is a hunch wearing a label."
            )
            raise ValueError(message)
        return self


class WorkerReport(BaseModel):
    """What an investigation came to: findings on evidence, and leads for later."""

    model_config = ConfigDict(extra="forbid")

    findings: list[WorkerFinding] = Field(default_factory=list, max_length=12)
    leads: list[WorkerLead] = Field(default_factory=list, max_length=8)
    coverage_note: str = Field(min_length=1, max_length=600)


class WorkerTurn(BaseModel):
    """One turn: requests for code to execute, or the final report. Never both, never neither."""

    model_config = ConfigDict(extra="forbid")

    requests: list[ToolRequest] = Field(default_factory=list, max_length=4)
    report: WorkerReport | None = None

    @model_validator(mode="after")
    def _exactly_one_of_the_two(self) -> WorkerTurn:
        if self.requests and self.report is not None:
            message = "A turn either requests tools or reports; doing both hides which one counted."
            raise ValueError(message)
        if not self.requests and self.report is None:
            message = "A turn with no requests and no report says nothing."
            raise ValueError(message)
        return self


class WorkerInput(BaseModel):
    """What one turn of the loop shows the worker.

    ``internal_results`` carries structured answers from the platform's own tables —
    fact rows, source listings — as data the prompt renders. ``untrusted_evidence``
    carries anything whose text originated outside — fetched content, document titles —
    and reaches the model only inside ``<untrusted_source>`` delimiters, via the base
    agent's wrapping, which this input deliberately cannot bypass.
    """

    model_config = ConfigDict(extra="forbid")

    topic: ResearchTopic
    company_name: str
    ticker: str
    as_of_date: str
    remaining_tool_calls: int

    # Turns left, this one included. Told to the worker for the same reason the tool budget
    # is: a bound it cannot see is a bound it cannot plan against -- see `user_message`.
    remaining_rounds: int = 1

    # The tools this run can actually execute: the role's allowlist narrowed to the
    # executors that were bound. Permission is not availability — `fetch_known_url` is
    # granted to the role but absent until something binds a fetcher — and the worker needs
    # to be told the narrower of the two, not the wider.
    available_tools: list[str] = Field(default_factory=list)

    internal_results: list[dict[str, Any]] = Field(default_factory=list)
    untrusted_evidence: list[dict[str, str]] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


def _cap(model: type[BaseModel], name: str) -> int:
    """The declared maximum for one field of the worker's contract.

    Raises:
        AssertionError: The field has no ``max_length``. A programming error rather than a
            condition — every field :data:`_LIMITS` names has one, and this fires at import
            so a bound that disappears from the schema cannot quietly stay in the prompt.
    """
    for constraint in model.model_fields[name].metadata:
        limit = getattr(constraint, "max_length", None)
        if limit is not None:
            return int(limit)
    message = f"{model.__name__}.{name} has no max_length for the prompt to state."
    raise AssertionError(message)


# The schema's own bounds, in words the model can work to.
#
# **They have to be said, because the API does not enforce them.** The SDK moves
# ``max_length`` into the schema's *description* before sending — the API's JSON-schema mode
# rejects the constraint outright — so the server checks a reply's shape and nothing else.
# A structurally perfect reply can therefore overrun a bound by forty characters, and it is
# rejected here, on the way in, after the money has been spent. Three of five workers died
# that way in one run: two over-long coverage notes and one finding citing more fact ids
# than a finding may carry.
#
# Read off the models rather than typed out again. A limit written in a prompt and again in
# a schema is a limit that drifts, and the prompt is always the copy that loses.
_LIMITS: Final = f"""\
- the report: at most {_cap(WorkerReport, "findings")} findings and \
{_cap(WorkerReport, "leads")} leads, and a coverage_note of at most \
{_cap(WorkerReport, "coverage_note")} characters
- each finding: a statement of at most {_cap(WorkerFinding, "statement")} characters, and \
at most {_cap(WorkerFinding, "source_document_ids")} source document ids and \
{_cap(WorkerFinding, "fact_ids")} fact ids
- each lead: a question of at most {_cap(WorkerLead, "question")} characters and a \
why_it_matters of at most {_cap(WorkerLead, "why_it_matters")} characters
- each turn: at most {_cap(WorkerTurn, "requests")} tool requests, each with a query of at \
most {_cap(ToolRequest, "query")} characters and a why of at most \
{_cap(ToolRequest, "why")} characters"""


# What each tool does, in the worker's own terms. Keyed by the names in the ``analysis``
# role's allowlist, and checked against it by a test — a tool the registry grants but this
# map does not describe would be a tool the model is never told it has.
#
# **This map exists because of a wasted run.** The prompt told the worker that requests are
# executed "only if the tool is on your role's allowlist" and then never said what the
# allowlist was. The worker did the only thing it could: it guessed. It asked for
# ``news_search`` and ``sec_filings_search``, neither of which has ever existed here, was
# refused twice, and spent its five rounds discovering by trial and error what it could
# have been told in one sentence.
_TOOL_BRIEFS: Final[dict[str, str]] = {
    "search_facts": (
        "search_facts — query: a financial concept, e.g. 'Revenues' or 'OperatingIncome'. "
        "Returns stored facts for this run with their ids, periods and units."
    ),
    "search_sources": (
        "search_sources — query: words to match against the titles of documents this run "
        "has acquired. Returns those documents with their ids and tiers."
    ),
    "search_filings_full_text": (
        "search_filings_full_text — query: a phrase to look for in this company's filings, "
        "e.g. 'segment operating margin' or 'material weakness'. Searches the regulator's "
        "full-text index, scoped to this company and to filings public by the as-of date, "
        "and returns the filings that contain it with their forms, dates and URLs. It "
        "returns a listing, not the text: to read one, spend a fetch_known_url call on its "
        "URL."
    ),
    "fetch_known_url": (
        "fetch_known_url — query: one URL, on a host whose documents search_sources has "
        "already shown you. Fetches and archives it, and gives you back its text and a "
        "new source document id you can cite. A host this run holds nothing from is "
        "refused, however plausible the URL: the platform never learns a new host from a "
        "request."
    ),
}


_SYSTEM_PROMPT: Final = """\
You are one research worker inside an equity research platform, investigating a single \
topic for a single company. Your whole output is one JSON object matching the schema you \
are given.

Each turn you either ask for tools or deliver your report — never both.

Rules that are enforced outside this conversation, stated so you can work with them:
1. You never assert a figure. A number belongs to a stored fact; you cite its id.
2. Every finding cites at least one source document id or fact id from the evidence you \
were shown. Ids you were not shown do not exist. A finding with nothing to cite is not a \
finding: if your point is that the evidence is silent on something, that is a lead.
2a. An id is a UUID, and the only ids that exist are in the `fact_id` and \
`source_document_id` fields of the results you were given. Copy one of those, character \
for character. A value, a concept name or a title from the same result is not an id, and \
citing one is refused. Document numbers are the trap: an accession number such as \
0001193125-11-282113 names a filing but is never its id — the id is the UUID in that \
result's `source_document_id` field.
3. Tool requests are executed by the platform's code, only within your remaining budget, \
and only for the tools listed below. There are no others. Asking for a tool that is not \
listed is refused and wastes a turn.
3a. You are bounded twice: by tool calls and by turns. Each message tells you what is left \
of both. Reaching the last turn without a report is a failure, and a report drawn from \
partial evidence is worth more than none.
4. Where the evidence cannot settle a question, record it as a lead rather than \
stretching a finding.

Length limits, checked when your reply arrives. Not advisory: a reply that breaks one of \
these cannot be read at all, so it is thrown away whole and costs you the turn. Write to \
fit them — say less, in fewer findings, rather than trimming what you cite.
{limits}

The tools available to you on this run, and nothing else:
{tools}

If none of them can reach what a question needs, that is a lead, not a failure. Say so and \
deliver your report.

Your topic:
{brief}
"""

# What the worker is told when a run has bound no executors at all. Better than an empty
# bullet list, which reads as an oversight rather than as a statement.
_NO_TOOLS: Final = (
    "  (none — this run bound no tools. Work from the evidence you are shown, and record "
    "what you could not reach as leads.)"
)


class ResearchWorker(Agent[WorkerInput, WorkerTurn]):
    """One per-topic investigator. Five topics, one class, one contract."""

    role: ClassVar[str] = "analysis"
    output_schema: ClassVar[type[BaseModel]] = WorkerTurn
    prompt_version: ClassVar[str] = "3"

    def system_prompt(self, payload: WorkerInput) -> str:
        return _SYSTEM_PROMPT.format(
            brief=_TOPIC_BRIEFS[payload.topic],
            limits=_LIMITS,
            tools=_tool_menu(payload.available_tools),
        )

    def user_message(self, payload: WorkerInput) -> str:
        internal = payload.model_dump(mode="json", exclude={"untrusted_evidence"})
        parts = [
            f"Investigate {payload.company_name} ({payload.ticker}), as of "
            f"{payload.as_of_date}. Topic: {payload.topic.value}.",
            f"Remaining tool budget: {payload.remaining_tool_calls} call(s). "
            f"Remaining turns, this one included: {payload.remaining_rounds}.",
            f"Internal results so far, as data:\n{internal['internal_results']}",
        ]
        if payload.problems:
            # "Reply", not "report": the same channel carries a report the validator refused
            # and one the schema could not read at all, and the second is not a report.
            parts.append(
                "Your previous reply was not accepted, for these reasons; fix them:\n- "
                + "\n- ".join(payload.problems)
            )
        if payload.remaining_rounds <= 1:
            # **The bound that actually kills workers, and the one they were never told.**
            # Only the tool budget had a warning, so a worker spending two calls a turn
            # reached the fifth turn with calls to spare, was never once told to wrap up,
            # and was failed for producing nothing. Five workers died that way in one run —
            # one of them having spent seven of its twelve calls.
            parts.append(
                "This is your final turn. Deliver your report now. A report from partial "
                "evidence is what is wanted here: state what you found, and record "
                "everything you did not settle as leads."
            )
        elif payload.remaining_tool_calls == 0:
            parts.append("Your tool budget is spent. Deliver your report this turn.")
        return "\n\n".join(parts)

    def untrusted_sources(self, payload: WorkerInput) -> list[UntrustedSource]:
        return [
            UntrustedSource(
                source_document_id=item.get("source_document_id", "unknown"),
                tier=item.get("tier", "T5_SECONDARY"),
                text=item.get("text", ""),
                title=item.get("title"),
            )
            for item in payload.untrusted_evidence
        ]


@dataclass(slots=True)
class ExecutedTool:
    """One request's outcome, as the next turn (and the step record) sees it."""

    tool: str
    query: str
    executed: bool
    refusal: str = ""
    internal_results: list[dict[str, Any]] = field(default_factory=list)
    untrusted_evidence: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "query": self.query,
            "executed": self.executed,
            "refusal": self.refusal,
            "results": len(self.internal_results) + len(self.untrusted_evidence),
        }


# An executor answers one authorised request. It returns internal (structured, trusted)
# results and/or untrusted evidence for the wrapper — never text that goes to the model
# outside a delimiter.
ToolExecutor = Callable[[ToolRequest], Awaitable[ExecutedTool]]

# Validates a report against the platform's tables; returns problems, empty means sound.
ReportValidator = Callable[[WorkerReport], Awaitable[list[str]]]


@dataclass(slots=True)
class Investigation:
    """What one worker's loop came to, with the audit trail of every request."""

    topic: ResearchTopic
    report: WorkerReport
    executed: list[ExecutedTool]
    rounds: int

    @property
    def tool_calls(self) -> int:
        return sum(1 for item in self.executed if item.executed)


async def investigate(
    context: AgentContext,
    *,
    topic: ResearchTopic,
    company_name: str,
    ticker: str,
    as_of_date: str,
    executors: Mapping[str, ToolExecutor],
    validate: ReportValidator,
    max_tool_calls: int = MAX_TOOL_CALLS,
    max_rounds: int = MAX_ROUNDS,
) -> Investigation:
    """Run one worker's request/execute loop to a validated report.

    **A reply the schema cannot read is a problem to feed back, not a death.** The bounds on
    the contract reach the model as description text rather than as a rule the API applies
    (see :data:`_LIMITS`), so an otherwise sound report can arrive forty characters too long
    and be rejected on the way in. That used to propagate straight out of here and kill the
    node — losing four other topics along with it — when it is the most trivially fixable
    failure the loop can see: naming the field that overran is usually the whole remedy.
    The retry runs **within the same round**, bounded by :data:`MAX_UNREADABLE_REPLIES`
    rather than by the round budget: an unreadable reply moved the investigation nowhere,
    and when it consumed a round the likeliest place for one — the final round, with the
    whole report to write against the output ceiling — was also the one place the
    feedback had no round left to run in. A live worker died exactly there.

    **A validator refusal on the last round narrows the report rather than killing it.**
    Feedback is always preferred while a round remains to act on it; when none does, and
    the refusal is down to specific findings, those findings are dropped and the
    validated remainder is delivered — see :func:`_narrowed_report`.

    Raises:
        WorkerExhaustedError: If the rounds run out without a report the validator
            accepts. Deliberately an error, with the audit trail in its context; the
            caller decides whether that fails the node or degrades it visibly.
        ValidationError: If the model produced :data:`MAX_UNREADABLE_REPLIES` replies the
            contract could not read. Re-raised rather than translated: it names the field
            and the constraint, which is what whoever reads the failed step needs — with
            the topic and the audit trail added on the way past.
    """
    worker = ResearchWorker()
    # Permission ∩ availability, settled once. The registry says what the role may ask for;
    # the executors say what this run can actually do.
    available = sorted(set(worker.allowed_tools) & set(executors))
    executed: list[ExecutedTool] = []
    internal: list[dict[str, Any]] = []
    untrusted: list[dict[str, str]] = []
    problems: list[str] = []
    spent = 0
    unreadable = 0

    for round_number in range(1, max_rounds + 1):
        turn: WorkerTurn | None = None
        while turn is None:
            payload = WorkerInput(
                topic=topic,
                company_name=company_name,
                ticker=ticker,
                as_of_date=as_of_date,
                remaining_tool_calls=max_tool_calls - spent,
                remaining_rounds=max_rounds - round_number + 1,
                available_tools=available,
                internal_results=internal,
                untrusted_evidence=untrusted,
                problems=problems,
            )
            problems = []

            try:
                turn = await worker.run(context, payload)
            except ValidationError as rejected:
                salvaged = _salvaged_turn(rejected)
                if salvaged is not None:
                    turn, dropped = salvaged
                    # Warning, not info: findings the model asserted have been discarded,
                    # and anyone auditing the run should see the report is narrower than
                    # the reply that was billed.
                    _log.warning(
                        "worker.findings_salvaged",
                        topic=topic.value,
                        round=round_number,
                        dropped=dropped,
                        kept=len(turn.report.findings) if turn.report is not None else 0,
                    )
                    continue
                unreadable += 1
                problems = schema_problems(rejected)
                _log.warning(
                    "worker.reply_unreadable",
                    topic=topic.value,
                    round=round_number,
                    attempt=unreadable,
                    problems=problems,
                    # The provider's own diagnosis — the ceiling, the refusal, the broken
                    # field. `problems` is written for the model to act on; whoever reads
                    # this log needs the version that names the fix.
                    error=rejected.message,
                )
                if unreadable >= MAX_UNREADABLE_REPLIES:
                    # What this frame knows and the provider does not: which of the five
                    # topics just died, and the audit trail of what it had done. The
                    # workflow's degraded step is built from exactly this context.
                    rejected.context["topic"] = topic.value
                    rejected.context["tool_calls"] = spent
                    rejected.context["rounds"] = round_number
                    rejected.context["requests"] = [item.as_dict() for item in executed]
                    raise
                # Retry within the same round. The round bound exists for turns that
                # moved the investigation; this one moved nothing, and consuming a round
                # on it meant an unreadable reply on the *final* round — the likeliest
                # place, with the whole report to write — got its feedback delivered
                # into a loop with no round left to act on it. A live worker died
                # exactly there. The retries stay bounded by MAX_UNREADABLE_REPLIES,
                # which is the bound that was doing the work anyway.

        if turn.report is not None:
            found = await validate(turn.report)
            if not found:
                return Investigation(
                    topic=topic, report=turn.report, executed=executed, rounds=round_number
                )
            rescued = await _rescued_at_exhaustion(
                turn.report,
                validate,
                topic=topic,
                round_number=round_number,
                max_rounds=max_rounds,
                problems=found,
            )
            if rescued is not None:
                return Investigation(
                    topic=topic, report=rescued, executed=executed, rounds=round_number
                )
            problems = found
            _log.info(
                "worker.report_refused", topic=topic.value, problems=found, round=round_number
            )
            continue

        for request in turn.requests:
            outcome = await _execute_one(
                worker,
                request,
                executors=executors,
                spent=spent,
                max_tool_calls=max_tool_calls,
            )
            executed.append(outcome)
            if outcome.executed:
                spent += 1
                internal.extend(outcome.internal_results)
                untrusted.extend(outcome.untrusted_evidence)
            elif outcome.refusal:
                internal.append({"tool": request.tool, "refused": outcome.refusal})

    message = (
        f"The {topic.value} worker used {spent} tool call(s) over {max_rounds} round(s) "
        "and never produced a report its validator accepted."
    )
    if problems:
        # What the last attempt was refused for. Without it the error says only that five
        # rounds happened, and the fix always starts from why the last one was not enough.
        message += " Its final attempt was refused for: " + "; ".join(problems)
    raise WorkerExhaustedError(
        message,
        context={
            "topic": topic.value,
            "tool_calls": spent,
            "rounds": max_rounds,
            "problems": problems,
            # The audit trail travels with the failure: the workflow's degraded step
            # records what the worker did do, not only that it died.
            "requests": [item.as_dict() for item in executed],
        },
    )


def degraded_report(topic: ResearchTopic, reason: str) -> WorkerReport:
    """An empty report standing in for an investigation that died, saying why.

    For the workflow layer, which continues a run past a dead worker rather than
    abandoning four finished topics and everything downstream of them. The stand-in
    asserts nothing — no findings, no leads — and its coverage note is the failure,
    so every consumer of worker output sees an honest absence instead of a hole.
    Built here because this module owns the contract: the note is trimmed to the
    coverage note's own bound, read off the model exactly as :data:`_LIMITS` reads it.
    """
    limit = _cap(WorkerReport, "coverage_note")
    note = f"The {topic.value} investigation did not complete: {reason}"
    if len(note) > limit:
        note = note[: limit - 1] + "…"
    return WorkerReport(coverage_note=note)


def _salvaged_turn(  # noqa: PLR0911 -- each return is a distinct reason not to salvage
    rejected: ValidationError,
) -> tuple[WorkerTurn, int] | None:
    """The rejected reply with its inadmissible findings removed — when that repairs it.

    A live run died because one finding in ten arrived without a citation: the other nine
    were admissible, the reply was already paid for, and the loop threw all of it away.
    Dropping the inadmissible finding is a pure narrowing — nothing the model did not
    assert is added, and the decision to discard is code's, not the model's (ADR 0036).
    Returns ``None`` on any condition where a repair would not be safe or would not be a
    repair at all, which lands the loop on the existing say-it-back path instead. What
    survives salvage still faces the caller's ``validate`` over real evidence.
    """
    payload = getattr(rejected, "response_payload", None)
    if not isinstance(payload, dict):
        return None
    text = "".join(
        str(block.get("text", ""))
        for block in payload.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
    try:
        raw = json.loads(text)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    report = raw.get("report")
    if not isinstance(report, dict):
        return None
    # No explicit refusal of tool-request turns is needed: a turn carrying requests and a
    # report together fails ``WorkerTurn``'s own validator below, and a pure request turn
    # has no report dict to reach here with. The test pinning the property proved the
    # explicit check was an equivalent mutant and it was removed rather than kept as
    # decoration.
    findings = report.get("findings")
    if not isinstance(findings, list):
        return None
    kept = []
    for finding in findings:
        try:
            WorkerFinding.model_validate(finding)
        except PydanticValidationError:
            continue
        kept.append(finding)
    if len(kept) == len(findings):
        # The findings were not what was wrong, so there is nothing here to repair.
        return None
    try:
        turn = WorkerTurn.model_validate({**raw, "report": {**report, "findings": kept}})
    except PydanticValidationError:
        return None
    if turn.report is None:  # pragma: no cover -- a requests turn was refused above
        return None
    return turn, len(findings) - len(kept)


async def _rescued_at_exhaustion(
    report: WorkerReport,
    validate: ReportValidator,
    *,
    topic: ResearchTopic,
    round_number: int,
    max_rounds: int,
    problems: list[str],
) -> WorkerReport | None:
    """The refused report, narrowed and delivered — only when no round remains to fix it.

    Feedback is always preferred while a round can act on it, so before the final round
    this declines without looking: the refusal goes back to the model, which can fix the
    citation and *keep* the finding. On the final round, feeding back is dying with a
    good diagnosis, and dropping what the validator refuses is the repair code can make —
    the same narrowing :func:`_salvaged_turn` performs one layer down (ADR 0036).
    """
    if round_number != max_rounds:
        return None
    narrowed = await _narrowed_report(report, validate)
    if narrowed is None:
        return None
    survivors, dropped = narrowed
    # Warning, not info: findings the model asserted have been discarded, and an audit
    # should see the report is narrower than what was delivered.
    _log.warning(
        "worker.findings_dropped_at_exhaustion",
        topic=topic.value,
        round=round_number,
        dropped=dropped,
        kept=len(survivors.findings),
        problems=problems,
    )
    return survivors


async def _narrowed_report(
    report: WorkerReport, validate: ReportValidator
) -> tuple[WorkerReport, int] | None:
    """The report with the findings the validator refuses removed — when that satisfies it.

    A live run died on its final round citing accession numbers where UUIDs belong: the
    validator's refusal named the one broken finding, the feedback that would have fixed
    it had no round left to run in, and the whole topic — three sound findings included —
    went down with the run. The remedy the feedback would have asked for, *drop or fix
    that finding*, has a half code can perform.

    Which findings are refused is discovered by asking the validator, not by parsing its
    messages: each finding is validated alone, and only the clean ones are kept. That
    keeps this function independent of the refusal wording, and it means a report-level
    problem — one no singleton escapes — refuses every finding and lands on ``None``,
    which is the existing exhaustion path. The survivors are then validated **together**
    before they are accepted, because passing alone does not prove they pass as a set.
    """
    kept = [
        finding
        for finding in report.findings
        if not await validate(report.model_copy(update={"findings": [finding]}))
    ]
    if not kept or len(kept) == len(report.findings):
        # Nothing survived, or the findings were not what was wrong: no repair here.
        return None
    survivors = report.model_copy(update={"findings": kept})
    if await validate(survivors):
        return None
    return survivors, len(report.findings) - len(kept)


def _tool_menu(available: list[str]) -> str:
    """The tools, as bullets the prompt can carry.

    An available tool with no brief is listed by name rather than dropped: telling the
    worker less than the truth is the failure this whole function exists to prevent.
    """
    if not available:
        return _NO_TOOLS
    return "\n".join(f"- {_TOOL_BRIEFS.get(tool, tool)}" for tool in available)


async def _execute_one(
    worker: ResearchWorker,
    request: ToolRequest,
    *,
    executors: Mapping[str, ToolExecutor],
    spent: int,
    max_tool_calls: int,
) -> ExecutedTool:
    """Authorise then execute one request — in that order, and both in code.

    The registry check comes first: a tool the role does not hold is refused whatever the
    budget says, and the refusal costs nothing, because a poisoned document must not be
    able to burn the worker's budget by asking for capabilities it will never get.
    """
    try:
        worker.require_tool(request.tool)
    except ToolNotPermittedError as refused:
        _log.warning("worker.tool_refused", tool=request.tool, reason="not_permitted")
        return ExecutedTool(
            tool=request.tool, query=request.query, executed=False, refusal=str(refused)
        )

    if spent >= max_tool_calls:
        return ExecutedTool(
            tool=request.tool,
            query=request.query,
            executed=False,
            refusal=(
                f"The tool budget of {max_tool_calls} executed call(s) is spent. "
                "Deliver your report."
            ),
        )

    executor = executors.get(request.tool)
    if executor is None:
        return ExecutedTool(
            tool=request.tool,
            query=request.query,
            executed=False,
            refusal=f"{request.tool} is not available in this run.",
        )

    try:
        return await executor(request)
    except ValidationError as unusable:
        return ExecutedTool(
            tool=request.tool, query=request.query, executed=False, refusal=str(unusable)
        )
