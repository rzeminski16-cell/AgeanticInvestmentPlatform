"""Both documents through one shape, so a judge is reading the argument and not the letterhead.

`docs/V1.0_Alpha/11-testing-strategy.md` §3.5 asks for identically-shaped A/B paths, a seeded
assignment, and a recorded identity-guess hit rate. This module is all three, and none of it
makes a model call.

**September's panel was told it was reading blind, and it was not.** Measured over the
recorded texts rather than argued: the platform's note carries 316 Markdown footnote markers
and the console's carries none; the console's carries 451 ``[S12]`` citations and the
platform's carries none; the console numbers its sections and the platform does not; and all
three console notes open with the assistant's own working — *"I'll research this thoroughly
before writing. Let me begin with the primary sources."* — before the title. Every one of
those separates the two sides completely, and a judge needed only one of them. So the
September comparison is a comparison between two documents whose authorship was legible in
the first line, and ISSUE 2's number inherits that caveat whatever this round finds.

What a *tell* is, here: a pattern that appears in one side's documents and never in the
other's. Not a style opinion — each is counted on both sides before it is admitted, and
:func:`tells_in` is what the offline dry run asserts is empty after neutralising.

**What is deliberately not neutralised.** The argument, the figures, the structure of the
reasoning and the evidence behind each claim all survive: a blinding that flattened those
would be blinding the thing being judged. What goes is the *presentation* — marker style,
the header block, section numbering, the standing disclaimer, and the working the console
left at the top.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

__all__ = [
    "BASELINE",
    "PLATFORM",
    "TELLS",
    "Assignment",
    "Tell",
    "assign",
    "hit_rate",
    "neutralise",
    "tells_in",
]

PLATFORM: Final = "platform"
BASELINE: Final = "baseline"


@dataclass(frozen=True, slots=True)
class Tell:
    """One pattern that says which system wrote a document.

    ``betrays`` is the side it appears on, and is a claim about the recorded texts that
    `tests/test_blinding.py` re-counts on both sides — a tell that fires on both is not a
    tell, and one that fires on neither is a rule nobody needs.
    """

    name: str
    pattern: re.Pattern[str]
    betrays: str
    why: str


# Counted over `docs/plan/readiness-audit-2026-09/` on 19 September 2026, across all three
# subjects. The counts in the module docstring are msft1's; the test re-counts every pair.
TELLS: Final[tuple[Tell, ...]] = (
    Tell(
        name="markdown footnote markers",
        pattern=re.compile(r"\[\^[0-9]+\]"),
        betrays=PLATFORM,
        why="The renderer marks every cited figure `[^12]`; the console never does.",
    ),
    Tell(
        name="bracketed source markers",
        pattern=re.compile(r"\[S[0-9]+\]"),
        betrays=BASELINE,
        why="The console numbers its sources `[S12]`; the renderer never does.",
    ),
    Tell(
        name="numbered section headings",
        pattern=re.compile(r"^#{1,3} [0-9]+\.", re.M),
        betrays=BASELINE,
        why="The console numbers its sections; the spine's eighteen are never numbered.",
    ),
    Tell(
        name="the run's header block",
        pattern=re.compile(
            r"^\*\*(?:Ticker|As-of date|Base currency|Point-in-time|Generated|"
            r"Non-binding view):\*\*",
            re.M,
        ),
        betrays=PLATFORM,
        why="Six labelled lines under the title, in a fixed order, that only a run produces.",
    ),
    Tell(
        name="the standing disclaimer",
        pattern=re.compile(r"personal research tool"),
        betrays=PLATFORM,
        why="Every rendered report carries the same sentence, word for word.",
    ),
    Tell(
        name="the author's first person",
        pattern=re.compile(r"\bmy calculation\b|\bI have no position\b|\bmy own calculation\b"),
        betrays=BASELINE,
        why="The console writes as a person; the platform's sections never say 'my'.",
    ),
    Tell(
        name="the assistant's working",
        pattern=re.compile(
            r"(?:I'll research|Let me begin|Let me pull|Search budget is now exhausted|"
            r"Let me compute)"
        ),
        betrays=BASELINE,
        why=(
            "All three console notes open with the assistant's own narration before the "
            "title — the single most legible tell in the recorded corpus."
        ),
    ),
    Tell(
        name="the retired point-in-time line",
        pattern=re.compile(r"\bpoint-in-time\b", re.I),
        betrays=PLATFORM,
        why=(
            "The renderer's own vocabulary, in the header and in the prose. The rule went "
            "with ADR 0113, but September's texts predate that and a re-read meets it."
        ),
    ),
    # **`as at` is not here, and §3.5 names it.** Counted before it was admitted: the AZN
    # console note uses it twice — once inside a source's own title — so it is ordinary
    # financial English rather than a house habit, and a tell that fires on both sides
    # distinguishes nothing. It is still normalised on both sides by `_HOUSE_WORDS`,
    # because the strategy is right that it is a habit worth losing; what it is not is
    # evidence of who wrote the document.
)


def tells_in(text: str) -> tuple[Tell, ...]:
    """Every tell that fires on this text, in declared order.

    The offline dry run asserts this is empty after :func:`neutralise`, on both sides of
    every pair. A tell left in is a judge who did not have to read the document.
    """
    return tuple(tell for tell in TELLS if tell.pattern.search(text))


# Where the console's narration stops and the document starts: the first Markdown title.
# Split on it rather than on the narration's own phrases, because the phrases vary by
# subject and the title does not.
#
# **Not anchored to a line start**, which is the whole difficulty: two of the three console
# notes run their narration straight into the heading with no newline between —
# `…from the figures I read.# Microsoft Corporation (MSFT, NASDAQ) — Initiation`. Anchored,
# this found the heading *after* that one and left the tell in place, which is how the first
# measurement still showed the narration on all three baselines.
_TITLE: Final = re.compile(r"#\s+(?=\S)")

# Both marker styles, so both renumber into one.
_MARKER: Final = re.compile(r"\[\^([0-9]+)\]|\[S([0-9]+)\]")

# The header block, as whole lines, so removing one leaves no stub. Both sides' labels:
# the run's six and the console's own five, because a note that keeps its dateline while
# the other loses its header block has been made *more* distinguishable, not less.
_HEADER_LINE: Final = re.compile(
    r"^\*\*(?:Ticker|As-of date|Base currency|Point-in-time|Generated|Non-binding view|"
    r"Date of note|Date of analysis|Currency|Horizon|Reference price):\*\*.*$",
    re.M,
)

# The standing disclaimers, by a phrase each carries, removed as whole paragraphs. **Not by
# blockquote**: the renderer's is a blockquote under the header and the console's is bold
# text, and the renderer repeats its own at the foot as an ordinary paragraph — which is
# where it survived the first pass. Removing every blockquote would have taken quoted
# evidence with it, which is argument rather than presentation.
_DISCLAIMER_PHRASES: Final = re.compile(
    r"personal research (?:tool|document)|not regulated investment advice|"
    r"not investment advice|do their own work",
    re.I,
)

_SECTION_NUMBER: Final = re.compile(r"^(#{1,3}) [0-9]+\.\s*", re.M)

# The renderer's own vocabulary where it reaches the prose rather than the header. ADR 0113
# retired the rule, but September's texts predate that and a re-read of them meets it.
_HOUSE_WORDS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\bpoint-in-time window\b", re.I), "review window"),
    (re.compile(r"\bpoint-in-time\b", re.I), "review period"),
    (re.compile(r"\bas at\b", re.I), "as of"),
    # The console's first person, which is a tell and not an argument: both sides are
    # saying the same thing — that the figure is the author's arithmetic rather than a
    # source's — and only one of them says it with a pronoun.
    (re.compile(r"\bmy own calculations?\b", re.I), "calculated here"),
    (re.compile(r"\bmy calculations?\b", re.I), "calculated here"),
)


def neutralise(text: str) -> str:
    """One document, in the shape both sides share.

    Order matters: the narration goes first so nothing downstream sees it, then the
    header block and the disclaimers, then the markers renumber together so the two
    numbering schemes end up as one sequence.

    The markers are renumbered rather than removed. A judge under the sceptic's lens is
    asked whether it can get from a figure to a filing, and stripping the references
    would decide that question for it — which is the same mistake as leaving them in
    their two distinguishable styles, made in the other direction.
    """
    body = _after_the_title(text)
    body = _HEADER_LINE.sub("", body)
    body = _without_disclaimers(body)
    body = _SECTION_NUMBER.sub(r"\1 ", body)
    for pattern, neutral in _HOUSE_WORDS:
        body = pattern.sub(neutral, body)
    body = _renumbered(body)
    return _tidied(body)


def _after_the_title(text: str) -> str:
    """The document from its own title onwards, dropping anything a tool said first."""
    found = _TITLE.search(text)
    return text[found.start() :] if found else text


def _without_disclaimers(text: str) -> str:
    """Every paragraph carrying a standing-disclaimer phrase, on either side.

    By paragraph rather than by line, because both disclaimers are one long paragraph and
    a line-wise rule would leave whichever half did not carry the phrase.
    """
    kept = [
        paragraph for paragraph in text.split("\n\n") if not _DISCLAIMER_PHRASES.search(paragraph)
    ]
    return "\n\n".join(kept)


def _renumbered(text: str) -> str:
    """Both marker styles as one, numbered in order of first appearance.

    In order of appearance rather than keeping the original numbers, because the two
    sides number differently — the renderer per document, the console per source — and a
    marker sequence that starts at [^42] is itself a tell.
    """
    seen: dict[str, int] = {}

    def once(match: re.Match[str]) -> str:
        key = match.group(0)
        if key not in seen:
            seen[key] = len(seen) + 1
        return f"[{seen[key]}]"

    return _MARKER.sub(once, text)


def _tidied(text: str) -> str:
    """No run of blank lines longer than one, and no leading or trailing whitespace.

    Removing whole lines leaves holes, and a document with a three-line gap where its
    header used to be is a document that says something was removed from it.
    """
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


@dataclass(frozen=True, slots=True)
class Assignment:
    """Which side is A for one subject, and the record of it the judge never sees."""

    subject: str
    a: str
    b: str

    def identity(self) -> dict[str, str]:
        return {"A": self.a, "B": self.b}


def assign(*, seed: int, subjects: Sequence[str]) -> tuple[Assignment, ...]:
    """Which document is A, per subject: reproducible from the seed, balanced over the round.

    **Balanced, not merely random.** With two subjects a fair coin gives the platform
    document the A slot twice one time in four, and a judge that prefers A — an order
    effect nobody has measured away — would then be preferring the platform on every
    comparison for a reason that has nothing to do with the documents. So the sides are
    dealt half and half and the *seed decides which half*, which keeps reproducibility and
    removes the run where position and identity coincide.

    An odd number of subjects cannot be halved; the remainder is dealt by the seed, and is
    the one place the balance is one subject short of exact.
    """
    ordered = sorted(subjects)
    half = len(ordered) // 2
    # A digest rather than `random`, so the assignment is a pure function of the seed and
    # the subject list and does not depend on which interpreter or which module seeded the
    # global generator first.
    ranked = sorted(ordered, key=lambda name: _digest(seed, name))
    platform_first = set(ranked[:half])
    if len(ordered) % 2 and _digest(seed, "remainder") % 2:
        platform_first.add(ranked[half])
    return tuple(
        Assignment(
            subject=name,
            a=PLATFORM if name in platform_first else BASELINE,
            b=BASELINE if name in platform_first else PLATFORM,
        )
        for name in ordered
    )


def _digest(seed: int, name: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{name}".encode()).digest()[:8], "big")


def hit_rate(guesses: Iterable[Mapping[str, str]]) -> Decimal:
    """How often a judge named the platform's document correctly, over every guess.

    One guess is one judge's answer for one slot of one comparison: ``{"guessed": "A",
    "platform_was": "B"}``. At 0.5 the blinding held and the comparison stands on its own;
    materially above it, the comparison is reported with the caveat the pre-registration
    fixes in advance and never without it.

    Returns ``Decimal("0")`` for no guesses, which is not a hit rate and is why the caller
    checks the count: a round that forgot to ask reads as perfect blinding otherwise.
    """
    asked = list(guesses)
    if not asked:
        return Decimal(0)
    right = sum(1 for one in asked if one["guessed"] == one["platform_was"])
    return Decimal(right) / Decimal(len(asked))
