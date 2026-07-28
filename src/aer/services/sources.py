"""Recording provenance, and refusing what cannot be dated.

A source document says where a set of bytes came from and what may be done with it. This
module writes those records and applies the one admissibility rule that can be decided at
acquisition time:

**Under point-in-time rules, a source whose publication date is unknown is quarantined.**

That rule is the whole defence against look-ahead bias at the boundary where it is
cheapest to enforce. A document that cannot be dated cannot be shown to have existed
before the request's as-of date, so it cannot honestly support a claim made as at that
date. It might be from last week; it might be from after the quarter that is being
analysed, in which case a report citing it would be quietly using information nobody had
at the time.

The document is **kept**, not discarded. Losing it would erase the record of what the run
looked at, and "we saw this and refused to use it" is a more useful audit trail than
silence. It is flagged instead, so nothing downstream can cite it by accident.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import Provider, SourceTier
from aer.db.models import Artefact, AuditEvent, ResearchRequest, SourceDocument
from aer.db.models.source_document import NO_PUBLICATION_DATE
from aer.errors import ValidationError

__all__ = [
    "NOT_CITABLE",
    "NO_PUBLICATION_DATE",
    "QuarantineDecision",
    "decide_quarantine",
    "record_source_document",
]

_log = structlog.get_logger("aer.services.sources")

NOT_CITABLE = "tier_not_citable"
"""Quarantine reason for a tier that may never be cited as evidence."""


@dataclass(frozen=True, slots=True)
class QuarantineDecision:
    """Whether a source is admissible, and why not if it is not."""

    quarantined: bool
    reason: str | None = None


def decide_quarantine(
    *,
    publication_date: date | None,
    point_in_time: bool,
    source_tier: SourceTier,
) -> QuarantineDecision:
    """Decide admissibility from the facts alone.

    A pure function, separate from the write, so the rule can be tested exhaustively
    without a database and read without tracing through a service call.

    Order matters. An undatable source is quarantined for *that* reason first, because it
    is the reason the operator can act on — supplying a date makes it admissible, whereas
    a tier-6 source is inadmissible whatever its date.
    """
    if point_in_time and publication_date is None:
        return QuarantineDecision(
            quarantined=True,
            reason=NO_PUBLICATION_DATE,
        )
    if not source_tier.is_citable:
        return QuarantineDecision(quarantined=True, reason=NOT_CITABLE)
    return QuarantineDecision(quarantined=False)


async def record_source_document(
    session: AsyncSession,
    *,
    request: ResearchRequest,
    artefact: Artefact,
    url: str,
    provider: Provider,
    source_tier: SourceTier,
    retrieved_at: datetime | None = None,
    canonical_url: str | None = None,
    title: str | None = None,
    publisher: str | None = None,
    publication_date: date | None = None,
    publication_date_confidence: float | None = None,
    http_status: int | None = None,
    licence_note: str | None = None,
    robots_allowed: bool | None = None,
    job_id: uuid.UUID | None = None,
) -> SourceDocument:
    """Record where an artefact came from, applying the admissibility rules.

    ``retrieved_at`` defaults to now. It is a parameter rather than always the clock so
    that a replayed or backfilled acquisition can record when it actually happened rather
    than when it was written down.

    Raises:
        ValidationError: If ``retrieved_at`` is naive. A provenance timestamp without a
            timezone is ambiguous by up to a day, which is exactly the precision a
            point-in-time decision depends on.
    """
    moment = retrieved_at or datetime.now(UTC)
    if moment.tzinfo is None:
        message = (
            "retrieved_at must be timezone-aware. A provenance timestamp without an "
            "offset cannot be compared against an as-of date without guessing."
        )
        raise ValidationError(message, context={"url": url})

    decision = decide_quarantine(
        publication_date=publication_date,
        point_in_time=request.point_in_time,
        source_tier=source_tier,
    )

    document = SourceDocument(
        request_id=request.id,
        job_id=job_id,
        artefact_id=artefact.id,
        url=url,
        canonical_url=canonical_url,
        title=title,
        publisher=publisher,
        provider=provider,
        source_tier=source_tier,
        publication_date=publication_date,
        publication_date_confidence=publication_date_confidence,
        retrieved_at=moment.astimezone(UTC),
        http_status=http_status,
        licence_note=licence_note,
        robots_allowed=robots_allowed,
        quarantined=decision.quarantined,
        quarantine_reason=decision.reason,
    )
    session.add(document)
    await session.flush()

    if decision.quarantined:
        # Audited, not merely logged. "What did this run refuse to use, and why?" is a
        # question a reviewer will ask about a report, and the answer has to survive the
        # process that decided it.
        previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
        session.add(
            AuditEvent.create_linked(
                actor="system",
                event_type="source.quarantined",
                payload={
                    "source_document_id": str(document.id),
                    "url": url,
                    "provider": provider.value,
                    "source_tier": source_tier.value,
                    "reason": decision.reason,
                },
                previous=previous,
                request_id=request.id,
                job_id=job_id,
            )
        )
        await session.flush()

    _log.info(
        "source.recorded",
        source_document_id=str(document.id),
        provider=provider.value,
        source_tier=source_tier.value,
        quarantined=decision.quarantined,
        quarantine_reason=decision.reason,
    )
    return document


async def list_quarantined(session: AsyncSession, *, request_id: uuid.UUID) -> list[SourceDocument]:
    """Every source this request gathered but may not cite."""
    result = await session.scalars(
        select(SourceDocument)
        .where(SourceDocument.request_id == request_id, SourceDocument.quarantined)
        .order_by(SourceDocument.created_at)
    )
    return list(result.all())
