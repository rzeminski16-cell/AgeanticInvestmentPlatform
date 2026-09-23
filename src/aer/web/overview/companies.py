"""The company record's two rows on Today (page specification §1.1, ranks 3 and 6).

A holding with no thesis behind it, and a report older than its cadence's window. Neither
tool that owns the underlying rows — the book, the research tool — could say either on its
own: "held with no thesis" is a fact about a holding *and* the theses, and "gone stale" is a
fact about a report *and* the cadence the operator set. The company record reads both on
the way to the Companies page (item 59), so the same reading is what Today shows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from aer.db.models import User
from aer.services import company_record as record_service
from aer.web import figures
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.services.company_record import CompanyRecord

__all__ = ["TOOL", "items"]

TOOL: Final = "watchlist"

# The same bound the other providers keep, with a closing row for the rest.
_LIMIT: Final = 8


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    """Held with no thesis, then researched and gone stale, each bounded."""
    user = await session.get(User, user_id)
    if user is None:
        return []
    now = datetime.now(UTC)
    records = await record_service.records_for(session, user=user, now=now)

    unwritten = [row for row in records if row.is_held and row.thesis_state == "none"]
    stale = [row for row in records if row.report_state == "stale" and row.report is not None]

    collected: list[Attention] = [_no_thesis(row, now=now) for row in unwritten[:_LIMIT]]
    collected.extend(
        _and_more(len(unwritten), "holdings have no thesis behind them", "/portfolio", "no_thesis")
    )
    collected.extend(_stale(row, now=now) for row in stale[:_LIMIT])
    collected.extend(
        _and_more(len(stale), "reports are older than their window", "/companies", "stale")
    )
    return collected


def _no_thesis(record: CompanyRecord, *, now: datetime) -> Attention:
    """§1.1 rank 3: *{company} is held with no thesis behind it*. Not started, since the
    work is writing one; the page shows it in the warning tone the specification gives it."""
    holding = record.holding
    opened = holding.security.created_at if holding is not None else None
    return Attention(
        key=f"companies.no_thesis.{record.key}",
        tool=TOOL,
        severity=Severity.IDLE,
        title=f"{record.name} is held with no thesis behind it",
        detail=(
            "Money is committed for reasons nobody wrote down, and the monitor has nothing "
            "to read against. A thesis is what you believe and what would show you were wrong."
        ),
        href=f"/theses?company={record.company.id}" if record.company is not None else "/theses",
        action="Write a thesis",
        waited=figures.waited_for(opened, now=now) if opened is not None else "",
    )


def _stale(record: CompanyRecord, *, now: datetime) -> Attention:
    """§1.1 rank 6: *{company} was last researched {n} days ago*."""
    report = record.report
    assert report is not None
    dated = report.approved_at or datetime.combine(
        report.as_of_date, datetime.min.time(), tzinfo=UTC
    )
    days = max((now - dated).days, 0)
    window = record_service.STALE_AFTER.get(record.cadence, record_service.DEFAULT_STALE_AFTER)
    return Attention(
        key=f"companies.stale.{record.key}",
        tool=TOOL,
        severity=Severity.IDLE,
        title=f"{record.name} was last researched {days} day{'s' if days != 1 else ''} ago",
        detail=(
            f"Older than its {window.days}-day window. A refresh reads only what has been "
            "filed since and re-drafts only what moved; it is priced on the company page."
        ),
        href=record.href,
        action="Open the company",
        waited=figures.waited_for(dated, now=now),
    )


def _and_more(total: int, noun: str, href: str, slug: str) -> list[Attention]:
    if total <= _LIMIT:
        return []
    return [
        Attention(
            key=f"companies.more.{slug}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"{total - _LIMIT} more {noun}",
            detail="This list is bounded, so the rest are not shown here.",
            href=href,
            action="See every company",
        )
    ]
