"""The challenge briefer: what each side of an unsettled objection costs the reader.

ADR 0095. Gate 3 shows every red-team challenge the run could not resolve — an objection,
its basis, the evidence it cites — and asks the operator to keep the draft's position or
accept the challenge. What it never showed is what either choice *means*: what you have to
believe for each side to hold, and what the report becomes if you pick it.

This role writes that down, once, over a draft that has stopped changing, and leans to a
side with one sentence of why. **The lean settles nothing.** It changes no row, prefills no
rationale, and sits beside controls that are exactly what they were; the operator's reason
is still theirs to write, and ``settle_by_hand`` still refuses an empty one.

**It is never evidence and never leaves the review page.** No claim may name a brief, no
citation may resolve to one, and no rendered report carries it — the appendix carries every
challenge and the resolution a person recorded, and a machine's lean on a conflict somebody
settled has no place in a shareable artefact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent

__all__ = [
    "BRIEF_BUDGET",
    "BRIEF_CEILING",
    "ChallengeBrief",
    "ChallengeBriefAgent",
    "ChallengeBriefInput",
    "ChallengeBriefs",
    "ChallengeSide",
    "UnsettledChallenge",
]


class ChallengeSide(StrEnum):
    """The two sides of a thesis conflict, and deliberately no third.

    There is no "either", no "cannot say" and no "escalate". The platform has already
    escalated: that is why a person is reading this. A brief that could decline to lean
    would reliably decline, and the operator would be back to comparing two paragraphs.
    """

    DRAFT = "draft"
    """Keep what the draft says. The challenge is noted and does not change the report."""

    CHALLENGE = "challenge"
    """Accept the objection. The draft's position does not survive it."""


# The budget-versus-ceiling split every bounded field carries (see plan_critic and verdict):
# the prompt asks for the budget, the ceiling stops a blob after it has been paid for.
BRIEF_BUDGET: Final = 200
BRIEF_CEILING: Final = 600

# How many challenges one call briefs. A red team that produced more than this has produced
# more than an operator will read at one sitting, and the surplus keeps its controls with no
# brief beside them -- which is exactly the page as it was before this role existed.
MAX_BRIEFS: Final = 8


class ChallengeBrief(BaseModel):
    """One unsettled challenge, read as the choice it actually puts to the operator.

    **No field for a figure, a source, an excerpt or a claim id**, and the absence is the
    enforcement rather than a convention (ADR 0074, ADR 0087): a type with no column for
    the forbidden thing is the only rule a later prompt and a later template are equally
    unable to argue with.
    """

    model_config = ConfigDict(extra="forbid")

    disagreement_id: str = Field(min_length=1)
    """Which challenge this brief is about. Checked against the run's own rows by the
    service, exactly as a challenge's claim attribution is; an id the run does not hold is
    dropped rather than trusted."""

    keeping_assumes: str = Field(min_length=1, max_length=BRIEF_CEILING)
    keeping_means: str = Field(min_length=1, max_length=BRIEF_CEILING)
    accepting_assumes: str = Field(min_length=1, max_length=BRIEF_CEILING)
    accepting_means: str = Field(min_length=1, max_length=BRIEF_CEILING)

    leans: ChallengeSide
    because: str = Field(min_length=1, max_length=BRIEF_CEILING)


class ChallengeBriefs(BaseModel):
    """What the role returns: one brief per challenge it was shown, and nothing else."""

    model_config = ConfigDict(extra="forbid")

    briefs: list[ChallengeBrief] = Field(default_factory=list, max_length=MAX_BRIEFS)


class UnsettledChallenge(BaseModel):
    """One challenge as the briefer sees it: the argument, and what it attacks."""

    model_config = ConfigDict(extra="forbid")

    disagreement_id: str
    dimension: str
    severity: int
    material: bool
    statement: str
    basis: str
    draft_position: str = ""
    """What the draft says on the point, where the run recorded it."""


class ChallengeBriefInput(BaseModel):
    """The arguments, and deliberately not the evidence behind them.

    There is no field for a fact, a calculation or a source document. A briefer that could
    read the evidence would be a second validator with an input nobody reviewed, and its
    output would start to look like a finding rather than a reading. What it compares is
    two arguments, which is the thing the operator is comparing.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    challenges: list[UnsettledChallenge] = Field(default_factory=list, max_length=MAX_BRIEFS)


_SYSTEM_PROMPT: Final = f"""\
You brief an analyst who is about to settle disagreements between a research draft and an
adversarial reviewer of that draft. For each disagreement they must choose one side and
record why. Your entire output is one JSON object matching the schema you are given.

You receive each unsettled challenge: what it attacks, how serious the reviewer thought it
was, the objection, its stated basis, and what the draft says on the point. From those
alone, for every challenge you are shown, write:

1. `keeping_assumes` — what a reader has to accept as true to keep the draft's position.
2. `keeping_means` — what the report then says, and what it is exposed to if that is wrong.
3. `accepting_assumes` — what a reader has to accept as true to side with the challenge.
4. `accepting_means` — what the report then says, and what it is exposed to if that is wrong.
5. `leans` — "draft" or "challenge". Choose one. You are not settling anything; a person
reads this and decides, and a brief that refuses to lean is a brief that helps nobody.
6. `because` — one sentence for the lean. The strongest reason, not a summary of both sides.

Rules:

- At most {BRIEF_BUDGET} characters per field. Plain sentences. No headings, no markdown,
no lists, no hedging stacked on hedging.
- **State no figure and assert no fact about the company.** You have not been shown the
evidence and you are not being asked to check anything. Write about what each choice
assumes and implies, never about what is true.
- Do not repeat the objection back. The reader has it above your brief; your value is the
consequence they cannot read off it.
- Say `disagreement_id` back exactly as given, once per challenge, for every challenge.
- Severity is the reviewer's own opinion of their objection. Weigh it, do not defer to it.
- UK English."""


class ChallengeBriefAgent(Agent[ChallengeBriefInput, ChallengeBriefs]):
    """One call per run, after the revise pass, over challenges that have stopped changing."""

    role: ClassVar[str] = "challenge_brief"
    output_schema: ClassVar[type[BaseModel]] = ChallengeBriefs
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: ChallengeBriefInput) -> str:  # noqa: ARG002 -- by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: ChallengeBriefInput) -> str:
        body = payload.model_dump(mode="json")
        return "\n\n".join(
            [
                f"The draft is about {payload.company_name} ({payload.ticker}).",
                f"Unsettled challenges:\n{body['challenges']}",
            ]
        )
