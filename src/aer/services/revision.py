"""The revise pass: the writer's second attempt, on the record (ADR 0091).

The deterministic half of the draft loop. The red team recorded which claims each
challenge attacks and which sections those claims belong to; this module decides what to
do about it, and everything it decides is a bound or a refusal:

**Material challenges only.** Severity ≥ 4 — the same line the §2.4 banner draws. A
quibble is recorded and shown at gate 2; it does not buy a redraft.

**At most four sections, most severe first, each revised once.** The bound is what the
step's cost estimate is priced against, so it is enforced here rather than hoped for.

**Custom sections are never auto-revised.** A user-authored section executes under its
pinned composed policy (ADR 0037), and a platform-initiated redraft would execute content
under that policy that gate 1 never displayed. The loop stands aside and writes down that
it did.

**A revised section's previous claims are replaced — by the draft that replaces them.**
The claims model always said a redrafted section replaces its claims; this is the first
caller that redrafts, and the replacement happens inside `record_draft_claims`, which runs
only for a draft that passed.

**A refused revision changes nothing** (ADR 0098). The section's content, status,
confidence, reason and claims all stand as the draft step left them. This module used to
delete the claims and redraft over the content before knowing whether the attempt would
stand up, which made the loop that exists to improve a draft the only way to lose one that
had already passed — two sections of a live run, with 24 and 21 recorded claims between
them. The spend is not unwound with it: the attempt's calls happened and its cost rows
stay.

Every decision — revised, refused, stood aside — lands as a ``revision_notes`` row,
which is the memory half's substrate: `aer lessons` counts recurrence over these rows, and
nothing ever reads them back into a prompt.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aer.agents.base import AgentContext
from aer.db.models import (
    Disagreement,
    Job,
    ReportSection,
    ResearchRequest,
    RevisionNote,
    SectionStatus,
)
from aer.db.models.revision_note import (
    DISPOSITION_REVISED,
    DISPOSITION_REVISION_REFUSED,
    DISPOSITION_SKIPPED_CUSTOM,
    SCOPE_DRAFT,
)
from aer.db.models.section_definition import SKILL
from aer.sections.writing import execute_builtin_section
from aer.services.disagreements import escalations_for_job
from aer.services.red_team import MATERIAL_SEVERITY

__all__ = [
    "MAX_REVISED_SECTIONS",
    "ReviseOutcome",
    "revise_challenged_sections",
    "revisions_for_job",
]

_log = structlog.get_logger("aer.services.revision")

# How many sections one run may revise. The revise step's cost estimate is this bound
# times a section draft, so raising it is a budget decision, not a tweak.
MAX_REVISED_SECTIONS: Final = 4


@dataclass(slots=True)
class ReviseOutcome:
    """What the revise pass did, as the workflow step records it."""

    challenges_material: int = 0
    revised: list[dict[str, Any]] = field(default_factory=list)
    skipped_custom: list[str] = field(default_factory=list)
    over_bound: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "challenges_material": self.challenges_material,
            "revised": list(self.revised),
            "skipped_custom": sorted(set(self.skipped_custom)),
            "over_bound": list(self.over_bound),
        }


@dataclass(frozen=True, slots=True)
class _Approved:
    """The section as the draft step left it, so a refused revision can put it back.

    ADR 0098. Held in Python rather than taken as a savepoint on purpose: the refused
    attempt's ``agent_runs`` and ``costs`` rows are written on this same session, the
    money was genuinely spent, and an audit trail that discards the calls it did not like
    is not an audit trail. Only the four fields a redraft mutates are carried.
    """

    content: dict[str, Any] | None
    status: SectionStatus
    confidence: float | None
    low_confidence_reason: str | None

    @property
    def was_generated(self) -> bool:
        """Whether there is an approved draft to keep. A section the draft step already
        failed has nothing to protect, and its revision's failure is the same failure."""
        return self.status is SectionStatus.GENERATED

    @classmethod
    def of(cls, section: ReportSection) -> _Approved:
        return cls(
            content=section.content,
            status=section.status,
            confidence=section.confidence,
            low_confidence_reason=section.low_confidence_reason,
        )

    def restore(self, section: ReportSection) -> None:
        section.content = self.content
        section.status = self.status
        section.confidence = self.confidence
        section.low_confidence_reason = self.low_confidence_reason


@dataclass(frozen=True, slots=True)
class _Target:
    """One section the challenges point at, with everything provoking it."""

    section_key: str
    severity: int
    statements: tuple[str, ...]
    challenges: tuple[tuple[str, int, str], ...]  # (dimension, severity, statement)


async def revise_challenged_sections(
    context: AgentContext,
    session: AsyncSession,
    *,
    job: Job,
    request: ResearchRequest,
    focus_by_key: dict[str, str] | None = None,
) -> ReviseOutcome:
    """Redraft the sections the material challenges attack, and write down every decision.

    Spends nothing when no material challenge names a section — which is both the run
    with a clean draft and every challenge recorded before attribution existed, and the
    conservative direction for each.
    """
    outcome = ReviseOutcome()
    targets, material = _targets_of(await escalations_for_job(session, job.id))
    outcome.challenges_material = material
    if not targets:
        return outcome

    # A retried step re-decides from the same challenges, so its record replaces the
    # earlier attempt's rather than piling onto it — this step is the only writer of the
    # draft-scope notes, and duplicates would double every row the gate-2 payload shows.
    await session.execute(
        delete(RevisionNote).where(RevisionNote.job_id == job.id, RevisionNote.scope == SCOPE_DRAFT)
    )
    await session.flush()

    sections = {
        section.section_key: section
        for section in await session.scalars(
            select(ReportSection)
            .where(ReportSection.job_id == job.id)
            .options(selectinload(ReportSection.definition))
        )
    }

    revised = 0
    for target in targets:
        section = sections.get(target.section_key)
        if section is None:
            # Attribution outlived the section listing — a workflow variant, an older
            # run resumed across versions. Nothing to revise is a fact, not a failure.
            _log.warning("revision.section_missing", job_id=str(job.id), section=target.section_key)
            continue

        if section.definition.origin == SKILL:
            outcome.skipped_custom.append(target.section_key)
            await _note_each(
                session,
                job_id=job.id,
                target=target,
                disposition=DISPOSITION_SKIPPED_CUSTOM,
            )
            continue

        if revised >= MAX_REVISED_SECTIONS:
            outcome.over_bound.append(target.section_key)
            continue

        # Held before the attempt, restored if it does not stand up (ADR 0098). The
        # section's claims are not touched here at all: `record_draft_claims` replaces
        # them, and it runs only for a draft that passed.
        approved = _Approved.of(section)
        execution = await execute_builtin_section(
            context,
            section=section,
            request=request,
            focus=(focus_by_key or {}).get(target.section_key, ""),
            challenges=list(target.statements),
        )
        revised += 1
        kept = execution.status is not SectionStatus.GENERATED and approved.was_generated
        if kept:
            approved.restore(section)
            await session.flush()
        disposition = DISPOSITION_REVISION_REFUSED if kept else DISPOSITION_REVISED
        outcome.revised.append(
            {
                "section_key": target.section_key,
                "status": execution.status.value,
                "attempts": execution.attempts,
                "challenges": len(target.statements),
                "max_severity": target.severity,
                # The draft the reader will actually see, which after a refused revision
                # is not the one this attempt produced.
                "kept_approved_draft": kept,
            }
        )
        await _note_each(session, job_id=job.id, target=target, disposition=disposition)
        _log.info(
            "revision.section_revised",
            job_id=str(job.id),
            section=target.section_key,
            status=execution.status.value,
            kept_approved_draft=kept,
            challenges=len(target.statements),
        )

    await session.flush()
    return outcome


async def revisions_for_job(session: AsyncSession, job_id: uuid.UUID) -> list[dict[str, Any]]:
    """The run's draft-scope revision record, as the gate-2 payload carries it.

    Inside the hash, so "approved with these revisions in view" is verifiable afterwards.
    Frozen once the revise step has run — the step is the only writer of these rows — so
    the hash the step seals and the one the review page recomputes agree.
    """
    rows = await session.scalars(
        select(RevisionNote)
        .where(RevisionNote.job_id == job_id, RevisionNote.scope == SCOPE_DRAFT)
        .order_by(RevisionNote.created_at, RevisionNote.id)
    )
    return [
        {
            "section_key": row.section_key,
            "dimension": row.dimension,
            "severity": row.severity,
            "disposition": row.disposition,
        }
        for row in rows
    ]


def _targets_of(rows: Sequence[Disagreement]) -> tuple[list[_Target], int]:
    """The challenged sections, most severe first, and the material challenge count.

    Reads the attribution the red team service recorded (``detail.sections``). A material
    challenge with no attribution — including every challenge recorded before ADR 0091 —
    counts, and provokes nothing.
    """
    material = 0
    by_section: dict[str, list[tuple[str, int, str]]] = {}
    for row in rows:
        detail = row.detail or {}
        severity = int(detail.get("severity", 0) or 0)
        if "dimension" not in detail or severity < MATERIAL_SEVERITY:
            continue
        material += 1
        statement = str(detail.get("challenge", ""))
        dimension = str(detail.get("dimension", ""))
        for section_key in detail.get("sections", []) or []:
            by_section.setdefault(str(section_key), []).append((dimension, severity, statement))

    targets = [
        _Target(
            section_key=section_key,
            severity=max(severity for _, severity, _ in challenges),
            statements=tuple(dict.fromkeys(statement for _, _, statement in challenges)),
            challenges=tuple(challenges),
        )
        for section_key, challenges in by_section.items()
    ]
    targets.sort(key=lambda target: (-target.severity, target.section_key))
    return targets, material


async def _note_each(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    target: _Target,
    disposition: str,
) -> None:
    for dimension, severity, statement in target.challenges:
        session.add(
            RevisionNote(
                job_id=job_id,
                scope=SCOPE_DRAFT,
                section_key=target.section_key,
                dimension=dimension,
                severity=severity,
                statement=statement,
                disposition=disposition,
            )
        )
    await session.flush()
