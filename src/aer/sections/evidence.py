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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from aer.core.concepts import is_canonical_concept
from aer.core.enums import AnalysisMode, ClaimKind, SourceTier
from aer.core.section_output import (
    MAX_GAP_SENTENCES,
    contract_violations,
    gap_sentences,
    prose_word_count,
    unsourced_numerals,
)
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

# How a request's depth scales the drafting budgets (gap O5). Quick reads and writes
# roughly half, full half as much again; standard is the calibrated baseline.
_MODE_FACTORS: Final[dict[AnalysisMode, float]] = {
    AnalysisMode.QUICK: 0.6,
    AnalysisMode.STANDARD: 1.0,
    AnalysisMode.FULL: 1.4,
}

# Rows per evidence category before the token budget is even consulted. Bounds the
# queries; the budget bounds the composition.
EVIDENCE_ITEM_CAP: Final = 40

# The pools the ranking chooses from. Wider than the caps above on purpose: ranking can
# only surface what the query returned, and a live large-cap run proved that a pool the
# size of the cap makes the *ordering clause* the real selector (gap A39 — forty facts
# ordered newest-period-then-alphabetically delivered "Accrued…, Accumulated…,
# AvailableForSale…" to every section, and Revenue never survived the alphabet).
_FACT_POOL: Final = 400
_EXCERPT_POOL: Final = 200

# The fiscal_period value a full-year fact carries — the same marker the analysis pass
# filters on. A section declaring fact_basis "annual" sees only these; "interim" sees
# only the rest.
_ANNUAL_PERIOD: Final = "FY"

# How much of a section's token budget the compact listings may consume, leaving the
# remainder for excerpts. Without the reservation the listings — gathered first because
# they are id-bearing and cheap — starved every excerpt out of the composition, and the
# writers were left describing prose they were never shown.
_FACT_BUDGET_SHARE: Final = 0.45
_LISTING_BUDGET_SHARE: Final = 0.6

# The estimate the truncation works in. Four characters per token matches the fake
# provider's arithmetic and is close enough for a *budget* — the role's hard input cap is
# still enforced against a real count at the provider boundary.
_CHARS_PER_TOKEN: Final = 4

# -- What a section most wants to see (gap A39) ----------------------------------------------
#
# Deterministic relevance, not a model's: facts are ranked by where their canonical
# concept sits in the section's declared preference order, excerpts by keyword affinity
# with the section's subject. The preferences are **data on the section definition's
# ``evidence_policy``** (migration 0029) — sections are rows, and the hardcoded-key
# guard rightly refused the first cut that keyed them in code. A policy that declares
# none — every custom section — gets the default ordering below, which still puts the
# statements' own lines ahead of footnote debris.

_INCOME_CONCEPTS: Final[tuple[str, ...]] = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_expenses",
    "sg_and_a",
    "research_and_development",
    "operating_income",
    "interest_expense",
    "interest_income",
    "pre_tax_income",
    "income_tax_expense",
    "net_income",
    "earnings_per_share_basic",
    "earnings_per_share_diluted",
)
_BALANCE_CONCEPTS: Final[tuple[str, ...]] = (
    "cash_and_equivalents",
    "short_term_investments",
    "accounts_receivable",
    "inventory",
    "current_assets",
    "property_plant_and_equipment",
    "goodwill",
    "intangible_assets",
    "assets",
    "accounts_payable",
    "deferred_revenue",
    "short_term_debt",
    "current_liabilities",
    "long_term_debt",
    "lease_liabilities",
    "liabilities",
    "total_debt",
    "retained_earnings",
    "equity",
)
_CASH_FLOW_CONCEPTS: Final[tuple[str, ...]] = (
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capital_expenditure",
    "depreciation_and_amortisation",
    "share_based_compensation",
    "share_repurchases",
    "dividends_paid",
    "proceeds_from_debt",
    "repayments_of_debt",
    "net_change_in_cash",
)
_SHARE_CONCEPTS: Final[tuple[str, ...]] = (
    "shares_outstanding",
    "diluted_shares_outstanding",
    "basic_shares_outstanding",
    "dividends_per_share",
)

_DEFAULT_CONCEPT_ORDER: Final[tuple[str, ...]] = (
    _INCOME_CONCEPTS + _BALANCE_CONCEPTS + _CASH_FLOW_CONCEPTS + _SHARE_CONCEPTS
)

# Text a filing carries that no analysis rests on. An excerpt matching one of these is
# administrative furniture — the live failure delivered a signature block to fourteen
# sections in a row, each of which then truthfully reported it had nothing to work with.
_NON_SUBSTANTIVE_MARKERS: Final[tuple[str, ...]] = (
    "pursuant to the requirements of the securities exchange act",
    "duly caused this report to be signed",
    "thereunto duly authorized",
    "duly authorised",
    "power of attorney",
    "signature page",
)


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

    # The section's evidence preferences, read from its definition row (migration 0029).
    # Empty means the module's default concept order and no keyword affinity — which is
    # every custom section, and every definition seeded before the row carried them.
    concept_priority: tuple[str, ...] = ()
    excerpt_keywords: tuple[str, ...] = ()

    # Which reporting basis this section's facts should come from: "annual" for the
    # sections whose argument is built on full-year figures (the history, the earnings
    # quality signals — both computed FY-only), "interim" for one that wants only the
    # newest quarters, "any" for the rest. The live report put a quarterly revenue
    # against an annual EBITDA in one sentence because nothing declared this.
    fact_basis: str = "any"

    # The section's target length in words, stated to the model and refused in code past
    # a headroom factor (gap O4). Zero means unbounded — every definition seeded before
    # the row carried one, and every custom section.
    word_budget: int = 0

    def as_prompt_payload(self) -> dict[str, Any]:
        """What the model is told about the floor. The budget is not the model's business."""
        payload = {
            "min_sources": self.min_sources,
            "requires_primary": self.requires_primary,
            "max_tier": self.max_tier_rank,
            "allow_forward_looking": self.allow_forward_looking,
            "fact_basis": self.fact_basis,
        }
        if self.word_budget > 0:
            # The one budget the model is told: it is asked to write to it, and the
            # ceiling that refuses an overrun is enforced in `validate_draft`.
            payload["target_words"] = self.word_budget
        return payload

    def scaled(self, mode: AnalysisMode) -> SectionPolicy:
        """This policy at a request's depth (gap O5): budgets multiplied, floors kept.

        Quick runs read and write less, full runs more; the evidence floor and the tier
        ceiling never move, because depth is how much work a run does — not how little
        support a claim may stand on.
        """
        factor = _MODE_FACTORS[mode]
        if factor == 1:
            return self
        return replace(
            self,
            token_budget=max(1, int(self.token_budget * factor)),
            word_budget=int(self.word_budget * factor),
        )


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
    """Assemble what a section may see, ranked by relevance, inside the budget.

    Deterministic on purpose: ``categories`` decides which listings are assembled — a
    custom section's pinned tool grant, the full set for a built-in — and code enumerates
    what the run already holds rather than a model asking round by round. §2.12 gives the
    generation exactly one call, and a section's evidence should not depend on a model
    thinking to ask for it.

    Ranked on purpose too (gap A39). A live large-cap run held 18,588 facts and 69
    excerpts and still starved every section: forty facts chosen newest-period-then-
    alphabetically delivered footnote debris and never Revenue, excerpts chosen
    oldest-first delivered the same signature page to every section, and the compact
    listings consumed the budget so the excerpts were pure overflow. Selection is now the
    section's: facts by the section's concept preference then recency, excerpts by
    keyword affinity with the section's subject, and the listings capped to a share of
    the budget so the excerpts always keep a seat. The preferences are the policy's —
    data from the section's definition row — and a policy declaring none falls back to
    the default order, which still puts the statements' own lines ahead of the alphabet.
    """
    units: list[EvidenceUnit] = []
    concept_rank = _concept_rank_for(policy.concept_priority)
    fact_budget = int(policy.token_budget * _FACT_BUDGET_SHARE)
    listing_budget = int(policy.token_budget * _LISTING_BUDGET_SHARE)
    spent_on_listings = 0

    if "search_facts" in categories:
        selection = (
            select(FinancialFact)
            .join(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
            # Consolidated figures only. A fact item carries no dimension field, so a
            # segment's slice in the pack would be indistinguishable from the company's
            # line — and a writer citing it would state a fraction as the whole.
            .where(SourceDocument.request_id == request.id, FinancialFact.dimension_axis.is_(None))
        )
        # The section's declared basis, applied in the query rather than after ranking:
        # a history section that wants annual figures should spend its whole fact budget
        # on them, not on whatever quarterly rows out-ranked them on recency.
        if policy.fact_basis == "annual":
            selection = selection.where(FinancialFact.fiscal_period == _ANNUAL_PERIOD)
        elif policy.fact_basis == "interim":
            selection = selection.where(FinancialFact.fiscal_period != _ANNUAL_PERIOD)
        pool = list(
            await session.scalars(
                selection.order_by(FinancialFact.period_end.desc(), FinancialFact.concept).limit(
                    _FACT_POOL
                )
            )
        )
        pool.sort(key=lambda row: (concept_rank(row.concept), _period_recency(row)))
        for row in pool:
            identifier = str(row.id)
            source_id = str(row.source_document_id)
            unit = EvidenceUnit(
                internal={
                    "fact_id": identifier,
                    "concept": row.concept,
                    "value": str(row.value),
                    "unit": row.unit,
                    # The full span, not just its end. A June quarter and a nine-month
                    # year-to-date share a period_end, and the live report compared one
                    # against an annual ratio without any of the three saying which
                    # basis it was on.
                    "period_start": row.period_start.isoformat() if row.period_start else None,
                    "period_end": row.period_end.isoformat(),
                    "fiscal_period": row.fiscal_period,
                    "fiscal_year": row.fiscal_year,
                    "source_document_id": source_id,
                },
                fact_source=(identifier, source_id),
            )
            if spent_on_listings + unit.cost > fact_budget:
                break
            spent_on_listings += unit.cost
            units.append(unit)

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
            unit = EvidenceUnit(
                internal={
                    "calculation_id": identifier,
                    "name": calc.name,
                    "value": str(calc.output_value),
                    "unit": calc.output_unit,
                    # "FY2025", or None for a figure that is not of any statement
                    # period. Beside the facts' fiscal fields this is what lets the
                    # writer put an annual ratio and a quarterly fact in one sentence
                    # without presenting them as the same basis.
                    "period": calc.period_label,
                },
                calculation_id=identifier,
            )
            if spent_on_listings + unit.cost > listing_budget:
                break
            spent_on_listings += unit.cost
            units.append(unit)

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
                        # What this source actually is, in its recorded words. A live
                        # report described twenty footnotes of XBRL-aggregate figures as
                        # "a single primary filing, the Form 10-Q" (gap R12): the writer
                        # was shown ids and tiers and invented the rest, and a wrong
                        # description of a right citation is still a wrong sentence.
                        "title": source.title or source.url,
                        "tier": source.source_tier.value,
                        "publication_date": (
                            source.publication_date.isoformat() if source.publication_date else None
                        ),
                    },
                    source_tier=(identifier, source.source_tier),
                )
            )

        if admissible:
            extractions = list(
                await session.scalars(
                    select(Extraction)
                    .where(Extraction.source_document_id.in_([source.id for source in admissible]))
                    .order_by(Extraction.created_at)
                    .limit(_EXCERPT_POOL)
                )
            )
            keywords = policy.excerpt_keywords
            substantive = [row for row in extractions if _is_substantive(row.excerpt)]
            ranked = sorted(
                enumerate(substantive),
                key=lambda item: (-_keyword_hits(item[1].excerpt, keywords), item[0]),
            )
            tier_by_source = {str(source.id): source.source_tier.value for source in admissible}
            for _, extraction in ranked:
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


def _concept_rank_for(declared: tuple[str, ...]) -> Callable[[str], int]:
    """How early a fact's concept sits in this section's preference order.

    Preferred canonical concepts rank by their position; canonical concepts the section
    did not name come next — the statements' own lines still beat the alphabet — and
    everything else (unmapped tags, footnote debris) ranks last. The live failure is the
    argument: 18,588 facts ordered alphabetically within the newest period delivered
    "Accrued…, Accumulated…, AvailableForSale…" to every section and Revenue to none.
    """
    order = declared or _DEFAULT_CONCEPT_ORDER
    preferred = {concept: index for index, concept in enumerate(order)}
    after_preferred = len(order)

    def rank(concept: str) -> int:
        found = preferred.get(concept)
        if found is not None:
            return found
        if is_canonical_concept(concept):
            return after_preferred
        return after_preferred + 1

    return rank


def _period_recency(row: FinancialFact) -> float:
    """Newest period first, as a sort key ascending."""
    return -row.period_end.toordinal()


def _is_substantive(excerpt: str) -> bool:
    """Whether an excerpt carries anything an analysis could rest on.

    A signature block or a power of attorney is administrative furniture: the live run
    delivered one to fourteen sections in a row, and each spent budget faithfully
    reporting that it said nothing. Filtered at gathering rather than at extraction so
    already-extracted runs benefit too.
    """
    lowered = excerpt.lower()
    return not any(marker in lowered for marker in _NON_SUBSTANTIVE_MARKERS)


def _keyword_hits(excerpt: str, keywords: tuple[str, ...]) -> int:
    """How many of the section's keywords the excerpt mentions. Zero when it has none."""
    lowered = excerpt.lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def _within_budget(units: list[EvidenceUnit], *, budget: int) -> Evidence:
    """The evidence the budget admits, whole units at a time, in gathering order.

    Compact id-bearing listings were gathered first — already capped to a share of the
    budget — and the ranked excerpts last, so what overflows is the *least relevant*
    excerpt rather than every excerpt. A dropped unit drops entirely — listing, excerpt
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


# The refusal line sits above the stated budget, as the claim ceilings do: the budget is
# the instruction, the ceiling is the rule, and the gap between them is what stops a
# draft two words over from costing a retry.
_WORD_CEILING_FACTOR: Final = 1.25


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

    # The gap budget (R4). A live report spent a third of its prose describing absent
    # disclosure — honestly, and uselessly: rule 6 said "one clause and move on", nothing
    # enforced it, and advisory rules drift. One sentence per section may be about what
    # is missing; the rest must be about the company.
    gaps = gap_sentences(draft.content)
    if len(gaps) > MAX_GAP_SENTENCES:
        shown = "; ".join(f"{sentence[:80]!r}" for sentence in gaps[:4])
        problems.append(
            f"{len(gaps)} sentences describe missing evidence ({shown}). At most "
            f"{MAX_GAP_SENTENCES} is allowed: state the gap in one clause and spend the "
            "rest of the section on what the evidence does support."
        )

    if policy.word_budget > 0:
        words = prose_word_count(draft.content)
        ceiling = int(policy.word_budget * _WORD_CEILING_FACTOR)
        if words > ceiling:
            problems.append(
                f"The content runs to {words} words against this section's budget of "
                f"{policy.word_budget}. Cut it to the budget: keep the analysis, drop "
                "the restatement and the narration."
            )
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


def degradation_note(shortfalls: list[str]) -> str | None:
    """The banner text, §2.12's own words first so a reader cannot mistake the state.

    Evidence shortfalls only — deliberately not truncation (gap R2). The truncation
    sentence printed under every section of a live report, because every section's
    evidence now exceeds its budget; a banner that always shows carries no information,
    and "token budget" is the platform talking to its operator in the reader's document.
    The fact itself survives where the people watching the run look: the execution
    outcome, the step output and the structured log all carry ``evidence_truncated``.
    """
    if not shortfalls:
        return None
    return "Insufficient evidence: " + " ".join(shortfalls)


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value is not None else None
