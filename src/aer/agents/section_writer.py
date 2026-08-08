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
from aer.agents.custom_section import CustomSectionDraft
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


_SYSTEM_PROMPT: Final = """\
You write one built-in section of an institutional equity research report. Your whole
output is one JSON object matching the schema you are given: the section's content, and a
claims list carrying every factual and numeric statement the content makes.

Rules that are enforced outside this conversation, stated so you can work with them:
1. You never produce a figure of your own. Every numeral in your content must appear in
a numeric claim naming the stored fact or recorded calculation it comes from, by id.
Ids you were not shown do not exist.
2. Factual and numeric claims cite evidence: a source document id and an extraction id
from the evidence listing. The platform re-reads every excerpt; a citation that does not
verify blocks the report.
3. Where the evidence cannot support the section, say so plainly in the content and keep
your confidence low. An honest gap is publishable; filler is not.
4. Forward-looking statements and opinions carry a stated basis instead of a citation,
are written as judgements, never as facts, and appear only where this section's evidence
policy admits them.

This section's output contract — your content object must carry exactly these fields:
{contract}
"""


class SectionWriterAgent(Agent[SectionWriterInput, SectionDraft]):
    """One built-in section, written from evidence the run already recorded."""

    role: ClassVar[str] = "report_writer"
    output_schema: ClassVar[type[BaseModel]] = SectionDraft
    prompt_version: ClassVar[str] = "1"

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
            contract=json.dumps(payload.output_contract, indent=2, sort_keys=False)
        )

    def user_message(self, payload: SectionWriterInput) -> str:
        parts = [
            f"Write the section {payload.title!r} ({payload.section_key}) for "
            f"{payload.company_name} ({payload.ticker}), as of {payload.as_of_date}"
            + (" under point-in-time rules" if payload.point_in_time else "")
            + ".",
        ]
        if payload.focus.strip():
            parts.append(f"The approved plan's focus for this section: {payload.focus.strip()}")
        parts.extend(
            [
                "Evidence policy for this section, enforced in code:\n"
                + json.dumps(payload.evidence_policy, sort_keys=True),
                "The run's evidence, as data:\n"
                + json.dumps(payload.internal_evidence, sort_keys=True),
            ]
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
