"""The risk analyst: a reading of figures it cannot write.

ADR 0080 admitted the role and refused it every number: volatility, drawdown, exposure,
concentration, a position's contribution, a scenario's profit and loss are all traced
calculations, computed before this role is asked anything and handed to it as rendered
strings. Its whole output is three commentaries — what the exposure and concentration
mean together, what the volatility and drawdown mean, what the scenarios mean — and no
field able to carry a figure.

**The deterministic edge is here, beside the contract** (ADR 0106 §4). A commentary that
names a numeral the block does not hold is restating or inventing a figure, and one that
reaches for a size, a limit, an order or a recommendation is doing the four things ADR
0080 says this role does not do. :func:`commentary_problems` refuses both by name, so a
retry is told which term to drop rather than losing the draft.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent
from aer.core.section_output import numerals_in

__all__ = [
    "COMMENTARY_CEILING",
    "RiskAnalystAgent",
    "RiskCommentary",
    "RiskInput",
    "commentary_problems",
]

COMMENTARY_CEILING: Final = 1_200


class FigureLine(BaseModel):
    """One rendered figure: a label and the string the page shows. Never a number."""

    model_config = ConfigDict(extra="forbid")

    label: str
    value: str
    note: str = ""


class HoldingLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    weight: str
    volatility: str = ""
    beta_to_book: str = ""
    contribution: str = ""
    problem: str = ""


class ScenarioLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    shocks: str
    pnl: str = ""
    impact: str = ""
    problem: str = ""


class RiskInput(BaseModel):
    """Everything the analyst sees: the block as the page renders it, and nothing else."""

    model_config = ConfigDict(extra="forbid")

    book_name: str
    currency: str
    as_of: str
    window: str
    coverage: str
    exposure: list[FigureLine] = Field(default_factory=list)
    """Each band's largest slices and the concentration, as label and share."""
    book: list[FigureLine] = Field(default_factory=list)
    """Volatility, drawdown and expected shortfall, with their notes."""
    holdings: list[HoldingLine] = Field(default_factory=list)
    scenarios: list[ScenarioLine] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    """Why the previous draft was refused, carried back on the one retry. Empty at first."""


class RiskCommentary(BaseModel):
    """What the role returns: three readings of a table, and no field for a figure.

    Absent rather than validated empty (ADR 0034): no size, no limit, no scenario, no
    ranking, no score.
    """

    model_config = ConfigDict(extra="forbid")

    exposure: str = Field(min_length=1, max_length=COMMENTARY_CEILING)
    """What the exposure bands and the concentration mean read together."""
    movement: str = Field(min_length=1, max_length=COMMENTARY_CEILING)
    """What the volatility, drawdown, tail and contributions mean read together."""
    scenarios: str = Field(default="", max_length=COMMENTARY_CEILING)
    """What the stated scenarios mean, or empty when none is stated."""


_SYSTEM_PROMPT: Final = """\
You read a book's risk figures and say what the pattern means. Your entire output is one
JSON object matching the schema you are given. Every figure you see was computed by the
platform and is shown beside your words; you may name a figure exactly as it is given and
may not restate, round, derive or correct one.

Rules:

- **Say what the table does not say.** That three of the five largest exposures are one
  end market under different sector codes; that the drawdown sits in one position and the
  rest barely moved; that a scenario about one currency reaches most of the book through
  cash. A reading is the one thing a table does not supply.
- **No number of your own.** Quote a figure only in the form it is given. A percentage
  you computed, a total you added, a figure you rounded is refused.
- **No size, no limit, no order, no recommendation, no ranking, no score.** There is no
  field for any of them, and a sentence that says what to buy, sell, trim, add, cap or
  stop is refused. Describe; do not prescribe.
- **The figures are ex-ante and in listing currency.** They say how the book as it stands
  would have moved; they are not its history, and they leave currency moves to the
  currency band. Read them that way.
- Plain sentences, no headings, no lists. UK English."""


class RiskAnalystAgent(Agent[RiskInput, RiskCommentary]):
    """One call per book per reading, over figures already recorded."""

    role: ClassVar[str] = "risk_analyst"
    output_schema: ClassVar[type[BaseModel]] = RiskCommentary
    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: RiskInput) -> str:  # noqa: ARG002 -- by design
        return _SYSTEM_PROMPT

    def user_message(self, payload: RiskInput) -> str:
        body = payload.model_dump(mode="json")
        return "\n\n".join(
            [
                f"The book is {payload.book_name}, reported in {payload.currency}, as at "
                f"{payload.as_of}. Window: {payload.window}. Coverage: {payload.coverage}.",
                f"Exposure and concentration:\n{body['exposure']}",
                f"The book's movement:\n{body['book']}",
                f"Each measured holding:\n{body['holdings']}",
                f"Stated scenarios:\n{body['scenarios']}",
                *(
                    [
                        "Your previous draft was refused for these reasons; write it again "
                        "without them:\n"
                        + "\n".join(f"- {problem}" for problem in payload.problems)
                    ]
                    if payload.problems
                    else []
                ),
            ]
        )


# -- The deterministic edge -----------------------------------------------------------------

# The four things ADR 0080 says this role does not do, as the words a sentence doing them
# uses. Matched on word boundaries, case-insensitively.
_PRESCRIPTIONS: Final[tuple[str, ...]] = (
    r"should (?:buy|sell|trim|add|reduce|cut|increase|hedge|close|exit)",
    r"(?:buy|sell|trim|reduce|hedge) (?:the|this|that|your)",
    r"stop[- ]loss",
    r"position (?:size|sizing)",
    r"(?:recommend|recommended|recommendation)",
    r"(?:set|impose|breach(?:es|ed)?) (?:a |the )?limit",
    r"risk score",
    r"(?:target|recommended) weight",
)


def commentary_problems(commentary: RiskCommentary, block: RiskInput) -> list[str]:
    """What the commentary says that the block cannot back, each named so a retry can drop it.

    Two rules. A numeral the rendered block does not hold is a figure the model wrote
    (ADR 0097's rule on this surface): the block's own numerals, in every field, are the
    only ones a commentary may carry. And a sentence that sizes, limits, orders or
    recommends is refused by the words it uses (ADR 0080).
    """
    allowed = numerals_in(_flattened(block.model_dump(mode="json")))
    problems: list[str] = []
    for field, text in (
        ("exposure", commentary.exposure),
        ("movement", commentary.movement),
        ("scenarios", commentary.scenarios),
    ):
        if not text:
            continue
        unbacked = sorted(numerals_in(text) - allowed)
        if unbacked:
            problems.append(
                f"The {field} commentary names {', '.join(unbacked)}, and the figures hold no "
                "such number. Quote a figure only as it is given; never restate, round, "
                "derive or total one."
            )
        for pattern in _PRESCRIPTIONS:
            match = re.search(rf"\b(?:{pattern})\b", text, flags=re.IGNORECASE)
            if match:
                problems.append(
                    f"The {field} commentary says {match.group(0)!r}, which sizes, limits, "
                    "orders or recommends. This role describes what the figures show and "
                    "prescribes nothing."
                )
    return problems


def _flattened(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_flattened(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flattened(item) for item in value)
    return str(value)
