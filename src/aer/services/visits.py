"""Today's record of when it was opened (revision 0092), for its briefing's window.

The window itself is decided in :mod:`aer.core.visits`, which is pure; this is the one place
the two columns are read and written, so the rule and its storage cannot drift apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aer.core.visits import Visit, visit_of

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import User

__all__ = ["open_today"]


async def open_today(session: AsyncSession, *, user: User, now: datetime) -> Visit:
    """The window this look at Today briefs, recorded so the next look knows where it began.

    Flushed and not committed: the handler owns the transaction, and a page that failed to
    render after this ran should not have moved the operator's window for nothing.
    """
    visit = visit_of(seen_at=user.today_seen_at, looked_before=user.looked_before, now=now)
    user.today_seen_at = now
    user.looked_before = visit.last_looked
    await session.flush()
    return visit
