"""Turning a :class:`~aer.core.disagreement.Resolution` into a row, and back out at gate 2.

The ladder in :mod:`aer.core.disagreement` decides; this persists. Keeping the two apart is
what lets the ladder be pure and property-tested — the decision has no database in it, and
this module has no rules in it.

**Agreement is not recorded, and that is a decision rather than an omission.** Two sources
saying the same thing is the ordinary case; a row per agreeing pair would bury the rows that
mean something. :func:`record_resolution` returns ``None`` for an agreement so the caller
can count them without storing them.

**Recording is idempotent on a fingerprint.** A retried step, or a re-run over the same
evidence, must produce one row rather than two — otherwise the disagreement appendix grows
every time a run is repeated and the duplicates look like independent conflicts.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.disagreement import (
    DisagreementKind,
    Position,
    Resolution,
    ResolutionOutcome,
    ResolvedBy,
    resolve,
)
from aer.core.enums import GateKind, SourceTier
from aer.core.hashing import canonical_json, sha256_hex
from aer.db.models import Disagreement, FinancialFact, User
from aer.errors import ValidationError

__all__ = [
    "ESCALATION_GATE",
    "disagreements_for_job",
    "escalations_for_job",
    "fingerprint_for",
    "position_from_fact",
    "record_resolution",
    "resolve_and_record",
    "settle_by_hand",
]

_log = structlog.get_logger("aer.services.disagreements")

# Where an escalation goes. Gate 2 is the last point at which a person sees the evidence
# before it becomes a report, and `docs/archive/PLAN.md` section 2.4 raises every escalation trigger
# there rather than inventing a gate per trigger.
ESCALATION_GATE = GateKind.FINAL


def fingerprint_for(
    *, topic: str, kind: DisagreementKind, position_a: Position, position_b: Position
) -> str:
    """A stable identity for one comparison.

    Over the topic, the kind and the two references — **not** the values or the outcome. A
    fingerprint that moved when a value was re-extracted would let the same conflict be
    recorded twice with slightly different numbers, which is precisely the duplicate this
    exists to prevent. The references are sorted, so it does not matter which position the
    ladder called A.
    """
    return sha256_hex(
        canonical_json(
            {
                "topic": topic,
                "kind": kind.value,
                "references": sorted([position_a.reference, position_b.reference]),
            }
        )
    )


def position_from_fact(fact: FinancialFact, *, tier: SourceTier, label: str) -> Position:
    """A ladder position built from a stored fact.

    ``tier`` is passed rather than read off the fact, because a fact has no tier — the
    *document* it came from does, and joining to fetch it here would make a pure comparison
    depend on how the caller loaded its rows.
    """
    return Position(
        reference=str(fact.id),
        label=label,
        value=fact.value,
        unit=fact.unit,
        tier=tier,
        filed_date=fact.filed_date,
        basis=fact.basis,
        scale=fact.scale,
    )


async def resolve_and_record(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    topic: str,
    first: Position,
    second: Position,
    kind: DisagreementKind = DisagreementKind.SOURCE_CONFLICT,
) -> Disagreement | None:
    """Run the ladder over two positions and store the outcome if there is one.

    The ordinary path for a caller with two facts in hand. Returns ``None`` when the two
    agree.
    """
    return await record_resolution(
        session,
        job_id=job_id,
        topic=topic,
        kind=kind,
        resolution=resolve(first, second),
    )


async def record_resolution(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    topic: str,
    kind: DisagreementKind,
    resolution: Resolution,
    detail: dict[str, Any] | None = None,
) -> Disagreement | None:
    """Store a resolution the ladder already produced.

    Args:
        detail: The structured parts of the conflict beyond what the ladder states — a
            red-team challenge's statement, basis, severity and evidence ids. Stored
            beside the rationale, never composed into it: the appendix renders these as
            columns and footnotes, and a blob cannot be un-composed (gap R5).

    Returns:
        The row, or ``None`` if the positions agreed and nothing was recorded.

    Raises:
        ValidationError: If ``topic`` is blank. A conflict nobody can name is a conflict
            nobody will act on, and the gate-2 banner would show an empty line.
    """
    if not topic.strip():
        message = (
            "A disagreement needs a topic. Without one the gate-2 banner shows a conflict "
            "with nothing to say what it is about, which is the same as showing nothing."
        )
        raise ValidationError(message, context={"job_id": str(job_id)})

    if not resolution.is_recordable:
        return None

    fingerprint = fingerprint_for(
        topic=topic,
        kind=kind,
        position_a=resolution.position_a,
        position_b=resolution.position_b,
    )

    existing = await session.scalar(
        select(Disagreement).where(
            Disagreement.job_id == job_id, Disagreement.fingerprint == fingerprint
        )
    )
    if existing is not None:
        return existing

    row = Disagreement(
        job_id=job_id,
        topic=topic.strip(),
        kind=kind,
        position_a=resolution.position_a.as_record(),
        position_b=resolution.position_b.as_record(),
        resolution=resolution.outcome,
        rule=resolution.rule,
        resolved_by=ResolvedBy.RULE,
        resolution_rationale=resolution.rationale,
        detail=detail,
        escalated_to_gate=ESCALATION_GATE if resolution.escalates else None,
        material=resolution.material,
        relative_difference=resolution.relative_difference,
        fingerprint=fingerprint,
    )
    session.add(row)
    await session.flush()

    _log.info(
        "disagreement.recorded",
        job_id=str(job_id),
        topic=row.topic,
        rule=resolution.rule.value,
        outcome=resolution.outcome.value,
        material=resolution.material,
    )
    return row


async def disagreements_for_job(session: AsyncSession, job_id: uuid.UUID) -> Sequence[Disagreement]:
    """Every recorded conflict for a run, oldest first.

    What the report's disagreement appendix is built from — resolved ones included. "We
    found two answers and here is why we used this one" is the part of a research report
    that makes the rest of it trustworthy.
    """
    rows = await session.scalars(
        select(Disagreement)
        .where(Disagreement.job_id == job_id)
        .order_by(Disagreement.created_at, Disagreement.id)
    )
    return list(rows)


async def escalations_for_job(session: AsyncSession, job_id: uuid.UUID) -> Sequence[Disagreement]:
    """The conflicts a person still has to settle, material ones first.

    This is the query gate 2 asks. An escalation that nothing surfaced would be a conflict
    the platform noticed, recorded, and then said nothing about — worse than not having
    looked, because the record implies somebody saw it.
    """
    rows = await session.scalars(
        select(Disagreement)
        .where(
            Disagreement.job_id == job_id,
            Disagreement.resolution == ResolutionOutcome.ESCALATED,
        )
        .order_by(Disagreement.material.desc(), Disagreement.created_at, Disagreement.id)
    )
    return list(rows)


async def settle_by_hand(
    session: AsyncSession,
    *,
    disagreement: Disagreement,
    outcome: ResolutionOutcome,
    actor: User,
    rationale: str,
) -> Disagreement:
    """Record a person's decision on a conflict the ladder declined to make.

    The rule that fired is **not** overwritten. It still says which rung escalated this,
    beside the human decision that followed — replacing it would erase the reason a person
    was asked in the first place.

    Raises:
        ValidationError: If the conflict was not escalated, if the decision is not a choice
            between the two positions, or if no reason is given.
    """
    if disagreement.resolution is not ResolutionOutcome.ESCALATED:
        message = (
            "This disagreement was settled by rule and is not open. Overriding a rule "
            "decision is a different act from settling an escalation, and it needs its own "
            "record rather than quietly reusing this one."
        )
        raise ValidationError(
            message,
            context={"disagreement_id": str(disagreement.id), "rule": disagreement.rule.value},
        )

    if outcome not in (ResolutionOutcome.CHOSE_A, ResolutionOutcome.CHOSE_B):
        message = (
            "Settling an escalation means choosing one of the two positions. Marking it "
            "agreed or escalated again leaves the run in the state it was already in."
        )
        raise ValidationError(message, context={"outcome": outcome.value})

    if not rationale.strip():
        message = (
            "A human resolution needs a reason. The rule-based ones state theirs, and a "
            "decision that overrides a rule without saying why is the least reviewable row "
            "in the table."
        )
        raise ValidationError(message, context={"disagreement_id": str(disagreement.id)})

    disagreement.resolution = outcome
    disagreement.resolved_by = ResolvedBy.HUMAN
    disagreement.resolved_by_user_id = actor.id
    disagreement.resolved_at = datetime.now(UTC)
    disagreement.resolution_rationale = (
        f"{disagreement.resolution_rationale}\n\nSettled by {actor.email}: {rationale.strip()}"
    )
    # No longer waiting at a gate. The check constraint requires this and the outcome to
    # move together, so a half-applied settlement cannot be written.
    disagreement.escalated_to_gate = None

    await session.flush()

    _log.info(
        "disagreement.settled",
        disagreement_id=str(disagreement.id),
        outcome=outcome.value,
        actor=actor.email,
    )
    return disagreement
