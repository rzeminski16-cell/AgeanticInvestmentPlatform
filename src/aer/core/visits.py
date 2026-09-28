"""When Today's briefing starts: the operator's last look, and what counts as a new one.

Page specification §1, corrected 28 September 2026: *Since {day}* is what happened while the
operator was away, and *{day}* is when they last opened Today, which the page records. A first
visit shows the last seven days.

**A visit is not a page load.** Moving the window on every render would empty the briefing the
moment the operator reloaded the page, or came back to it from a report five minutes later — it
would describe the last five minutes rather than the time they were away. So a visit ends only
after a gap: while the operator keeps coming back within it, the last look *before the visit*
stays the window's start, and the first look after the gap starts a new visit from the last
look before it.

Two moments are stored, and they are both looks: the last render, and the last look before the
current visit began — never a window start, which on a first visit would be a date nobody
looked on, and *you last looked on 21 September* would then be a sentence about nothing.

Pure, in ``core`` (``mypy --strict``): the clock is the caller's, so a test can hold it still
at the hour it wants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

__all__ = ["FIRST_LOOK", "VISIT_GAP", "Visit", "visit_of"]

VISIT_GAP: Final = timedelta(hours=3)
"""How long away before the next look is a new visit. Long enough to cover reading a report
and coming back; short enough that the morning after is always a new visit."""

FIRST_LOOK: Final = timedelta(days=7)
"""What a first visit's briefing covers, having no last look to start from (§1)."""


@dataclass(frozen=True, slots=True)
class Visit:
    """The window this render briefs."""

    since: datetime
    """Where the briefing starts."""

    last_looked: datetime | None
    """The last look before this visit, or ``None`` on the first: what to store, as well as
    what the page says about when the operator was last here."""

    @property
    def is_first(self) -> bool:
        return self.last_looked is None


def visit_of(*, seen_at: datetime | None, looked_before: datetime | None, now: datetime) -> Visit:
    """The window for a look at ``now``.

    ``seen_at`` is the last time Today was rendered; ``looked_before`` is the last look before
    the visit that render belonged to, or ``None`` if that visit was the first.

    Raises:
        ValueError: If a timestamp is naive. A window compared across a naive and an aware
            time would be a comparison between two different clocks.
    """
    for moment in (seen_at, looked_before, now):
        if moment is not None and moment.tzinfo is None:
            message = "A visit is measured in aware timestamps; a naive one has no clock."
            raise ValueError(message)
    if seen_at is not None and now - seen_at >= VISIT_GAP:
        return Visit(since=seen_at, last_looked=seen_at)
    if seen_at is None or looked_before is None:
        return Visit(since=now - FIRST_LOOK, last_looked=None)
    return Visit(since=looked_before, last_looked=looked_before)
