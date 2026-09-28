"""*Since {day}* on Today: what the platform did while the operator was away.

Page specification §1, corrected 28 September 2026. A briefing, not a queue: one line per thing
that happened, dated, with no controls — a line's title may lead to the record it describes, and
nothing more, because a button on every line would turn it back into the inbox the hub replaced
(page guide, *The hub*). What needs a decision is in *Needs you*, beside it.

Four sources, each read from its own service and scoped to the operator (ADR 0120): the monitor's
findings, the refreshes that finished, the reports approved, and the daily pass. Every line is
worded here from rows already stored; nothing is computed that a page could get wrong, and a
figure a line carries — a price move — is the one its finding already recorded.

A premise read and found holding is not a line of its own. Forty of those would bury the one
that broke, so they are counted into the daily pass's line instead: the briefing says what
changed, and that nothing else did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

from aer.core.enums import FindingKind, PremiseStatus
from aer.services import daily_pass, price_alerts, thesis_monitor
from aer.services import refresh as refresh_service
from aer.web.verdict import Count

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aer.db.models import Finding, User

__all__ = ["SHOWN", "Briefing", "Line", "ago", "briefing_for"]

# Enough to read over coffee. Past it the briefing says how many more and where they are.
SHOWN: Final = 8


@dataclass(frozen=True, slots=True)
class Line:
    """One thing that happened: its tone, what, why it matters, when, and where it is."""

    key: str
    tone: str
    title: str
    detail: str
    at: datetime
    when: str
    href: str = ""


@dataclass(frozen=True, slots=True)
class Briefing:
    """The lines to show, and how many more there were."""

    lines: tuple[Line, ...]
    more: int


async def briefing_for(
    session: AsyncSession, *, user: User, since: datetime, now: datetime
) -> Briefing:
    """Everything since ``since``, newest first."""
    lines: list[Line] = []
    held = 0
    moved = 0
    for finding in await thesis_monitor.findings_since(session, user_id=user.id, since=since):
        line = _finding_line(finding, now=now)
        if line is None:
            held += 1
            continue
        moved += finding.kind is FindingKind.PRICE_MOVE
        lines.append(line)
    for refresh, company, produced in await refresh_service.finished_since(
        session, user_id=user.id, since=since
    ):
        finished = refresh.finished_at
        assert finished is not None
        lines.append(
            Line(
                key=f"refresh.{refresh.id}",
                tone="info",
                title=f"{company}'s report was refreshed",
                detail=(
                    "You have read what changed."
                    if refresh.changes_read_at is not None
                    else "The summary of what changed has not been read."
                ),
                at=finished,
                when=ago(finished, now=now),
                href=f"/reports/{produced}" if produced is not None else f"/runs/{refresh.id}",
            )
        )
    passes = await daily_pass.passes_since(session, user_id=user.id, since=since)
    if passes:
        latest = max(job.finished_at for job in passes if job.finished_at is not None)
        lines.append(
            Line(
                key="daily.passes",
                tone="muted",
                title=_pass_title(len(passes)),
                detail=_pass_detail(held=held, moved=moved),
                at=latest,
                when=ago(latest, now=now),
            )
        )
    ordered = sorted(lines, key=lambda line: line.at, reverse=True)
    return Briefing(lines=tuple(ordered[:SHOWN]), more=max(len(ordered) - SHOWN, 0))


def _finding_line(finding: Finding, *, now: datetime) -> Line | None:
    """A finding's line, or ``None`` for a premise read and found standing."""
    href = f"/monitor/findings/{finding.id}"
    subject = _subject_of(finding)
    if finding.kind is FindingKind.PRICE_MOVE:
        return Line(
            key=f"finding.{finding.id}",
            tone="info",
            title=price_alerts.headline_of(finding),
            detail=_first_sentence(finding.justification),
            at=finding.created_at,
            when=ago(finding.created_at, now=now),
            href=href,
        )
    if finding.kind is FindingKind.STOPPED:
        return Line(
            key=f"finding.{finding.id}",
            tone="warning",
            title=f"The monitor stopped on {subject}",
            detail=_first_sentence(finding.justification),
            at=finding.created_at,
            when=ago(finding.created_at, now=now),
            href=href,
        )
    if finding.status is PremiseStatus.CONTRADICTED:
        title = f"A premise of {subject} broke"
        tone = "failure"
    elif finding.status is PremiseStatus.WEAKENED:
        title = f"A premise of {subject} weakened"
        tone = "warning"
    else:
        return None
    return Line(
        key=f"finding.{finding.id}",
        tone=tone,
        title=title,
        detail=_first_sentence(finding.justification),
        at=finding.created_at,
        when=ago(finding.created_at, now=now),
        href=href,
    )


def _subject_of(finding: Finding) -> str:
    if finding.thesis is not None:
        return finding.thesis.title
    if finding.security is not None:
        return finding.security.name or finding.security.ticker
    return "a listing no longer on record"


def _pass_title(passes: int) -> str:
    if passes == 1:
        return "The daily pass ran"
    return f"The daily pass ran {Count(passes, 'time', 'times').worded()}"


def _pass_detail(*, held: int, moved: int) -> str:
    """What the passes found, beyond the lines above: the premises that held, counted."""
    read = "It read the prices of every listing you hold or watch"
    if held:
        standing = Count(held, "premise was read and held", "premises were read and held")
        read = f"{read}, and {standing.worded()}"
    if moved:
        moves = Count(moved, "move past a threshold is", "moves past a threshold are")
        return f"{read}. {moves.worded().capitalize()} above."
    return f"{read}. Nothing else moved."


def _first_sentence(text: str) -> str:
    head, _, _ = text.partition(". ")
    return head if head.endswith(".") else f"{head}."


def ago(moment: datetime, *, now: datetime) -> str:
    """When, in the words a briefing uses: *today*, *yesterday*, *3 days ago*.

    Counted in calendar days rather than in hours, because *yesterday* at 23:50 and at 00:10
    is two different days to the operator and one to a stopwatch.
    """
    days = (now.date() - moment.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"
