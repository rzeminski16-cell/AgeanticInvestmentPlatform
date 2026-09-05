"""The custom-section agent: the operator's prose runs inside the platform's contract.

`docs/archive/PLAN.md` §2.12 and ADR 0037. One structured-output call per section, composed in
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

from pydantic import BaseModel, ConfigDict, Field

from aer.agents.base import Agent
from aer.agents.contract_schema import draft_model_for
from aer.agents.untrusted import UntrustedSource
from aer.agents.user_skill import USER_SKILL_RULE, wrap_user_skill

__all__ = [
    "CLAIMS_BUDGET",
    "CLAIMS_CEILING",
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

# The claims list, by the same lesson. The confirmation run's `business_overview` first
# reply carried 27 claims against a `max_length` of 24 that the prompt had never mentioned
# — 6,027 output tokens refused for a bound the writer was not told. The ceiling is set
# clear of the budget, and the budget is asked for.
CLAIMS_BUDGET: Final = 24
CLAIMS_CEILING: Final = 48


class ProposedCitation(BaseModel):
    """One excerpt a claim rests on, named by the extraction id the listing supplied.

    The extraction id alone, as an opaque handle (gap A51b). An extraction already
    belongs to exactly one source document, so a second field restating that document
    was a fact the model could get wrong — and one live section filed nineteen claims
    pairing real extractions with the wrong source, each refused. With ``extra="forbid"``
    the mismatch is now unrepresentable: the platform resolves the document from its own
    record when the citation is recorded.
    """

    model_config = ConfigDict(extra="forbid")

    extraction_id: str = Field(min_length=1, max_length=64)


class ProposedClaim(BaseModel):
    """One assertion the section makes, with what it stands on.

    The shape mirrors the ``claims`` table's own rules so a violation fails in the
    response schema rather than surviving until the insert: a numeric claim names exactly
    one figure and stands on it (ADR 0109), a non-numeric claim names none, a factual
    claim carries at least one proposed citation, and a forward-looking statement or an
    opinion carries a stated basis.
    """

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=CLAIM_STATEMENT_CEILING)
    kind: Literal["numeric", "factual", "forward_looking", "opinion"]
    financial_fact_id: str | None = None
    calculation_id: str | None = None
    basis: str | None = Field(default=None, max_length=CLAIM_BASIS_CEILING)
    citations: list[ProposedCitation] = Field(default_factory=list, max_length=4)

    @property
    def malformed_reason(self) -> str | None:
        """Why this claim does not stand on what its kind requires, or ``None``.

        **Read by the caller rather than raised here, and that is the whole point.** This
        was a ``model_validator``: one claim that named no figure raised, which failed the
        parse of the *whole reply*, which meant the draft never became an object — so the
        salvage had nothing to narrow and the section was recorded with no content at all.
        Four of the eight sections a live run lost died exactly there (roadmap §2.1), and
        each took a dozen sound claims and a finished draft down with it. It is the same
        blast radius ``RedTeamChallenge.cites_nothing`` was moved out of the schema to
        stop, for the same reason.

        **The rule is not weakened.** A malformed claim is a refusal like any other in
        :func:`aer.sections.evidence.validate_draft`, it is dropped before anything is
        recorded, and the numerals it used to cover lose their cover — so the sentences
        resting on it go too. What changes is that the rest of the draft survives.

        This is also the one rule the wire format cannot carry: it is a relation between
        fields, JSON Schema has no way to say it, and the server's constrained decoder is
        therefore free to produce a reply that breaks it. A rule enforced only after the
        reply is paid for should cost the offending claim, not the section.
        """
        named = (self.financial_fact_id is not None) + (self.calculation_id is not None)
        if self.kind == "numeric":
            return self._numeric_reason(named)
        if named:
            return f"A {self.kind} claim must not name a figure."
        if self.kind == "factual" and not self.citations:
            return "A factual claim needs at least one proposed citation."
        if self.kind in {"forward_looking", "opinion"} and not (self.basis or "").strip():
            return f"A {self.kind} claim needs a stated basis."
        return None

    def _numeric_reason(self, named: int) -> str | None:
        """What a numeric claim owes: exactly one figure, and nothing else (ADR 0109).

        The figure is the evidence. A stored fact carries the document it was extracted
        from, by code; a recorded calculation carries its inputs, each with a source. The
        rule that also demanded a prose excerpt refused a section about a company's own
        filings nine claims at a time, because the statement lines are fact rows and not
        sentences in any excerpt — and an excerpt attached to satisfy it would have been
        verified for being in the document, never for holding the figure. A citation is
        still admitted, and verified, where one states the figure.
        """
        if named != 1:
            return (
                "A numeric claim names exactly one figure — a financial fact id or a "
                f"calculation id, not {named}."
            )
        return None


class CustomSectionDraft(BaseModel):
    """The envelope every custom section returns: content, and the claims inside it.

    ``extra="forbid"`` is the §2.12 rating rule at the type level, re-asserted at the
    execution boundary: there is no field here for a rating, a recommendation or a
    valuation range, so output carrying one fails validation rather than arriving.
    """

    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any]
    claims: list[ProposedClaim] = Field(default_factory=list, max_length=CLAIMS_CEILING)


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
temporal word; a bare unanchored year is treated as a quantity and refused. Quote a
figure at a precision it rounds to — "50.9" or "51" for a stored 50.88, never "50" — and
carry its sign: "-51.8 days" or "negative 51.8 days" for a stored -51.79.
2. A numeric claim stands on the figure it names: the fact or calculation id is its whole
evidence, and it needs no excerpt. A factual claim cites evidence: the extraction id of an
excerpt from the evidence listing. The excerpt's source document is on record, so the id
alone is the whole citation. Cite an excerpt on a numeric claim only where the excerpt
states the figure. The platform re-reads every excerpt; a citation that does not verify
blocks the report.
3. Where the evidence cannot support what the operator asked for, say so plainly in the
content and keep your confidence low. An honest gap is publishable; filler is not.
4. Forward-looking statements and opinions carry a stated basis instead of a citation,
and are written as judgements, never as facts.
5. Keep each claim within its length — a `statement` under {statement_budget} characters
and a `basis` under {basis_budget} — and the claims list to at most {claims_budget} claims.
These are asked for here because the schema's own bounds reach you as description text
rather than as a rule the server applies — a reply that overruns them is thrown away after
it has been paid for.
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

{user_skill_rule}

This section's output contract — your content object must carry exactly these fields:
{contract}
"""


class CustomSectionAgent(Agent[CustomSectionInput, CustomSectionDraft]):
    """One user-authored section, drafted under the pinned composed policy."""

    role: ClassVar[str] = "custom_section"
    output_schema: ClassVar[type[BaseModel]] = CustomSectionDraft
    # "2": a citation is the extraction id alone; the source document is resolved in
    # code from the extraction's own record (gap A51b).
    # "3": a numeric claim stands on the figure it names and owes no excerpt (ADR 0109);
    # a quoted figure is written at a precision it rounds to, with its sign.
    # "4": the claims list's budget is asked for, as the claim lengths already were.
    prompt_version: ClassVar[str] = "4"

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
            claims_budget=CLAIMS_BUDGET,
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
