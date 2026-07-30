"""Recording where an excerpt was found.

Two operations, and one property that matters more than either: **recording the same span
twice is not two pieces of evidence.** A resumed run re-extracts a document it already
extracted, and without idempotency each attempt would add another copy of the same sentence —
inflating every "sources consulted" count and putting duplicate footnotes in a report.

The uniqueness key is `(source_document, extractor, extractor_version, locator)`, and the
extractor version is in it on purpose. The same character range means something different after
the extractor changes, so those are genuinely different rows rather than a collision.

**The excerpt is stored as the extraction found it.** Not normalised, not trimmed. Whitespace
normalisation belongs at comparison time, in the verifier, where both sides get the same
treatment; doing it here would mean the stored excerpt no longer matched the document it came
from, and the stored copy is what a reviewer is shown.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import ExtractionKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.schemas.extraction import Excerpt, ExtractedText, Locator
from aer.db.models import Extraction

__all__ = ["locator_hash", "record_excerpt", "record_excerpts"]

_log = structlog.get_logger("aer.services.extractions")


def locator_hash(locator: Locator) -> str:
    """The canonical hash of a locator.

    Canonical JSON, so the hash depends on the locator's *values* and not on Python's dict
    ordering or on which optional fields happened to be set to ``None``. The same span hashed
    on two machines must collide, or the uniqueness constraint stops preventing duplicates.
    """
    return sha256_hex(canonical_json(locator.model_dump(mode="json", exclude_none=True)))


async def record_excerpt(
    session: AsyncSession,
    *,
    source_document_id: uuid.UUID,
    extracted: ExtractedText,
    excerpt: Excerpt,
    kind: ExtractionKind = ExtractionKind.TEXT,
) -> Extraction:
    """Store one located excerpt, returning the existing row if it is already there.

    Returns the row either way rather than a "was it new?" flag: a caller needs the id to hang
    a citation off, and whether this attempt or a previous one created it is not information
    anything downstream acts on.
    """
    digest = locator_hash(excerpt.locator)

    statement = (
        pg_insert(Extraction)
        .values(
            source_document_id=source_document_id,
            kind=kind,
            extractor=extracted.extractor,
            extractor_version=extracted.extractor_version,
            locator=excerpt.locator.model_dump(mode="json", exclude_none=True),
            locator_hash=digest,
            excerpt=excerpt.text,
            content_hash=extracted.content_hash,
        )
        .on_conflict_do_nothing(
            index_elements=("source_document_id", "extractor", "extractor_version", "locator_hash")
        )
        .returning(Extraction)
    )

    inserted = (await session.scalars(statement)).one_or_none()
    if inserted is not None:
        _log.debug(
            "extraction.recorded",
            source_document_id=str(source_document_id),
            extractor=extracted.extractor,
            characters=excerpt.locator.length,
        )
        return inserted

    # `DO NOTHING` returns no row on a collision, so the existing one is fetched by the same
    # key the constraint uses. Two statements rather than one, and still preferable to
    # select-then-insert: the constraint remains the thing that guarantees uniqueness, so two
    # workers racing here cannot both succeed.
    existing = await session.scalar(
        select(Extraction).where(
            Extraction.source_document_id == source_document_id,
            Extraction.extractor == extracted.extractor,
            Extraction.extractor_version == extracted.extractor_version,
            Extraction.locator_hash == digest,
        )
    )
    if existing is None:  # pragma: no cover -- the row was deleted between the two statements
        message = "The extraction was neither inserted nor found; it was removed concurrently."
        raise RuntimeError(message)
    return existing


async def record_excerpts(
    session: AsyncSession,
    *,
    source_document_id: uuid.UUID,
    extracted: ExtractedText,
    excerpts: list[Excerpt],
    kind: ExtractionKind = ExtractionKind.TEXT,
) -> list[Extraction]:
    """Store several excerpts from one extraction, in order.

    One statement each rather than a batched insert, because the batched form cannot return
    the pre-existing rows on conflict and callers need every id. Excerpt counts are in the
    dozens per document — this is not the fact-persistence path, where the count is five
    figures and batching is the difference between working and not.
    """
    return [
        await record_excerpt(
            session,
            source_document_id=source_document_id,
            extracted=extracted,
            excerpt=excerpt,
            kind=kind,
        )
        for excerpt in excerpts
    ]
