"""The validator's assistant: advisory judgement where the deterministic answer ran out.

ADR 0038. Two of the per-run validators (task 39) meet genuine ambiguity: a claim whose
citation failed the excerpt match — is the supporting text simply *elsewhere* in the
document? — and a source with no established publication date — does the text itself say
when it was published? Both are questions a model answers well and a rule answers badly.

**Advice is the entire output.** The assist's response is recorded in the evaluation
row's details and nowhere else. There is no path from anything here to
``citations.excerpt_verified`` (one function writes that, and this is not it), to a
source's quarantine flag, or to a metric's value — the deterministic verdict stands
whatever the model thinks of it, which is the property the tests pin.

The document text an assist reads is fetched content: it travels in the untrusted
channel, delimited and neutralised, exactly as it does for every other agent.
"""

from __future__ import annotations

from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aer.agents.base import Agent
from aer.agents.untrusted import UntrustedSource

__all__ = ["DOCUMENT_WINDOW_CHARS", "AssistInput", "ValidatorAdvisory", "ValidatorAssist"]

# How much document text one assist is shown. Bounded by the caller rather than trusted
# to fit: the role's input cap is the tripwire, this is the budget.
DOCUMENT_WINDOW_CHARS: Final = 20_000


class ValidatorAdvisory(BaseModel):
    """What the assist concluded — a proposal for a person, never a verdict for a column."""

    model_config = ConfigDict(extra="forbid")

    found: bool
    candidate_excerpt: str | None = Field(default=None, max_length=600)
    proposed_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    rationale: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _found_carries_a_proposal(self) -> ValidatorAdvisory:
        if self.found and self.candidate_excerpt is None and self.proposed_date is None:
            message = (
                "An advisory that claims to have found something must carry it — a "
                "candidate excerpt or a proposed date."
            )
            raise ValueError(message)
        return self


class AssistInput(BaseModel):
    """One question for the assist, with the bounded evidence it may read."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["excerpt_location", "date_adjudication"]
    question: str = Field(min_length=1, max_length=1_000)
    source_document_id: str
    source_tier: str = "T5_SECONDARY"
    document_text: str = Field(default="", max_length=DOCUMENT_WINDOW_CHARS)


_SYSTEM_PROMPT: Final = """\
You assist an equity research platform's deterministic validators with one narrowly \
scoped question at a time. Your whole output is one JSON object matching the schema you \
are given.

Your answer is advisory. The platform's own checks have already reached their verdict, \
and nothing you say changes it — your proposal is recorded for a person to review.

Rules:
1. For an excerpt-location question: search the quoted document for a passage that \
supports the claim. Return it verbatim in candidate_excerpt if you find one; say found: \
false if you do not. Never compose a passage the document does not contain.
2. For a date-adjudication question: propose the publication date the quoted text itself \
supports, as YYYY-MM-DD, with the evidence in your rationale. If the text does not \
establish one, say found: false.
3. State your confidence honestly. A confident wrong answer wastes a person's time twice."""


class ValidatorAssist(Agent[AssistInput, ValidatorAdvisory]):
    """One advisory question, answered from bounded evidence. Batched where there are many."""

    role: ClassVar[str] = "validator"
    output_schema: ClassVar[type[BaseModel]] = ValidatorAdvisory
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: AssistInput) -> str:  # noqa: ARG002 -- fixed by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: AssistInput) -> str:
        return f"Question ({payload.kind}):\n{payload.question}"

    def untrusted_sources(self, payload: AssistInput) -> list[UntrustedSource]:
        if not payload.document_text:
            return []
        return [
            UntrustedSource(
                source_document_id=payload.source_document_id,
                tier=payload.source_tier,
                text=payload.document_text,
            )
        ]
