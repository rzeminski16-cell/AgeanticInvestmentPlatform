"""The ask reader: one question, answered from a company's record and nothing else (F6).

ADR 0130 §4 admitted the role and settled what it may say. It is dealt what the record
holds for the company — extracted excerpts in the untrusted channel labelled with the ids to
cite them by, the company's facts, the calculations of the run the current report was
written from — within a token budget code sets, and it returns paragraphs each resting on
ids from that pack, what the question needed that the pack does not hold, and whether in
its own view the material answers the question at all.

**What it says is checked by code before anything is shown.** A citation naming anything
outside the pack is dropped and an answer left with none is discarded; an answer whose
reader says the material does not answer the question is discarded; a numeral no dealt
figure or cited excerpt reads as is a figure of the model's own, and the answer is
discarded. A discarded answer becomes a refusal with the third tier's price. That is how
*no answer from the model's own knowledge* is enforced: not by asking, but by the pack
being all it sees and the checks being all that reaches the page.

**No field for a recommendation, a rating or a target.** There is nothing to validate
empty; the shape cannot carry one.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent
from aer.agents.untrusted import UntrustedSource

__all__ = [
    "MAX_CITATIONS",
    "MAX_PARAGRAPHS",
    "NOT_HELD_CEILING",
    "PARAGRAPH_CEILING",
    "AnswerParagraph",
    "AskAnswer",
    "AskInput",
    "AskReaderAgent",
]

PARAGRAPH_CEILING: Final = 1_200
NOT_HELD_CEILING: Final = 240
MAX_PARAGRAPHS: Final = 6
MAX_CITATIONS: Final = 12


class AskInput(BaseModel):
    """Everything the reader sees: the question, and the pack code dealt it."""

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    question: str
    # The listings — facts, calculations, sources — as data. Each carries the id a
    # paragraph cites it by.
    internal_evidence: list[dict[str, Any]] = Field(default_factory=list)
    # Excerpts of fetched documents. Quoted beneath the ask in the untrusted channel by the
    # base agent, never interpolated here.
    untrusted_evidence: list[dict[str, str]] = Field(default_factory=list)
    truncated: bool = False


class AnswerParagraph(BaseModel):
    """One paragraph, and the ids from the pack it rests on."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=PARAGRAPH_CEILING)
    cites: list[str] = Field(default_factory=list, max_length=MAX_CITATIONS)


class AskAnswer(BaseModel):
    """What the reader returns. Paragraphs over the pack, and what the pack lacked."""

    model_config = ConfigDict(extra="forbid")

    # The reader's own view of whether the material answers the question. False is the
    # honest reply to a question the pack cannot reach, and code treats it as a refusal.
    answered: bool
    paragraphs: list[AnswerParagraph] = Field(default_factory=list, max_length=MAX_PARAGRAPHS)
    # What the question needed that the material does not hold: another company, a period,
    # a document. Said rather than filled in.
    not_in_record: list[str] = Field(default_factory=list, max_length=MAX_CITATIONS)


_SYSTEM_PROMPT: Final = """\
You answer one question about a company from the material you are handed, and from nothing
else. Your entire output is one JSON object matching the schema you are given.

You receive: the question; a listing of the company's stored facts, the recorded
calculations of its current research run, and its source documents, each as data with an
id; and excerpts of fetched documents, each labelled with the ids to cite it by. This is the
whole of the company's record as the platform holds it, and it is all you may use.

Rules:

- **Answer only from the material.** If the material answers the question, say what it says
  in plain paragraphs and, on each paragraph, list the ids of the excerpts, facts or
  calculations it rests on. A paragraph resting on nothing in the material is not written.
- **Say what is missing rather than filling it in.** If the question needs something the
  material does not hold — another company, a period, a kind of document, an event after
  the material was fetched — name it in `not_in_record` and set `answered` to false. Never
  answer from what you know or believe about the company or the world.
- **No figure of your own.** Quote a number only as a fact, a calculation or an excerpt
  states it, and cite the id it came from. Do not compute, estimate, convert or round.
- **No recommendation, no rating, no target, no advice.** There is no field for any of them.
- Plain sentences. No headings, no lists, no hedging stacked on hedging. UK English."""


class AskReaderAgent(Agent[AskInput, AskAnswer]):
    """One call per question, over a pack code dealt, with no tools."""

    role: ClassVar[str] = "ask_reader"
    output_schema: ClassVar[type[BaseModel]] = AskAnswer
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: AskInput) -> str:  # noqa: ARG002 -- by design
        return _SYSTEM_PROMPT

    def stable_context(self, payload: AskInput) -> str:
        """The pack, ahead of the ask: the same bytes for a retry of the same question."""
        return "The company's record, as data:\n" + json.dumps(
            payload.internal_evidence, sort_keys=True
        )

    def user_message(self, payload: AskInput) -> str:
        parts = [
            f"The question, about {payload.company_name} ({payload.ticker}): "
            f"{payload.question.strip()}",
        ]
        if payload.truncated:
            parts.append(
                "The record was cut to a token budget; what you see is all you may use, and "
                "a question the cut material cannot answer is one to say so about."
            )
        return "\n\n".join(parts)

    def untrusted_sources(self, payload: AskInput) -> list[UntrustedSource]:
        return [
            UntrustedSource(
                source_document_id=item.get("source_document_id", "unknown"),
                tier=item.get("tier", "T5_SECONDARY"),
                text=item.get("text", ""),
                title=item.get("title"),
            )
            for item in payload.untrusted_evidence
        ]
