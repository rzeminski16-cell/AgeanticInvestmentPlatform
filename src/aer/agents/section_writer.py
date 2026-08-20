"""The section writer: one built-in section from the run's structured evidence.

`docs/PLAN.md` §1.8's `report_writer` role, admitted by ADR 0042. The shape is the
custom-section agent's — one structured-output call, content against the section's
contract, claims proposing their evidence by id — with two deliberate differences:

* **No operator text.** A built-in section has no skill body, so there is no
  ``<user_skill>`` block and nothing user-authored in the composition. What steers the
  section beyond its contract is the planner's *focus* line — text a human approved at
  gate 1.
* **No tools** (the whole of ADR 0042). The evidence pack was assembled by code before
  the call; a writer that could search would be a researcher whose searches nobody gated.

The envelope is the custom section's envelope on purpose: content plus proposed claims,
``extra="forbid"`` keeping ratings and valuation ranges unrepresentable, and the same
deterministic validation in :mod:`aer.sections.writing` deciding what is recorded.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent
from aer.agents.contract_schema import draft_model_for
from aer.agents.custom_section import (
    CLAIM_BASIS_BUDGET,
    CLAIM_STATEMENT_BUDGET,
    CustomSectionDraft,
)
from aer.agents.untrusted import UntrustedSource

__all__ = ["SectionDraft", "SectionWriterAgent", "SectionWriterInput"]


class SectionDraft(CustomSectionDraft):
    """The writer's envelope: identical to a custom section's, under its own name.

    A subclass rather than an alias so the registry's role → schema binding stays
    one-to-one and a scripted provider can answer the two roles differently.
    """


class SectionWriterInput(BaseModel):
    """Everything one section's call is shown, typed so nothing is interpolated ad hoc."""

    model_config = ConfigDict(extra="forbid")

    section_key: str
    title: str
    company_name: str
    ticker: str
    as_of_date: str
    point_in_time: bool
    output_contract: dict[str, Any]
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    internal_evidence: list[dict[str, Any]] = Field(default_factory=list)
    untrusted_evidence: list[dict[str, str]] = Field(default_factory=list)

    # The planner's one-line brief for this section, approved at gate 1. Empty when the
    # plan named none — the contract alone is a sufficient specification.
    focus: str = ""

    problems: list[str] = Field(default_factory=list)
    evidence_truncated: bool = False

    # The section's word budget and the count past which the validator refuses, stated
    # with their consequence in the user message (gap A50). Zero means unbounded. In the
    # user message rather than the cached policy block, because a truncation retry runs
    # at a *cut* budget (gap A51a) and the stable context must stay byte-identical.
    word_budget: int = 0
    word_ceiling: int = 0


_SYSTEM_PROMPT: Final = """\
You write one built-in section of an institutional equity research report. Your whole
output is one JSON object matching the schema you are given: the section's content, and a
claims list carrying every factual and numeric statement the content makes.

Rules that are enforced outside this conversation, stated so you can work with them:
1. You never produce a figure of your own. Every numeral in your content must appear in
a numeric claim naming the stored fact or recorded calculation it comes from, by id.
Ids you were not shown do not exist. Dates and document references are not figures when
they are written recognisably — "March 2026", "Q3 2025", "in 2024", "Item 2.02",
"Exhibit 99.1", "CIK 0000320193" — so anchor every year to a month, a quarter or a
temporal word; a bare unanchored year is treated as a quantity and refused.
2. Factual and numeric claims cite evidence: the extraction id of an excerpt from the
evidence listing. The excerpt's source document is on record, so the id alone is the
whole citation. The platform re-reads every excerpt; a citation that does not verify
blocks the report.
3. Where the evidence cannot support the section, say so plainly in the content and keep
your confidence low. An honest gap is publishable; filler is not.
4. Forward-looking statements and opinions carry a stated basis instead of a citation,
are written as judgements, never as facts, and appear only where this section's evidence
policy admits them.
5. Keep each claim within its length: a `statement` under {statement_budget} characters
and a `basis` under {basis_budget}. These are asked for here because the schema's own
bounds reach you as description text rather than as a rule the server applies — a reply
that overruns them is thrown away after it has been paid for.
6. Write for the reader of a research note, never for the platform's operator. Do not
mention evidence budgets, token limits, truncation, retrieval, extraction, re-running,
or what a future revision should fetch — those are the platform's internals, not
analysis. Where the evidence is silent on something, say so in one clause and move on
to what it does support; a section that spends its length describing its own
limitations has not analysed anything.
7. Never name the plan, the run, the model, the evidence pack, or the platform. The
reader is reading a research note, not operating a system: "the plan asks us to flag",
"the run's stored figures" and "recorded in the model" are sentences about machinery.
Any direction you are given is for you alone — follow it without referring to it.
8. Write plain prose with no markdown notation — no asterisks for emphasis, no inline
headings. The renderer strips such markers rather than obeying them. Where the contract
offers a lead-in field, that is where an opening label belongs.

This section's output contract — your content object must carry exactly these fields:
{contract}
"""


class SectionWriterAgent(Agent[SectionWriterInput, SectionDraft]):
    """One built-in section, written from evidence the run already recorded."""

    role: ClassVar[str] = "report_writer"
    output_schema: ClassVar[type[BaseModel]] = SectionDraft
    # "2": the user message states the word budget with its consequence (gap A50).
    # "3": a citation is the extraction id alone; the source document is resolved in
    # code from the extraction's own record (gap A51b).
    prompt_version: ClassVar[str] = "3"

    def __init__(self, *, route_role: str | None = None) -> None:
        """A writer, optionally billed at a cheaper configured route (gap O1).

        The route changes which model answers; the role — its allowlist, its caps, its
        prompt contract — stays ``report_writer``'s, resolved from the registry as ever.
        """
        super().__init__()
        if route_role:
            self.route_role = route_role

    def response_schema(self, payload: SectionWriterInput) -> type[BaseModel]:
        """The declared envelope, with ``content`` replaced by this section's contract.

        The prompt below still shows the contract, because the model writes better having
        read what each field is for. But being *shown* a contract and being *bound* by one
        are different things, and only the second put a thesis in the reply — see
        :mod:`aer.agents.contract_schema`.
        """
        return draft_model_for(SectionDraft, payload.output_contract, name=payload.section_key)

    def system_prompt(self, payload: SectionWriterInput) -> str:
        return _SYSTEM_PROMPT.format(
            contract=json.dumps(payload.output_contract, indent=2, sort_keys=False),
            statement_budget=CLAIM_STATEMENT_BUDGET,
            basis_budget=CLAIM_BASIS_BUDGET,
        )

    def stable_context(self, payload: SectionWriterInput) -> str:
        """The evidence policy and the evidence, which repeat across calls.

        Both are a function of the section's evidence policy, not of the section — the
        nineteen built-in sections resolve to a handful of distinct policies, so sections
        sharing one are handed a byte-identical listing, and every retry of a single
        section is handed the same listing again. Sent ahead of the ask, with a cache
        breakpoint after it, that block is written once and read thereafter.

        ``sort_keys=True`` on both dumps is load-bearing rather than tidiness: a dictionary
        serialised in a different order is different bytes, and different bytes are a cache
        miss on everything downstream of them.
        """
        return "\n\n".join(
            [
                "Evidence policy for this section, enforced in code:\n"
                + json.dumps(payload.evidence_policy, sort_keys=True),
                "The run's evidence, as data:\n"
                + json.dumps(payload.internal_evidence, sort_keys=True),
            ]
        )

    def user_message(self, payload: SectionWriterInput) -> str:
        """What differs between calls: which section, its focus, and any refusals to fix.

        The evidence is not here — see :meth:`stable_context`. It arrives as the preceding
        block, so this reads as the instruction that follows the material, which is both
        the shape prompt caching needs and the safer order for a turn that carries quoted
        documents.
        """
        parts = [
            f"Write the section {payload.title!r} ({payload.section_key}) for "
            f"{payload.company_name} ({payload.ticker}), as of {payload.as_of_date}"
            + (" under point-in-time rules" if payload.point_in_time else "")
            + ".",
        ]
        if payload.focus.strip():
            # Plain direction, deliberately unattributed. The first wording — "the
            # approved plan's focus" — taught the writer to tell the reader about the
            # plan (gap R3): the model wrote "on the point the plan asks us to flag"
            # because the prompt had named a plan that asks.
            parts.append(
                f"Direction for this section, for you and never to be quoted: "
                f"{payload.focus.strip()}"
            )
        if payload.word_budget > 0:
            # The ceiling and its consequence, from the same numbers the validator reads
            # (gap A50). The live run bought 14,475 output tokens against a 711-word
            # budget: the budget was enforced only after it had been paid for, because
            # the prompt asked for a target without saying what happens past it.
            parts.append(
                f"Write the content to about {payload.word_budget} words. This is a "
                f"ceiling with a consequence, not a suggestion: past {payload.word_ceiling} "
                "words the platform refuses or cuts the draft — the overrun is paid for "
                "and then thrown away, never published."
            )
        if payload.evidence_truncated:
            parts.append(
                "The evidence listing was truncated to this section's token budget; "
                "what you see is all you may use."
            )
        if payload.problems:
            parts.append(
                "Your previous draft was refused for these reasons; fix them:\n- "
                + "\n- ".join(payload.problems)
            )
        return "\n\n".join(parts)

    def untrusted_sources(self, payload: SectionWriterInput) -> list[UntrustedSource]:
        return [
            UntrustedSource(
                source_document_id=item.get("source_document_id", "unknown"),
                tier=item.get("tier", "T5_SECONDARY"),
                text=item.get("text", ""),
                title=item.get("title"),
            )
            for item in payload.untrusted_evidence
        ]
