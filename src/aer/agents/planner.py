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

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent
from aer.core.schemas.request import ResearchRequestRead

__all__ = ["PlannedSource", "PlannerAgent", "PlannerInput", "ResearchPlanDraft"]

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


class PlannerInput(BaseModel):
    """What the planner is told. Typed, so nothing reaches the prompt unvalidated."""

    model_config = ConfigDict(extra="forbid")

    request: ResearchRequestRead
    available_section_keys: list[str] = Field(default_factory=list)


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
section rather than one long one.

Be specific. "Consult SEC filings" is not a plan; "retrieve the FY2022 10-K for revenue \
and operating income, and the FY2021 10-K for the comparative" is."""


class PlannerAgent(Agent[PlannerInput, ResearchPlanDraft]):
    """Proposes a research plan for a request."""

    role: ClassVar[str] = "planner"
    output_schema: ClassVar[type[BaseModel]] = ResearchPlanDraft

    # Tools and token caps are deliberately absent: they live in this role's
    # `aer.agents.registry` definition, and a declaration here would grant nothing.

    # Bumped because rule 6 was added deliberately. `_ensure_prompt` records an unbumped
    # edit under a hash-suffixed version so a run is never attributed to the wrong
    # instruction — that safety net is for accidents, and this was not one.
    prompt_version: ClassVar[str] = "2"

    def system_prompt(self, payload: PlannerInput) -> str:  # noqa: ARG002
        """The planner's instruction, which does not vary with the request.

        ``payload`` is part of the interface because other agents do vary -- a section
        writer's system prompt carries that section's evidence policy. Taking it here and
        ignoring it keeps the signature uniform, and keeps the prompt a constant that
        hashes to one ``prompts`` row rather than one per request.
        """
        return _SYSTEM_PROMPT

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
