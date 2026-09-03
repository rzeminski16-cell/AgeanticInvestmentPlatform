"""The post-trade reviewer: a closed position's record, read for the quality of the decision.

ADR 0081 admitted the role and settled what it may say: a per-premise verdict from a closed
enum, a `process_quality` free to disagree with the outcome, and lessons — and an `outcome`
it receives and cannot write. ADR 0105 settled what becomes of what it says: a **proposal**,
stored on the pass's job step, which the operator confirms as their own judgement. Nothing
this role returns is a judgement until a person has read it and said so.

**Two independent fields, and no field that could collapse them.** `process_quality` is
about how the decision was made; the outcome the role reads is about what happened. The
four cells — good process with a bad outcome, bad process with a good one — are the ones
this design exists to make reachable, and a schema with one blended score could not reach
them.

**No field recommending a methodology change.** A methodology is a skill file, and a skill
edit is a human act under invariant 7. The reviewer may observe that an exit plan was
ignored; it may not rewrite the checklist that contained it.

**No figure of its own.** It is handed the realised return, the holding period and the
intended horizon as figures it may quote as given, and every one of them is a recorded
calculation before this role sees it.
"""

from __future__ import annotations

from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent
from aer.core.enums import PremiseVerdict, ProcessQuality

__all__ = [
    "BASIS_CEILING",
    "LESSONS_CEILING",
    "MAX_PREMISES",
    "NOTE_CEILING",
    "DecisionUnderReview",
    "FindingWhileOpen",
    "OutcomeFigures",
    "PostTradeReviewerAgent",
    "PremiseUnderReview",
    "PremiseVerdictDraft",
    "ReviewDraft",
    "ReviewInput",
]

BASIS_CEILING: Final = 1_200
LESSONS_CEILING: Final = 1_600
NOTE_CEILING: Final = 400
MAX_PREMISES: Final = 24


class DecisionUnderReview(BaseModel):
    """One decision the episode carried out, as it was written before the outcome."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    action: str
    statement: str
    basis: str
    decided_on: str
    size_statement: str = ""
    horizon_months: int | None = None
    exit_plan: str = ""
    carried_out_by: int = 0
    """How many of the episode's trades named this decision."""


class PremiseUnderReview(BaseModel):
    """One premise of the thesis, as it stands: statement, basis, predicate, withdrawn or not."""

    model_config = ConfigDict(extra="forbid")

    premise_id: str
    statement: str
    basis: str
    predicate: str = ""
    review_by: str = ""
    withdrawn: bool = False
    withdrawn_reason: str = ""


class FindingWhileOpen(BaseModel):
    """What the monitor said about a premise while the position was open."""

    model_config = ConfigDict(extra="forbid")

    premise_id: str
    status: str
    justification: str
    raised_on: str


class OutcomeFigures(BaseModel):
    """Platform-filled. The reviewer reads these and may quote them; it may not restate them."""

    model_config = ConfigDict(extra="forbid")

    opened_on: str
    closed_on: str
    holding_days: int
    intended_horizon_months: int | None = None
    realised_return: str
    """As a fraction of cost, already computed and recorded. "0.20" is twenty per cent."""
    currency: str
    cost: str
    proceeds: str


class ReviewInput(BaseModel):
    """Everything the reviewer sees. No price series, no other position, no other book."""

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    thesis_title: str = ""
    decisions: list[DecisionUnderReview] = Field(default_factory=list)
    premises: list[PremiseUnderReview] = Field(default_factory=list, max_length=MAX_PREMISES)
    findings: list[FindingWhileOpen] = Field(default_factory=list)
    outcome: OutcomeFigures


class PremiseVerdictDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    premise_id: str
    verdict: PremiseVerdict
    note: str = Field(default="", max_length=NOTE_CEILING)


class ReviewDraft(BaseModel):
    """What the role returns: verdicts, a quality with its basis, lessons. Nothing else.

    No `outcome`, which the platform filled and the role only read. No recommendation, no
    methodology change, no action, no size: absent rather than validated empty (ADR 0081).
    """

    model_config = ConfigDict(extra="forbid")

    verdicts: list[PremiseVerdictDraft] = Field(default_factory=list, max_length=MAX_PREMISES)
    process_quality: ProcessQuality
    basis: str = Field(min_length=1, max_length=BASIS_CEILING)
    lessons: str = Field(default="", max_length=LESSONS_CEILING)


_SYSTEM_PROMPT: Final = """\
You review one closed position for the quality of the decision-making behind it, not for
whether it made money. Your entire output is one JSON object matching the schema you are
given, and it is a proposal: the operator reads it and confirms or amends it as their own
judgement.

You receive: the decisions as they were written before the outcome (action, statement,
basis, intended size and holding period, exit plan), the thesis's premises as they stand,
what the monitor found about those premises while the position was open, and the outcome
the platform computed — the realised return on cost, the holding period, and the intended
horizon. The outcome figures are given; quote them as given, never restate or derive one.

Rules:

- **Score the process, not the outcome.** Was a decision written down before the trade,
  with a basis? Was the size stated? Was there an exit plan, and was the exit consistent
  with it? Was the holding period near the intended horizon, and if not, was the early or
  late exit explained by the premises or by the price? A well-made decision that lost money
  is `sound`; a trade with no decision behind it that made money is `flawed`. The two axes
  are independent and you must let them disagree.
- **A verdict per premise**, by its id: `held` if the record bears it out, `partially_held`,
  `failed` if the record contradicts it, `untested` if the position closed before anything
  could have answered it (a premise about a year not yet filed), `unobservable` if nothing
  could ever have answered it. Never grade a premise by what the price did.
- **No recommendation, no methodology change, no size, no action.** There is no field for
  any of them. You may observe that an exit plan was ignored; you may not say what the
  checklist should say next time.
- `basis`: plain sentences naming which decisions, premises and findings the quality rests
  on. `lessons`: what the operator might look at before making the same call again, as
  observations rather than instructions. No headings, no lists, no hedging stacked on
  hedging.
- UK English."""


class PostTradeReviewerAgent(Agent[ReviewInput, ReviewDraft]):
    """One call per closed position, after it has closed, and never over an open one."""

    role: ClassVar[str] = "post_trade_reviewer"
    output_schema: ClassVar[type[BaseModel]] = ReviewDraft
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: ReviewInput) -> str:  # noqa: ARG002 -- by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: ReviewInput) -> str:
        body = payload.model_dump(mode="json")
        return "\n\n".join(
            [
                f"The position is in {payload.company_name} ({payload.ticker})"
                + (f", on the thesis {payload.thesis_title!r}." if payload.thesis_title else "."),
                f"Outcome, computed by the platform:\n{body['outcome']}",
                f"Decisions written before the outcome:\n{body['decisions']}",
                f"Premises of the thesis:\n{body['premises']}",
                f"What the monitor found while the position was open:\n{body['findings']}",
            ]
        )
