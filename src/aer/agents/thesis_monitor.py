"""The thesis monitor: one premise, the facts that arrived after it, and a status.

ADR 0079 admitted the role and settled what it may say: a status from a closed enum and a
justification naming source documents, over one premise at a time, with no tools and no
price. ADR 0103 settled what it is handed. **Code has already measured the crossing** — the
metric is resolved, the threshold's unit normalised, the comparison made through
:class:`~aer.calc.units.Quantity` — and the observation arrives with the verdict in it.
What the model adds is an interpretation of *why that matters* and a reading of the facts
around it, bounded by the crossing: a defeated premise is `contradicted` whatever it says,
and a confirmed one is never `contradicted` and never `unobservable`.

**No field for a rating, an action, a target, a position size or a conviction.** Not fields
left null — absent, in the shape ADR 0034 settled for withheld figures and ADR 0102 for
judgements: a type with no column for the forbidden thing is the only rule a later prompt
cannot talk its way past. The service drops any source id the window does not hold, for
the reason the red team's claim attribution is dropped: an id the run never showed the
model is an id it guessed.
"""

from __future__ import annotations

from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent
from aer.core.enums import PremiseStatus

__all__ = [
    "JUSTIFICATION_BUDGET",
    "JUSTIFICATION_CEILING",
    "MAX_FACTS",
    "MAX_SOURCES",
    "Observation",
    "PremiseInput",
    "PremiseReading",
    "ThesisMonitorAgent",
    "WindowFact",
]

# The budget-versus-ceiling split every bounded field carries: the prompt asks for the
# budget, the ceiling stops a blob after it has been paid for.
JUSTIFICATION_BUDGET: Final = 400
JUSTIFICATION_CEILING: Final = 1_200

# How many facts one reading is shown. Two fiscal years of consolidated statement lines is
# well under this; the bound is against a window that somehow grew, not an expectation.
MAX_FACTS: Final = 120

# How many source documents a justification may name. A window is one or two filings.
MAX_SOURCES: Final = 8


class WindowFact(BaseModel):
    """One stored fact in the window, as the monitor sees it: a line, a period, a source."""

    model_config = ConfigDict(extra="forbid")

    concept: str
    value: str
    unit: str
    period_end: str
    filed_date: str
    source_document_id: str


class Observation(BaseModel):
    """What code measured, verdict included. The model reads this; it does not redo it."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    value: str
    unit: str
    period_end: str
    prior_value: str = ""
    """The same metric one fiscal year earlier, where the platform holds it."""
    prior_period_end: str = ""
    threshold: str
    comparator: str
    holds: bool
    """Whether the premise's predicate holds on this observation. Code's, not the model's."""


class PremiseInput(BaseModel):
    """The premise, the observation and the window. Nothing else.

    No price, no position, no other premise of the thesis and no prior finding: one premise
    at a time, one scope (ADR 0079's consequences). The basis is here because "why the
    holder believed it" is what a reading of new evidence is read against.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    premise_id: str
    statement: str
    basis: str
    held_on: str
    predicate: str
    """The predicate as a sentence — "revenue growth at least 25 percent"."""
    observation: Observation
    facts: list[WindowFact] = Field(default_factory=list, max_length=MAX_FACTS)


class PremiseReading(BaseModel):
    """What the role returns. Three fields, and the absence of a fourth is the design."""

    model_config = ConfigDict(extra="forbid")

    status: PremiseStatus
    justification: str = Field(min_length=1, max_length=JUSTIFICATION_CEILING)
    source_document_ids: list[str] = Field(default_factory=list, max_length=MAX_SOURCES)
    """The documents the justification rests on. Validated by code against the window."""


_SYSTEM_PROMPT: Final = f"""\
You read one premise of an investment thesis against the financial facts that were filed
after the premise was written, and say whether the new evidence leaves it unchanged,
weakens it, strengthens it, or contradicts it. Your entire output is one JSON object
matching the schema you are given.

You receive: the premise as its holder wrote it, the basis they gave for holding it, the
predicate that tests it, an observation code has already made — the metric measured from
the filing, the threshold, and whether the predicate holds — and the facts of the periods
that arrived since the premise was last read, each with the id of the document it came from.

Rules:

- **The observation's verdict is not yours to revise.** If `holds` is false the premise has
been contradicted by the filing and your status is `contradicted`; explain what the figure
shows. If `holds` is true the premise stands: choose `weakened` if the facts moved against
it, `strengthened` if they moved in its favour, `unchanged` otherwise. Never `unobservable`
when an observation was made — it was.
- **Compute nothing and estimate nothing.** Every figure you mention is one you were given,
quoted as given. Do not derive a growth rate, a margin or a difference of your own.
- **Do not read price, and do not recommend.** There is no field for an action, a target or
a conviction, and the reader is not asking for one. Say what the evidence does to the
premise; a person decides what to do.
- `justification`: at most {JUSTIFICATION_BUDGET} characters, plain sentences, naming the
facts you relied on and their periods. No headings, no lists, no hedging stacked on hedging.
- `source_document_ids`: the ids, exactly as given, of the documents whose facts your
justification rests on. Nothing else may appear there.
- UK English."""


class ThesisMonitorAgent(Agent[PremiseInput, PremiseReading]):
    """One call per premise per window of new evidence."""

    role: ClassVar[str] = "thesis_monitor"
    output_schema: ClassVar[type[BaseModel]] = PremiseReading
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: PremiseInput) -> str:  # noqa: ARG002 -- by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: PremiseInput) -> str:
        body = payload.model_dump(mode="json")
        return "\n\n".join(
            [
                f"The thesis is about {payload.company_name} ({payload.ticker}).",
                f"Premise {payload.premise_id}, held since {payload.held_on}: {payload.statement}",
                f"Basis given by the holder: {payload.basis}",
                f"Predicate: {payload.predicate}",
                f"Observation made by code:\n{body['observation']}",
                f"Facts filed since the premise was last read:\n{body['facts']}",
            ]
        )
