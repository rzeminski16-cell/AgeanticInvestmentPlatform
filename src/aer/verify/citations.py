"""Confirming that a cited excerpt actually appears in the document it cites.

**This module owns the only write to ``citations.excerpt_verified``, and a test proves it by
scanning the source tree.** The rule it enforces is invariant 2 and threat T10 — *the model may
propose a citation; only code may confirm one* — and a rule of that kind survives exactly as
long as there is one place it can be broken from.

Nothing here trusts anything stored on the citation except coordinates. The excerpt saved
alongside it is **not** what is checked against; it is checked *for*. The comparison runs
against text re-derived from the artefact:

1. Read the artefact by its SHA-256. The store verifies the digest as it reads, so a tampered
   file fails here and never reaches the comparison (threat T8).
2. Re-run the extractor the citation names, at the version it names, over those bytes.
3. Confirm the extraction still hashes to what was recorded. If it does not, the extractor's
   output has changed and **every** locator from it has moved — reported as its own outcome,
   because "the tool changed" and "the quote is wrong" need different responses and look
   identical from the mismatch alone.
4. Slice at the locator, normalise whitespace on both sides, and compare.

**Equality after normalisation, not a similarity threshold.** An excerpt can differ from its
source in ways no reader can see — a typographic quote against a straight one, a non-breaking
space, a soft hyphen at a line break, a reflowed paragraph — so both sides are put through
:func:`comparable`, which folds exactly those differences away. What survives has to match
*exactly*.

This module used to admit anything scoring 0.95 or better on a fuzzy ratio, and the reasoning
written here was that "a fabricated excerpt does not score 0.9 by accident". The evaluation
corpus in ``tests/citation_corpus.py`` disproved it on the first run: ``$198,270`` cited as
``$198,720`` scores **0.971**, and "Dividends declared were $18,135 million" cited as
"…were **not** $18,135 million" scores **0.951**. Both were accepted. A transposed pair of
digits in a revenue figure and an inserted negation are the two most damaging things a citation
can get wrong, and they are the two a character-similarity score is worst at seeing. See ADR
0025.

:data:`MATCH_THRESHOLD` survives as what it always usefully was — the line between "a near
miss worth a person's attention" and "nothing like it" — and the ratio is still computed and
stored on every verdict, because 0.97 and 0.02 send an operator to different places.

**A failed verification is recorded, not raised.** Every citation gets a verdict, and a run
with four bad citations should tell an operator about four rather than about the first. The
refusal happens at gate 2, where a person can see all of them at once.

**Point-in-time is checked again here, and that repetition is the design.** A source is already
screened at acquisition, in :mod:`aer.services.sources`, and screening it a second time looks
redundant until you notice the two moments know different things. Acquisition cannot know what a
claim will later rest on: a document is fetched while the as-of date is one thing and cited after
an operator has moved it earlier, or it is gathered for background and ends up under a numeric
claim. This check runs against the request's as-of date **as it stands when the claim is made**,
which is the only moment at which the question "does this report use information nobody had?" has
a final answer. Threat T13.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import structlog
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.config import Settings
from aer.core.enums import Provider
from aer.core.schemas.extraction import (
    ExtractedText,
    Locator,
    comparable,
    normalise_whitespace,
)
from aer.db.models import (
    Artefact,
    Citation,
    Claim,
    Extraction,
    ReportSection,
    SourceDocument,
    WorkOrder,
)
from aer.errors import IntegrityError
from aer.extract import ExtractionError, extract_text
from aer.storage.protocol import ArtefactStore

__all__ = [
    "MATCH_THRESHOLD",
    "VERIFICATION_METHOD",
    "ReadOnce",
    "VerificationOutcome",
    "verify",
    "verify_job_citations",
]

_log = structlog.get_logger("aer.verify.citations")

# Recorded on every verified citation. Versioned because a threshold or a normalisation rule
# changing means earlier verdicts were reached under a different rule, and "which citations
# need re-checking?" has to be answerable without guessing from a timestamp.
VERIFICATION_METHOD: Final = "excerpt_match_v1"

# The similarity a normalised excerpt must reach. From §2.10, and high on purpose: it tolerates
# invisible differences and nothing else.
MATCH_THRESHOLD: Final = Decimal("0.95")

_RATIO_PLACES: Final = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """What the check found. Returned rather than raised — see the module docstring."""

    verified: bool
    ratio: Decimal
    reason: str | None = None

    @property
    def failed(self) -> bool:
        return not self.verified


class ReadOnce:
    """Re-extracts each document once, however many citations point into it.

    Without this, a gate check costs one subprocess spawn and one full parse **per citation**.
    Forty citations across two filings is forty parses of two documents, which turns a check
    that should take a second into one that takes a minute — and a slow gate is a gate somebody
    finds a way around.

    Memoising is sound rather than merely convenient: extraction is deterministic in the bytes
    and the extractor, which is a property :mod:`aer.extract` asserts directly. Two reads of the
    same artefact through the same extractor cannot differ, so caching one cannot hide a change.
    The cache lives for one pass and is then discarded, so a document edited between passes is
    still caught.
    """

    __slots__ = ("_settings", "_store", "_texts")

    def __init__(self, store: ArtefactStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings
        self._texts: dict[tuple[str, str], ExtractedText] = {}

    async def text(self, sha256: str, extractor: str) -> ExtractedText:
        key = (sha256, extractor)
        if key not in self._texts:
            document = await extract_text(
                self._store, sha256=sha256, extractor=extractor, settings=self._settings
            )
            self._texts[key] = document.text
        return self._texts[key]


async def verify(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    citation: Citation,
    settings: Settings,
    documents: ReadOnce | None = None,
) -> VerificationOutcome:
    """Check one citation against the document it names, and record the verdict.

    **The only function that may set ``excerpt_verified``.** It is also the only one that
    clears it: a re-check of a citation whose document or extractor has changed must be able to
    withdraw an earlier pass, or a stale ``true`` would outlive the thing that justified it.

    Args:
        documents: Shared across a batch by :func:`verify_job_citations`. A single call makes
            its own, so verifying one citation needs no ceremony.
    """
    reader = documents if documents is not None else ReadOnce(store, settings)

    # Before the text is even re-read: a source that may not be used at this as-of date fails
    # whatever it says, and re-parsing a filing to confirm a quote nobody may cite is work done
    # to reach an answer that was already decided.
    inadmissible = await _refuse_if_out_of_time(session, citation=citation)
    if inadmissible is not None:
        return _record(citation, inadmissible)

    reread = await _reread(session, reader, citation=citation)
    if isinstance(reread, VerificationOutcome):
        return _record(citation, reread)

    extraction, extracted = reread
    return _record(citation, _compare(extraction, extracted))


async def _refuse_if_out_of_time(
    session: AsyncSession, *, citation: Citation
) -> VerificationOutcome | None:
    """Whether this citation's source may be used at the request's as-of date.

    ``None`` means it may. The check is on the source document's own record rather than on a
    recomputed date: the quarantine decision was made at acquisition with the evidence in hand,
    and re-deriving it here from a stored date would use less information than the decision it
    was second-guessing.

    What *is* re-derived is the comparison against the as-of date, because the as-of date can
    change after acquisition and a citation is only sound against the request as it now stands.
    """
    source = await _source_for(session, citation=citation)
    if source is None:  # pragma: no cover -- RESTRICT makes this unreachable in practice
        return VerificationOutcome(False, Decimal(0), "the source document is gone")

    # The run root rather than the mandate (ADR 0072). What this needs is a date and a
    # boolean, and both are properties of the run; reaching them through a research request
    # made the look-ahead guard unavailable to any run that is not about a company.
    work_order = await session.get(WorkOrder, source.work_order_id)
    if work_order is None:  # pragma: no cover -- source_documents.work_order_id is NOT NULL
        return VerificationOutcome(False, Decimal(0), "the work order is gone")

    refused = _refuse_source(source)
    if refused is not None:
        return refused

    if not work_order.point_in_time:
        return None

    # The latest date any evidence supports, not the best estimate. A document that *might* be
    # from after the as-of date cannot be shown to have predated it, which is the question.
    latest = source.publication_date_latest or source.publication_date
    if latest is not None and latest > work_order.as_of_date:
        return VerificationOutcome(
            False,
            Decimal(0),
            f"the source was published on {latest.isoformat()}, after the run's as-of date "
            f"of {work_order.as_of_date.isoformat()}. Citing it would use information nobody "
            "had at the time.",
        )

    return None


def _refuse_source(source: SourceDocument) -> VerificationOutcome | None:
    """Refusals that follow from what the source *is*, before any date arithmetic."""
    if source.provider is Provider.INTERNAL_PRIOR_RUN:
        # Section 2.8 rule 4, and a hard failure by design: prior research may inform a
        # hypothesis but cannot support a claim. A platform citing its own earlier output
        # would launder yesterday's inference into today's evidence.
        return VerificationOutcome(
            False,
            Decimal(0),
            "the cited source is a prior run's own output (provider internal_prior_run). "
            "Prior research cannot support a claim; re-source it from primary evidence.",
        )

    if not source.is_admissible:
        return VerificationOutcome(
            False,
            Decimal(0),
            f"the source is quarantined ({source.quarantine_reason}) and has no recorded "
            "override, so it may not support a claim",
        )

    return None


async def _source_for(session: AsyncSession, *, citation: Citation) -> SourceDocument | None:
    """The source document behind a citation, resolved through its extraction.

    Through the extraction rather than from anything on the citation, so a citation cannot name
    one document and be checked against another's admissibility.
    """
    found: SourceDocument | None = await session.scalar(
        select(SourceDocument)
        .join(Extraction, Extraction.source_document_id == SourceDocument.id)
        .where(Extraction.id == citation.extraction_id)
    )
    return found


async def _reread(
    session: AsyncSession,
    documents: ReadOnce,
    *,
    citation: Citation,
) -> tuple[Extraction, ExtractedText] | VerificationOutcome:
    """Rebuild the text this citation's locator points into, or say why it cannot be.

    Four ways to fail before any comparison happens, and they are separated from the
    comparison because each is a statement about the *document* rather than about the quote.
    Reporting "this excerpt does not match" when the real problem is a tampered artefact would
    send an operator to re-read a filing that is no longer the filing.
    """
    extraction = await session.get(Extraction, citation.extraction_id)
    if extraction is None:  # pragma: no cover -- RESTRICT makes this unreachable in practice
        return VerificationOutcome(False, Decimal(0), "the extraction is gone")

    sha256 = await _artefact_hash(session, extraction)
    if sha256 is None:  # pragma: no cover -- source_documents.artefact_id is NOT NULL
        return VerificationOutcome(False, Decimal(0), "the artefact is gone")

    try:
        extracted = await documents.text(sha256, extraction.extractor)
    except (ExtractionError, IntegrityError) as exc:
        # `IntegrityError` is what the store raises when an artefact's bytes no longer hash to
        # its name — a tampered document, threat T8, and the one failure here that is not
        # about parsing at all.
        return VerificationOutcome(False, Decimal(0), f"the document could not be re-read: {exc}")

    if extracted.content_hash != extraction.content_hash:
        return VerificationOutcome(
            False,
            Decimal(0),
            f"{extraction.extractor} produces different text from the version this citation "
            f"was recorded under ({extraction.extractor_version}), so its locator no longer "
            "points where it did. Re-extract the document rather than treating this as a bad "
            "quote.",
        )

    return extraction, extracted


def _compare(extraction: Extraction, extracted: ExtractedText) -> VerificationOutcome:
    """Does the text at the locator say what the citation claims it says?"""
    try:
        found = extracted.excerpt(Locator.model_validate(extraction.locator))
    except ValueError as exc:
        return VerificationOutcome(False, Decimal(0), str(exc))

    if comparable(extraction.excerpt) == comparable(found.text):
        return VerificationOutcome(True, Decimal(1).quantize(_RATIO_PLACES))

    # Computed only to describe the failure. A near miss and a fabrication are the same
    # verdict and completely different problems, and an operator deciding whether to override
    # needs to see which one they have.
    ratio = _similarity(extraction.excerpt, found.text)
    nearly = ratio >= MATCH_THRESHOLD
    lead = (
        "The text at this locator nearly matches the cited excerpt but is not the same "
        f"(similarity {ratio})"
        if nearly
        else f"The text at this locator does not match the cited excerpt (similarity {ratio})"
    )
    return VerificationOutcome(False, ratio, f"{lead}. The document says: {found.text[:200]!r}")


async def verify_job_citations(
    session: AsyncSession,
    store: ArtefactStore,
    *,
    job_id: uuid.UUID,
    settings: Settings,
) -> list[tuple[Citation, VerificationOutcome]]:
    """Verify every citation in a run's draft, and return all of them with their verdicts.

    All of them, including the ones that passed: the caller is deciding whether a gate opens,
    and "how many were checked" is as much a part of that as "how many failed".
    """
    citations = await _citations_for_job(session, job_id)
    documents = ReadOnce(store, settings)
    results = [
        (c, await verify(session, store, citation=c, settings=settings, documents=documents))
        for c in citations
    ]
    await session.flush()

    failed = [outcome for _, outcome in results if outcome.failed]
    _log.info(
        "citations.verified",
        job_id=str(job_id),
        checked=len(results),
        failed=len(failed),
        method=VERIFICATION_METHOD,
    )
    return results


# -- Internals -----------------------------------------------------------------------------


def _record(citation: Citation, outcome: VerificationOutcome) -> VerificationOutcome:
    """Write the verdict onto the citation.

    Clears as well as sets. A citation re-checked after its extractor changed must lose its
    earlier pass, and a function that only ever set the flag would leave stale approvals
    standing behind reports whose evidence had moved.
    """
    citation.excerpt_verified = outcome.verified
    citation.match_ratio = outcome.ratio
    citation.verification_error = outcome.reason
    citation.verification_method = VERIFICATION_METHOD if outcome.verified else None
    citation.verified_at = datetime.now(UTC) if outcome.verified else None
    return outcome


def _similarity(cited: str, found: str) -> Decimal:
    """How alike two excerpts are, from 0 to 1, after whitespace normalisation.

    ``rapidfuzz`` reports 0 to 100; this is quantised to three places so the stored value fits
    ``NUMERIC(4,3)`` exactly and two runs over the same pair record the same number.
    """
    score = fuzz.ratio(normalise_whitespace(cited), normalise_whitespace(found)) / 100
    return Decimal(str(score)).quantize(_RATIO_PLACES)


async def _artefact_hash(session: AsyncSession, extraction: Extraction) -> str | None:
    """The SHA-256 of the bytes the extraction came from.

    Resolved through the source document rather than taken from the citation, so a citation
    cannot name one document and be checked against another's bytes.
    """
    found: str | None = await session.scalar(
        select(Artefact.sha256)
        .join(SourceDocument, SourceDocument.artefact_id == Artefact.id)
        .where(SourceDocument.id == extraction.source_document_id)
    )
    return found


async def _citations_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[Citation]:
    rows = await session.scalars(
        select(Citation)
        .join(Claim, Claim.id == Citation.claim_id)
        .join(ReportSection, ReportSection.id == Claim.report_section_id)
        .where(ReportSection.job_id == job_id)
        .options(selectinload(Citation.extraction))
        .order_by(Citation.created_at, Citation.id)
    )
    return list(rows)
