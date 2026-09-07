"""The verdict writer: one interpretive sentence over a draft that has stopped changing.

ADR 0087. The review gate leads with a verdict in two halves: counts and figures composed
in Python on every render, and — this role — one or two sentences of interpretation written
once, when the draft freezes, and stored as the step's output. It is the cheapest call in
the run and the only one whose output is *never evidence*: no claim may name it, no
citation may resolve to it, and the type that carries it to a page
(:class:`aer.web.verdict.Authored`) has no field for a source or a figure.

**The subject is a digest, not the draft.** The sentence this role writes is about the
*shape* of the record — complete or gappy, challenged or unchallenged, clean or flagged —
and that shape is fully stated by outcomes, challenges and verdicts. Handing it the prose
would multiply the input cost of the run's cheapest step for nothing the sentence needs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent

__all__ = [
    "AuthoredTone",
    "AuthoredVerdict",
    "VerdictAgent",
    "VerdictInput",
]


class AuthoredTone(StrEnum):
    """The closed tone vocabulary an authored verdict may claim.

    Values match ``aer.web.vocabulary.Tone`` members by construction, so the page maps them
    without a translation table. Deliberately only three: refusal and failure are the
    *platform's* claims about itself, and an interpretation that reached for either would be
    asserting a fault rather than reading a record.
    """

    SUCCESS = "success"
    WARNING = "warning"
    INFO = "info"


# The budget-versus-ceiling split every bounded field carries (see plan_critic): the prompt
# asks for the budget, the ceiling stops a blob after it has been paid for.
SENTENCE_BUDGET: Final = 280
SENTENCE_CEILING: Final = 700


class AuthoredVerdict(BaseModel):
    """The authored half: a sentence and its tone, and deliberately nothing else.

    No field for a figure, a source, an excerpt or a recommendation — the structural
    enforcement ADR 0087 asks for begins at the schema.
    """

    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(min_length=1, max_length=SENTENCE_CEILING)
    tone: AuthoredTone


class VerdictInput(BaseModel):
    """The frozen record's shape — and, deliberately, everything this role can be shown.

    Outcomes, challenges and verdicts as short structured rows. There is no field for the
    draft's prose beyond one opening excerpt, no field for a calculation and no field for
    the evidence — an interpreter that could quote the record would be one step from being
    cited, and the absence of the field is the rule nobody can argue with.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str

    sections: list[dict[str, Any]] = Field(default_factory=list)
    """Per section: its key, its recorded status, and its length in words."""

    not_generated: list[str] = Field(default_factory=list)
    """Section keys that produced nothing, in the producer's own record."""

    challenges: list[dict[str, Any]] = Field(default_factory=list)
    """The red team's objections: materiality and the challenged position, shortened."""

    open_conflicts: int = 0
    """Source disagreements not yet settled by the operator."""

    triggers: list[str] = Field(default_factory=list)
    """The names of anything the escalation engine says is actually wrong."""

    opening_excerpt: str = ""
    """The first lines of the draft's opening section, for register alone."""


_SYSTEM_PROMPT: Final = f"""\
You write the one-sentence verdict at the top of a research draft's review page. The draft
is finished and will not change; an operator is about to decide whether it becomes the
approved report. Your entire output is one JSON object matching the schema you are given.

You receive the shape of the record: which sections were written and which were not, what
the adversarial reviewer challenged, how many source conflicts remain unsettled, and what
the checks flagged. From that shape alone:

1. Write one or two sentences of interpretation, at most {SENTENCE_BUDGET} characters. Say
what kind of draft this is and what, if anything, deserves the reader's attention first.
2. State no figure. Assert no fact about the company. Quote nothing. Your sentence is
interpretation and is labelled as such; a number or a claim in it would be laundering.
3. Never recommend approving or rejecting. The decision is the reader's; your job is to
say what they are looking at, not what to do about it.
4. Challenges are value received, not faults: a reviewer that found something worth
reading is the run working. Say so in that spirit.
5. Choose the tone honestly: "success" when the record is complete and nothing demands
attention, "warning" when something should be read before deciding, "info" otherwise.
6. UK English, plain register, no headings, no markdown."""


class VerdictAgent(Agent[VerdictInput, AuthoredVerdict]):
    """One call per run, after the revise pass, over a subject that has stopped changing."""

    role: ClassVar[str] = "verdict"
    output_schema: ClassVar[type[BaseModel]] = AuthoredVerdict
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: VerdictInput) -> str:  # noqa: ARG002 -- fixed by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: VerdictInput) -> str:
        body = payload.model_dump(mode="json")
        lines = [
            f"The finished draft is about {payload.company_name} ({payload.ticker}).",
            f"Sections and outcomes:\n{body['sections']}",
        ]
        if payload.not_generated:
            lines.append(f"Sections that produced nothing:\n{body['not_generated']}")
        if payload.challenges:
            lines.append(f"The adversarial reviewer's challenges:\n{body['challenges']}")
        else:
            lines.append("The adversarial reviewer raised no challenges.")
        if payload.open_conflicts:
            lines.append(f"Unsettled source conflicts: {payload.open_conflicts}")
        if payload.triggers:
            lines.append(f"Flagged by the checks:\n{body['triggers']}")
        if payload.opening_excerpt:
            lines.append(f"The draft opens:\n{payload.opening_excerpt}")
        return "\n\n".join(lines)
