"""A report is superseded, never replaced, and exactly one is current (ADR 0116).

Three things happen to an approved report after approval, and all three record why. A new
report on the same company supersedes it when that report is approved (the render step calls
:func:`supersede`); the operator withdraws it, with a reason and nothing to put in its place
(:func:`withdraw`); and every current surface asks :func:`current_report` rather than
sorting approved reports by date and assuming the newest wins.

Nothing here edits a report. The superseded row keeps its bytes, its hash, its artefacts and
its evaluation rows; what changes is three columns that say what happened to it, and an audit
event that says who decided.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import AuditEvent, Report, User
from aer.errors import ConflictError, ValidationError

__all__ = [
    "SUPERSEDED_EVENT",
    "WITHDRAWN_EVENT",
    "current_report",
    "report_state",
    "supersede",
    "withdraw",
]

_log = structlog.get_logger("aer.services.reports")

SUPERSEDED_EVENT: Final = "report.superseded"
WITHDRAWN_EVENT: Final = "report.withdrawn"


def report_state(report: Report) -> str:
    """One word for what a report is now: ``draft``, ``current``, ``superseded``, ``withdrawn``.

    Three readings of two columns (ADR 0116), named once so the library, the report page
    and the API cannot disagree about them.
    """
    if not report.immutable:
        return "draft"
    if report.superseded_by is not None:
        return "superseded"
    if report.superseded_at is not None:
        return "withdrawn"
    return "current"


async def current_report(
    session: AsyncSession, *, company_id: uuid.UUID, excluding: uuid.UUID | None = None
) -> Report | None:
    """The one report the platform currently asserts about a company, or ``None``.

    The partial unique index guarantees at most one; ``excluding`` is for the report that
    is about to become current and must not find itself.
    """
    query = select(Report).where(
        Report.company_id == company_id,
        Report.immutable.is_(True),
        Report.superseded_by.is_(None),
        Report.superseded_at.is_(None),
    )
    if excluding is not None:
        query = query.where(Report.id != excluding)
    found: Report | None = await session.scalar(query)
    return found


def _require_reason(reason: str, *, what: str) -> str:
    cleaned = " ".join(reason.split())
    if not cleaned:
        message = (
            f"{what} needs a reason, in your own words. An action that changes what the "
            "platform asserts says why at the moment it is taken."
        )
        raise ValidationError(message)
    return cleaned[:4000]


def _require_current(report: Report, *, what: str) -> None:
    if not report.immutable:
        message = f"{what} applies to an approved report; this one was never approved."
        raise ConflictError(message, context={"report_id": str(report.id)})
    if report.is_withdrawn:
        message = f"This report was already withdrawn on {report.superseded_at:%d %B %Y}."
        raise ConflictError(message, context={"report_id": str(report.id)})
    if report.is_superseded:
        message = (
            f"This report was already superseded on {report.superseded_at:%d %B %Y}; the "
            "current one is what to act on."
        )
        raise ConflictError(
            message,
            context={"report_id": str(report.id), "superseded_by": str(report.superseded_by)},
        )


async def supersede(
    session: AsyncSession, *, previous: Report, successor: Report, reason: str, actor: User
) -> Report:
    """Record that ``successor`` replaces ``previous`` as the company's current report.

    Raises:
        ConflictError: ``previous`` is not current, or the two are the same report or about
            different companies.
        ValidationError: The reason is blank.
    """
    cleaned = _require_reason(reason, what="Superseding a report")
    _require_current(previous, what="Superseding")
    if successor.id == previous.id:
        message = "A report cannot supersede itself."
        raise ConflictError(message, context={"report_id": str(previous.id)})
    if successor.company_id != previous.company_id:
        message = "A report is superseded by a report about the same company."
        raise ConflictError(
            message, context={"previous": str(previous.id), "successor": str(successor.id)}
        )

    previous.superseded_by = successor.id
    previous.superseded_at = datetime.now(UTC)
    previous.supersession_reason = cleaned
    await _append_event(
        session,
        actor=actor,
        report=previous,
        event_type=SUPERSEDED_EVENT,
        payload={
            "report_id": str(previous.id),
            "superseded_by": str(successor.id),
            "reason": cleaned,
        },
    )
    _log.info(
        "report.superseded",
        report_id=str(previous.id),
        superseded_by=str(successor.id),
        actor=actor.email,
    )
    return previous


async def withdraw(session: AsyncSession, *, report: Report, reason: str, actor: User) -> Report:
    """Stop a report being current, with nothing to put in its place.

    The case ADR 0116 was written for: a report found wrong after approval. It stays readable,
    immutable and cited; it stops answering "what does the platform think".

    Raises:
        ConflictError: The report is not current.
        ValidationError: The reason is blank.
    """
    cleaned = _require_reason(reason, what="Withdrawing a report")
    _require_current(report, what="Withdrawing")
    report.superseded_at = datetime.now(UTC)
    report.supersession_reason = cleaned
    await _append_event(
        session,
        actor=actor,
        report=report,
        event_type=WITHDRAWN_EVENT,
        payload={"report_id": str(report.id), "reason": cleaned},
    )
    _log.info("report.withdrawn", report_id=str(report.id), actor=actor.email)
    return report


async def _append_event(
    session: AsyncSession,
    *,
    actor: User,
    report: Report,
    event_type: str,
    payload: dict[str, str],
) -> None:
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    session.add(
        AuditEvent.create_linked(
            actor=actor.email,
            event_type=event_type,
            payload=dict(payload),
            previous=previous,
            request_id=report.request_id,
            job_id=report.job_id,
        )
    )
    await session.flush()
