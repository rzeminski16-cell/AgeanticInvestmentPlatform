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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Final, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aer.agents.base import Agent, AgentContext, ToolNotPermittedError
from aer.agents.untrusted import UntrustedSource
from aer.errors import AerError, ValidationError

__all__ = [
    "MAX_ROUNDS",
    "MAX_TOOL_CALLS",
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
    "investigate",
]

_log = structlog.get_logger("aer.agents.worker")

# §2.5: max 12 tool calls each. Executed calls, counted in code; the thirteenth is refused
# with the budget named.
MAX_TOOL_CALLS: Final = 12

# Turns of the request/execute loop. A separate bound from the tool budget: a worker that
# spends nothing and produces nothing must still terminate, visibly.
MAX_ROUNDS: Final = 5


class WorkerExhaustedError(AerError):
    """A worker reached its bounds without producing a report.

    A deliberate failure rather than a shrug: the workflow node goes red and says which
    bound was hit, instead of the run continuing with a silently absent investigation.
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
    internal_results: list[dict[str, Any]] = Field(default_factory=list)
    untrusted_evidence: list[dict[str, str]] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT: Final = """\
You are one research worker inside an equity research platform, investigating a single \
topic for a single company. Your whole output is one JSON object matching the schema you \
are given.

Each turn you either ask for tools or deliver your report — never both.

Rules that are enforced outside this conversation, stated so you can work with them:
1. You never assert a figure. A number belongs to a stored fact; you cite its id.
2. Every finding cites at least one source document id or fact id from the evidence you \
were shown. Ids you were not shown do not exist.
3. Tool requests are executed by the platform's code, only if the tool is on your role's \
allowlist, and only within your remaining budget. Asking for anything else is refused and \
wastes a turn.
4. Where the evidence cannot settle a question, record it as a lead rather than \
stretching a finding.

Your topic:
{brief}
"""


class ResearchWorker(Agent[WorkerInput, WorkerTurn]):
    """One per-topic investigator. Five topics, one class, one contract."""

    role: ClassVar[str] = "analysis"
    output_schema: ClassVar[type[BaseModel]] = WorkerTurn
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: WorkerInput) -> str:
        return _SYSTEM_PROMPT.format(brief=_TOPIC_BRIEFS[payload.topic])

    def user_message(self, payload: WorkerInput) -> str:
        internal = payload.model_dump(mode="json", exclude={"untrusted_evidence"})
        parts = [
            f"Investigate {payload.company_name} ({payload.ticker}), as of "
            f"{payload.as_of_date}. Topic: {payload.topic.value}.",
            f"Remaining tool budget: {payload.remaining_tool_calls} call(s).",
            f"Internal results so far, as data:\n{internal['internal_results']}",
        ]
        if payload.problems:
            parts.append(
                "Your previous report was refused for these reasons; fix them:\n- "
                + "\n- ".join(payload.problems)
            )
        if payload.remaining_tool_calls == 0:
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

    Raises:
        WorkerExhaustedError: If the rounds run out without a report the validator
            accepts. Deliberately an error — the workflow node fails visibly rather than
            the run continuing with a silently absent investigation.
    """
    worker = ResearchWorker()
    executed: list[ExecutedTool] = []
    internal: list[dict[str, Any]] = []
    untrusted: list[dict[str, str]] = []
    problems: list[str] = []
    spent = 0

    for round_number in range(1, max_rounds + 1):
        turn = await worker.run(
            context,
            WorkerInput(
                topic=topic,
                company_name=company_name,
                ticker=ticker,
                as_of_date=as_of_date,
                remaining_tool_calls=max_tool_calls - spent,
                internal_results=internal,
                untrusted_evidence=untrusted,
                problems=problems,
            ),
        )
        problems = []

        if turn.report is not None:
            found = await validate(turn.report)
            if not found:
                return Investigation(
                    topic=topic, report=turn.report, executed=executed, rounds=round_number
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
    raise WorkerExhaustedError(
        message,
        context={"topic": topic.value, "tool_calls": spent, "rounds": max_rounds},
    )


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
