"""The risk tool's answer to "is anything waiting for me".

Two kinds of item. A reading that stopped at its cost ceiling *needs diagnosis*. A book
whose figures have not been read since it last changed — or never — is *not started*: the
figures are computed on every page load, but the analyst's reading is a pass somebody has
to run, and a reading of a book that has since traded is a reading of a different book.

Bounded, like every other tool's feed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from aer.db.models import Portfolio, Transaction
from aer.services import risk as risk_service
from aer.web import figures
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["items"]

TOOL: Final = "risk"
_LIMIT: Final = 8


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    now = datetime.now(UTC)
    books = list(
        await session.scalars(
            select(Portfolio)
            .where(Portfolio.user_id == user_id, Portfolio.archived_at.is_(None))
            .order_by(Portfolio.name)
        )
    )
    collected: list[Attention] = []
    for book in books[:_LIMIT]:
        holds_anything = await session.scalar(
            select(Transaction.attestation_id)
            .where(Transaction.portfolio_id == book.id, Transaction.security_id.is_not(None))
            .limit(1)
        )
        if holds_anything is None:
            continue
        reading = await risk_service.latest_reading(session, portfolio=book)
        if reading is not None and reading.failed:
            collected.append(
                Attention(
                    key=f"risk.stopped.{reading.job.id}",
                    tool=TOOL,
                    severity=Severity.BROKEN,
                    title=f"The risk reading of {book.name} stopped",
                    detail=reading.reason or "The pass recorded no reason.",
                    href="/risk",
                    action="Open the risk page",
                )
            )
            continue
        changed = await risk_service.last_trade_recorded_at(session, portfolio=book)
        stale = reading is None or (
            changed is not None
            and reading.job.started_at is not None
            and changed > reading.job.started_at
        )
        if stale:
            collected.append(
                Attention(
                    key=f"risk.unread.{book.id}",
                    tool=TOOL,
                    severity=Severity.IDLE,
                    title=(
                        f"{book.name} has not been read for risk"
                        if reading is None
                        else f"{book.name} has changed since its risk was last read"
                    ),
                    detail=(
                        "The figures are computed on the page; the analyst's reading of them "
                        "is a pass you run, and it spends."
                    ),
                    href="/risk",
                    action="Open the risk page",
                    waited=figures.waited_for(changed, now=now) if changed is not None else "",
                )
            )
    return collected
