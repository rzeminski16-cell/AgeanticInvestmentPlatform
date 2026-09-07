"""The monitor tool's answer to "is anything waiting for me".

Three kinds of item, and each is labelled for what it is (ADR 0078: every surface that
renders a finding must call it a finding, because a queue that looks like an inbox of
approvals will be worked like one). A contradicted premise is *waiting for you* — it opened
a gate, and nothing about the premise moves until a person decides. A pass that stopped at
its cost ceiling *needs diagnosis*: it left a finding rather than pausing for nobody. Every
other open finding, and every premise past the date a person said they would look at it
again, is *not started* — a question raised that nobody has read.

Bounded, like the research tool's feed, and for the same reason: forty findings is a
different day from three, and a feed that showed the first eight and stopped would describe
the smaller problem.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from aer.core.enums import FindingKind
from aer.services import thesis_monitor
from aer.web import figures
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Finding

__all__ = ["items"]

TOOL: Final = "monitor"

# Enough to see a pattern, few enough to read. Past this the feed says how many more.
_LIMIT: Final = 8


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    """Everything the monitor has waiting, worst first within this tool."""
    now = datetime.now(UTC)
    collected: list[Attention] = []

    open_findings = await thesis_monitor.findings_for(session, user_id=user_id, open_only=True)
    gated = [row for row in open_findings if row.opens_gate]
    stopped = [row for row in open_findings if row.kind is FindingKind.STOPPED]
    unread = [row for row in open_findings if not row.opens_gate and row not in stopped]

    collected.extend(
        Attention(
            key=f"monitor.gate.{row.id}",
            tool=TOOL,
            severity=Severity.BLOCKED,
            title=f"A premise of {row.thesis.title} was contradicted",
            detail=(
                "A filing defeated the predicate this premise carries. The finding opened a "
                "gate: withdraw the premise or keep it, with your reason."
            ),
            href=f"/monitor/findings/{row.id}",
            action="Decide what to do",
            waited=figures.waited_for(row.created_at, now=now),
        )
        for row in gated[:_LIMIT]
    )
    collected.extend(_and_more(len(gated), "premises were contradicted", Severity.BLOCKED, "gate"))

    collected.extend(
        Attention(
            key=f"monitor.stopped.{row.id}",
            tool=TOOL,
            severity=Severity.BROKEN,
            title=f"The monitor stopped on {row.thesis.title}",
            detail=_first_sentence(row.justification),
            href=f"/monitor/findings/{row.id}",
            action="Read the finding",
            waited=figures.waited_for(row.created_at, now=now),
        )
        for row in stopped[:_LIMIT]
    )
    collected.extend(
        _and_more(len(stopped), "passes stopped at a ceiling", Severity.BROKEN, "stopped")
    )

    collected.extend(
        Attention(
            key=f"monitor.finding.{row.id}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"A finding on {row.thesis.title}: {_status_word(row)}",
            detail=(
                "A finding, not a decision — the monitor read a premise against new evidence "
                "and this is what it noticed. Nothing changes until you say what you did about it."
            ),
            href=f"/monitor/findings/{row.id}",
            action="Read the finding",
            waited=figures.waited_for(row.created_at, now=now),
        )
        for row in unread[:_LIMIT]
    )
    collected.extend(_and_more(len(unread), "findings have not been read", Severity.IDLE, "unread"))

    due = await thesis_monitor.reviews_due(session, user_id=user_id, today=now.date())
    collected.extend(
        Attention(
            key=f"monitor.review.{premise.judgement_id}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"A premise of {thesis.title} is due for your review",
            detail=(
                f"{premise.statement} You said you would look at it again by "
                f"{premise.review_by:%d %B %Y}; no filing tests it, so nothing else will."
            ),
            href=f"/theses/{thesis.id}#premise-{premise.judgement_id}",
            action="Open the thesis",
            waited=figures.waited_for(
                datetime.combine(premise.review_by, datetime.min.time(), tzinfo=UTC), now=now
            )
            if premise.review_by is not None
            else "",
        )
        for thesis, premise in due[:_LIMIT]
    )
    collected.extend(_and_more(len(due), "premises are due for review", Severity.IDLE, "review"))

    return collected


def _status_word(row: Finding) -> str:
    return row.status.value if row.status is not None else row.kind.value


def _first_sentence(text: str) -> str:
    head, _, _ = text.partition(". ")
    return head if head.endswith(".") else f"{head}."


def _and_more(total: int, noun: str, severity: Severity, slug: str) -> list[Attention]:
    """The row that says a listing was cut short."""
    remaining = max(0, total - _LIMIT)
    if not remaining:
        return []
    return [
        Attention(
            key=f"monitor.more.{slug}",
            tool=TOOL,
            severity=severity,
            title=f"{remaining} more {noun}",
            detail="This list is bounded, so the rest are not shown here.",
            href="/monitor",
            action="Open the monitor",
        )
    ]
