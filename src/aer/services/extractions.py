"""Recording where an excerpt was found — and, since ADR 0119, which one may be printed.

Two write operations, and one property that matters more than either: **recording the same
span twice is not two pieces of evidence.** A resumed run re-extracts a document it already
extracted, and without idempotency each attempt would add another copy of the same sentence —
inflating every "sources consulted" count and putting duplicate footnotes in a report.

The uniqueness key is `(source_document, extractor, extractor_version, locator)`, and the
extractor version is in it on purpose. The same character range means something different after
the extractor changes, so those are genuinely different rows rather than a collision.

**The excerpt is stored as the extraction found it.** Not normalised, not trimmed. Whitespace
normalisation belongs at comparison time, in the verifier, where both sides get the same
treatment; doing it here would mean the stored excerpt no longer matched the document it came
from, and the stored copy is what a reviewer is shown.

**The read side answers one question: may this passage be printed, and which passage is it.**
ADR 0119 gates a printed excerpt on three conditions — the licence permits verbatim
reproduction, the source carries no injection signal, and it is an excerpt rather than a page
— and :func:`printable_excerpts` is where all three are applied together. Nothing that
renders may apply two of them and forget the third, because there is one function and it takes
no arguments that could switch one off.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.core.enums import ExtractionKind
from aer.core.hashing import canonical_json, sha256_hex
from aer.core.schemas.extraction import Excerpt, ExtractedText, Locator
from aer.db.models import Citation, Claim, Extraction, ReportSection, SourceDocument
from aer.fetch.policy import DEFAULT_POLICIES

__all__ = [
    "MAX_EXCERPT_CHARS",
    "locator_hash",
    "may_print_excerpt",
    "printable_excerpts",
    "record_excerpt",
    "record_excerpts",
]

# The longest text one excerpt may carry. Acquisition cuts to this bound — see
# :mod:`aer.services.filings`, which imports it — and ADR 0119's third gate re-reads it at
# the point of printing, because an excerpt recorded by some other path is not bound by the
# cutting that never ran on it. The bound is a copyright posture and an injection-surface
# bound at the same time.
MAX_EXCERPT_CHARS: Final = 2_000

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


# -- What may be printed (ADR 0119) ------------------------------------------------------------


def may_print_excerpt(document: SourceDocument, excerpt: str) -> bool:
    """ADR 0119's three gates, together, in the order they are cheapest to fail.

    **The licence permits verbatim reproduction.** Read from the provider's fetch policy,
    beside the licence note it has to agree with, and closed for a provider nobody has
    answered the question for. A licensed feed's payload is purgeable, and a purge that left
    the bytes quoted in every exported document would not be a purge.

    **The source carries no injection signal.** A flag is a badge for the reviewer at gate 2,
    not a refusal to acquire — but a flagged document is one whose text tried something, and
    reproducing that text into a shareable artefact is how it travels. The claim keeps its
    hash reference; only the passage is withheld.

    **It is an excerpt.** Bounded by the cap acquisition cuts to, re-read here because a row
    recorded by another path was never cut by it.
    """
    policy = DEFAULT_POLICIES.get(document.provider)
    if policy is None or not policy.verbatim_excerpt_publishable:
        return False
    if document.injection_flagged:
        return False
    return 0 < len(excerpt) <= MAX_EXCERPT_CHARS


async def printable_excerpts(
    session: AsyncSession, *, job_id: uuid.UUID, source_document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """One printable passage per source document this run verified against, where there is one.

    **Verified, never merely admissible.** A citation a person overrode is one the verifier
    could *not* find in the document it names, and printing its text would be the
    unverifiable quotation this platform exists to argue against. An overridden citation
    keeps its footnote and its hash; it does not get quotation marks.

    **One passage per document, chosen by how much of the report leans on it**, then by the
    run's own claim order. A source marker resolves to a document rather than to a sentence —
    that is what the footnote drill-down has always answered — so the passage is labelled as a
    passage of the document rather than as the support for one figure. Choosing the most-cited
    one makes it the passage the research leaned on hardest rather than whichever row sorted
    first.

    Returns an empty mapping for a run with nothing verified, which is the ordinary state of a
    run whose sections all failed and is not a condition to report.
    """
    wanted = list(dict.fromkeys(source_document_ids))
    if not wanted:
        return {}

    rows = (
        await session.scalars(
            select(Citation)
            .join(Claim, Claim.id == Citation.claim_id)
            .join(ReportSection, ReportSection.id == Claim.report_section_id)
            .where(
                ReportSection.job_id == job_id,
                Citation.source_document_id.in_(wanted),
                Citation.excerpt_verified.is_(True),
            )
            .options(
                selectinload(Citation.extraction),
                selectinload(Citation.source_document),
            )
            .order_by(ReportSection.position, Claim.created_at, Claim.id, Citation.created_at)
        )
    ).all()

    return {
        document_id: text
        for document_id in wanted
        if (text := _chosen(row for row in rows if row.source_document_id == document_id))
        is not None
    }


def _chosen(citations: Iterable[Citation]) -> str | None:
    """The passage one document contributes: most cited, ties broken by the order given."""
    ordered: Sequence[Citation] = list(citations)
    if not ordered:
        return None

    weight = Counter(citation.extraction_id for citation in ordered)
    for citation in sorted(ordered, key=lambda row: -weight[row.extraction_id]):
        excerpt = citation.extraction.excerpt
        if may_print_excerpt(citation.source_document, excerpt):
            return excerpt
    return None
