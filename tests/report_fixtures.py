"""Freezing a scene's report the way the platform freezes one: as the company's current report.

ADR 0116 makes two current reports for one company unrepresentable, so a scene that builds
a second approved report on a company — most history scenes do — supersedes the first as
the render step would. Written straight to the columns rather than through the service,
because a scene wants the shape and not the audit event.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Report
from aer.services.reports import current_report

__all__ = ["make_current"]


async def make_current(
    session: AsyncSession, report: Report, *, approved_at: datetime | None = None
) -> Report:
    """Approve and freeze ``report``, superseding the company's current one if there is one."""
    if approved_at is not None:
        report.approved_at = approved_at
    elif report.approved_at is None:
        report.approved_at = datetime.now(UTC)
    await session.flush()
    if report.company_id is not None:
        previous = await current_report(session, company_id=report.company_id, excluding=report.id)
        if previous is not None:
            previous.superseded_by = report.id
            previous.superseded_at = report.approved_at
            previous.supersession_reason = "A later report in this scene was approved."
            await session.flush()
    report.immutable = True
    await session.flush()
    return report
