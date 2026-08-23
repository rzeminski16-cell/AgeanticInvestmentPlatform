"""The deterministic half of the red team: what it sees, what survives, what is recorded.

The agent attacks; this module decides what it was allowed to see and what its challenges
are worth. Three responsibilities, each a control:

**The input is built from tables, not from context.** Claims come from the run's
``claims`` rows, the evidence index from ``financial_facts``, ``calculations`` and
``source_documents``. Section prose and worker findings are never loaded here, and the
input type has no field that could carry them — the structural isolation ADR 0039
records.

**Cited ids are verified in code.** A challenge citing an id the run does not hold is
rejected — not retried, not trimmed — because a challenge is one atomic argument and an
argument resting partly on fabricated evidence is not an argument with a weak footnote,
it is a fabrication with good ones.

**Every surviving challenge becomes a ``disagreements`` row** through the task 19
ladder's thesis rung: escalated to gate 2, never auto-resolved, both positions published.
Materiality follows severity — a severity-4 or worse challenge raises the §2.4 banner, a
quibble is recorded without one — and recording is idempotent on the challenge's own
content, so a re-run neither duplicates nor silently drops what the adversary said.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.agents.base import AgentContext
from aer.agents.red_team import (
    ClaimRecord,
    RedTeamAgent,
    RedTeamChallenge,
    RedTeamInput,
)
from aer.core.disagreement import (
    THESIS_UNIT,
    DisagreementKind,
    Position,
    UnresolvableDisagreementError,
    thesis_conflict,
)
from aer.core.enums import FactBasis, SourceTier
from aer.core.hashing import sha256_hex
from aer.db.models import (
    Calculation,
    Claim,
    Disagreement,
    FinancialFact,
    Job,
    ReportSection,
    ResearchRequest,
    SourceDocument,
)
from aer.services.disagreements import record_resolution
from aer.services.subject import subject_name

__all__ = ["EVIDENCE_ITEM_CAP", "MATERIAL_SEVERITY", "RedTeamOutcome", "run_red_team"]

_log = structlog.get_logger("aer.services.red_team")

# A challenge at this severity or above materially contradicts the thesis — the §2.4
# banner state task 41's escalation engine gates on. Below it the challenge is still
# escalated, recorded and published; it just does not claim the thesis is in danger.
MATERIAL_SEVERITY: Final = 4

# Rows per evidence category in the index. The same bound the other evidence-assembling
# services use: enough to argue from, small enough to stay inside the role's input cap.
EVIDENCE_ITEM_CAP: Final = 40


@dataclass(slots=True)
class RedTeamOutcome:
    """What the adversary's turn came to, as the workflow step records it."""

    skipped: bool = False
    challenges: int = 0
    recorded: list[Disagreement] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    coverage_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "skipped": self.skipped,
            "challenges": self.challenges,
            "recorded": len(self.recorded),
            "rejected": list(self.rejected),
            "coverage_note": self.coverage_note,
        }


@dataclass(slots=True)
class _EvidenceIndex:
    """What the run holds, in the form the input carries and validation checks."""

    facts: list[dict[str, Any]] = field(default_factory=list)
    calculations: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)

    fact_sources: dict[str, str] = field(default_factory=dict)
    calculation_ids: set[str] = field(default_factory=set)
    source_tiers: dict[str, SourceTier] = field(default_factory=dict)


async def run_red_team(
    context: AgentContext,
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    use_batch: bool = True,
) -> RedTeamOutcome:
    """Run the adversary over the draft's claims and record what survives.

    Skips — visibly, spending nothing — when the draft recorded no claims: a red team
    with nothing asserted to attack would be challenging prose it cannot see, and the
    honest outcome is a step that says so.

    Args:
        use_batch: Route the call through the provider's batch path (§1.8 prices the
            bear case there). The sync path exists for parity testing and produces
            identical rows, which is tested rather than assumed.
    """
    claims = await _claims_for_job(session, job.id)
    if not claims:
        _log.info("red_team.skipped", job_id=str(job.id), reason="no recorded claims")
        return RedTeamOutcome(skipped=True, coverage_note="The draft recorded no claims.")

    index = await _evidence_index(session, job=job, request=request)
    payload = RedTeamInput(
        # The filer's own name, not the one typed into the form (gap A67).
        company_name=await subject_name(session, request),
        ticker=request.ticker,
        as_of_date=request.as_of_date.isoformat(),
        claims=claims,
        facts=index.facts,
        calculations=index.calculations,
        sources=index.sources,
    )

    agent = RedTeamAgent()
    if use_batch:
        report = (await agent.run_batch(context, [payload]))[0]
    else:
        report = await agent.run(context, payload)

    outcome = RedTeamOutcome(challenges=len(report.challenges), coverage_note=report.coverage_note)
    base = _base_position(index, request=request, job_id=job.id)

    for number, challenge in enumerate(report.challenges, start=1):
        problem = _unresolvable_evidence(challenge, index)
        if problem is not None:
            outcome.rejected.append(f"challenge {number} ({challenge.dimension.value}): {problem}")
            _log.warning(
                "red_team.challenge_rejected",
                job_id=str(job.id),
                dimension=challenge.dimension.value,
                problem=problem,
            )
            continue

        row = await _record_challenge(
            session, job_id=job.id, base=base, challenge=challenge, index=index, request=request
        )
        outcome.recorded.append(row)

    _log.info(
        "red_team.finished",
        job_id=str(job.id),
        challenges=outcome.challenges,
        recorded=len(outcome.recorded),
        rejected=len(outcome.rejected),
        material=[row.topic for row in outcome.recorded if row.material],
    )
    return outcome


# ==========================================================================================
# What the adversary sees
# ==========================================================================================


async def _claims_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[ClaimRecord]:
    rows = await session.execute(
        select(Claim, ReportSection.section_key)
        .join(ReportSection, ReportSection.id == Claim.report_section_id)
        .where(ReportSection.job_id == job_id)
        .order_by(Claim.created_at, Claim.id)
    )
    return [
        ClaimRecord(
            claim_id=str(claim.id),
            section_key=section_key,
            kind=claim.kind.value,
            text=claim.text[:600],
        )
        for claim, section_key in rows
    ]


async def _evidence_index(
    session: AsyncSession, *, job: Job, request: ResearchRequest
) -> _EvidenceIndex:
    index = _EvidenceIndex()

    facts = await session.scalars(
        select(FinancialFact)
        .join(SourceDocument, SourceDocument.id == FinancialFact.source_document_id)
        # Consolidated figures only: a segment's slice listed here would read as the
        # company's own line, and the red team would flag the aggregate as wrong.
        .where(SourceDocument.work_order_id == request.id, FinancialFact.dimension_axis.is_(None))
        .order_by(FinancialFact.period_end.desc(), FinancialFact.concept)
        .limit(EVIDENCE_ITEM_CAP)
    )
    for row in facts:
        identifier = str(row.id)
        index.fact_sources[identifier] = str(row.source_document_id)
        index.facts.append(
            {
                "fact_id": identifier,
                "concept": row.concept,
                "value": str(row.value),
                "unit": row.unit,
                "period_end": row.period_end.isoformat(),
            }
        )

    calculations = await session.scalars(
        select(Calculation)
        .where(Calculation.job_id == job.id)
        .order_by(Calculation.sequence)
        .limit(EVIDENCE_ITEM_CAP)
    )
    for calc in calculations:
        identifier = str(calc.id)
        index.calculation_ids.add(identifier)
        index.calculations.append(
            {
                "calculation_id": identifier,
                "name": calc.name,
                "value": str(calc.output_value),
                "unit": calc.output_unit,
            }
        )

    sources = await session.scalars(
        select(SourceDocument)
        .where(SourceDocument.work_order_id == request.id)
        .order_by(SourceDocument.retrieved_at)
        .limit(EVIDENCE_ITEM_CAP)
    )
    for source in sources:
        identifier = str(source.id)
        index.source_tiers[identifier] = source.source_tier
        index.sources.append(
            {
                "source_document_id": identifier,
                "tier": source.source_tier.value,
                "publication_date": (
                    source.publication_date.isoformat() if source.publication_date else None
                ),
                "quarantined": source.quarantined,
            }
        )

    return index


# ==========================================================================================
# What survives, and how it lands
# ==========================================================================================


def _unresolvable_evidence(challenge: RedTeamChallenge, index: _EvidenceIndex) -> str | None:
    """The first cited id this run does not hold, or ``None`` when they all resolve."""
    for identifier in challenge.fact_ids:
        if identifier not in index.fact_sources:
            return f"cites fact {identifier!r}, which this run does not hold"
    for identifier in challenge.calculation_ids:
        if identifier not in index.calculation_ids:
            return f"cites calculation {identifier!r}, which this run does not hold"
    for identifier in challenge.source_document_ids:
        if identifier not in index.source_tiers:
            return f"cites source document {identifier!r}, which this run does not hold"
    return None


async def _record_challenge(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    base: Position,
    challenge: RedTeamChallenge,
    index: _EvidenceIndex,
    request: ResearchRequest,
) -> Disagreement:
    """One challenge onto the ladder's thesis rung, idempotently.

    The challenge position's reference digests the statement, so the same challenge
    re-recorded by a retried step is one row, while two different challenges on the same
    dimension are two.
    """
    challenge_position = Position(
        reference=f"red_team:{challenge.dimension.value}:{sha256_hex(challenge.statement)[:12]}",
        label=(
            f"Red team challenge ({challenge.dimension.value}, severity {challenge.severity}/5)"
        ),
        value=Decimal(0),
        unit=THESIS_UNIT,
        tier=_best_tier(challenge, index, fallback=base.tier),
        filed_date=request.as_of_date,
        basis=FactBasis.AS_REPORTED,
    )

    resolution = thesis_conflict(
        first=base,
        second=challenge_position,
        topic=f"{challenge.dimension.value}: {challenge.statement}",
        material=challenge.severity >= MATERIAL_SEVERITY,
    )

    # The challenge's own argument and evidence travel as a record beside the ladder's
    # rationale, never composed into it (gap R5): the appendix lays these out as columns
    # and footnotes, and a report reader should meet an id only as a footnote. A cited
    # fact is footnoted through the document it came from — a fact id names a row in
    # this platform's own tables, which is provenance no reader can follow.
    sources = list(challenge.source_document_ids)
    for fact_id in challenge.fact_ids:
        source_id = index.fact_sources.get(fact_id)
        if source_id is not None and source_id not in sources:
            sources.append(source_id)
    detail = {
        "challenge": challenge.statement,
        "basis": challenge.basis,
        "severity": challenge.severity,
        "dimension": challenge.dimension.value,
        "evidence": {
            "facts": list(challenge.fact_ids),
            "calculations": list(challenge.calculation_ids),
            "sources": sources,
        },
    }

    row = await record_resolution(
        session,
        job_id=job_id,
        topic=f"Red team ({challenge.dimension.value}): {challenge.statement[:120]}",
        kind=DisagreementKind.THESIS_CONFLICT,
        resolution=resolution,
        detail=detail,
    )
    if row is None:  # pragma: no cover -- the thesis rung always escalates, always records
        message = "The thesis rung produced no row, which the ladder makes impossible."
        raise UnresolvableDisagreementError(message)
    return row


def _base_position(
    index: _EvidenceIndex, *, request: ResearchRequest, job_id: uuid.UUID
) -> Position:
    """The base thesis as a ladder position.

    The tier is the best among the run's sources — the thesis stands on all its
    evidence — and the numeric fields are placeholders the thesis rung never compares.
    """
    best = min(
        (tier for tier in index.source_tiers.values()),
        key=lambda tier: tier.rank,
        default=SourceTier.T6_UNVERIFIED,
    )
    return Position(
        reference=f"draft:{job_id}",
        label="Base thesis (the draft's recorded claims)",
        value=Decimal(0),
        unit=THESIS_UNIT,
        tier=best,
        filed_date=request.as_of_date,
        basis=FactBasis.AS_REPORTED,
    )


def _best_tier(
    challenge: RedTeamChallenge, index: _EvidenceIndex, *, fallback: SourceTier
) -> SourceTier:
    """The most authoritative tier behind a challenge's cited evidence."""
    tiers: list[SourceTier] = []
    for fact_id in challenge.fact_ids:
        source_id = index.fact_sources.get(fact_id)
        if source_id is not None and source_id in index.source_tiers:
            tiers.append(index.source_tiers[source_id])
    for source_id in challenge.source_document_ids:
        if source_id in index.source_tiers:
            tiers.append(index.source_tiers[source_id])
    if not tiers:
        return fallback
    return min(tiers, key=lambda tier: tier.rank)
