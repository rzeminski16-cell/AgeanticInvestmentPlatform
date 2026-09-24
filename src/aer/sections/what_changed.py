"""The change summary: what a refresh found, at the head of the refreshed document (F4).

ADR 0131 §7. The section's platform-filled fields are composed here from the refresh's
own ``report_changes`` rows and the draft step's record, in the mechanism's order
(`08-mechanisms.md` §1.5): what broke, what moved materially, what is new, what did not
move, and which sections were re-drafted, carried, or carried stale. The writer's
commentary is the one model call the summary costs, held to the closing section's rule:
it interprets the rows and issues no instruction.

**The model never selects, orders or judges a row.** Every figure here is a change row
with the ids the drawer resolves; the prose is around them.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.changes import Movement
from aer.calc.comps import Audience
from aer.db.models import Job, ResearchRequest
from aer.sections.consequences import instructions_found
from aer.services import refresh as refresh_service

__all__ = [
    "WHAT_CHANGED_TITLE",
    "summary_for_audience",
    "what_changed_block",
    "what_changed_note",
    "what_changed_only",
    "what_changed_problems",
]

WHAT_CHANGED_TITLE = "What changed"

# What a watched row's sentence says after the move, and what the copy that leaves does not.
_FEEDS_A_PREMISE = " It feeds a premise you hold, and is material at half the ordinary threshold."


def summary_for_audience(content: dict[str, Any], audience: Audience) -> dict[str, Any]:
    """The change summary as one audience may have it (ADR 0122 §3, in ADR 0129's shape).

    The rows that name a premise — what broke, and why a watched figure was material at a
    smaller move — are the operator's own judgements read back. The operator's copy keeps
    them; the copy that leaves keeps every move as a move, drops the premise crossings into
    one sentence on the basis line, and loses the clause that says what a watched figure
    feeds. The contract admits no other key, so the sentence goes where the basis is.
    """
    broke = list(content.get("broke") or [])
    moved = list(content.get("moved") or [])
    watched = [
        row
        for row in moved
        if isinstance(row, dict) and str(row.get("movement") or "") == Movement.WATCHED.value
    ]
    if audience is Audience.INTERNAL or not (broke or watched):
        return content
    shown = dict(content)
    if broke:
        count = len(broke)
        crossed = (
            f" {count} premise{'' if count == 1 else 's'} the operator holds crossed "
            f"{'its' if count == 1 else 'their'} threshold; which {'one' if count == 1 else 'ones'}"
            " is withheld from this copy, because a premise is its holder's own view."
        )
        shown["basis"] = f"{str(content.get('basis') or '').rstrip()}{crossed}".strip()
        shown["broke"] = []
    if watched:
        shown["moved"] = [
            (
                {**row, "narrative": str(row.get("narrative") or "").replace(_FEEDS_A_PREMISE, "")}
                if row in watched
                else row
            )
            for row in moved
        ]
    return shown


async def what_changed_block(
    session: AsyncSession, *, job_id: uuid.UUID, request: ResearchRequest
) -> dict[str, Any]:
    """The platform-filled fields, from the refresh's rows. Empty rows on a full run."""
    job = await session.get(Job, job_id)
    if job is None or job.refreshes_report_id is None:
        return {
            "basis": "This run is not a refresh; there is no prior report to compare it with.",
            "broke": [],
            "moved": [],
            "new_documents": [],
            "unchanged": "",
            "sections": {"redrafted": [], "carried": [], "stale": []},
        }
    return await refresh_service.summary_block(session, job=job, request=request)


def what_changed_problems(content: dict[str, Any], block: dict[str, Any]) -> list[str]:
    """A commentary that instructs, or that cites a figure the rows do not carry."""
    commentary = str(content.get("commentary") or "")
    if not commentary:
        return []
    named = ", ".join(_labels(block)) or "none"
    return [
        f"The commentary says {phrase!r}, and this section states what moved since the prior "
        "report without issuing an instruction: no recommendation, no rating, no target, no "
        f"size. Say what the moves above amount to — {named} — and leave the decision to the "
        "operator."
        for phrase in instructions_found(commentary)
    ]


def what_changed_only(block: dict[str, Any]) -> str:
    """Why the rows are the section's whole content: nothing moved, so there is nothing
    for a commentary to interpret and a writer call would pay for prose about stillness."""
    if block.get("broke") or block.get("moved") or block.get("new_documents"):
        return ""
    return str(block.get("basis") or "Nothing moved since the prior report.")


def what_changed_note(block: dict[str, Any]) -> str:
    """What the writer is told about the block it cannot see."""
    named = ", ".join(_labels(block)) or "none"
    broke = len(block.get("broke") or [])
    return (
        "The block rendered above your commentary lists what moved between the prior report "
        f"and this refresh, largest relative change first: {named}. "
        + (
            f"{broke} premise{'' if broke == 1 else 's'} crossed a threshold and lead the list. "
            if broke
            else ""
        )
        + "Say what those moves amount to for a reader of the prior report, in two or three "
        "plain sentences, resting only on the rows shown. Quote no figure the rows do not "
        "carry. Issue no instruction: no buy, sell, add, trim, size, rating, target or "
        "recommendation."
    )


def _labels(block: dict[str, Any]) -> list[str]:
    return [
        str(row.get("label", "")).strip()
        for field in ("broke", "moved")
        for row in block.get(field) or []
        if str(row.get("label", "")).strip()
    ]


def is_material(movement: str) -> bool:
    return movement != Movement.UNCHANGED.value
