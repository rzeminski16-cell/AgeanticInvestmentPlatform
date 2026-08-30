"""What a state is called on a screen, and what it means, in one place.

Nineteen raw enum values reach the interface today as themselves: `AWAITING_APPROVAL` is the
most prominent word on the run console, `BUDGET_EXCEEDED` is a banner heading, and a coverage
table prints `SKIPPED_NOT_APPLICABLE`. None is a sentence, and the operator is left to work
out that two of those three are the platform behaving correctly.

So every state a person reads resolves through here, and carries two things: **a label** in
the product's own voice, and **a tone** the interface renders it in. A template asks for the
`HumanState` and never inspects an enum, which is what stops the same status being called
three things on three screens — the failure the redesign found and the reason the design
handoff makes "human status and step vocabulary replaces raw enums/keys" a condition of a
component being finished.

**The distinction this module exists for is refusal against failure.** A run that stopped at
its cost ceiling and a run whose worker died are both `not running`, and they are not the same
event: the first is a guardrail working exactly as designed, the second is a fault. The
console already says so in prose — *"the next step would take this run past a spending cap, so
it stopped before making the call rather than after paying for it"* — and the state it is
describing was, until now, `BUDGET_EXCEEDED` in the same red as a crash.

**Pure data, no I/O, no database.** Imported by handlers, by the shell and by tests, so it
carries the same discipline `web/nav.py` does: nothing here reads a clock, a session or a
setting.

Completeness is enforced rather than intended: `tests/test_presentation_vocabulary.py` fails
when a member of any mapped enum has no entry. A status added without a label renders as a
raw enum on the one screen nobody tested, which is exactly how the nineteen got there.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from aer.core.enums import (
    AnalysisMode,
    Decision,
    GateKind,
    Grade,
    JobStatus,
    RequestStatus,
    SkillKind,
    TransactionKind,
)
from aer.db.models.report_section import SectionStatus

__all__ = [
    "ANALYSIS_MODES",
    "DECISIONS",
    "GATES",
    "GRADES",
    "JOB_STATES",
    "REQUEST_STATES",
    "SECTION_STATES",
    "SKILL_KINDS",
    "STEP_WORDS",
    "TRANSACTION_KINDS",
    "GateCertainty",
    "GateWords",
    "HumanState",
    "Tone",
    "gate_words",
    "job_state",
    "request_state",
    "section_state",
    "step_label",
]


class Tone(StrEnum):
    """How the interface renders a state, from the six semantic pairs the palette defines.

    Six, and the boundary that matters is between the last four. ``REFUSAL`` and ``FAILURE``
    are never the same colour and never share a label, because a rule correctly withholding
    an unsafe answer and a broken dependency are opposite claims about whether the platform
    is working. ``INFO`` is the neutral in-progress tone; ``MUTED`` is for a state nobody
    needs to act on.
    """

    SUCCESS = "success"
    """Complete, confirmed, documented."""

    WARNING = "warning"
    """A person must decide, or money and time need attention."""

    REFUSAL = "refusal"
    """A guardrail deliberately withheld an answer. **Not a fault.**"""

    FAILURE = "failure"
    """A fault, or an operation that did not succeed."""

    INFO = "info"
    """Running, calculated, or a neutral notice."""

    MUTED = "muted"
    """Draft, queued, skipped, inactive."""


@dataclass(frozen=True, slots=True)
class HumanState:
    """One state, as a person reads it.

    ``label`` is sentence case and short enough for a chip. ``detail`` is the sentence a
    surface with room shows beside it, and is empty where the label is already the whole
    truth — a state whose explanation merely restates its label teaches the reader that the
    explanations are not worth reading.
    """

    label: str
    tone: Tone
    detail: str = ""


class GateCertainty(StrEnum):
    """Whether a gate will fire, which the run console must not overstate.

    Five of the eight gates depend on what the company turns out to be, and a sequence that
    presented them as steps one to eight would be a lie about the workflow — the design
    handoff names it directly: *"Do not pretend five conditional gates are certain."*
    """

    ALWAYS = "always"
    """Every run stops here."""

    SOMETIMES = "sometimes"
    """Only when the company makes it necessary."""

    ON_REFUSAL = "on_refusal"
    """Not part of the sequence at all: it exists only when a guardrail stops the run."""


@dataclass(frozen=True, slots=True)
class GateWords:
    """Everything a gate is called, in the three places it is named.

    ``asks`` is the phrase in the second person that the work list uses — *"The run stopped
    so you could confirm its peer set."* It is deliberately a clause rather than a sentence,
    because the row it lands in supplies the rest.

    ``question`` is the gate page's own heading. ``name`` is what a sequence calls it in
    passing, where there is room for two words and no more.
    """

    name: str
    asks: str
    question: str
    certainty: GateCertainty


# -- Runs and steps ------------------------------------------------------------------------

# What each workflow step is doing, in the operator's language. The technical key stays on
# the page as secondary text — it is what a log line and the worker terminal say, and the
# console's own explainer sends the operator to that terminal — but it is no longer the
# primary label: nineteen technical tokens were the main content of the main page of the
# main tool.
#
# Keyed by the workflow's own step keys rather than an enum, because step keys are strings a
# workflow declares. `tests/test_presentation_vocabulary.py` walks `build_steps()` and fails
# when a declared step has no words here; `step_label` still falls back to the key itself,
# because a run recorded under a workflow this build no longer declares must render its
# history rather than raise over it.
STEP_WORDS: Final[dict[str, str]] = {
    "plan": "Planning the research",
    "critique_plan": "Critiquing the plan",
    "gate_plan": "Your decision — the plan",
    "acquire": "Fetching the filings",
    "classify": "Deciding what kind of business this is",
    "gate_sector_specialist": "Your decision — the sector",
    "propose_peers": "Proposing comparable companies",
    "gate_peer_set": "Your decision — the peer set",
    "propose_themes": "Proposing themes",
    "gate_theme_set": "Your decision — the themes",
    "acquire_prices": "Fetching the price history",
    "extract": "Reading the financial statements",
    "gate_unmapped_concepts": "Your decision — the financials",
    "calculate": "Computing the ratios and history",
    "research_company": "Researching the company",
    "research_industry": "Researching the industry",
    "research_macro": "Researching the macro backdrop",
    "research_recent_developments": "Researching recent developments",
    "research_technical_context": "Researching the trading context",
    "comps": "Building the comparables table",
    "propose_assumptions": "Proposing valuation assumptions",
    "gate_assumptions": "Your decision — the assumptions",
    "value": "Valuing the company",
    "draft": "Drafting the report",
    "validate": "Checking the draft against the record",
    "red_team": "Challenging the thesis",
    "revise": "Redrafting the challenged sections",
    "verdict": "Summing up the draft for review",
    "gate_final": "Your decision — the report",
    "render": "Rendering the report document",
}


def step_label(key: str) -> str:
    """The human name of a step, or the key itself for one this build no longer declares."""
    return STEP_WORDS.get(key, key)


# `JobStatus` is the status of a step as well as of a run, so these labels are read in two
# places: a nineteen-row step list, and the one chip at the top of the console. That is why
# they are short — a step list of full sentences is a step list nobody scans.
JOB_STATES: Final[dict[JobStatus, HumanState]] = {
    JobStatus.QUEUED: HumanState(
        "Queued",
        Tone.MUTED,
        "The worker picks this up within a second or two.",
    ),
    JobStatus.RUNNING: HumanState("Running", Tone.INFO),
    JobStatus.PAUSED: HumanState("Paused", Tone.WARNING),
    JobStatus.AWAITING_APPROVAL: HumanState(
        "Waiting for you",
        Tone.WARNING,
        "Nothing further happens, and nothing further is spent, until you decide.",
    ),
    JobStatus.SUCCEEDED: HumanState("Finished", Tone.SUCCESS),
    JobStatus.FAILED: HumanState(
        "Failed",
        Tone.FAILURE,
        "Something went wrong. The timeline holds the last step it reached.",
    ),
    JobStatus.CANCELLED: HumanState("Cancelled", Tone.MUTED),
    # The one this module was written for. A run that stopped at its ceiling is the platform
    # working, and rendering it in the same red as a crash tells the operator to go looking
    # for a fault that is not there.
    JobStatus.BUDGET_EXCEEDED: HumanState(
        "Stopped before overspending",
        Tone.REFUSAL,
        "The next step would cross a spending cap, so it stopped before making the call "
        "rather than after paying for it.",
    ),
}

REQUEST_STATES: Final[dict[RequestStatus, HumanState]] = {
    RequestStatus.DRAFT: HumanState(
        "Draft",
        Tone.MUTED,
        "Written, and nothing has been fetched or spent on it.",
    ),
    RequestStatus.PLANNED: HumanState("Planned", Tone.INFO),
    RequestStatus.APPROVED: HumanState("Approved", Tone.INFO),
    RequestStatus.RUNNING: HumanState("Running", Tone.INFO),
    RequestStatus.AWAITING_REVIEW: HumanState("Waiting for you", Tone.WARNING),
    RequestStatus.COMPLETED: HumanState("Complete", Tone.SUCCESS),
    # An operator's own decision, not a fault: rejecting a plan is the gate doing its job.
    RequestStatus.REJECTED: HumanState("Rejected", Tone.MUTED),
    RequestStatus.FAILED: HumanState("Failed", Tone.FAILURE),
    RequestStatus.CANCELLED: HumanState("Cancelled", Tone.MUTED),
}

# "Not generated", not "0% coverage". A section that never ran has no coverage to report, and
# a zero in that column reads as a section that was written badly rather than one that was not
# written at all (roadmap §4.3).
SECTION_STATES: Final[dict[SectionStatus, HumanState]] = {
    SectionStatus.PENDING: HumanState("Not written yet", Tone.MUTED),
    SectionStatus.GENERATED: HumanState("Written", Tone.SUCCESS),
    SectionStatus.FAILED: HumanState(
        "Not generated",
        Tone.FAILURE,
        "The section refused rather than writing from evidence it did not have.",
    ),
    SectionStatus.SKIPPED_NOT_APPLICABLE: HumanState(
        "Not applicable",
        Tone.MUTED,
        "This company's filings do not carry what the section is about.",
    ),
}

DECISIONS: Final[dict[Decision, HumanState]] = {
    Decision.APPROVED: HumanState("Approved", Tone.SUCCESS),
    Decision.REJECTED: HumanState("Rejected", Tone.MUTED),
    Decision.AMENDED: HumanState("Amended", Tone.INFO),
}


# -- Gates ---------------------------------------------------------------------------------

# `asks` is the phrase `web/overview/research.py` has always used, moved here rather than
# copied: two answers to "what is this gate asking" is the drift a shared vocabulary exists to
# prevent, and that module now derives its mapping from this one.
GATES: Final[dict[GateKind, GateWords]] = {
    GateKind.PLAN: GateWords(
        name="The plan",
        asks="approve its research plan",
        question="Approve this research plan?",
        certainty=GateCertainty.ALWAYS,
    ),
    GateKind.UNMAPPED_CONCEPTS: GateWords(
        name="The financials",
        asks="decide about the figures nothing could map",
        question="Proceed without the figures nothing could map?",
        certainty=GateCertainty.SOMETIMES,
    ),
    GateKind.SECTOR_SPECIALIST: GateWords(
        name="The sector",
        asks="acknowledge that the standard model does not fit its sector",
        question="Confirm what kind of business this is?",
        certainty=GateCertainty.SOMETIMES,
    ),
    GateKind.PEER_SET: GateWords(
        name="The peers",
        asks="confirm its peer set",
        question="Confirm which companies this one is compared with?",
        certainty=GateCertainty.SOMETIMES,
    ),
    GateKind.THEME_SET: GateWords(
        name="The themes",
        asks="confirm the themes it belongs to",
        question="Confirm which stories this company is filed under?",
        certainty=GateCertainty.SOMETIMES,
    ),
    GateKind.ASSUMPTIONS: GateWords(
        name="The assumptions",
        asks="confirm the assumptions its valuation will be built on",
        question="Confirm the numbers the valuation will use?",
        certainty=GateCertainty.SOMETIMES,
    ),
    # Not a step in the sequence: it exists only where a cap would otherwise be crossed, so a
    # journey that listed it as "to come" would be promising a stop that most runs never make.
    GateKind.BUDGET: GateWords(
        name="The ceiling",
        asks="decide whether it may spend more than its ceiling",
        question="Allow this run to spend past its ceiling?",
        certainty=GateCertainty.ON_REFUSAL,
    ),
    GateKind.FINAL: GateWords(
        name="The report",
        asks="review the finished report",
        question="Approve this report?",
        certainty=GateCertainty.ALWAYS,
    ),
}


# -- The portfolio, skills and the request form ---------------------------------------------

# **"Typed", not "Attested".** The provenance vocabulary already spends the word *Attested* on
# a record class — a figure whose origin is the operator's own book — and a documented
# attestation is every bit as attested in that sense. This is the other axis: how strong the
# evidence is. Two vocabularies sharing one word teach a reader that the word means neither.
GRADES: Final[dict[Grade, HumanState]] = {
    Grade.DOCUMENTED: HumanState(
        "Documented",
        Tone.SUCCESS,
        "Extracted from a hashed document, with a citation behind it.",
    ),
    Grade.ATTESTED: HumanState(
        "Typed",
        Tone.WARNING,
        "Typed and self-certified. No document behind it, and every figure above it inherits that.",
    ),
}

# Neutral by construction: a transaction is a thing that happened, not a thing that went well
# or badly. A purchase rendered in success green would be the screen having an opinion about
# the operator's dealing.
TRANSACTION_KINDS: Final[dict[TransactionKind, HumanState]] = {
    TransactionKind.BUY: HumanState("Buy", Tone.MUTED, "Units in, cash out."),
    TransactionKind.SELL: HumanState("Sell", Tone.MUTED, "Units out, cash in."),
    TransactionKind.DIVIDEND: HumanState("Dividend", Tone.MUTED, "Cash received."),
    TransactionKind.FEE: HumanState(
        "Fee", Tone.MUTED, "Commission, stamp duty or custody charged."
    ),
    TransactionKind.DEPOSIT: HumanState(
        "Deposit",
        Tone.MUTED,
        "Cash paid into the account. A flow, never performance.",
    ),
    TransactionKind.WITHDRAWAL: HumanState(
        "Withdrawal",
        Tone.MUTED,
        "Cash taken out. A flow, never a loss.",
    ),
    TransactionKind.SPLIT: HumanState(
        "Split",
        Tone.MUTED,
        "Share count multiplied by the ratio shown. Derived from the corporate action, "
        "never typed; no cash moved.",
    ),
}

SKILL_KINDS: Final[dict[SkillKind, HumanState]] = {
    SkillKind.CUSTOM_SECTION: HumanState(
        "Custom section",
        Tone.INFO,
        "Writes a section of its own, under the same evidence policy as a built-in one.",
    ),
    SkillKind.METHODOLOGY: HumanState(
        "Methodology",
        Tone.INFO,
        "Composes into an existing agent's prompt rather than producing a section.",
    ),
    SkillKind.PREFERENCE: HumanState("Preference", Tone.INFO),
    SkillKind.HOUSE_VIEW: HumanState("House view", Tone.INFO),
}

ANALYSIS_MODES: Final[dict[AnalysisMode, HumanState]] = {
    AnalysisMode.QUICK: HumanState("Quick", Tone.MUTED, "A screen rather than a report."),
    AnalysisMode.STANDARD: HumanState("Standard", Tone.INFO),
    AnalysisMode.FULL: HumanState("Full", Tone.INFO, "The complete report."),
}


# -- Lookups -------------------------------------------------------------------------------
#
# Each raises rather than returning a placeholder. A missing entry is a state somebody added
# without deciding what it is called, and the honest moment to find that out is the first time
# it renders — not later, from a screenshot with `SKIPPED_NOT_APPLICABLE` in it. The
# completeness test means it should never reach a running server at all.


def _looked_up[M: StrEnum](mapping: Mapping[M, HumanState], key: M, what: str) -> HumanState:
    try:
        return mapping[key]
    except KeyError:
        message = (
            f"{key!r} has no {what} vocabulary. Add it to `aer.web.vocabulary` — a state "
            "with no label renders as its own enum name on whichever screen reaches it first."
        )
        raise KeyError(message) from None


def job_state(status: JobStatus) -> HumanState:
    """What a run or a step in that state is called."""
    return _looked_up(JOB_STATES, status, "job")


def request_state(status: RequestStatus) -> HumanState:
    return _looked_up(REQUEST_STATES, status, "request")


def section_state(status: SectionStatus) -> HumanState:
    return _looked_up(SECTION_STATES, status, "section")


def gate_words(gate: GateKind) -> GateWords:
    """What a gate is called, asks and questions."""
    try:
        return GATES[gate]
    except KeyError:
        message = (
            f"{gate!r} has no gate vocabulary. Without it the work list says only that a run "
            '"stopped at a gate and will not continue until you decide", which is true of '
            "every gate and useless about this one."
        )
        raise KeyError(message) from None
