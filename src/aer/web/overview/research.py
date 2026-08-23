"""The research tool's answer to "is anything waiting for me".

Four kinds of item, each read from a run's own status: stopped at a gate, stopped at a cost
ceiling, failed, or never started. The queries are `services/overview.py`'s; what is here
is the sentence each row becomes, which is a presentation decision and belongs on this side
of the boundary.

**A gate is named, not counted.** "One run is waiting" sends an operator to a console to
find out what it wants; "Contoso is waiting for you to confirm its peer set" is the same
row doing the work. That costs two statements per stopped run — bounded at eight by
`services/overview.py` — and it is the best two statements on the page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from aer.core.enums import GateKind
from aer.services import overview as overview_service
from aer.services.approvals import pending_gate
from aer.web.overview.attention import Attention, Severity

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Job, ResearchRequest
    from aer.services.overview import Bounded

__all__ = ["GATE_ASKS", "items"]

TOOL: Final = "research"

# What each gate is asking the operator to do, in the second person, because the item is
# addressed to them. Every member of `GateKind` appears; a gate added without a phrase is a
# red build rather than a run described as waiting for nothing in particular.
GATE_ASKS: Final[dict[GateKind, str]] = {
    GateKind.PLAN: "approve its research plan",
    GateKind.UNMAPPED_CONCEPTS: "decide about the figures nothing could map",
    GateKind.PEER_SET: "confirm its peer set",
    GateKind.SECTOR_SPECIALIST: "acknowledge that the standard model does not fit its sector",
    GateKind.THEME_SET: "confirm the themes it belongs to",
    GateKind.ASSUMPTIONS: "confirm the assumptions its valuation will be built on",
    GateKind.BUDGET: "decide whether it may spend more than its ceiling",
    GateKind.FINAL: "review the finished report",
}


async def items(session: AsyncSession, *, user_id: uuid.UUID) -> Sequence[Attention]:
    """Everything the research tool has waiting, worst first within this tool."""
    collected: list[Attention] = []

    stopped = await overview_service.stopped_runs(session, user_id=user_id)
    for job, request in stopped.rows:
        gate = await pending_gate(session, job)
        asked = GATE_ASKS.get(gate) if gate else None
        collected.append(
            Attention(
                key=f"research.gate.{job.id}",
                tool=TOOL,
                severity=Severity.BLOCKED,
                title=f"{_named(request)} is waiting for you",
                detail=(
                    f"The run stopped so you could {asked}."
                    if asked
                    else "The run stopped at a gate and will not continue until you decide."
                ),
                href=f"/runs/{job.id}",
                action="Open the run",
                preview_href=f"/research/runs/{job.id}/preview",
            )
        )
    collected.extend(_and_more(stopped, "runs are waiting at a gate", Severity.BLOCKED, "gate"))

    capped = await overview_service.capped_runs(session, user_id=user_id)
    collected.extend(
        Attention(
            key=f"research.budget.{job.id}",
            tool=TOOL,
            severity=Severity.BLOCKED,
            title=f"{_named(request)} reached its cost ceiling",
            detail=(
                "Not a failure: the run stopped rather than spend past what you allowed, "
                "and it continues from where it stopped once you raise the ceiling."
            ),
            href=f"/runs/{job.id}",
            action="Open the run",
            preview_href=f"/research/runs/{job.id}/preview",
        )
        for job, request in capped.rows
    )
    collected.extend(_and_more(capped, "runs stopped at their ceiling", Severity.BLOCKED, "budget"))

    failed = await overview_service.failed_runs(session, user_id=user_id)
    collected.extend(
        Attention(
            key=f"research.failed.{job.id}",
            tool=TOOL,
            severity=Severity.BROKEN,
            title=f"{_named(request)} failed",
            detail=_reason(job),
            href=f"/runs/{job.id}",
            action="Read the timeline",
            preview_href=f"/research/runs/{job.id}/preview",
        )
        for job, request in failed.rows
    )
    collected.extend(_and_more(failed, "runs failed", Severity.BROKEN, "failed"))

    idle = await overview_service.unstarted_requests(session, user_id=user_id)
    collected.extend(
        Attention(
            key=f"research.idle.{request.id}",
            tool=TOOL,
            severity=Severity.IDLE,
            title=f"{_named(request)} has never been run",
            detail="The request is written and nothing has been spent on it.",
            href=f"/requests/{request.id}",
            action="Open the request",
        )
        for request in idle.rows
    )
    collected.extend(_and_more(idle, "drafts have never been run", Severity.IDLE, "idle"))

    return collected


def _named(request: ResearchRequest) -> str:
    """How a request is referred to in a sentence about it."""
    return request.company_name or request.ticker or "An unnamed request"


def _reason(job: Job) -> str:
    """One line from a failed run's recorded error, or an honest admission of none.

    Read rather than re-derived: the engine writes what stopped the run, and a feed that
    guessed would be describing a different failure from the one the console shows.
    """
    error = job.error or {}
    message = str(error.get("message") or "").strip()
    return message or "The run recorded no reason. Its timeline holds the last step it reached."


def _and_more(
    bounded: Bounded[object], noun: str, severity: Severity, slug: str
) -> list[Attention]:
    """The row that says a listing was cut short.

    A feed that showed the first eight and stopped would describe a smaller problem than
    the operator has, and would do it in a way nothing on the page contradicts.
    """
    if not bounded.remaining:
        return []
    return [
        Attention(
            key=f"research.more.{slug}",
            tool=TOOL,
            severity=severity,
            title=f"{bounded.remaining} more {noun}",
            detail="This list is bounded, so the rest are not shown here.",
            href="/requests",
            action="See every request",
        )
    ]
