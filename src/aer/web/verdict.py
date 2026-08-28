"""What is true on this page, said before the record that proves it.

The redesign leads every operational surface with a plain-language sentence. ADR 0087 settles
where that sentence comes from, and the answer is two halves produced by different machinery.

**The composed half** — this module — is counts, states and figures assembled in Python from
rows the platform already holds, on every render. Live by construction, so it cannot go stale.
It ships everywhere, including the surfaces that never gain an authored half, and it is a
complete sentence on its own: a run that failed before its verdict step still gets a verdict.

**The authored half** is a model's interpretation of a subject that has stopped changing,
written once and stored. It is represented here by :class:`Authored`, which is deliberately a
type with no field for a source, a reference or a figure of its own — the house pattern ADR
0074 names, and the reason a caller cannot cite a verdict by mistake.

## What composition has to get right

Four things, and each is a way a leading sentence lies about the page beneath it.

**A zero is not a count.** "0 red-team challenges are available to read" is noise dressed as
information. A clause whose count is nothing is dropped, and when every clause drops the
verdict falls back to a sentence written for that case rather than rendering empty.

**A breakdown accounts for its total.** "42 claims · 38 confirmed · 3 unconfirmed" invites the
reader to work out that one claim is unaccounted for, or — far more likely — not to notice. If
parts are given at all, they must sum to the total, and a mapping that forgot a state is a
raise rather than a quiet subtraction.

**An incomplete record never reads as a complete one.** The attention feed reports a provider
that could not be asked (`web/overview/attention.py`), and a verdict composed over that feed is
counting part of the estate. It says so, and — the rule that matters — **it is never rendered
in the success tone**, because "nothing is waiting for you" is the one answer a partial count
must never give.

**One and two are words.** A sentence beginning "2 items" is a sentence set in the wrong
register, and the design handoff writes "Two decisions are stopping research; one run needs
diagnosis". Tallies keep their numerals, because a tally is a column of figures written on one
line and reads as one.

**Pure data, no I/O.** Same discipline as `web/vocabulary.py`: nothing here reads a clock, a
session or a setting. Handlers gather the rows; this decides the words.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from aer.web.vocabulary import Tone

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "Authored",
    "Count",
    "Part",
    "Verdict",
    "VerdictError",
    "sentence",
    "tally",
]

# Small numbers are words in a sentence. Nine is where the convention stops in UK style, and
# beyond it a numeral is easier to read than "seventeen".
_WORDS: Final[tuple[str, ...]] = (
    "no",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)

_CLAUSE_JOIN: Final = "; "
_TALLY_JOIN: Final = " · "


class VerdictError(ValueError):
    """A verdict was composed from parts that do not describe the record.

    A ``ValueError`` rather than an `AerError`: this is a programming mistake at a call site,
    caught by a test, and not a condition an operator can act on.
    """


@dataclass(frozen=True, slots=True)
class Count:
    """A number of things, with the words for one of them and for several.

    **Both forms are supplied by the caller.** Inflecting a noun in code is how "1 companys"
    reaches a screen, and the phrase that inflects here is not only the noun — "item is
    waiting" and "items are waiting" differ in the verb too.

    The phrase is a predicate, not a noun on its own, so a clause reads as a statement:
    ``Count(2, "item is waiting for you", "items are waiting for you")``.
    """

    n: int
    one: str
    many: str

    def __post_init__(self) -> None:
        if self.n < 0:
            message = f"A count of {self.n} is not a number of things."
            raise VerdictError(message)
        if not self.one.strip() or not self.many.strip():
            message = "A count needs both a singular and a plural phrase; neither is guessed."
            raise VerdictError(message)

    @property
    def phrase(self) -> str:
        """The phrase alone, inflected, with no number in front of it."""
        return self.one if self.n == 1 else self.many

    def worded(self) -> str:
        """For a sentence: "one item is waiting for you", "12 items are waiting for you"."""
        number = _WORDS[self.n] if self.n < len(_WORDS) else f"{self.n:,}"
        return f"{number} {self.phrase}"

    def numbered(self) -> str:
        """For a tally: "18 sources acquired", numerals throughout."""
        return f"{self.n:,} {self.phrase}"


@dataclass(frozen=True, slots=True)
class Part:
    """One share of a tally's total. An adjective or participle, never inflected."""

    n: int
    label: str

    def __post_init__(self) -> None:
        if self.n < 0:
            message = f"A part of {self.n} is not a share of anything."
            raise VerdictError(message)
        if not self.label.strip():
            message = "A tally part with no label is a number nobody can read."
            raise VerdictError(message)

    def rendered(self) -> str:
        return f"{self.n:,} {self.label}"


@dataclass(frozen=True, slots=True)
class Authored:
    """A model's reading of something that has stopped changing (ADR 0087).

    **There is no field here for a source, a reference, an excerpt or a figure**, and that
    absence is the enforcement rather than a convention: a type with no column for the
    forbidden thing is the only rule a later prompt, a later template and a later person under
    time pressure are all equally unable to argue with (ADR 0074).

    So no claim can name a verdict, no citation can resolve to one, and nothing here reaches a
    shareable surface as support for anything. It is interpretation, it sits beside composed
    counts that are not, and it is never the only thing on the page — :class:`Verdict` will not
    hold one without a composed half.
    """

    sentence: str
    tone: Tone

    def __post_init__(self) -> None:
        if not self.sentence.strip():
            message = "An authored verdict with no sentence is a model call nobody used."
            raise VerdictError(message)


@dataclass(frozen=True, slots=True)
class Verdict:
    """The sentence a page leads with, and optionally the interpretation beside it."""

    composed: str
    tone: Tone
    authored: Authored | None = None
    is_complete: bool = True
    """Whether the rows this was composed from are all of them.

    False when a source of the count could not be asked. The verdict then says so in its own
    words *and* refuses the success tone, because a partial count rendered as "nothing is
    waiting for you" is the one wrong answer that looks exactly like the right one.
    """

    def __post_init__(self) -> None:
        if not self.composed.strip():
            message = (
                "A verdict has no composed half. Every surface gets one, including a run that "
                "failed before it could write anything — the fallback exists for that case."
            )
            raise VerdictError(message)
        if not self.is_complete and self.tone is Tone.SUCCESS:
            message = (
                f"The verdict {self.composed!r} is composed from an incomplete record and "
                "rendered as a success. A count taken over part of the estate must not be "
                "presented as the all-clear."
            )
            raise VerdictError(message)


def sentence(
    clauses: Iterable[Count | str],
    *,
    when_none: str,
    tone: Tone,
    authored: Authored | None = None,
    is_complete: bool = True,
    gap: str = "",
) -> Verdict:
    """Independent statements, joined into one sentence, zeroes dropped.

    ``Two items are waiting for your decision; one item needs diagnosis.``

    A plain string clause is included as written, for the parts of a verdict that are a state
    rather than a count — "the draft is complete", "approved on 24 August". ``when_none`` is the
    sentence for the case where every clause drops, and is required: there is no render in which
    a page leads with nothing.

    ``gap`` is appended when ``is_complete`` is false, and saying what is missing is not
    optional there — an incomplete verdict that does not admit it is worse than no verdict.

    Raises:
        VerdictError: If the record is incomplete and no ``gap`` says what is missing, or if
            the resulting verdict would be empty or falsely reassuring.
    """
    rendered = [
        clause if isinstance(clause, str) else clause.worded()
        for clause in clauses
        if isinstance(clause, str) or clause.n
    ]
    # A trailing full stop is dropped before joining and restored at the end, so a clause
    # written as a whole sentence still reads correctly in the middle of one. Without this the
    # module's claim that a clause works in any position is true only of the last position.
    kept = [text.strip().rstrip(".") for text in rendered if text.strip().rstrip(".")]

    if not is_complete and not gap.strip():
        message = (
            "A verdict composed from an incomplete record must say what is missing. Silence "
            "there reads as a complete count, which is precisely what it is not."
        )
        raise VerdictError(message)

    body = _CLAUSE_JOIN.join(kept) if kept else when_none.strip()
    if not is_complete:
        body = _CLAUSE_JOIN.join([body, gap.strip()]) if body else gap.strip()

    return Verdict(
        composed=_as_sentence(body),
        tone=tone,
        authored=authored,
        is_complete=is_complete,
    )


def tally(
    total: Count,
    parts: Sequence[Part] = (),
    *,
    when_none: str,
    tone: Tone,
    authored: Authored | None = None,
    is_complete: bool = True,
) -> Verdict:
    """A total and its breakdown, on one line, numerals throughout.

    ``42 claims · 38 confirmed · 3 unconfirmed · 1 failed``

    **The parts must account for the total.** Supplying some of them is allowed — an empty
    sequence claims nothing about the breakdown — but a breakdown that does not sum invites the
    reader to conclude the remainder is nothing, and the usual cause is a state nobody mapped.

    Raises:
        VerdictError: If the parts are given and do not sum to the total.
    """
    if parts and sum(part.n for part in parts) != total.n:
        counted = sum(part.n for part in parts)
        message = (
            f"The breakdown of {total.numbered()} accounts for {counted:,}. A tally that does "
            "not sum tells the reader the rest is nothing, and the usual reason is a state "
            "with no part of its own."
        )
        raise VerdictError(message)

    if not total.n:
        body = when_none.strip()
    else:
        body = _TALLY_JOIN.join([total.numbered(), *(part.rendered() for part in parts if part.n)])

    return Verdict(composed=body, tone=tone, authored=authored, is_complete=is_complete)


def _as_sentence(body: str) -> str:
    """A capital at the front and a full stop at the end, decided once.

    Clauses are written lowercase and unpunctuated by their callers so they can appear in any
    position; deciding the capital here is what lets "one item needs diagnosis" lead a sentence
    on one page and follow a semicolon on another.
    """
    text = body.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else f"{text}."
