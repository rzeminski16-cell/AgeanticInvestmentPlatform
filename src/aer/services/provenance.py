"""The read side of the evidence chain: what a run gathered, and what each claim rests on.

Everything downstream of a report is a question about provenance — *where did this number
come from, and can I see it?* — and this module answers it once, for both surfaces. The
JSON API and the server-rendered pages call the same functions, so "what the API says" and
"what the page shows" cannot drift into two different accounts of the same evidence.

**Nothing here decides anything.** Admissibility, verification and quarantine were all
decided earlier, by code that owns those rules. This assembles what was decided into a
shape a reader can look at, and it must not soften any of it: a quarantined source is
returned quarantined, with its reason, and an unverified citation is returned unverified,
with the ratio it actually achieved and the error it actually produced. A viewer that
quietly omitted the failures would make the whole chain decorative.

**Ownership is not checked here.** The route knows who is asking; this takes an id it has
already been told the caller may see. Putting the user check in both places would mean two
implementations of it, and the one that drifted would be the one nobody was testing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.core.enums import ClaimKind, Provider, SourceTier
from aer.db.models import (
    Artefact,
    Calculation,
    Citation,
    Claim,
    Extraction,
    FinancialFact,
    ReportSection,
    SectionDefinition,
    SourceDocument,
)

__all__ = [
    "CitationView",
    "ClaimSummary",
    "ClaimView",
    "ExcerptView",
    "FigureView",
    "SourceView",
    "claim_view",
    "claims_for_run",
    "sources_for_run",
]

# How much of a digest a page prints. Enough to pick the artefact out of a run, short
# enough to read; the full value is in the record and in the JSON.
HASH_PREFIX = 12


@dataclass(frozen=True, slots=True)
class SourceView:
    """One acquired document, with everything that decides whether it may be relied on."""

    id: uuid.UUID
    url: str
    canonical_url: str | None
    title: str | None
    publisher: str | None
    provider: Provider
    source_tier: SourceTier

    publication_date: date | None
    publication_date_latest: date | None
    publication_date_confidence: float | None
    publication_date_source: str | None
    retrieved_at: datetime

    sha256: str
    media_type: str | None
    size_bytes: int | None

    http_status: int | None
    licence_note: str | None
    robots_allowed: bool | None

    quarantined: bool
    quarantine_reason: str | None
    override_reason: str | None
    overridden_at: datetime | None

    injection_flagged: bool
    injection_findings: tuple[Any, ...]

    excerpt_count: int

    @property
    def is_admissible(self) -> bool:
        """Whether this source may support a claim.

        Mirrors :attr:`aer.db.models.source_document.SourceDocument.is_admissible` rather
        than re-deriving it, so the page and the rule cannot disagree.
        """
        return not self.quarantined or self.override_reason is not None

    @property
    def short_hash(self) -> str:
        return self.sha256[:HASH_PREFIX]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "publisher": self.publisher,
            "provider": self.provider.value,
            "source_tier": self.source_tier.value,
            "tier_rank": self.source_tier.rank,
            "publication_date": _iso(self.publication_date),
            "publication_date_latest": _iso(self.publication_date_latest),
            "publication_date_confidence": self.publication_date_confidence,
            "publication_date_source": self.publication_date_source,
            "retrieved_at": self.retrieved_at.isoformat(),
            "sha256": self.sha256,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "http_status": self.http_status,
            "licence_note": self.licence_note,
            "robots_allowed": self.robots_allowed,
            "quarantined": self.quarantined,
            "quarantine_reason": self.quarantine_reason,
            "override_reason": self.override_reason,
            "overridden_at": _iso(self.overridden_at),
            "admissible": self.is_admissible,
            "injection_flagged": self.injection_flagged,
            "injection_findings": list(self.injection_findings),
            "excerpt_count": self.excerpt_count,
        }


@dataclass(frozen=True, slots=True)
class ExcerptView:
    """One located passage of a document, as stored."""

    id: uuid.UUID
    kind: str
    extractor: str
    extractor_version: str
    locator: dict[str, Any]
    excerpt: str
    content_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "kind": self.kind,
            "extractor": self.extractor,
            "extractor_version": self.extractor_version,
            "locator": self.locator,
            "excerpt": self.excerpt,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class CitationView:
    """A claim's link to one passage, and what the verifier made of it."""

    id: uuid.UUID
    excerpt: ExcerptView
    source: SourceView

    verified: bool
    verification_method: str | None
    match_ratio: Decimal | None
    verification_error: str | None
    verified_at: datetime | None

    override_reason: str | None
    overridden_at: datetime | None

    @property
    def is_admissible(self) -> bool:
        return self.verified or self.override_reason is not None

    @property
    def state(self) -> str:
        """One word for the badge, and the three states are genuinely different.

        ``verified`` means code re-read the artefact and found the excerpt.
        ``overridden`` means it did not, and a person accepted it anyway. ``unverified``
        means neither. Collapsing the middle one into either neighbour would lose the
        distinction a reader most needs.
        """
        if self.verified:
            return "verified"
        if self.override_reason is not None:
            return "overridden"
        return "unverified"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "state": self.state,
            "verified": self.verified,
            "verification_method": self.verification_method,
            "match_ratio": str(self.match_ratio) if self.match_ratio is not None else None,
            "verification_error": self.verification_error,
            "verified_at": _iso(self.verified_at),
            "override_reason": self.override_reason,
            "overridden_at": _iso(self.overridden_at),
            "admissible": self.is_admissible,
            "excerpt": self.excerpt.as_dict(),
            "source": self.source.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class FigureView:
    """The figure a numeric claim asserts, and where it came from.

    Either a stored fact or a recorded calculation — invariant 3 permits nothing else, and
    a claim carrying neither is a number with no lineage, which the schema already refuses.
    """

    kind: str
    id: uuid.UUID
    label: str
    value: str
    unit: str
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ClaimSummary:
    """A claim as it appears in a list."""

    id: uuid.UUID
    kind: ClaimKind
    text: str
    section_key: str
    citation_count: int
    verified_count: int

    @property
    def is_supported(self) -> bool:
        """Whether this claim would pass the gate's evidence check.

        Only claim kinds that *require* a citation can be unsupported; an opinion with no
        citation is not a defect, and marking it as one would train a reader to ignore the
        badge.
        """
        if self.kind not in (ClaimKind.NUMERIC, ClaimKind.FACTUAL):
            return True
        return self.verified_count > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "kind": self.kind.value,
            "text": self.text,
            "section_key": self.section_key,
            "citation_count": self.citation_count,
            "verified_count": self.verified_count,
            "supported": self.is_supported,
        }


@dataclass(frozen=True, slots=True)
class ClaimView:
    """One claim, everything it cites, and the figure it asserts."""

    id: uuid.UUID
    kind: ClaimKind
    text: str
    section_key: str
    section_title: str
    job_id: uuid.UUID
    figure: FigureView | None
    citations: tuple[CitationView, ...]

    @property
    def is_supported(self) -> bool:
        if self.kind not in (ClaimKind.NUMERIC, ClaimKind.FACTUAL):
            return True
        return any(citation.is_admissible for citation in self.citations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "kind": self.kind.value,
            "text": self.text,
            "section_key": self.section_key,
            "section_title": self.section_title,
            "job_id": str(self.job_id),
            "supported": self.is_supported,
            "figure": self.figure.as_dict() if self.figure else None,
            "citations": [citation.as_dict() for citation in self.citations],
        }


# -- Sources ---------------------------------------------------------------------------------


async def sources_for_run(session: AsyncSession, job_id: uuid.UUID) -> list[SourceView]:
    """Everything this run acquired, most authoritative first, then most recent.

    Ordered by tier because that is the order a reader assesses evidence in, and a table
    sorted by acquisition time would put a blog above a filing for no reason other than
    that it was fetched first.
    """
    rows = await session.scalars(
        select(SourceDocument)
        .where(SourceDocument.job_id == job_id)
        .options(selectinload(SourceDocument.artefact), selectinload(SourceDocument.extractions))
        .order_by(SourceDocument.source_tier, SourceDocument.retrieved_at.desc())
    )
    return [_source_view(row) for row in rows]


def _source_view(row: SourceDocument) -> SourceView:
    artefact: Artefact | None = row.artefact
    return SourceView(
        id=row.id,
        url=row.url,
        canonical_url=row.canonical_url,
        title=row.title,
        publisher=row.publisher,
        provider=row.provider,
        source_tier=row.source_tier,
        publication_date=row.publication_date,
        publication_date_latest=row.publication_date_latest,
        publication_date_confidence=row.publication_date_confidence,
        publication_date_source=row.publication_date_source,
        retrieved_at=row.retrieved_at,
        sha256=artefact.sha256 if artefact else "",
        media_type=artefact.media_type if artefact else None,
        size_bytes=artefact.size_bytes if artefact else None,
        http_status=row.http_status,
        licence_note=row.licence_note,
        robots_allowed=row.robots_allowed,
        quarantined=row.quarantined,
        quarantine_reason=row.quarantine_reason,
        override_reason=row.admissibility_override_reason,
        overridden_at=row.admissibility_overridden_at,
        injection_flagged=row.injection_flagged,
        injection_findings=tuple(row.injection_findings or ()),
        excerpt_count=len(row.extractions),
    )


def _excerpt_view(row: Extraction) -> ExcerptView:
    return ExcerptView(
        id=row.id,
        kind=row.kind.value,
        extractor=row.extractor,
        extractor_version=row.extractor_version,
        locator=dict(row.locator),
        excerpt=row.excerpt,
        content_hash=row.content_hash,
    )


# -- Claims ----------------------------------------------------------------------------------


async def claims_for_run(session: AsyncSession, job_id: uuid.UUID) -> list[ClaimSummary]:
    """Every claim this run's sections assert, in section order.

    The list a reader arrives at from a report. Section order rather than claim id, because
    the report is the thing they were just looking at.
    """
    rows = await session.scalars(
        select(Claim)
        .join(ReportSection, ReportSection.id == Claim.report_section_id)
        .where(ReportSection.job_id == job_id)
        .options(selectinload(Claim.citations), selectinload(Claim.section))
        .order_by(ReportSection.position, Claim.created_at, Claim.id)
    )
    return [
        ClaimSummary(
            id=row.id,
            kind=row.kind,
            text=row.text,
            section_key=row.section.section_key,
            citation_count=len(row.citations),
            verified_count=sum(1 for c in row.citations if c.is_admissible),
        )
        for row in rows
    ]


async def claim_view(session: AsyncSession, claim_id: uuid.UUID) -> ClaimView | None:
    """One claim with its evidence resolved: the excerpt, the source, the figure.

    This is the drill-down. Everything a reader needs to disagree with a sentence is on it,
    including the parts that did not go well — an unverified citation appears here with the
    ratio it reached and the error it produced, because "we could not confirm this" is the
    most important thing such a page can say.
    """
    claim = await session.scalar(
        select(Claim)
        .where(Claim.id == claim_id)
        .options(
            selectinload(Claim.section),
            selectinload(Claim.citations).selectinload(Citation.extraction),
            selectinload(Claim.citations)
            .selectinload(Citation.source_document)
            .selectinload(SourceDocument.artefact),
            selectinload(Claim.citations)
            .selectinload(Citation.source_document)
            .selectinload(SourceDocument.extractions),
        )
    )
    if claim is None:
        return None

    section = claim.section
    definition = await session.get(SectionDefinition, section.section_definition_id)

    return ClaimView(
        id=claim.id,
        kind=claim.kind,
        text=claim.text,
        section_key=section.section_key,
        section_title=definition.title if definition is not None else section.section_key,
        job_id=section.job_id,
        figure=await _figure_view(session, claim),
        citations=tuple(
            CitationView(
                id=citation.id,
                excerpt=_excerpt_view(citation.extraction),
                source=_source_view(citation.source_document),
                verified=citation.excerpt_verified,
                verification_method=citation.verification_method,
                match_ratio=citation.match_ratio,
                verification_error=citation.verification_error,
                verified_at=citation.verified_at,
                override_reason=citation.override_reason,
                overridden_at=citation.overridden_at,
            )
            for citation in sorted(claim.citations, key=lambda c: (c.created_at, c.id))
        ),
    )


async def _figure_view(session: AsyncSession, claim: Claim) -> FigureView | None:
    """The stored fact or recorded calculation a numeric claim names."""
    if claim.calculation_id is not None:
        calculation = await session.get(Calculation, claim.calculation_id)
        if calculation is None:  # pragma: no cover -- RESTRICT prevents this
            return None
        return FigureView(
            kind="calculation",
            id=calculation.id,
            label=calculation.name,
            value=str(calculation.output_value),
            unit=calculation.output_unit,
            detail={
                "formula": calculation.formula,
                "function_ref": calculation.function_ref,
                "code_version": calculation.code_version,
                "inputs": list(calculation.inputs),
            },
        )

    if claim.financial_fact_id is not None:
        fact = await session.get(FinancialFact, claim.financial_fact_id)
        if fact is None:  # pragma: no cover -- RESTRICT prevents this
            return None
        return FigureView(
            kind="financial_fact",
            id=fact.id,
            label=fact.concept,
            value=str(fact.value),
            unit=fact.unit,
            detail={
                "concept": fact.concept,
                "raw_concept": fact.raw_concept,
                "period_end": fact.period_end.isoformat(),
                "fiscal_period": fact.fiscal_period,
                "filed_date": fact.filed_date.isoformat(),
                "basis": fact.basis.value,
                "form": fact.form,
                "accession": fact.accession,
            },
        )

    return None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
