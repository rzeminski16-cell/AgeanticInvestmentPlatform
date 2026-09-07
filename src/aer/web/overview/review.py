"""The review tool's answer to "is anything waiting for me".

Two kinds of item. A closed position nobody has reviewed is *not started* — the review is
work the operator has not begun. A reviewer's proposal nobody has confirmed is *waiting for
you*: the pass ran, it spent, and its draft is a judgement of nobody's until a person
confirms it (ADR 0105). A pass that stopped at its cost ceiling *needs diagnosis*.

Bounded, like every other tool's feed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from aer.core.enums import JobStatus
from aer.db.models import Portfolio
from aer.services import post_trade
from aer.web import figures
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["items"]

TOOL: Final = "review"
_LIMIT: Final = 8


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    now = datetime.now(UTC)
    books = list(
        await session.scalars(
            select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.archived_at.is_(None))
        )
    )
    states = [
        state for book in books for state in await post_trade.states_for(session, portfolio=book)
    ]
    collected: list[Attention] = []

    proposed = [state for state in states if state.state == "proposed"]
    collected.extend(
        Attention(
            key=f"review.proposed.{state.proposal.id}",
            tool=TOOL,
            severity=Severity.BLOCKED,
            title=f"The reviewer has read {state.episode.security.ticker} and is waiting for you",
            detail=(
                "A proposal, not a judgement: confirm it, amending anything, and it becomes "
                "your review of the position."
            ),
            href=f"/review/passes/{state.proposal.id}",
            action="Read the proposal",
            waited=figures.waited_for(state.proposal.started_at, now=now)
            if state.proposal.started_at
            else "",
        )
        for state in proposed[:_LIMIT]
        if state.proposal is not None
    )

    stopped = [state for state in states if state.state == "stopped" and state.proposal is not None]
    collected.extend(
        Attention(
            key=f"review.stopped.{state.proposal.id}",
            tool=TOOL,
            severity=Severity.BROKEN,
            title=f"The review of {state.episode.security.ticker} stopped",
            detail=str(
                (state.proposal.error or {}).get("message") or "The pass recorded no reason."
            ),
            href=f"/review/passes/{state.proposal.id}",
            action="Read the pass",
        )
        for state in stopped[:_LIMIT]
        if state.proposal is not None and state.proposal.status is JobStatus.FAILED
    )

    unreviewed = [state for state in states if state.state == "unreviewed"]
    collected.extend(
        Attention(
            key=f"review.unreviewed.{state.episode.key}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"{state.episode.security.ticker} closed and has not been reviewed",
            detail=(
                f"Closed on {state.episode.closed_on:%d %B %Y}. A review scores the decision "
                "against the process, not the result, and this one has not been scored."
            ),
            href="/review",
            action="Open the review list",
            waited=figures.waited_for(
                datetime.combine(state.episode.closed_on, datetime.min.time(), tzinfo=UTC), now=now
            ),
        )
        for state in unreviewed[:_LIMIT]
    )
    if len(unreviewed) > _LIMIT:
        collected.append(
            Attention(
                key="review.more.unreviewed",
                tool=TOOL,
                severity=Severity.IDLE,
                title=f"{len(unreviewed) - _LIMIT} more closed positions have not been reviewed",
                detail="This list is bounded, so the rest are not shown here.",
                href="/review",
                action="Open the review list",
            )
        )
    return collected
