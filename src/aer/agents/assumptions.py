"""The two numbers no filing answers, proposed with reasons and bounded by code.

ADR 0046. Six of a discounted cash flow's eight assumptions have a history —
`aer.services.assumption_proposals` derives those from the filings the run already
acquired, and a trailing average is arithmetic, not a judgement. Two do not:

* **Terminal growth** is a claim about the rate the business grows at *for ever* after the
  explicit forecast ends. No series answers it.
* **The exit multiple** is a claim about what somebody would pay at the end of it. Not in
  any filing either.

Between them they usually decide most of the value, which is why the platform refused to
pick them and why leaving the operator to guess unaided served nobody.

**The confinement is the schema.** :class:`AssumptionProposalDraft` has a field for each of
those two and no other fields, so this role cannot propose a revenue path, a margin or a
discount rate — there is nowhere to put one. A prompt cannot talk its way past a type.

**The bounds are code, and a breach is a refusal.** Whatever comes back is checked against
the discount rate and against stated ceilings before it becomes a proposal, and a value
outside them is *dropped*, never clamped. Clamping would put this platform's number under
the model's justification — a figure attributed to reasoning that did not produce it, which
is worse than no figure. A dropped proposal leaves the assumption unproposed, the operator
types it, and nothing is lost but a suggestion.

**It proposes; it never confirms.** `aer.services.assumptions.propose` writes an unconfirmed
row whatever its caller says, and `as_quantity` refuses one, so nothing here reaches a
calculation until a person has agreed to it at the assumptions gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent

__all__ = [
    "EXIT_MULTIPLE_CEILING",
    "EXIT_MULTIPLE_FLOOR",
    "PROPOSED_BY",
    "TERMINAL_GROWTH_CEILING",
    "TERMINAL_GROWTH_FLOOR",
    "AssumptionProposalAgent",
    "AssumptionProposalDraft",
    "AssumptionProposalInput",
    "BoundedProposal",
    "OpinionProposal",
    "within_bounds",
]

PROPOSED_BY: Final = "aer.agents.assumptions"

# A business cannot shrink for ever and remain a going concern, and one growing faster than
# the economy for ever eventually becomes the economy. Both ends are arithmetic rather than
# taste: the floor because a perpetuity shrinking without limit is not a business, the
# ceiling because long-run nominal growth is the outer bound of what "for ever" can mean.
TERMINAL_GROWTH_FLOOR: Final = Decimal("-0.02")
TERMINAL_GROWTH_CEILING: Final = Decimal("0.04")

# An EV/EBITDA exit outside this band is not a view, it is a typo or a hallucination. Wide
# on purpose: the band exists to catch 250x and 0.02x, not to express a house opinion about
# what a fair multiple is.
EXIT_MULTIPLE_FLOOR: Final = Decimal("2")
EXIT_MULTIPLE_CEILING: Final = Decimal("40")

_JUSTIFICATION_CEILING = 1200
_JUSTIFICATION_BUDGET = 500


class OpinionProposal(BaseModel):
    """One proposed value and the reasoning for it.

    ``confidence`` is the model's own, and is recorded rather than acted on: nothing
    downstream weights by it. It is there so an operator reading two proposals can see
    which one the model was less sure of, which is a different and more useful signal than
    a number presented flatly.
    """

    model_config = ConfigDict(extra="forbid")

    value: Decimal = Field(description="The proposed value, as a decimal.")
    justification: str = Field(
        min_length=1,
        max_length=_JUSTIFICATION_CEILING,
        description="Why this value and not a higher or lower one.",
    )
    confidence: float = Field(ge=0.0, le=1.0)


class AssumptionProposalDraft(BaseModel):
    """The whole of what this role may return: two numbers, each with its reasons.

    **Two fields and no others.** ADR 0046's containment is this class. A role that could
    propose the whole forecast would be a model setting every number in a valuation, and
    review fatigue over a long list is a real failure mode where review of two is not.
    """

    model_config = ConfigDict(extra="forbid")

    terminal_growth: OpinionProposal = Field(
        description="Perpetual growth after the explicit forecast, as a decimal fraction."
    )
    exit_multiple: OpinionProposal = Field(
        description="EV/EBITDA at the end of the explicit forecast."
    )


class AssumptionProposalInput(BaseModel):
    """What the role is given. No tools, so this is the whole of what it can see.

    ``extra="forbid"`` for the same reason the output contract has it: a caller that could
    attach an extra hint would be composing an instruction this role's prompt never
    describes, and the confinement would hold in one direction only.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str
    ticker: str
    as_of_date: str
    base_currency: str

    # The discount rate this valuation will use, as a decimal, when the run already knows it.
    # Supplied because the terminal growth rate has to sit below it, and a model told the
    # constraint proposes inside it rather than having a plausible answer thrown away.
    #
    # **None is a real state, not a missing value.** The rate is decomposed by
    # `aer.calc.wacc` from a risk-free rate, a beta and a premium, each of which is itself an
    # assumption somebody has to confirm — so at the moment these two are proposed the run
    # frequently does not have it yet. Inventing one to fill the field would be exactly the
    # house number this platform refuses elsewhere. The stated ceiling still applies, and the
    # binding check is `aer.calc.dcf.gordon_terminal_value`, which refuses a growth rate at or
    # above the discount rate when the valuation actually runs.
    discount_rate: Decimal | None = None

    # The derived assumptions, already computed, as "name = value — justification" lines.
    # A terminal rate proposed without knowing the company grew at 3% is a guess about a
    # company in general.
    derived: tuple[str, ...] = ()

    # What the research workers found, as statements. Findings rather than raw documents:
    # this role holds no tools and reads no untrusted text directly.
    findings: tuple[str, ...] = ()

    # Which sector model applies, so a proposal is made about the right kind of business.
    sector: str = ""


@dataclass(frozen=True, slots=True)
class BoundedProposal:
    """One proposal after the bounds have been applied.

    ``refusal`` is set when the value was outside them, and the value is then *not* to be
    proposed. Carrying the reason rather than dropping it silently: an assumption missing
    from the gate with no explanation is indistinguishable from a defect, and "the model
    proposed 9% and this platform does not accept a perpetual rate above 4%" is something
    an operator can act on.
    """

    name: str
    value: Decimal
    justification: str
    confidence: float
    refusal: str | None = None

    @property
    def accepted(self) -> bool:
        return self.refusal is None


def within_bounds(
    draft: AssumptionProposalDraft, *, discount_rate: Decimal | None
) -> tuple[BoundedProposal, ...]:
    """Apply the deterministic bounds to a draft. Never clamps; refuses.

    The discount-rate check is the one that is not a matter of taste: at or above it the
    Gordon terminal value is undefined or negative, so a forecast built on such a rate
    would not be wrong by a little.

    ``discount_rate`` of ``None`` means the run does not know it yet — see
    :class:`AssumptionProposalInput`. The check is then **skipped rather than guessed**: the
    stated ceiling still applies here, and :func:`aer.calc.dcf.gordon_terminal_value` applies
    the real comparison when the valuation runs, on the confirmed rate rather than a
    provisional one. Skipping it can only ever let a *proposal* through, and a proposal
    reaches nothing until a person confirms it.
    """
    growth = draft.terminal_growth
    multiple = draft.exit_multiple

    return (
        BoundedProposal(
            name="terminal_growth",
            value=growth.value,
            justification=growth.justification,
            confidence=growth.confidence,
            refusal=_growth_refusal(growth.value, discount_rate=discount_rate),
        ),
        BoundedProposal(
            name="exit_multiple",
            value=multiple.value,
            justification=multiple.justification,
            confidence=multiple.confidence,
            refusal=_multiple_refusal(multiple.value),
        ),
    )


def _growth_refusal(value: Decimal, *, discount_rate: Decimal | None) -> str | None:
    if discount_rate is not None and value >= discount_rate:
        return (
            f"A perpetual growth rate of {value} is at or above the discount rate of "
            f"{discount_rate}, which makes the terminal value undefined or negative rather "
            "than merely large. Not proposed; enter one below the discount rate."
        )
    if value > TERMINAL_GROWTH_CEILING:
        return (
            f"A perpetual growth rate of {value} is above the {TERMINAL_GROWTH_CEILING} "
            "ceiling this platform accepts. A business growing faster than the economy for "
            "ever eventually becomes the economy. Not proposed."
        )
    if value < TERMINAL_GROWTH_FLOOR:
        return (
            f"A perpetual growth rate of {value} is below the {TERMINAL_GROWTH_FLOOR} floor. "
            "A business shrinking without limit for ever is not a going concern, and a "
            "terminal value is the wrong instrument for one. Not proposed."
        )
    return None


def _multiple_refusal(value: Decimal) -> str | None:
    if not EXIT_MULTIPLE_FLOOR <= value <= EXIT_MULTIPLE_CEILING:
        return (
            f"An exit multiple of {value}x is outside the {EXIT_MULTIPLE_FLOOR}x to "
            f"{EXIT_MULTIPLE_CEILING}x band this platform accepts. Outside it the number is "
            "not a view, it is a slip. Not proposed."
        )
    return None


class AssumptionProposalAgent(Agent[AssumptionProposalInput, AssumptionProposalDraft]):
    """Proposes the terminal growth rate and the exit multiple. Nothing else."""

    role: ClassVar[str] = "assumption_proposal"
    output_schema: ClassVar[type[BaseModel]] = AssumptionProposalDraft

    # Tools and token caps live in this role's `aer.agents.registry` definition. A
    # declaration here would grant nothing, and would be a second place to read.

    prompt_version: ClassVar[str] = "1"

    def system_prompt(self, payload: AssumptionProposalInput) -> str:  # noqa: ARG002
        return _SYSTEM_PROMPT

    def user_message(self, payload: AssumptionProposalInput) -> str:
        rate = (
            str(payload.discount_rate)
            if payload.discount_rate is not None
            # Said plainly rather than omitted. A model given no rate and no explanation
            # tends to assume one and reason from it silently.
            else (
                "not yet determined — its components are themselves assumptions awaiting "
                "confirmation. Propose a perpetual rate that would sit below any ordinary "
                "cost of capital for this business, and say in your justification what "
                "discount rate you took it to be below."
            )
        )
        lines = [
            f"Company: {payload.company_name} ({payload.ticker})",
            f"As-of date: {payload.as_of_date}",
            f"Reporting currency: {payload.base_currency}",
            f"Discount rate for this valuation: {rate}",
        ]
        if payload.sector:
            lines.append(f"Sector model: {payload.sector}")

        if payload.derived:
            lines.append("")
            lines.append("Assumptions already derived from this company's filings:")
            lines.extend(f"  - {item}" for item in payload.derived)

        if payload.findings:
            lines.append("")
            lines.append("What the research found:")
            lines.extend(f"  - {item}" for item in payload.findings)

        return "\n".join(lines)


_SYSTEM_PROMPT = f"""\
You propose exactly two numbers for a discounted cash flow: the perpetual growth rate after \
the explicit forecast ends, and the EV/EBITDA multiple the business might be worth at that \
point. You propose nothing else, and there is no field in your output for anything else.

Both are judgements. Every other assumption in this valuation was derived from the \
company's own filings, and you are being asked for these two precisely because no filing \
answers them.

Rules.

1. Ground each proposal in what you were given — the derived history, the research \
findings, the sector. "Mature software businesses in a growing category" is a reason. \
"A standard assumption" is not: if the only support for a number is that it is conventional, \
say so plainly, because an operator should know when they are being handed a convention.

2. The perpetual growth rate must be below the discount rate you were given. Above it the \
terminal value is undefined or negative rather than merely large. This platform also \
refuses anything above {TERMINAL_GROWTH_CEILING} or below {TERMINAL_GROWTH_FLOOR}: a \
business cannot outgrow the economy for ever, and one shrinking without limit is not a \
going concern. A proposal outside those bounds is discarded and the operator is left to \
enter the number by hand, so proposing one helps nobody.

3. The exit multiple is EV/EBITDA and must fall between {EXIT_MULTIPLE_FLOOR}x and \
{EXIT_MULTIPLE_CEILING}x. The band is wide because it exists to catch slips, not to express \
an opinion about what is fair.

4. The two should be consistent with each other. A high perpetual growth rate and a low \
exit multiple describe different companies, and if you propose both, say which you think is \
the better estimate of terminal value and why.

5. Keep each justification to roughly {_JUSTIFICATION_BUDGET} characters. Say what the \
number rests on and what would change it. A reviewer needs to be able to disagree with you \
specifically, not in general.

6. Your confidence is your own and is recorded, not acted on. Nothing downstream weights by \
it. State it honestly — a low confidence on a number you had little to go on is more useful \
than a high one everywhere.

7. You are proposing. A person confirms every value before any calculation uses it, and may \
replace yours. Nothing you return sets a figure in a report by itself.
"""
