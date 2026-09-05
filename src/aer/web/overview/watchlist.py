"""The watchlist's answer to "is anything waiting for me".

One row, about the queue. Companies followed and not yet researched are *not started* —
the queue is work the operator has not begun. A run the queue started is the research
tool's to report on: its gates, its stops and its failures are that feed's rows, and the
standing budget's room is on the watchlist page, where the settings that set it are.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from aer.services import watchlist as watchlist_service
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["items"]

TOOL: Final = "watchlist"


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    queued = await watchlist_service.queue_for(session, user_id=user_id)
    if not queued:
        return []
    count = len(queued)
    first = queued[0].entry
    return [
        Attention(
            key="watchlist.queued",
            tool=TOOL,
            severity=Severity.IDLE,
            title=(
                f"{count} followed compan{'y is' if count == 1 else 'ies are'} waiting to be "
                "researched"
            ),
            detail=(
                f"Next in the queue is {first.company_name}. The queue starts research within "
                "the standing budget; the page says how much room is left this month."
            ),
            href="/watchlist",
            action="Open the watchlist",
        )
    ]
