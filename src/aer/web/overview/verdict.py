"""The sentence the front door leads with, composed from the feed beneath it.

**This verdict has no authored half and never will** (ADR 0087). Its subject is live state
aggregated across every open run, so there is no moment at which it is frozen and nothing to
write it once about; authoring it on each page load would be a model call per view, which
every cost rule in this platform exists to prevent. It is composed, permanently.

That is worth more than it looks. The main menu is the page an operator opens when something
is wrong, and it already renders with no database and no provider configured. A verdict that
needed a model would take the front door down with the thing that broke.

**The rule this module exists to keep.** `attention.items_for` reports a provider it could not
ask rather than swallowing it, because an empty feed is a claim — *nothing is waiting for you*
— and that is exactly the claim a broken query makes by accident. The verdict is where that
claim is actually made in words, so it is where the guard has to hold: when any provider
failed, the sentence says the estate is only partly counted, and the tone can never be the
all-clear.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from aer.web.overview.attention import Severity
from aer.web.verdict import Count, Verdict, sentence
from aer.web.vocabulary import Tone

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aer.web.overview.attention import Attention

__all__ = ["NOTHING_WAITING", "overview_verdict"]

# What the page says when the feed is empty and the feed is trustworthy. Both halves matter:
# this sentence is only ever reached when every provider answered.
NOTHING_WAITING: Final = "nothing is waiting for you"

# And what it says to somebody who has not started. The distinction is the whole reason both
# sentences exist: an empty work list is an achievement to one reader and a dead platform to
# the other, and the verdict is the first line either of them reads. Congratulating a new
# operator on being up to date, two inches above a panel telling them how to begin, is the
# front door contradicting itself.
NOTHING_COMMISSIONED: Final = "no research has been commissioned yet"

# And what it says when it could not ask at all. The page beneath this renders a notice naming
# the failure, so the verdict states the consequence rather than repeating the diagnosis.
_NOT_ASKED: Final = "nothing here has been counted"

_NOT_ASKED_GAP: Final = (
    "at least one tool could not be asked what it is holding, so this is not the whole estate"
)

_NOT_REACHED_GAP: Final = (
    "the record could not be read, so nothing below is a count of what is waiting"
)

# What each severity is called when it is counted rather than listed. Singular and plural are
# both written out; inflecting in code is how "1 items need diagnosis" reaches a screen.
_PHRASES: Final[dict[Severity, tuple[str, str]]] = {
    Severity.BLOCKED: ("item is waiting for your decision", "items are waiting for your decision"),
    Severity.BROKEN: ("item needs diagnosis", "items need diagnosis"),
    Severity.IDLE: ("item has not been started", "items have not been started"),
}

# The order the clauses are said in, matching the order the feed is grouped into below. A
# sentence that led with the idle work while the page listed the blocked work first would read
# as being about a different screen.
_CLAUSE_ORDER: Final[tuple[Severity, ...]] = (
    Severity.BLOCKED,
    Severity.BROKEN,
    Severity.IDLE,
)

# How loud the page is, which is deliberately not the order above. The feed is ordered by what
# to do first — a stopped run resumes the moment somebody decides, so it leads. The tone is
# ordered by what has gone wrong, and a fault is louder than a decision however you sort the
# list. Worst first; the first match wins.
_TONES: Final[tuple[tuple[Severity, Tone], ...]] = (
    (Severity.BROKEN, Tone.FAILURE),
    (Severity.BLOCKED, Tone.WARNING),
    (Severity.IDLE, Tone.INFO),
)


def overview_verdict(
    items: Sequence[Attention], *, gathered: bool = True, first_run: bool = False
) -> Verdict:
    """What is waiting, across every tool, in one sentence.

    ``Two items are waiting for your decision; one item needs diagnosis.``

    Counting rows the page is about to list, so the sentence and the list cannot disagree: a
    severity with nothing in it contributes no clause, and an empty feed produces the sentence
    written for an empty feed rather than an empty verdict.

    ``first_run`` is true for an operator who has never written a request. Their empty feed
    is not the same fact as a caught-up operator's, and the sentence has to match the panel
    underneath it or the page argues with itself in its own first line.

    ``gathered`` is false when the feed could not be built at all — the database is unreachable
    or its schema has drifted, the two cases this page is specifically designed to survive.
    **That is not the same as an empty feed and must never render as one.** The launcher still
    draws, the notice still says which failure it is, and the verdict says it counted nothing
    rather than that there was nothing to count.
    """
    counts = dict.fromkeys(_CLAUSE_ORDER, 0)
    for item in items:
        counts[item.severity] += 1

    complete = gathered and not any(item.feed_is_incomplete for item in items)
    return sentence(
        [Count(counts[severity], *_PHRASES[severity]) for severity in _CLAUSE_ORDER],
        when_none=_when_empty(gathered=gathered, first_run=first_run),
        tone=_tone_for(counts, complete=complete, first_run=first_run),
        is_complete=complete,
        gap="" if complete else (_NOT_ASKED_GAP if gathered else _NOT_REACHED_GAP),
    )


def _when_empty(*, gathered: bool, first_run: bool) -> str:
    """Which of three sentences an empty feed gets. They are three different facts."""
    if not gathered:
        return _NOT_ASKED
    return NOTHING_COMMISSIONED if first_run else NOTHING_WAITING


def _tone_for(counts: dict[Severity, int], *, complete: bool, first_run: bool = False) -> Tone:
    """Worst thing present, and never the all-clear over a partial count.

    An empty feed is a success only when the feed is the whole of it. Reporting "nothing is
    waiting for you" in green over an estate nobody finished counting is the single failure
    this whole path is built to avoid, and `Verdict` refuses that pairing outright — this
    returns `INFO` first so the refusal stays a backstop rather than the mechanism.
    """
    for severity, tone in _TONES:
        if counts[severity]:
            return tone
    if first_run:
        # Not the all-clear. Nothing is waiting because nothing has been started, and a green
        # tick over that is the platform congratulating somebody for not using it.
        return Tone.INFO
    return Tone.SUCCESS if complete else Tone.INFO
