"""The custom-section agent: the operator's prose runs inside the platform's contract.

`docs/PLAN.md` §2.12 and ADR 0037. One structured-output call per section, composed in
the fixed order the user cannot alter: the immutable platform contract leads (the base
agent puts it there and nothing can displace it), this agent's instruction carries the
section's output schema, the user message carries the structured evidence, and the
operator's text sits last, inside ``<user_skill>`` delimiters. Quoted document excerpts —
untrusted text — trail the whole composition inside ``<untrusted_source>`` blocks, via
the base agent's wrapping, which this agent deliberately cannot bypass.

**The output envelope is fixed; the section's contract is data.** The registry registers
:class:`CustomSectionDraft` — ``content`` plus proposed claims — and the *content* is
validated against the pinned ``output_contract`` by deterministic code in
:mod:`aer.skills.execution`. That split is what makes a rating unwritable by type twice
over: the envelope has no such field (``extra="forbid"``), and the contract validation
refuses any content key the contract did not declare — while task 35 made the reserved
names undeclarable in a contract in the first place.

**Claims are proposals.** Each names its evidence — the figure it asserts by id, the
excerpt that supports it by extraction id — and code decides whether the ids resolve and
the excerpts verify. The model can propose a citation; it has no path to confirming one.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aer.agents.base import Agent
from aer.agents.contract_schema import draft_model_for
from aer.agents.untrusted import UntrustedSource
from aer.agents.user_skill import USER_SKILL_RULE, wrap_user_skill

__all__ = [
    "CustomSectionAgent",
    "CustomSectionDraft",
    "CustomSectionInput",
    "ProposedCitation",
    "ProposedClaim",
]


# A ceiling is a sanity bound; a budget is what the prompt asks for. The distinction is
# ADR-less but hard-won — the planner learned it when a 660-character reply against a
# 600-character `max_length` threw away a paid-for call, and the section writer inherited
# the bounds without the lesson. `max_length` reaches the model as *description text*, not
# as a server-side rule, so a structurally perfect reply can still miss it: three sections
# of one live report died that way, one of them with twenty-two over-long claims at once.
#
# So the ceiling is set well clear of the budget, ordinary variance costs nothing, and the
# prompts interpolate the budgets from these same constants — instruction and validation
# cannot drift apart.
CLAIM_STATEMENT_BUDGET: Final = 600
CLAIM_STATEMENT_CEILING: Final = 1_500

CLAIM_BASIS_BUDGET: Final = 400
CLAIM_BASIS_CEILING: Final = 1_000


class ProposedCitation(BaseModel):
    """One excerpt a claim rests on, named by ids the evidence listing supplied."""

    model_config = ConfigDict(extra="forbid")

    source_document_id: str = Field(min_length=1, max_length=64)
    extraction_id: str = Field(min_length=1, max_length=64)


class ProposedClaim(BaseModel):
    """One assertion the section makes, with what it stands on.

    The shape mirrors the ``claims`` table's own rules so a violation fails in the
    response schema rather than surviving until the insert: a numeric claim names exactly
    one figure, a non-numeric claim names none, and the kinds that cannot stand on a
    stated basis carry at least one proposed citation.
    """

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=CLAIM_STATEMENT_CEILING)
    kind: Literal["numeric", "factual", "forward_looking", "opinion"]
    financial_fact_id: str | None = None
    calculation_id: str | None = None
    basis: str | None = Field(default=None, max_length=CLAIM_BASIS_CEILING)
    citations: list[ProposedCitation] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _stands_on_the_right_thing(self) -> ProposedClaim:
        named = (self.financial_fact_id is not None) + (self.calculation_id is not None)
        if self.kind == "numeric":
            if named != 1:
                message = (
                    "A numeric claim names exactly one figure — a financial fact id or a "
                    f"calculation id, not {named}."
                )
                raise ValueError(message)
            if not self.citations:
                message = "A numeric claim needs at least one proposed citation."
                raise ValueError(message)
        else:
            if named:
                message = f"A {self.kind} claim must not name a figure."
                raise ValueError(message)
            if self.kind == "factual" and not self.citations:
                message = "A factual claim needs at least one proposed citation."
                raise ValueError(message)
            if self.kind in {"forward_looking", "opinion"} and not (self.basis or "").strip():
                message = f"A {self.kind} claim needs a stated basis."
                raise ValueError(message)
        return self


class CustomSectionDraft(BaseModel):
    """The envelope every custom section returns: content, and the claims inside it.

    ``extra="forbid"`` is the §2.12 rating rule at the type level, re-asserted at the
    execution boundary: there is no field here for a rating, a recommendation or a
    valuation range, so output carrying one fails validation rather than arriving.
    """

    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any]
    claims: list[ProposedClaim] = Field(default_factory=list, max_length=24)


class CustomSectionInput(BaseModel):
    """Everything one generation call is shown, typed so nothing is interpolated ad hoc."""

    model_config = ConfigDict(extra="forbid")

    section_key: str
    title: str
    company_name: str
    ticker: str
    as_of_date: str
    output_contract: dict[str, Any]
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    internal_evidence: list[dict[str, Any]] = Field(default_factory=list)
    untrusted_evidence: list[dict[str, str]] = Field(default_factory=list)
    skill_body: str
    problems: list[str] = Field(default_factory=list)
    evidence_truncated: bool = False


_SYSTEM_PROMPT: Final = """\
You draft one custom section of an equity research report, to the operator's own
specification. Your whole output is one JSON object matching the schema you are given:
the section's content, and a claims list carrying every factual and numeric statement
the content makes.

Rules that are enforced outside this conversation, stated so you can work with them:
1. You never produce a figure of your own. Every numeral in your content must appear in
a numeric claim naming the stored fact or recorded calculation it comes from, by id.
Ids you were not shown do not exist. Dates and document references are not figures when
they are written recognisably — "March 2026", "Q3 2025", "in 2024", "Item 2.02",
"Exhibit 99.1", "CIK 0000320193" — so anchor every year to a month, a quarter or a
temporal word; a bare unanchored year is treated as a quantity and refused.
2. Factual and numeric claims cite evidence: a source document id and an extraction id
from the evidence listing. The platform re-reads every excerpt; a citation that does not
verify blocks the report.
3. Where the evidence cannot support what the operator asked for, say so plainly in the
content and keep your confidence low. An honest gap is publishable; filler is not.
4. Forward-looking statements and opinions carry a stated basis instead of a citation,
and are written as judgements, never as facts.
5. Keep each claim within its length: a `statement` under {statement_budget} characters
and a `basis` under {basis_budget}. These are asked for here because the schema's own
bounds reach you as description text rather than as a rule the server applies — a reply
that overruns them is thrown away after it has been paid for.

{user_skill_rule}

This section's output contract — your content object must carry exactly these fields:
{contract}
"""


class CustomSectionAgent(Agent[CustomSectionInput, CustomSectionDraft]):
    """One user-authored section, drafted under the pinned composed policy."""

    role: ClassVar[str] = "custom_section"
    output_schema: ClassVar[type[BaseModel]] = CustomSectionDraft
    prompt_version: ClassVar[str] = "1"

    def response_schema(self, payload: CustomSectionInput) -> type[BaseModel]:
        """The declared envelope, with ``content`` bound to the pinned contract.

        The projected contract is what the operator approved, so binding the reply to it is
        also the tightest reading of the pin: a field the pin does not declare is not
        merely refused afterwards, it cannot be returned. See
        :mod:`aer.agents.contract_schema` for why being shown a contract was not enough.
        """
        return draft_model_for(
            CustomSectionDraft, payload.output_contract, name=payload.section_key
        )

    def system_prompt(self, payload: CustomSectionInput) -> str:
        return _SYSTEM_PROMPT.format(
            user_skill_rule=USER_SKILL_RULE,
            contract=json.dumps(payload.output_contract, indent=2, sort_keys=False),
            statement_budget=CLAIM_STATEMENT_BUDGET,
            basis_budget=CLAIM_BASIS_BUDGET,
        )

    def user_message(self, payload: CustomSectionInput) -> str:
        """Structured evidence first, the operator's text last. The order is the point."""
        parts = [
            f"Draft the section {payload.title!r} ({payload.section_key}) for "
            f"{payload.company_name} ({payload.ticker}), as of {payload.as_of_date}.",
            "Evidence policy for this section, already composed and enforced in code:\n"
            + json.dumps(payload.evidence_policy, sort_keys=True),
            "The run's evidence, as data:\n"
            + json.dumps(payload.internal_evidence, sort_keys=True),
        ]
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
        parts.append(wrap_user_skill(payload.skill_body))
        return "\n\n".join(parts)

    def untrusted_sources(self, payload: CustomSectionInput) -> list[UntrustedSource]:
        return [
            UntrustedSource(
                source_document_id=item.get("source_document_id", "unknown"),
                tier=item.get("tier", "T5_SECONDARY"),
                text=item.get("text", ""),
                title=item.get("title"),
            )
            for item in payload.untrusted_evidence
        ]
