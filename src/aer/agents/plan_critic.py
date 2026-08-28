"""The plan critic: a separate context that attacks the plan before a person sees it.

ADR 0091. The red team's posture applied to the cheapest, highest-leverage target in the
workflow: a wrong plan sends the whole run after the wrong target, and gate 1 is the last
moment that costs a planner call to fix rather than a run. The critic sees the request and
the proposed plan — summary, per-section focus, planned sources, named risks — and nothing
else, because nothing else exists yet: no findings, no evidence, no draft.

**Challenges are scored on a closed vocabulary of aspects**, for the reason the red team's
dimensions are closed: a class is only recognisable as recurring if the platform can group
and count by it, and `revision_notes` groups by exactly this value. A severity threshold in
the workflow — not here — decides whether the planner gets a second attempt.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent

__all__ = [
    "PlanChallenge",
    "PlanChallengeAspect",
    "PlanCriticAgent",
    "PlanCriticInput",
    "PlanCritique",
]


class PlanChallengeAspect(StrEnum):
    """The scored aspects a plan challenge may attack.

    Closed, like the red team's dimensions, because recurrence is counted by this value
    across runs (ADR 0091) and free text cannot be counted.
    """

    COVERAGE = "coverage"
    SOURCES = "sources"
    RISKS = "risks"
    FEASIBILITY = "feasibility"
    POINT_IN_TIME = "point_in_time"
    FOCUS = "focus"


# The same budget-versus-ceiling split every bounded field carries: the API's schema mode
# moves `max_length` into description text, so the prompt asks for the budget and the
# ceiling exists to stop a blob after it has been paid for.
CHALLENGE_STATEMENT_BUDGET: Final = 500
CHALLENGE_STATEMENT_CEILING: Final = 1_200

SUGGESTION_BUDGET: Final = 400
SUGGESTION_CEILING: Final = 1_000

COVERAGE_NOTE_BUDGET: Final = 400
COVERAGE_NOTE_CEILING: Final = 1_000


class PlanChallenge(BaseModel):
    """One structured objection to the proposed plan."""

    model_config = ConfigDict(extra="forbid")

    aspect: PlanChallengeAspect
    severity: int = Field(ge=1, le=5)
    statement: str = Field(min_length=1, max_length=CHALLENGE_STATEMENT_CEILING)

    # What the revised plan should do about it. A suggestion, not an instruction: the
    # planner weighs it, and the person at gate 1 sees both positions either way.
    suggestion: str = Field(min_length=1, max_length=SUGGESTION_CEILING)


class PlanCritique(BaseModel):
    """What the critic found — scored challenges, and an honest note on scope."""

    model_config = ConfigDict(extra="forbid")

    challenges: list[PlanChallenge] = Field(default_factory=list, max_length=6)
    coverage_note: str = Field(min_length=1, max_length=COVERAGE_NOTE_CEILING)


class PlanCriticInput(BaseModel):
    """Everything the critic is shown — and, deliberately, everything it can be shown.

    The request's own parameters and the plan as proposed. ``extra="forbid"`` plus the
    absence of any evidence- or findings-shaped field is the same structural isolation the
    red team's input carries: there is nothing of the run's later work to leak, and no
    field to put it in if there were.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    exchange: str
    as_of_date: str
    point_in_time: bool
    analysis_mode: str
    investment_horizon_months: int
    focus_questions: list[str] = Field(default_factory=list)

    summary: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
    planned_sources: list[dict[str, Any]] = Field(default_factory=list)
    known_risks: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT: Final = f"""\
You are the plan critic inside an equity research platform. Another role has proposed a
research plan; your entire job is to find what is wrong with it before a person is asked
to approve it. You did not write it and you owe it nothing. Your whole output is one JSON
object matching the schema you are given.

You receive the research request and the proposed plan: its summary, its per-section
focus, its planned sources and its named risks. From these alone:

1. Attack the plan, never the company. You have no evidence and no findings — a view on
the stock is outside your role, and a challenge asserting one is wrong by construction.
2. Every challenge names the aspect it attacks and a severity from 1 (a quibble) to 5
(this plan researches the wrong thing), scored honestly.
3. Look hardest for what the plan misses: a source that obviously bears on the request
and is not planned, a focus question the sections never answer, a risk to the plan itself
nobody named, work the as-of date makes impossible under point-in-time rules, a section
focus that asks for figures no planned source can establish.
4. You never produce a figure of your own, and you never assert a fact about the company.
Where a challenge rests on something needing verification, phrase it as the question the
plan should be asking.
5. If the plan genuinely survives your attack, say so in the coverage note and return few
or no challenges. A manufactured objection wastes the reviewer's trust in the real ones.
6. Keep each field within its length: a challenge `statement` under
{CHALLENGE_STATEMENT_BUDGET} characters, its `suggestion` under {SUGGESTION_BUDGET}, and
the `coverage_note` under {COVERAGE_NOTE_BUDGET}. These are asked for here because the
schema's own bounds reach you as description text rather than as a rule the server
applies — a reply that overruns them is thrown away after it has been paid for."""


class PlanCriticAgent(Agent[PlanCriticInput, PlanCritique]):
    """The plan's adversary. One call per run, before gate 1, in its own context."""

    role: ClassVar[str] = "plan_critic"
    output_schema: ClassVar[type[BaseModel]] = PlanCritique
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: PlanCriticInput) -> str:  # noqa: ARG002 -- fixed by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: PlanCriticInput) -> str:
        body = payload.model_dump(mode="json")
        lines = [
            f"Critique the proposed research plan for {payload.company_name} "
            f"({payload.ticker} on {payload.exchange}), as of {payload.as_of_date}.",
            f"Point-in-time: {'on' if payload.point_in_time else 'off'}",
            f"Analysis mode: {payload.analysis_mode}",
            f"Investment horizon: {payload.investment_horizon_months} months",
        ]
        if payload.focus_questions:
            lines.append("The operator specifically wants these answered:")
            lines.extend(f"  - {question}" for question in payload.focus_questions)
        lines.append("")
        lines.append(f"The plan's summary:\n{payload.summary}")
        lines.append(f"Its sections and their focus:\n{body['sections']}")
        lines.append(f"Its planned sources:\n{body['planned_sources']}")
        lines.append(f"Its named risks:\n{body['known_risks']}")
        return "\n".join(lines)
