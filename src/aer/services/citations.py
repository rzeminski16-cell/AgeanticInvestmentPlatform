"""Recording what a report asserts, and refusing to let an unsupported claim through a gate.

Three operations and one rule.

The rule is §2.9's: **a numeric or factual claim needs at least one citation that is either
verified or consciously overridden.** Forward-looking statements and opinions need a stated
basis instead, which is a different requirement and is checked differently — an opinion with a
citation attached is not better supported than one without, and treating it as though it were
is how a hedge starts reading like a finding.

This module does not verify anything. Verification lives in :mod:`aer.verify.citations`, which
owns the only write to ``excerpt_verified``; what happens here is deciding what the verdicts
*mean* for a gate, and recording a human's decision to proceed anyway.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.core.enums import CITATION_REQUIRING_CLAIMS, ClaimKind
from aer.db.models import Citation, Claim, ReportSection, User
from aer.errors import ConflictError, ValidationError

__all__ = [
    "EvidenceReview",
    "override_citation",
    "record_citation",
    "record_claim",
    "review_evidence",
]

_log = structlog.get_logger("aer.services.citations")


@dataclass(frozen=True, slots=True)
class EvidenceReview:
    """What a run's evidence looks like as a gate sees it."""

    claims: int
    citations: int
    verified: int
    overridden: int
    unsupported: tuple[Claim, ...]
    unverified: tuple[Citation, ...]

    @property
    def is_admissible(self) -> bool:
        return not self.unsupported and not self.unverified

    def as_message(self) -> str:
        """One sentence naming what is wrong, for the pause a gate raises."""
        parts: list[str] = []
        if self.unsupported:
            parts.append(
                f"{len(self.unsupported)} claim(s) have no admissible citation — a numeric or "
                "factual statement with nothing behind it cannot go in a report"
            )
        if self.unverified:
            parts.append(
                f"{len(self.unverified)} citation(s) did not verify against the document they "
                "name. Each can be overridden individually with a written reason, which is "
                "recorded; there is no way to wave all of them through at once"
            )
        return ". ".join(parts) + "."


async def record_claim(
    session: AsyncSession,
    *,
    section: ReportSection,
    kind: ClaimKind,
    text: str,
    financial_fact_id: uuid.UUID | None = None,
    calculation_id: uuid.UUID | None = None,
) -> Claim:
    """Write down one assertion a section makes.

    Raises:
        ValidationError: A numeric claim that does not name exactly one figure. The database
            refuses this too, and the check is repeated here so the message says which rule
            was broken rather than quoting a constraint name.
    """
    named = (financial_fact_id is not None) + (calculation_id is not None)
    if kind is ClaimKind.NUMERIC and named != 1:
        message = (
            "A numeric claim must name exactly one figure — either a financial fact or a "
            f"calculation, not {named}. No number reaches a report unless something computed "
            "or reported it."
        )
        raise ValidationError(message, context={"kind": kind.value, "figures_named": named})
    if kind is not ClaimKind.NUMERIC and named:
        message = (
            f"A {kind.value} claim must not name a figure. Attaching one to a statement nothing "
            "checks would make it look verified to every reader downstream."
        )
        raise ValidationError(message, context={"kind": kind.value, "figures_named": named})

    claim = Claim(
        report_section_id=section.id,
        kind=kind,
        text=text,
        financial_fact_id=financial_fact_id,
        calculation_id=calculation_id,
    )
    session.add(claim)
    await session.flush()
    return claim


async def record_citation(
    session: AsyncSession,
    *,
    claim: Claim,
    source_document_id: uuid.UUID,
    extraction_id: uuid.UUID,
) -> Citation:
    """Attach a proposed citation to a claim.

    **Unverified, always.** There is no argument to this function that could make it otherwise,
    which is the point: a caller — including one acting on a model's suggestion — can propose
    a citation and cannot confirm one.
    """
    citation = Citation(
        claim_id=claim.id,
        source_document_id=source_document_id,
        extraction_id=extraction_id,
    )
    session.add(citation)
    await session.flush()
    return citation


async def review_evidence(session: AsyncSession, *, job_id: uuid.UUID) -> EvidenceReview:
    """Assess a run's claims and citations against §2.9's rule.

    Reads verdicts; it does not produce them. Call
    :func:`aer.verify.citations.verify_job_citations` first, or this reports every citation as
    unverified — which is the correct reading of "nobody has checked it".
    """
    claims = list(
        await session.scalars(
            select(Claim)
            .join(ReportSection, ReportSection.id == Claim.report_section_id)
            .where(ReportSection.job_id == job_id)
            .options(selectinload(Claim.citations))
            .order_by(Claim.created_at, Claim.id)
        )
    )

    citations = [citation for claim in claims for citation in claim.citations]
    unsupported = tuple(
        claim
        for claim in claims
        if claim.kind in CITATION_REQUIRING_CLAIMS
        and not any(citation.is_admissible for citation in claim.citations)
    )

    return EvidenceReview(
        claims=len(claims),
        citations=len(citations),
        verified=sum(1 for c in citations if c.excerpt_verified),
        overridden=sum(1 for c in citations if not c.excerpt_verified and c.is_admissible),
        unsupported=unsupported,
        unverified=tuple(c for c in citations if not c.is_admissible),
    )


async def override_citation(
    session: AsyncSession,
    *,
    citation: Citation,
    actor: User,
    reason: str,
) -> Citation:
    """Accept an unverified citation, on the record.

    **This does not set ``excerpt_verified``.** The check failed and the report will go on
    saying so; what is added is that a named person decided to proceed anyway and why. Both
    facts belong in the output, and collapsing them into one boolean would let an override
    read as a verification to everything downstream.

    Raises:
        ValidationError: The reason is empty. An override with no justification records a
            click, not a decision.
        ConflictError: The citation verified. There is nothing to override, and permitting it
            would put a reason in the record implying doubt that the evidence does not support.
    """
    if not reason.strip():
        message = "An override needs a written reason. Without one it records a click."
        raise ValidationError(message, context={"citation_id": str(citation.id)})

    if citation.excerpt_verified:
        message = (
            "This citation verified against its source, so there is nothing to override. "
            "Recording a reason against it would imply a doubt the evidence does not support."
        )
        raise ConflictError(message, context={"citation_id": str(citation.id)})

    citation.override_reason = reason.strip()
    citation.overridden_by_user_id = actor.id
    citation.overridden_at = datetime.now(UTC)
    await session.flush()

    _log.info(
        "citation.overridden",
        citation_id=str(citation.id),
        actor_id=str(actor.id),
        match_ratio=str(citation.match_ratio),
    )
    return citation
