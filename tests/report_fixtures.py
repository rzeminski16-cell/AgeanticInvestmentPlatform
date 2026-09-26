"""Freezing a scene's report the way the platform freezes one: as the company's current report.

ADR 0116 makes two current reports for one company unrepresentable, so a scene that builds
a second approved report on a company — most history scenes do — supersedes the first as
the render step would. Written straight to the columns rather than through the service,
because a scene wants the shape and not the audit event.

A report's valuation is its run's recorded rows, never a column on the report (ROADMAP §3.19
item 76), so a scene that wants one records the rows the valuation step would.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import Calculation, Report
from aer.services.reports import current_report

__all__ = ["make_current", "record_valuation"]


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


async def record_valuation(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    rows: Iterable[tuple[str, str, str]],
    name: str = "value_per_share",
    discriminator: str = "method",
    currency: str = "USD",
) -> None:
    """A run's per-share answers as the valuation step records them: one row per method.

    Each row is ``(method, case, value)``. A discounted cash flow's rows are
    ``value_per_share`` told apart by ``method``; a bank's are ``residual_income_per_share``
    told apart by ``treatment``.
    """
    for sequence, (method, case, value) in enumerate(rows):
        session.add(
            Calculation(
                job_id=job_id,
                name=name,
                formula="value per share = equity value / shares outstanding",
                function_ref="aer.calc.dcf:value_per_share",
                code_version="scenecode1234",
                inputs=[],
                parameters={discriminator: method, "case": case},
                output_value=Decimal(value),
                output_unit=f"{currency}/shares",
                sequence=sequence,
            )
        )
    await session.flush()
