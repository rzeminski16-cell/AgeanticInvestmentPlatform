"""The decisions tool's answer to "is anything waiting for me".

Two kinds of item, both *not started* (ADR 0104). A decision that moves the book and has
no trade behind it is work the operator decided on and has not done — the journal's
equivalent of a research request nobody ran. A decision past the date its holder said they
would look at it again is a review nobody has started. Neither is blocked and neither is
broken; nothing here needs diagnosis, and nothing is stopped waiting for a person.

Bounded, like every other tool's feed, and for the same reason.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from aer.services import decisions as decision_service
from aer.services.decisions import ACTION_WORDS
from aer.web import figures
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["items"]

TOOL: Final = "decisions"
_LIMIT: Final = 8


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    """Decisions not carried out, and decisions due for review."""
    now = datetime.now(UTC)
    collected: list[Attention] = []

    held = await decision_service.decisions_for(session, user_id=user_id)
    undone = [row for row in held if row.action.moves_the_book and not row.transactions]
    collected.extend(
        Attention(
            key=f"decisions.undone.{row.judgement_id}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"You decided to {ACTION_WORDS[row.action]} on {row.thesis.title}",
            detail=(
                f"{row.statement} No trade has carried it out yet; record one on the "
                "portfolio form and name this decision, or withdraw the decision with a reason."
            ),
            href=f"/decisions/{row.judgement_id}",
            action="Open the decision",
            waited=figures.waited_for(row.judgement.held_at, now=now),
        )
        for row in undone[:_LIMIT]
    )
    collected.extend(_and_more(len(undone), "decisions have not been carried out", "undone"))

    due = await decision_service.reviews_due(session, user_id=user_id, today=now.date())
    collected.extend(
        Attention(
            key=f"decisions.review.{row.judgement_id}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"A decision on {row.thesis.title} is due for your review",
            detail=(
                f"{ACTION_WORDS[row.action].capitalize()}: {row.statement} You said you would "
                f"look at it again by {row.review_by:%d %B %Y}."
            ),
            href=f"/decisions/{row.judgement_id}",
            action="Open the decision",
            waited=figures.waited_for(
                datetime.combine(row.review_by, datetime.min.time(), tzinfo=UTC), now=now
            )
            if row.review_by is not None
            else "",
        )
        for row in due[:_LIMIT]
    )
    collected.extend(_and_more(len(due), "decisions are due for review", "review"))
    return collected


def _and_more(total: int, noun: str, slug: str) -> list[Attention]:
    remaining = max(0, total - _LIMIT)
    if not remaining:
        return []
    return [
        Attention(
            key=f"decisions.more.{slug}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"{remaining} more {noun}",
            detail="This list is bounded, so the rest are not shown here.",
            href="/decisions",
            action="Open the journal",
        )
    ]
