"""The planner: what a run intends to do, before it does any of it.

The first model call of a run, and the one that produces what a human approves at gate 1.
Its output is a *proposal* — sections to write, sources to consult, risks that are known in
advance — and nothing in it is acted on until somebody says so.

**The planner never asserts a fact and never produces a number.** It says "consult the
FY2022 10-K", not "revenue was 198bn". That division is the whole architecture: planning is
judgement under ambiguity, which is what a model is for; the figure is arithmetic over
evidence, which is what :mod:`aer.calc` is for.

**Known risks are part of the output, not an afterthought.** A plan that cannot say what
might go wrong with it is a plan a reviewer has to second-guess from scratch. Asking for
them up front costs nothing and is the difference between "approve?" and "approve, knowing
these three things".
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from aer.agents.base import Agent
from aer.agents.untrusted import UntrustedSource
from aer.core.schemas.request import ResearchRequestRead
from aer.errors import ValidationError

__all__ = [
    "PlannedSource",
    "PlannerAgent",
    "PlannerInput",
    "PriorResearch",
    "ResearchPlanDraft",
    "salvaged_plan",
]

MAX_SECTIONS = 12
MAX_SOURCES = 20
MAX_RISKS = 10

# Lengths on the prose the planner writes, in characters. Two numbers per field, and the
# gap between them is the point.
#
# **The API does not enforce a `max_length`.** The SDK moves it into the schema's
# description, where it is guidance the model can miss — and a miss raises *after* the call
# has been paid for, killing the run over a field that was 40 words long instead of 30. That
# happened on the first real run: a `focus` of 660 characters against a 600 ceiling, and a
# £0.05 planner call thrown away.
#
# So a ceiling is a sanity bound — it exists to stop a runaway blob reaching the database,
# not to enforce a house style. What the platform actually *asks* for is the budget, stated
# in the prompt where the model will read it, and set to a fraction of the ceiling so that
# ordinary variance is not a failed run.
#
# Both come from these constants and the prompt interpolates the budgets, so the instruction
# and the validation cannot drift apart.
_SUMMARY_CEILING = 4000
_FOCUS_CEILING = 2000
_REASON_CEILING = 1000

_SUMMARY_BUDGET = 1200
_FOCUS_BUDGET = 600
_REASON_BUDGET = 300


class PlannedSource(BaseModel):
    """A source the plan intends to consult.

    ``provider`` and ``tier`` are what the gate shows, so a reviewer can see at a glance
    whether a plan rests on regulatory filings or on secondary commentary — which is the
    single most useful thing to know about a research plan before it runs.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    tier: str = Field(min_length=2, max_length=32)
    what: str = Field(
        min_length=1, max_length=_REASON_CEILING, description="What will be retrieved."
    )
    why: str = Field(
        min_length=1, max_length=_REASON_CEILING, description="What question it answers."
    )


class PlannedSection(BaseModel):
    """A section the plan intends to produce.

    ``key`` refers to a ``section_definitions`` row. The planner proposes *which* sections
    apply; it does not invent new ones, because a section that exists only in a plan has no
    output contract and nothing could validate it.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    focus: str = Field(
        min_length=1,
        max_length=_FOCUS_CEILING,
        description="What this section should concentrate on.",
    )


class ResearchPlanDraft(BaseModel):
    """What the planner proposes. Shown at gate 1 and approved or rejected as a whole."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        max_length=_SUMMARY_CEILING,
        description="What this run will do, in a few sentences a reviewer can check.",
    )
    sections: list[PlannedSection] = Field(min_length=1, max_length=MAX_SECTIONS)
    planned_sources: list[PlannedSource] = Field(min_length=1, max_length=MAX_SOURCES)

    # Named in advance because a risk identified after the fact is an excuse. "The company
    # restated FY2021, so as-reported and restated figures will differ" is exactly the kind
    # of thing a reviewer wants before approving, not in the post-mortem.
    known_risks: list[str] = Field(default_factory=list, max_length=MAX_RISKS)

    # The planner's own view of how tractable the request is. Not a confidence in the
    # answer -- there is no answer yet -- but in whether the plan can be carried out.
    confidence: float = Field(ge=0, le=1, default=0.5)


class PriorResearch(BaseModel):
    """One prior approved report, digested for the planner (K2).

    Every field is already a rendered string — see ``history.PriorDigest``, which this
    mirrors. The planner reads conclusions; it is never handed a value it could quote as
    a figure, and it is never handed evidence it could cite.
    """

    model_config = ConfigDict(extra="forbid")

    report_id: str
    as_of_date: str
    rating: str
    confidence: str
    valuation_range: str
    named_risks: list[str] = Field(default_factory=list)
    catalyst_lines: list[str] = Field(default_factory=list)


class PlannerInput(BaseModel):
    """What the planner is told. Typed, so nothing reaches the prompt unvalidated."""

    model_config = ConfigDict(extra="forbid")

    request: ResearchRequestRead
    available_section_keys: list[str] = Field(default_factory=list)
    # Prior approved research on the same company, oldest conclusions the platform holds
    # about it. Hypothesis material only: it enters the prompt as an untrusted quotation
    # labelled not-evidence, and the citation verifier hard-rejects any claim resting on a
    # prior run regardless of what a prompt says (ADR 0064).
    prior_research: list[PriorResearch] = Field(default_factory=list)


_SYSTEM_PROMPT = f"""\
You are the research planner for an equity research platform. You produce a plan; you do \
not produce findings.

Rules you must follow:

1. You never state a financial figure. Not an estimate, not an approximation, not a \
number you remember. Every figure in the final report is computed by deterministic code \
from filed evidence. If you write a number, it is wrong by construction.
2. You never assert a fact about the company. You say which source would establish it.
3. You propose only sections from the list you are given. A section that is not on that \
list has no output contract and nothing could validate what it produced.
4. You name the risks to the plan itself: data that may not exist, filings that may be \
late, a business whose structure the standard analysis does not fit.
5. Point-in-time research means nothing published after the as-of date may be used. If the \
as-of date makes part of the request impossible, say so in the risks rather than planning \
around it silently.
6. Keep each field within its length: `summary` under {_SUMMARY_BUDGET} characters, each \
section's `focus` under {_FOCUS_BUDGET}, and each source's `what` and `why` under \
{_REASON_BUDGET}. These are hard limits on the stored plan, not suggestions. Write a second \
section rather than one long one. The lists are bounded the same way: at most \
{MAX_SECTIONS} sections, {MAX_SOURCES} planned sources and {MAX_RISKS} known risks. Order \
each list strongest first and stop at the bound — an over-full list is refused after the \
call has been paid for.

Be specific. "Consult SEC filings" is not a plan; "retrieve the FY2022 10-K for revenue \
and operating income, and the FY2021 10-K for the comparative" is."""

_PRIOR_RESEARCH_RULE = """\
This request comes with prior approved research on the same company, quoted below as \
untrusted material labelled not-evidence. It may shape which questions the plan asks — \
what changed since the last view, whether a named risk materialised, whether a catalyst \
window closed — and it may never support a claim. Do not repeat its ratings, figures or \
conclusions as findings; do not plan to cite it as a source. Every claim in the eventual \
report must rest on primary evidence fetched by this run, and the platform rejects a \
citation of prior research in code regardless of what any prompt says."""


class PlannerAgent(Agent[PlannerInput, ResearchPlanDraft]):
    """Proposes a research plan for a request."""

    role: ClassVar[str] = "planner"
    output_schema: ClassVar[type[BaseModel]] = ResearchPlanDraft

    # Tools and token caps are deliberately absent: they live in this role's
    # `aer.agents.registry` definition, and a declaration here would grant nothing.

    # Bumped when rule 6 was added, again when it grew the list bounds (gap A42), and
    # again when prior research began feeding forward (K2, ADR 0064). `_ensure_prompt`
    # records an unbumped edit under a hash-suffixed version so a run is never attributed
    # to the wrong instruction — that safety net is for accidents, and none of these was
    # one.
    prompt_version: ClassVar[str] = "4"

    def system_prompt(self, payload: PlannerInput) -> str:
        """The planner's instruction. One constant, plus one rule that travels with priors.

        The prior-research rule is appended only when this call carries prior research —
        the same shape as the base's containment rule: a prompt recorded against a run
        should describe that run, and the variants hash to different ``prompts`` rows,
        which is correct because they are different instructions.
        """
        if not payload.prior_research:
            return _SYSTEM_PROMPT
        return f"{_SYSTEM_PROMPT}\n\n{_PRIOR_RESEARCH_RULE}"

    def untrusted_sources(self, payload: PlannerInput) -> list[UntrustedSource]:
        """The prior digests, quoted rather than interpolated (K2).

        Declared here so the base does the wrapping and the delimiter neutralisation —
        the one path that cannot forget. ``tier`` reads ``not_evidence`` so the label a
        reviewer sees in the archived prompt states the digest's standing, in the same
        place a filing would state ``regulatory``.
        """
        return [
            UntrustedSource(
                source_document_id=f"prior-report:{prior.report_id}",
                tier="not_evidence",
                title=f"Prior approved research, as of {prior.as_of_date}",
                text=_digest_text(prior),
            )
            for prior in payload.prior_research
        ]

    def user_message(self, payload: PlannerInput) -> str:
        request = payload.request
        lines = [
            f"Company: {request.company_name}",
            f"Ticker: {request.ticker} on {request.exchange}",
            f"As-of date: {request.as_of_date.isoformat()}",
            f"Point-in-time: {'on' if request.point_in_time else 'off'}",
            f"Base currency: {request.base_currency}",
            f"Analysis mode: {request.analysis_mode.value}",
            f"Investment horizon: {request.investment_horizon_months} months",
        ]

        if request.focus_questions:
            lines.append("")
            lines.append("The operator specifically wants these answered:")
            lines.extend(f"  - {question}" for question in request.focus_questions)

        if request.excluded_sources:
            lines.append("")
            lines.append("Do not plan to use these sources:")
            lines.extend(f"  - {source}" for source in request.excluded_sources)

        lines.append("")
        lines.append("Sections available to you (propose only from this list):")
        lines.extend(f"  - {key}" for key in payload.available_section_keys)

        return "\n".join(lines)


def _digest_text(prior: PriorResearch) -> str:
    """One prior report as plain lines. Conclusions and their standing, never evidence."""
    lines = [
        f"Non-binding view: {prior.rating} (confidence {prior.confidence})",
        f"Valuation range: {prior.valuation_range}",
    ]
    if prior.named_risks:
        lines.append("Key risks named:")
        lines.extend(f"  - {risk}" for risk in prior.named_risks)
    if prior.catalyst_lines:
        lines.append("Catalysts:")
        lines.extend(f"  - {catalyst}" for catalyst in prior.catalyst_lines)
    return "\n".join(lines)


def salvaged_plan(rejected: ValidationError) -> tuple[ResearchPlanDraft, dict[str, int]] | None:
    """The rejected plan with its over-full lists cut to their bounds — when that repairs it.

    A live run died at step one: eleven ``known_risks`` against a bound of ten, one call
    with no retry, and the whole run failed carrying a £0.12 bill and a plan that was
    sound in every other respect. Cutting a list back to its bound is a pure narrowing of
    what the model proposed — the prompt asks for each list strongest-first, so the tail
    is by the model's own ordering the weakest — and the decision to cut is code's
    (ADR 0036), made from the billed reply's archived payload. Declines (``None``) on
    any condition where trimming is not the repair, and the original error stands.
    The plan gate then shows the operator the trimmed plan, exactly as it will run.
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

    bounds = {"sections": MAX_SECTIONS, "planned_sources": MAX_SOURCES, "known_risks": MAX_RISKS}
    trimmed: dict[str, int] = {}
    for name, bound in bounds.items():
        value = raw.get(name)
        if isinstance(value, list) and len(value) > bound:
            trimmed[name] = len(value) - bound
            raw[name] = value[:bound]
    if not trimmed:
        # The lists were not what was wrong, so there is nothing here to repair.
        return None
    try:
        draft = ResearchPlanDraft.model_validate(raw)
    except PydanticValidationError:
        return None
    return draft, trimmed
