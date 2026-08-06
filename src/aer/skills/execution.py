"""Custom-section execution: gather, generate once, validate in code, record the outcome.

The deterministic half of ADR 0037. The agent proposes one draft; everything around it —
what evidence it sees, whether its content satisfies the pinned contract, whether its
claims resolve, what happens when they do not — is ordinary code over the run's own
tables, reading the **pin's snapshot** rather than recomposing policy, because a floor
that moved between approval and execution must not silently change what runs.

Every failure mode is a visible state on the section row, never an absent section:

* a second validation failure marks the section ``failed`` with the reasons recorded;
* evidence short of the composed policy generates with an insufficiency note and low
  confidence — never fabricated prose;
* evidence over the token budget is truncated at whole-item boundaries and flagged;
* a projected contract carrying a reserved field is refused before any money is spent —
  task 35 makes that unrepresentable upstream, and this boundary does not rely on it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Final

import structlog
from sqlalchemy import select

from aer.agents.base import AgentContext, TokenCapExceededError
from aer.agents.custom_section import CustomSectionAgent, CustomSectionDraft, CustomSectionInput
from aer.core.enums import ClaimKind, SourceTier
from aer.core.section_output import contract_violations, reserved_fields_in, unsourced_numerals
from aer.db.models import (
    Calculation,
    Extraction,
    FinancialFact,
    PlanSkillPin,
    ReportSection,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
)
from aer.errors import ValidationError
from aer.services.citations import record_citation, record_claim

__all__ = [
    "EVIDENCE_ITEM_CAP",
    "MAX_GENERATION_ATTEMPTS",
    "SectionExecution",
    "execute_custom_section",
]

_log = structlog.get_logger("aer.skills.execution")

# §2.12: one structured-output call, one retry on a validation failure, then failed.
MAX_GENERATION_ATTEMPTS: Final = 2

# Rows per evidence category before the token budget is even consulted. Bounds the
# queries; the budget bounds the composition.
EVIDENCE_ITEM_CAP: Final = 40

# The estimate the truncation works in. Four characters per token matches the fake
# provider's arithmetic and is close enough for a *budget* — the role's hard input cap is
# still enforced against a real count at the provider boundary.
_CHARS_PER_TOKEN: Final = 4


@dataclass(slots=True)
class SectionExecution:
    """What executing one custom section came to — every outcome, as data."""

    section: ReportSection
    status: SectionStatus
    attempts: int
    claims_recorded: int = 0
    insufficient_evidence: bool = False
    evidence_truncated: bool = False
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "section_key": self.section.section_key,
            "status": self.status.value,
            "attempts": self.attempts,
            "claims": self.claims_recorded,
            "insufficient_evidence": self.insufficient_evidence,
            "evidence_truncated": self.evidence_truncated,
            "problems": list(self.problems),
        }


@dataclass(slots=True)
class _Unit:
    """One indivisible piece of evidence: its listing, its excerpt, and its index entry.

    Truncation keeps or drops a unit whole. An excerpt without its id row would be
    uncitable; an id row without its excerpt would invite a citation of text the model
    never read — and the validation maps are built from the survivors only, so an id the
    budget dropped genuinely does not exist for this call.
    """

    internal: dict[str, Any]
    untrusted: dict[str, str] | None = None
    fact_source: tuple[str, str] | None = None
    calculation_id: str | None = None
    source_tier: tuple[str, SourceTier] | None = None
    extraction_source: tuple[str, str] | None = None

    @property
    def cost(self) -> int:
        untrusted_chars = len(self.untrusted.get("text", "")) if self.untrusted else 0
        return max(1, (len(str(self.internal)) + untrusted_chars) // _CHARS_PER_TOKEN)


@dataclass(slots=True)
class _Evidence:
    """What this call may see and cite, built from the units the budget kept."""

    internal: list[dict[str, Any]] = field(default_factory=list)
    untrusted: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False

    fact_sources: dict[str, str] = field(default_factory=dict)
    calculation_ids: set[str] = field(default_factory=set)
    source_tiers: dict[str, SourceTier] = field(default_factory=dict)
    extraction_sources: dict[str, str] = field(default_factory=dict)

    def admit(self, unit: _Unit) -> None:
        self.internal.append(unit.internal)
        if unit.untrusted is not None:
            self.untrusted.append(unit.untrusted)
        if unit.fact_source is not None:
            self.fact_sources[unit.fact_source[0]] = unit.fact_source[1]
        if unit.calculation_id is not None:
            self.calculation_ids.add(unit.calculation_id)
        if unit.source_tier is not None:
            self.source_tiers[unit.source_tier[0]] = unit.source_tier[1]
        if unit.extraction_source is not None:
            self.extraction_sources[unit.extraction_source[0]] = unit.extraction_source[1]


async def execute_custom_section(
    context: AgentContext,
    *,
    section: ReportSection,
    pin: PlanSkillPin,
    request: ResearchRequest,
    evidence_job_id: uuid.UUID | None = None,
) -> SectionExecution:
    """Run one custom section to a recorded outcome. Never raises for a bad draft.

    The section row is mutated in place — content, status, confidence, and the
    low-confidence reason that carries the insufficiency and truncation flags — and the
    claims and citations are recorded through the same services every built-in section
    will use. What comes back is the audit summary the workflow step stores.

    Args:
        evidence_job_id: Whose recorded calculations count as this section's evidence.
            Defaults to the executing run's own job, which is the only answer a real run
            has. A dry run (task 43) executes in its own job against a finished run's
            evidence, and passes that run's id here — an explicit argument rather than a
            silent widening of the query, because "which run's figures may this section
            cite?" is exactly the question a reader of a citation needs answered.
    """
    contract: dict[str, Any] = section.definition.output_contract or {}

    # Defence in depth at the last boundary before a model call. Task 35 refuses these
    # names at authoring and task 36 projects only validated contracts, so reaching this
    # means a row was written around the service layer — and the answer to that is a
    # refusal here, not trust there.
    reserved = reserved_fields_in(contract)
    if reserved:
        message = (
            f"The projected contract declares the reserved field(s) {sorted(reserved)}. "
            "Ratings, recommendations and valuation ranges are owned by built-in "
            "sections; this section is refused unrun."
        )
        return _failed(section, attempts=0, problems=[message])

    policy = _policy_of(pin, context)
    evidence = await _gather(
        context,
        pin=pin,
        request=request,
        budget=policy["token_budget"],
        evidence_job_id=evidence_job_id or context.job_step.job_id,
    )

    agent = CustomSectionAgent()
    body = pin.skill_version.body
    problems: list[str] = []
    draft: CustomSectionDraft | None = None

    attempts = 0
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        attempts = attempt
        payload = CustomSectionInput(
            section_key=section.section_key,
            title=section.definition.title,
            company_name=request.company_name,
            ticker=request.ticker,
            as_of_date=request.as_of_date.isoformat(),
            output_contract=contract,
            evidence_policy={k: v for k, v in policy.items() if k != "token_budget"},
            internal_evidence=evidence.internal,
            untrusted_evidence=evidence.untrusted,
            skill_body=body,
            problems=problems,
            evidence_truncated=evidence.truncated,
        )
        try:
            candidate = await agent.run(context, payload)
        except TokenCapExceededError as refused:
            # Deterministic in the composition: a retry would compose the same call and
            # be refused the same way, so the section fails now, visibly and for free.
            return _failed(
                section,
                attempts=attempt,
                problems=[str(refused)],
                truncated=evidence.truncated,
            )
        except ValidationError as unparsable:
            problems = [f"The output did not satisfy the response schema: {unparsable}"]
            continue

        problems = _validate(candidate, contract=contract, evidence=evidence, pin=pin)
        if not problems:
            draft = candidate
            break
        _log.info(
            "custom_section.draft_refused",
            section=section.section_key,
            attempt=attempt,
            problems=problems,
        )

    if draft is None:
        return _failed(section, attempts=attempts, problems=problems, truncated=evidence.truncated)

    recorded = 0
    cited_source_ids: set[str] = set()
    for proposal in draft.claims:
        claim = await record_claim(
            context.session,
            section=section,
            kind=ClaimKind(proposal.kind),
            text=proposal.statement,
            financial_fact_id=_uuid_or_none(proposal.financial_fact_id),
            calculation_id=_uuid_or_none(proposal.calculation_id),
        )
        for citation in proposal.citations:
            await record_citation(
                context.session,
                claim=claim,
                source_document_id=uuid.UUID(citation.source_document_id),
                extraction_id=uuid.UUID(citation.extraction_id),
            )
            cited_source_ids.add(citation.source_document_id)
        if proposal.financial_fact_id is not None:
            cited_source_ids.add(evidence.fact_sources[proposal.financial_fact_id])
        recorded += 1

    shortfalls = _policy_shortfalls(cited_source_ids, evidence=evidence, policy=policy)

    section.content = draft.content
    section.status = SectionStatus.GENERATED
    section.confidence = _confidence(draft.content, degraded=bool(shortfalls))
    section.low_confidence_reason = _degradation_note(shortfalls, truncated=evidence.truncated)
    await context.session.flush()

    _log.info(
        "custom_section.generated",
        section=section.section_key,
        attempts=attempts,
        claims=recorded,
        insufficient_evidence=bool(shortfalls),
        evidence_truncated=evidence.truncated,
    )
    return SectionExecution(
        section=section,
        status=SectionStatus.GENERATED,
        attempts=attempts,
        claims_recorded=recorded,
        insufficient_evidence=bool(shortfalls),
        evidence_truncated=evidence.truncated,
        problems=shortfalls,
    )


def _failed(
    section: ReportSection,
    *,
    attempts: int,
    problems: list[str],
    truncated: bool = False,
) -> SectionExecution:
    """Mark the section failed with its reasons on the row. The run continues."""
    section.status = SectionStatus.FAILED
    section.content = None
    section.confidence = None
    section.low_confidence_reason = " ".join(problems)[:2000] or None
    _log.warning(
        "custom_section.failed",
        section=section.section_key,
        attempts=attempts,
        problems=problems,
    )
    return SectionExecution(
        section=section,
        status=SectionStatus.FAILED,
        attempts=attempts,
        evidence_truncated=truncated,
        problems=problems,
    )


def _policy_of(pin: PlanSkillPin, context: AgentContext) -> dict[str, Any]:
    """The pin's snapshot as one mapping — what gate 1 approved, not a recomposition."""
    return {
        "min_sources": pin.min_sources if pin.min_sources is not None else 1,
        "requires_primary": bool(pin.requires_primary),
        "max_tier": pin.max_tier if pin.max_tier is not None else 5,
        "allow_forward_looking": (
            pin.allow_forward_looking if pin.allow_forward_looking is not None else True
        ),
        "token_budget": (
            pin.token_budget
            if pin.token_budget is not None
            else context.settings.custom_section_token_ceiling
        ),
    }


async def _gather(
    context: AgentContext,
    *,
    pin: PlanSkillPin,
    request: ResearchRequest,
    budget: int,
    evidence_job_id: uuid.UUID,
) -> _Evidence:
    """Assemble what this section may see, gated by the pinned grant, inside the budget.

    Deterministic on purpose: the granted tools decide which categories are assembled,
    and code enumerates what the run already holds rather than a model asking round by
    round — §2.12 gives the generation exactly one call, and a section's evidence should
    not depend on a model thinking to ask for it.
    """
    granted = set(pin.granted_tools or [])
    units: list[_Unit] = []
    session = context.session

    if "search_facts" in granted:
        facts = await session.scalars(
            select(FinancialFact)
            .join(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
            .where(SourceDocument.request_id == request.id)
            .order_by(FinancialFact.period_end.desc(), FinancialFact.concept)
            .limit(EVIDENCE_ITEM_CAP)
        )
        for row in facts:
            identifier = str(row.id)
            source_id = str(row.source_document_id)
            units.append(
                _Unit(
                    internal={
                        "fact_id": identifier,
                        "concept": row.concept,
                        "value": str(row.value),
                        "unit": row.unit,
                        "period_end": row.period_end.isoformat(),
                        "source_document_id": source_id,
                    },
                    fact_source=(identifier, source_id),
                )
            )

        # Recorded calculations travel with the facts: both are the deterministic
        # layer's own figures, and the §2.12 numeral rule is unusable without them.
        calculations = await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == evidence_job_id)
            .order_by(Calculation.sequence)
            .limit(EVIDENCE_ITEM_CAP)
        )
        for calc in calculations:
            identifier = str(calc.id)
            units.append(
                _Unit(
                    internal={
                        "calculation_id": identifier,
                        "name": calc.name,
                        "value": str(calc.output_value),
                        "unit": calc.output_unit,
                    },
                    calculation_id=identifier,
                )
            )

    if "search_sources" in granted:
        max_tier = _policy_of(pin, context)["max_tier"]
        sources = list(
            await session.scalars(
                select(SourceDocument)
                .where(
                    SourceDocument.request_id == request.id,
                    SourceDocument.quarantined.is_(False),
                )
                .order_by(SourceDocument.retrieved_at.desc())
                .limit(EVIDENCE_ITEM_CAP)
            )
        )
        admissible = [source for source in sources if source.source_tier.rank <= max_tier]
        for source in admissible:
            identifier = str(source.id)
            units.append(
                _Unit(
                    internal={
                        "source_document_id": identifier,
                        "tier": source.source_tier.value,
                        "publication_date": (
                            source.publication_date.isoformat() if source.publication_date else None
                        ),
                    },
                    source_tier=(identifier, source.source_tier),
                )
            )

        if admissible:
            extractions = await session.scalars(
                select(Extraction)
                .where(Extraction.source_document_id.in_([source.id for source in admissible]))
                .order_by(Extraction.created_at)
                .limit(EVIDENCE_ITEM_CAP)
            )
            tier_by_source = {str(source.id): source.source_tier.value for source in admissible}
            for extraction in extractions:
                source_id = str(extraction.source_document_id)
                extraction_id = str(extraction.id)
                units.append(
                    _Unit(
                        internal={
                            "extraction_id": extraction_id,
                            "source_document_id": source_id,
                        },
                        # The excerpt is fetched-document text: it reaches the model only
                        # in the untrusted channel, labelled with the ids to cite it by.
                        untrusted={
                            "source_document_id": source_id,
                            "tier": tier_by_source[source_id],
                            "title": f"extraction {extraction_id}",
                            "text": extraction.excerpt,
                        },
                        extraction_source=(extraction_id, source_id),
                    )
                )

    # `fetch_known_url` may be granted but has nothing bound in this slice — the
    # allowlist grants the capability, the run decides the availability, exactly as for
    # the research workers.

    return _within_budget(units, budget=budget)


def _within_budget(units: list[_Unit], *, budget: int) -> _Evidence:
    """The evidence the budget admits, whole units at a time, in gathering order.

    Compact id-bearing listings were gathered first and the bulky excerpts last, so the
    excerpts are the natural overflow. A dropped unit drops entirely — listing, excerpt
    and index entry together — so an id the model was not shown is also an id the
    validator refuses.
    """
    evidence = _Evidence()
    spent = 0
    for unit in units:
        cost = unit.cost
        if spent + cost > budget:
            evidence.truncated = True
            continue
        spent += cost
        evidence.admit(unit)
    return evidence


def _validate(
    draft: CustomSectionDraft,
    *,
    contract: dict[str, Any],
    evidence: _Evidence,
    pin: PlanSkillPin,
) -> list[str]:
    """Everything a draft must satisfy before anything is recorded. Empty means sound."""
    problems = contract_violations(draft.content, contract)

    for index, claim in enumerate(draft.claims, start=1):
        if claim.financial_fact_id is not None and claim.financial_fact_id not in (
            evidence.fact_sources
        ):
            problems.append(
                f"Claim {index} names fact {claim.financial_fact_id!r}, which this "
                "section's evidence does not hold."
            )
        if claim.calculation_id is not None and claim.calculation_id not in (
            evidence.calculation_ids
        ):
            problems.append(
                f"Claim {index} names calculation {claim.calculation_id!r}, which this "
                "run does not hold."
            )
        for citation in claim.citations:
            expected = evidence.extraction_sources.get(citation.extraction_id)
            if expected is None:
                problems.append(
                    f"Claim {index} cites extraction {citation.extraction_id!r}, which "
                    "this section's evidence does not hold."
                )
            elif expected != citation.source_document_id:
                problems.append(
                    f"Claim {index} cites extraction {citation.extraction_id!r} against "
                    f"source {citation.source_document_id!r}, but that extraction "
                    f"belongs to source {expected!r}."
                )
        if claim.kind == "forward_looking" and not _policy_allows_forward_looking(pin):
            problems.append(
                f"Claim {index} is forward-looking, and this section's composed policy "
                "does not admit forward-looking support."
            )

    covered = [claim.statement for claim in draft.claims if claim.kind == "numeric"]
    problems.extend(unsourced_numerals(draft.content, covered))
    return problems


def _policy_allows_forward_looking(pin: PlanSkillPin) -> bool:
    return pin.allow_forward_looking if pin.allow_forward_looking is not None else True


def _policy_shortfalls(
    cited_source_ids: set[str], *, evidence: _Evidence, policy: dict[str, Any]
) -> list[str]:
    """Where the recorded evidence falls short of the composed policy, named.

    A shortfall degrades — banner, low confidence — and never blocks: §2.12's ladder is
    explicit that thin evidence is presented as thin, not padded until it looks thick.
    """
    shortfalls: list[str] = []
    distinct = len(cited_source_ids)
    if distinct < policy["min_sources"]:
        shortfalls.append(
            f"This section's policy requires {policy['min_sources']} distinct source(s); "
            f"its claims cite {distinct}."
        )
    if policy["requires_primary"]:
        tiers = [
            evidence.source_tiers.get(source_id) or _tier_from_fact_source(source_id, evidence)
            for source_id in cited_source_ids
        ]
        if not any(tier is not None and tier.is_primary for tier in tiers):
            shortfalls.append(
                "This section's policy requires at least one primary source (tier 1 or "
                "2); none of its cited evidence is primary."
            )
    return shortfalls


def _tier_from_fact_source(source_id: str, evidence: _Evidence) -> SourceTier | None:
    """The tier of a source cited only through a fact.

    Facts arrive through the acquisition pipeline, which records their documents'
    tiers on the sources listing when `search_sources` is granted; a fact-only grant
    leaves the tier unknown, and unknown does not count as primary.
    """
    return evidence.source_tiers.get(source_id)


def _confidence(content: dict[str, Any], *, degraded: bool) -> float:
    declared = content.get("confidence")
    chosen = (
        float(declared)
        if isinstance(declared, (int, float))
        and not isinstance(declared, bool)
        and 0 <= float(declared) <= 1
        else 0.5
    )
    # §2.12: findings under an insufficiency banner are marked low-confidence, whatever
    # the model thought of them.
    return min(chosen, 0.3) if degraded else chosen


def _degradation_note(shortfalls: list[str], *, truncated: bool) -> str | None:
    """The banner text, §2.12's own words first so a reader cannot mistake the state."""
    notes = []
    if shortfalls:
        notes.append("Insufficient evidence: " + " ".join(shortfalls))
    if truncated:
        notes.append(
            "The evidence offered to this section was truncated to its token budget; "
            "the analysis rests on what remained."
        )
    return " ".join(notes) if notes else None


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value is not None else None
