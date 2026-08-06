"""The red-team challenger: a separate context that attacks the thesis.

`docs/PLAN.md` §2.5's evaluator row and ADR 0039. The defence against self-consistent
nonsense is an adversary that did not help write the draft — so the input here is
**structurally incapable of carrying the drafting context**. :class:`RedTeamInput` has
fields for the draft's recorded claims and the run's evidence index, and no field for
section prose, worker findings, coverage notes or anything else the bull case was built
from. Isolation is a property of the type, not a discipline of the caller: there is
nowhere to put working notes, so no caller can leak them.

**Challenges are scored and cited, or they are nothing.** Each names the dimension it
attacks, a severity, and the evidence it rests on — a challenge citing no evidence fails
the schema before any code sees it, because an objection resting on nothing is an opinion
wearing armour. The service layer then checks the cited ids resolve inside this run, and
each surviving challenge lands as a ``disagreements`` row through the task 19 ladder's
thesis rung: escalated to gate 2, never auto-resolved, both positions published.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aer.agents.base import Agent

__all__ = [
    "ChallengeDimension",
    "ClaimRecord",
    "RedTeamAgent",
    "RedTeamChallenge",
    "RedTeamInput",
    "RedTeamReport",
]


class ChallengeDimension(StrEnum):
    """The scored dimensions a challenge may attack.

    A closed vocabulary rather than free text, because §2.4's escalation trigger reads
    "materially contradicts the base thesis **on a scored dimension**" — and a dimension
    is only scored if the platform can group, count and compare challenges by it.
    """

    GROWTH = "growth"
    PROFITABILITY = "profitability"
    VALUATION = "valuation"
    BALANCE_SHEET = "balance_sheet"
    COMPETITIVE_POSITION = "competitive_position"
    GOVERNANCE = "governance"
    MACRO = "macro"


class ClaimRecord(BaseModel):
    """One recorded claim from the draft, as the red team sees it."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    section_key: str
    kind: str
    text: str = Field(max_length=600)


class RedTeamChallenge(BaseModel):
    """One structured attack on the thesis: dimension, severity, and its evidence."""

    model_config = ConfigDict(extra="forbid")

    dimension: ChallengeDimension
    severity: int = Field(ge=1, le=5)
    statement: str = Field(min_length=1, max_length=600)
    basis: str = Field(min_length=1, max_length=500)
    fact_ids: list[str] = Field(default_factory=list, max_length=6)
    calculation_ids: list[str] = Field(default_factory=list, max_length=6)
    source_document_ids: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def _stands_on_evidence(self) -> RedTeamChallenge:
        if not self.fact_ids and not self.calculation_ids and not self.source_document_ids:
            message = (
                "A challenge must cite at least one fact, calculation or source document "
                "id from the evidence index. An objection resting on nothing is an "
                "opinion, and opinions do not get a row in the disagreement appendix."
            )
            raise ValueError(message)
        return self


class RedTeamReport(BaseModel):
    """What the adversary found — challenges on evidence, and an honest note on scope."""

    model_config = ConfigDict(extra="forbid")

    challenges: list[RedTeamChallenge] = Field(default_factory=list, max_length=8)
    coverage_note: str = Field(min_length=1, max_length=600)


class RedTeamInput(BaseModel):
    """Everything the red team is shown — and, deliberately, everything it can be shown.

    The claims and the evidence index, nothing else. ``extra="forbid"`` plus the absence
    of any prose-shaped field is the structural isolation the task requires: a caller
    holding section content or worker notes has no field to put them in.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    as_of_date: str
    claims: list[ClaimRecord] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    calculations: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)


_SYSTEM_PROMPT: Final = """\
You are the red team inside an equity research platform. A draft report has been written \
by other roles; your entire job is to attack its thesis. You did not help write it, you \
have not seen its prose, and you owe it nothing. Your whole output is one JSON object \
matching the schema you are given.

You receive the draft's recorded claims and an index of the run's evidence — facts, \
calculations and sources, each with an id. From these alone:

1. Find the strongest arguments that the thesis is wrong, weak, or resting on less than \
it asserts. Attack the load-bearing claims, not the phrasing.
2. Every challenge names the dimension it attacks and a severity from 1 (a quibble) to 5 \
(the thesis does not survive this), scored honestly — a page of severity-5 objections is \
as useless as none.
3. Every challenge cites the evidence it rests on, by id, from the index you were shown. \
Ids you were not shown do not exist. A challenge you cannot evidence is not a challenge; \
leave it out and mention the gap in your coverage note.
4. You never produce a figure of your own. Where the evidence is thin, say the evidence \
is thin — that is itself a finding.
5. If the claims genuinely survive your attack, say so in the coverage note and return \
few or no challenges. A manufactured objection wastes the reader's trust in the real \
ones."""


class RedTeamAgent(Agent[RedTeamInput, RedTeamReport]):
    """The adversary. One call per run, on the batch path, in its own context."""

    role: ClassVar[str] = "red_team"
    output_schema: ClassVar[type[BaseModel]] = RedTeamReport
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: RedTeamInput) -> str:  # noqa: ARG002 -- fixed by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: RedTeamInput) -> str:
        body = payload.model_dump(mode="json")
        return (
            f"Attack the draft thesis for {payload.company_name} ({payload.ticker}), "
            f"as of {payload.as_of_date}.\n\n"
            f"The draft's recorded claims:\n{body['claims']}\n\n"
            f"The run's evidence index — facts:\n{body['facts']}\n\n"
            f"Recorded calculations:\n{body['calculations']}\n\n"
            f"Sources:\n{body['sources']}"
        )
