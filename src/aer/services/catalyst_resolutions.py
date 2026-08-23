"""Recording what happened to a catalyst — the operator's answer, never a model's.

`docs/archive/knowledge-graph.md` K4. "The stated window has passed" is knowable from rows and
has always been said honestly. Whether the event *occurred* is not: no query answers it,
and a model asserting it would be making a factual claim with no citation. If that
determination ever becomes automatic it goes through the normal evidence path with a
source — not through this module, whose whole job is to hold a person's answer.

**A resolution must name a catalyst that exists.** The label is validated against the
catalysts the company's approved reports actually proposed, read through the same
:func:`~aer.services.history.catalyst_outcomes_for` every other surface uses. Without
that check the table would accumulate rows about events nobody forecast, which no note
could project and no statistic could honestly count.
"""

from __future__ import annotations

import uuid
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import CatalystOutcomeKind
from aer.db.models import CatalystResolution, User
from aer.errors import ValidationError
from aer.services.history import approved_reports_for, catalyst_outcomes_for

__all__ = [
    "known_catalyst_labels",
    "record_catalyst_resolution",
    "resolutions_for",
]

_log = structlog.get_logger("aer.services.catalyst_resolutions")


async def known_catalyst_labels(session: AsyncSession, *, company_id: uuid.UUID) -> set[str]:
    """Every catalyst label the company's approved reports have proposed."""
    labels: set[str] = set()
    for prior in await approved_reports_for(session, company_id=company_id):
        for outcome in await catalyst_outcomes_for(session, prior=prior, as_of=date.max):
            labels.add(outcome.label)
    return labels


async def resolutions_for(
    session: AsyncSession, *, company_id: uuid.UUID
) -> dict[str, CatalystResolution]:
    """The company's recorded resolutions, by label."""
    rows = await session.scalars(
        select(CatalystResolution).where(CatalystResolution.company_id == company_id)
    )
    return {row.label: row for row in rows}


async def record_catalyst_resolution(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    label: str,
    outcome: CatalystOutcomeKind,
    reason: str,
    actor: User,
) -> CatalystResolution:
    """Record — or correct — what happened to one catalyst.

    An existing resolution is **updated**, not refused: this is operator bookkeeping
    rather than a gate decision, the row keeps who recorded the current answer and when,
    and the vault regenerates from whatever the row says.

    Raises:
        ValidationError: If the reason is blank — an outcome with no stated reason is a
            verdict nobody can argue with — or the label names a catalyst no approved
            report of this company ever proposed.
    """
    cleaned = reason.strip()
    if not cleaned:
        message = (
            "A catalyst resolution needs a reason. An outcome with no stated reason is a "
            "verdict nobody can argue with, and the whole point of recording it is that a "
            "later reader can."
        )
        raise ValidationError(message, context={"label": label})

    known = await known_catalyst_labels(session, company_id=company_id)
    if label not in known:
        message = (
            f"No approved report on this company proposed a catalyst labelled {label!r}, "
            "so there is nothing to resolve. Resolutions attach to the catalyst labels "
            "the research actually named."
        )
        raise ValidationError(message, context={"label": label, "known": sorted(known)[:20]})

    row = await session.scalar(
        select(CatalystResolution).where(
            CatalystResolution.company_id == company_id,
            CatalystResolution.label == label,
        )
    )
    if row is None:
        row = CatalystResolution(
            company_id=company_id,
            label=label,
            outcome=outcome,
            reason=cleaned,
            recorded_by=actor.email,
        )
        session.add(row)
    else:
        row.outcome = outcome
        row.reason = cleaned
        row.recorded_by = actor.email
    await session.flush()
    _log.info(
        "catalyst.resolved",
        company_id=str(company_id),
        label=label,
        outcome=outcome.value,
        recorded_by=actor.email,
    )
    return row
