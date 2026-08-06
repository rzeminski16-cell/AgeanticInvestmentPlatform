"""What one section's drafting call may see, cite and get away with — shared by design.

Built-in sections (task 45, ADR 0042) and custom sections (ADR 0037) are drafted under
one discipline: code assembles the evidence inside the section's token budget, the model
proposes exactly one draft per attempt, and deterministic validation decides what is
recorded. This module is that discipline, factored to a policy rather than to either
caller — the custom boundary in :mod:`aer.skills.execution` derives its
:class:`SectionPolicy` from the pin gate 1 approved, the built-in boundary in
:mod:`aer.sections.writing` derives it from the definition row, and neither can drift
from the other because there is nothing else to drift to.

Three properties the sharing preserves:

* **Truncation keeps or drops a unit whole.** An excerpt without its id row would be
  uncitable; an id row without its excerpt would invite a citation of text the model
  never read. The validation maps are built from the survivors only, so an id the budget
  dropped genuinely does not exist for that call.
* **Fetched text reaches the model only in the untrusted channel**, labelled with the
  ids to cite it by.
* **A draft's claims may name only ids the call was shown.** The evidence index is the
  closed world; everything outside it is refused before anything is recorded.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from aer.core.enums import ClaimKind, SourceTier
from aer.core.section_output import contract_violations, unsourced_numerals
from aer.db.models import (
    Calculation,
    Extraction,
    FinancialFact,
    ReportSection,
    ResearchRequest,
    SectionStatus,
    SourceDocument,
)
from aer.services.citations import record_citation, record_claim

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.agents.custom_section import CustomSectionDraft

__all__ = [
    "EVIDENCE_ITEM_CAP",
    "MAX_GENERATION_ATTEMPTS",
    "Evidence",
    "EvidenceUnit",
    "SectionExecution",
    "SectionPolicy",
    "confidence_of",
    "content_source_ids",
    "degradation_note",
    "gather_evidence",
    "policy_shortfalls",
    "record_draft_claims",
    "validate_draft",
]

# §2.12: one structured-output call, one retry on a validation failure, then failed.
MAX_GENERATION_ATTEMPTS: Final = 2

# Rows per evidence category before the token budget is even consulted. Bounds the
# queries; the budget bounds the composition.
EVIDENCE_ITEM_CAP: Final = 40

# The estimate the truncation works in. Four characters per token matches the fake
# provider's arithmetic and is close enough for a *budget* — the role's hard input cap is
# still enforced against a real count at the provider boundary.
_CHARS_PER_TOKEN: Final = 4


@dataclass(frozen=True, slots=True)
class SectionPolicy:
    """One section's floor and budget, whoever set them.

    For a custom section these are the pin's snapshot — what gate 1 approved, not a
    recomposition. For a built-in they are the definition row. Either way, by the time
    they are here they are just numbers, which is the point: the drafting discipline
    cannot tell operator-authored policy from platform policy and so cannot privilege
    either.
    """

    min_sources: int
    requires_primary: bool
    max_tier_rank: int
    allow_forward_looking: bool
    token_budget: int

    def as_prompt_payload(self) -> dict[str, Any]:
        """What the model is told about the floor. The budget is not the model's business."""
        return {
            "min_sources": self.min_sources,
            "requires_primary": self.requires_primary,
            "max_tier": self.max_tier_rank,
            "allow_forward_looking": self.allow_forward_looking,
        }


@dataclass(slots=True)
class SectionExecution:
    """What executing one section came to — every outcome, as data."""

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
class EvidenceUnit:
    """One indivisible piece of evidence: its listing, its excerpt, and its index entry."""

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
class Evidence:
    """What one call may see and cite, built from the units the budget kept."""

    internal: list[dict[str, Any]] = field(default_factory=list)
    untrusted: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False

    fact_sources: dict[str, str] = field(default_factory=dict)
    calculation_ids: set[str] = field(default_factory=set)
    source_tiers: dict[str, SourceTier] = field(default_factory=dict)
    extraction_sources: dict[str, str] = field(default_factory=dict)

    def admit(self, unit: EvidenceUnit) -> None:
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


async def gather_evidence(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    evidence_job_id: uuid.UUID,
    policy: SectionPolicy,
    categories: frozenset[str],
) -> Evidence:
    """Assemble what a section may see, category by category, inside the budget.

    Deterministic on purpose: ``categories`` decides which listings are assembled — a
    custom section's pinned tool grant, the full set for a built-in — and code enumerates
    what the run already holds rather than a model asking round by round. §2.12 gives the
    generation exactly one call, and a section's evidence should not depend on a model
    thinking to ask for it.
    """
    units: list[EvidenceUnit] = []

    if "search_facts" in categories:
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
                EvidenceUnit(
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
        # layer's own figures, and the numeral rule is unusable without them.
        calculations = await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == evidence_job_id)
            .order_by(Calculation.sequence)
            .limit(EVIDENCE_ITEM_CAP)
        )
        for calc in calculations:
            identifier = str(calc.id)
            units.append(
                EvidenceUnit(
                    internal={
                        "calculation_id": identifier,
                        "name": calc.name,
                        "value": str(calc.output_value),
                        "unit": calc.output_unit,
                    },
                    calculation_id=identifier,
                )
            )

    if "search_sources" in categories:
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
        admissible = [
            source for source in sources if source.source_tier.rank <= policy.max_tier_rank
        ]
        for source in admissible:
            identifier = str(source.id)
            units.append(
                EvidenceUnit(
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
                    EvidenceUnit(
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

    evidence = _within_budget(units, budget=policy.token_budget)

    # The tier index, always, over every one of the request's sources — including ones
    # the listings excluded (quarantined, or over the section's ceiling). Not an evidence
    # listing: nothing here reaches the prompt. It answers "how authoritative is the
    # thing this content cites", which the primary-source shortfall check needs even when
    # the cited source arrived through a fact rather than through the sources listing.
    tier_rows = await session.execute(
        select(SourceDocument.id, SourceDocument.source_tier).where(
            SourceDocument.request_id == request.id
        )
    )
    for source_id, tier in tier_rows:
        evidence.source_tiers.setdefault(str(source_id), tier)

    return evidence


def _within_budget(units: list[EvidenceUnit], *, budget: int) -> Evidence:
    """The evidence the budget admits, whole units at a time, in gathering order.

    Compact id-bearing listings were gathered first and the bulky excerpts last, so the
    excerpts are the natural overflow. A dropped unit drops entirely — listing, excerpt
    and index entry together — so an id the model was not shown is also an id the
    validator refuses.
    """
    evidence = Evidence()
    spent = 0
    for unit in units:
        cost = unit.cost
        if spent + cost > budget:
            evidence.truncated = True
            continue
        spent += cost
        evidence.admit(unit)
    return evidence


def validate_draft(
    draft: CustomSectionDraft,
    *,
    contract: dict[str, Any],
    evidence: Evidence,
    policy: SectionPolicy,
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
        if claim.kind == "forward_looking" and not policy.allow_forward_looking:
            problems.append(
                f"Claim {index} is forward-looking, and this section's policy does not "
                "admit forward-looking support."
            )

    # Content ids live under the same closed world as claim ids. A figure row naming a
    # calculation is how a numeral carries lineage without a claim (the built-in
    # convention the renderer footnotes), so an id the call was never shown must be
    # refused here or "names its figure" would cover fabrications.
    problems.extend(_content_id_violations(draft.content, evidence=evidence))

    covered = [claim.statement for claim in draft.claims if claim.kind == "numeric"]
    problems.extend(unsourced_numerals(draft.content, covered))
    return problems


def _content_id_violations(content: dict[str, Any], *, evidence: Evidence) -> list[str]:
    known_sources = set(evidence.source_tiers) | set(evidence.fact_sources.values())
    checks: dict[str, Any] = {
        "calculation_id": lambda value: value in evidence.calculation_ids,
        "financial_fact_id": lambda value: value in evidence.fact_sources,
        "source_document_id": lambda value: value in known_sources,
        "extraction_id": lambda value: value in evidence.extraction_sources,
    }
    problems: list[str] = []
    for path, key, value in _ids_by_path(content, path="content"):
        if not checks[key](value):
            problems.append(
                f"{path} names {key} {value!r}, which this section's evidence does not hold."
            )
    return problems


def _ids_by_path(value: Any, *, path: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                str(key)
                in {"calculation_id", "financial_fact_id", "source_document_id", "extraction_id"}
                and isinstance(item, str)
                and item
            ):
                found.append((f"{path}.{key}", str(key), item))
            else:
                found.extend(_ids_by_path(item, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_ids_by_path(item, path=f"{path}[{index}]"))
    return found


def content_source_ids(content: dict[str, Any]) -> set[str]:
    """Every source document the content itself cites, by the renderer's own key.

    Counted towards a section's evidence floor alongside the sources its claims cite —
    the same reading the coverage metric takes — so a section whose figures cite through
    content rows is not reported as citing nothing.
    """
    return {
        value
        for _, key, value in _ids_by_path(content, path="content")
        if key == "source_document_id"
    }


async def record_draft_claims(
    session: AsyncSession,
    *,
    section: ReportSection,
    draft: CustomSectionDraft,
    evidence: Evidence,
) -> tuple[int, set[str]]:
    """Record a validated draft's claims and citations. Returns (count, cited source ids).

    Runs only after :func:`validate_draft` came back empty, so every id here resolves;
    the cited-source set feeds the policy-shortfall check, with a fact's own source
    counting for the claim that names the fact.
    """
    recorded = 0
    cited_source_ids: set[str] = set()
    for proposal in draft.claims:
        claim = await record_claim(
            session,
            section=section,
            kind=ClaimKind(proposal.kind),
            text=proposal.statement,
            financial_fact_id=_uuid_or_none(proposal.financial_fact_id),
            calculation_id=_uuid_or_none(proposal.calculation_id),
        )
        for citation in proposal.citations:
            await record_citation(
                session,
                claim=claim,
                source_document_id=uuid.UUID(citation.source_document_id),
                extraction_id=uuid.UUID(citation.extraction_id),
            )
            cited_source_ids.add(citation.source_document_id)
        if proposal.financial_fact_id is not None:
            cited_source_ids.add(evidence.fact_sources[proposal.financial_fact_id])
        recorded += 1
    return recorded, cited_source_ids


def policy_shortfalls(
    cited_source_ids: set[str], *, evidence: Evidence, policy: SectionPolicy
) -> list[str]:
    """Where the recorded evidence falls short of the policy floor, named.

    A shortfall degrades — banner, low confidence — and never blocks: §2.12's ladder is
    explicit that thin evidence is presented as thin, not padded until it looks thick.
    """
    shortfalls: list[str] = []
    distinct = len(cited_source_ids)
    if distinct < policy.min_sources:
        shortfalls.append(
            f"This section's policy requires {policy.min_sources} distinct source(s); "
            f"its claims cite {distinct}."
        )
    if policy.requires_primary:
        tiers = [evidence.source_tiers.get(source_id) for source_id in cited_source_ids]
        if not any(tier is not None and tier.is_primary for tier in tiers):
            shortfalls.append(
                "This section's policy requires at least one primary source (tier 1 or "
                "2); none of its cited evidence is primary."
            )
    return shortfalls


def confidence_of(content: dict[str, Any], *, degraded: bool) -> float:
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


def degradation_note(shortfalls: list[str], *, truncated: bool) -> str | None:
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
