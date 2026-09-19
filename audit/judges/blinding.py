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
:func:`tells_in` after neutralising is what the offline dry run pins against
:data:`LEFT_STANDING`.

**The list was wrong, and a judge said so before any test did.** The first live identity
guess came back three of three, every judge *certain*, and every one of them naming
`aer.calc.ratios:gross_margin` and the validator's metrics table before anything else. The
tells above had been written by reading the two documents' surface style — markers,
headings, disclaimers — and had never looked *inside the prose*, where the renderer prints
the machinery that produced each figure: module paths, code versions, stored Decimal scales
and its own symbol names. 367 code identifiers across the three reports and none in any
console note. Those are now tells too, and the lesson is the cheaper half of the row: an
instrument built by eye finds what its author was already looking at.

**What is deliberately not neutralised.** The argument, the figures, the structure of the
reasoning and the evidence behind each claim all survive: a blinding that flattened those
would be blinding the thing being judged. What goes is the *presentation* — marker style,
the header block, section numbering, the standing disclaimer, the working the console left
at the top, and the machinery. Two things stay on purpose and each has its reason written
where it is decided: the evidence base, which is substance (see the note at the foot of
:data:`TELLS`), and full stored precision, which cannot go without rewriting a figure (see
:data:`LEFT_STANDING`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from aer.core.enums import SourceTier

__all__ = [
    "BASELINE",
    "LEFT_STANDING",
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

# One `snake_case` token, bounded so it is a word in prose rather than a piece of a path.
#
# The dot rules are the fiddly half and each cost a measurement. Excluding *any* adjacent
# dot was the first attempt, and it silently skipped `citation_accuracy.` at the end of a
# sentence — a leak the offline test caught only because a case was written for it. So the
# dot is refused only when a word character follows it (`some_thing.attr`, a real path) or
# precedes it (`module.some_thing`), and a full stop is just a full stop.
_IDENT_BOUNDED: Final = r"(?<![`\w/])(?<!\w\.)[a-z][a-z0-9]+(?:_[a-z0-9]+)+(?![\w/])(?!\.\w)"


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
    # The five below were not written from reading the documents. They were named by the
    # judges themselves, on the first live identity guess — three of three, all *certain*,
    # all three quoting `aer.calc.ratios:gross_margin` before anything else. The list above
    # was assembled by eye from the two documents' surface style and never looked inside
    # the prose, where the renderer prints the machinery that produced each figure.
    Tell(
        name="a code identifier in the prose",
        # No backtick requirement and no module prefix: the metrics table prints
        # `citation_accuracy` as a bare word in a cell, and the footnotes print a dotted
        # module path. Both are the same habit. 367 across the three reports, none in any
        # console note — English prose has no underscores in it.
        pattern=re.compile(_IDENT_BOUNDED),
        betrays=PLATFORM,
        why=(
            "Every judge on the first live guess named this first. The platform writes its "
            "own symbol names into the document; a person writing about a company does not."
        ),
    ),
    Tell(
        name="the module path behind a figure",
        pattern=re.compile(r"\baer(?:\.[a-z_]+)+\b"),
        betrays=PLATFORM,
        why="A Python import path under a number is the letterhead of the thing that ran.",
    ),
    Tell(
        name="an architecture decision cited to a reader",
        pattern=re.compile(r"\bADR \d{4}\b"),
        betrays=PLATFORM,
        why="The platform's own decision record, numbered, in a report about a company.",
    ),
    Tell(
        name="the code version that produced it",
        pattern=re.compile(r"code version `[0-9a-f]{6,}`"),
        betrays=PLATFORM,
        why="A git hash in a footnote is provenance written as an artefact rather than a fact.",
    ),
    Tell(
        name="a Decimal printed as it is stored",
        # `0E-8` is zero in Decimal's own exponent notation and `1.00000000` is one at the
        # scale the metric was stored at. Neither is a number a person writes.
        pattern=re.compile(r"\b0E-\d+\b|\b\d+\.\d{7,}\b"),
        betrays=PLATFORM,
        why="Decimal's stored scale reaching the page — `0E-8` where a reader expects zero.",
    ),
    Tell(
        name="the renderer's note about its other editions",
        pattern=re.compile(r"Rendered in the [A-Za-z ]+ editions?"),
        betrays=PLATFORM,
        why="A placeholder that only exists because one renderer produces three formats.",
    ),
    Tell(
        name="a stored enum shouted into the prose",
        # 135 across the three reports and none in any console note, and all of them one of
        # two source tiers. The journey harness found the same habit on the web surfaces and
        # `46c5727` gave every tier a spoken form; September's texts predate that.
        pattern=re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b"),
        betrays=PLATFORM,
        why="`tier T1_REGULATORY` under a figure a reader is being asked to trust.",
    ),
    # **`as at` is not here, and §3.5 names it.** Counted before it was admitted: the AZN
    # console note uses it twice — once inside a source's own title — so it is ordinary
    # financial English rather than a house habit, and a tell that fires on both sides
    # distinguishes nothing. It is still normalised on both sides by `_HOUSE_WORDS`,
    # because the strategy is right that it is a habit worth losing; what it is not is
    # evidence of who wrote the document.
    #
    # **What is deliberately still not a tell, after two live guesses.** Three things, each
    # named by judges and each left alone on purpose, because each is the product rather
    # than its wrapping:
    #
    # *The evidence base.* One XBRL feed and a handful of filings on one side, thirty
    # heterogeneous web sources on the other. Every judge's second reason, both rounds. It
    # is exactly what the sceptic's lens exists to weigh.
    #
    # *The validator's scoreboard.* A table of checks, thresholds and verdicts, named first
    # by every judge in the second round. Only a pipeline produces one — and that is the
    # point of having one. Deleting it to win a blinding would delete the strongest thing
    # the platform does.
    #
    # *The refusals and the escalations.* "Insufficient evidence: this section's policy
    # requires one distinct source", "escalated for human decision at approval". These
    # report what the run did, to a reader who should know it.
    #
    # Which is the finding, and it is a real one: **these two documents cannot be blinded
    # to authorship, because what distinguishes them is what they are.** The rate is
    # reported at whatever it comes to, and the pre-registration decided in advance that a
    # rate above chance attaches a caveat rather than voiding the comparison.
)


# **The one tell the neutraliser refuses to remove, and why it is named rather than
# dropped.** `0.073126011145` is a cost of debt printed at the precision it is stored at,
# and twelve significant figures on a discount-rate input is not a number any person
# writes. It is as legible as the module paths were. Removing it means *rewriting a digit*,
# and that is where this module stops: renumbering a marker that goes wrong produces a
# citation pointing at the wrong source, which a reader can see, while rounding that goes
# wrong produces a figure that is simply false, which nobody can see. The asymmetry of the
# harm decides it.
#
# So the dry run reports a residue instead of claiming none. The remedy is in the renderer,
# which should not be printing stored precision to a reader in the first place — the same
# class of defect as `tier T1_REGULATORY` in a footnote, and recorded as its own work.
# A tell arriving here later is a decision somebody has to make on purpose, with a reason
# written next to it; the test pins the set, so growing it silently is not available.
LEFT_STANDING: Final[dict[str, frozenset[str]]] = {
    "msft1": frozenset({"a Decimal printed as it is stored"}),
    "mtb": frozenset({"a Decimal printed as it is stored"}),
}


def tells_in(text: str) -> tuple[Tell, ...]:
    """Every tell that fires on this text's prose, in declared order.

    The offline dry run asserts this is :data:`LEFT_STANDING` after :func:`neutralise`, on
    both sides of every pair. A tell left in is a judge who did not have to read the
    document.

    **Prose, because a URL is not the author's writing.** A price vendor's query string
    carries `api_token`, which looks exactly like the code identifiers the platform prints
    and is nothing of the kind: it was copied from a source, both sides copy such things,
    and neither side chose the spelling. Counting it would report a leak where there is a
    citation — and the neutraliser leaves URLs alone for the same reason, so a tell that
    read them would be one the neutraliser could never clear.
    """
    prose = _URL.sub(" ", text)
    return tuple(tell for tell in TELLS if tell.pattern.search(prose))


_URL: Final = re.compile(r"https?://[^\s)\]]+")


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
    # The console's account of its own tool budget, which the judges read as clearly as
    # they read the platform's module paths: *"My web research budget was exhausted"*.
    # Both sides ran out of something; only one narrates it in the document.
    (
        re.compile(r"\bmy (?:web )?research budget was exhausted\b", re.I),
        "the evidence gathered did not reach this",
    ),
    (
        re.compile(r"\b(?:search|research) budget is now exhausted\b", re.I),
        "no further evidence was gathered",
    ),
)

# The machinery the renderer prints under and around its figures. Each of these is a *how*
# rather than a *what*: the claim that a figure was computed from a named formula survives
# every one of them, and what goes is the symbol, the hash and the stored scale.
#
# Ordered, and the order is load-bearing: the footnote parenthetical must go before the
# bare-identifier rule, or the module path is half-rewritten into prose first.
_Replacement = str | Callable[[re.Match[str]], str]

_MACHINERY: Final[tuple[tuple[re.Pattern[str], _Replacement], ...]] = (
    # `(`aer.calc.ratios:gross_margin`, code version `6d9c6c80d78a`)` — 236 times across the
    # three reports. The footnote around it already states the formula and the value, so
    # the parenthetical loses its identifiers and keeps its claim.
    (
        re.compile(r"\(`aer\.[a-z_.]+:[a-z_0-9]+`,\s*code version `[0-9a-f]{6,}`\)"),
        "(formula and code version recorded)",
    ),
    # Any dotted module path, not only the `module:symbol` form the footnotes use. The
    # first version required the colon and missed `proposed by aer.services.
    # assumption_proposals`, which three of the nine judges then named *first* — 248 across
    # the three reports against the twelve the colon form catches. A rule written from one
    # example is a rule that closes one example.
    (re.compile(r"`?\baer(?:\.[a-z_]+)+(?::[a-z_0-9]+)?`?"), "the platform's own code"),
    (re.compile(r"code version `[0-9a-f]{6,}`"), "a recorded code version"),
    # An architecture decision's number, cited in a report a private investor reads.
    (re.compile(r"\bADR \d{4}\b"), "a recorded decision"),
    (re.compile(r"\*?Rendered in the [A-Za-z ]+ editions? of this report\.\*?"), ""),
    # `tier T1_REGULATORY` reads as `a regulatory filing`, which is what the renderer itself
    # says since `46c5727`. The label is dropped with the code: "tier a regulatory filing"
    # would be the leak replaced by a stutter.
    (re.compile(r"\btier ([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)"), lambda f: _spoken(f.group(1))),
    (re.compile(r"\b([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)\b"), lambda f: _spoken(f.group(1))),
)


def _spoken(name: str) -> str:
    """A stored code as the words the product already has for it, where it has them.

    The two in September's texts are both source tiers, and `SourceTier.spoken` is where
    that wording lives — read from the product rather than copied here, so a tier reworded
    once is reworded in both places. Anything else becomes its own words, which is worse
    prose than a real phrase and still better than a symbol.
    """
    try:
        return SourceTier[name].spoken
    except KeyError:
        return name.lower().replace("_", " ")


# A `snake_case` token in prose, which is the general form of the leak the first live guess
# found: 367 across the three reports, none in any console note. Replaced by its own words
# rather than removed, because `citation_accuracy` means *citation accuracy* and a reader
# loses nothing by being told it that way.
#
# **Alternated with a URL, and the URL wins.** The first version rewrote `api_token` inside
# a price-vendor query string and lost two source links out of the AZN report — a blinding
# that breaks a citation has damaged the evidence to hide the letterhead, which is the one
# thing it must never do. Matching the URL first and returning it untouched is what keeps
# the rule inside prose, where it belongs.
_IDENTIFIER: Final = re.compile(rf"(?P<url>https?://[^\s)\]]+)|(?P<ident>{_IDENT_BOUNDED})")

# `0E-8` is zero and `1.00000000` is one, both at the scale the metric was stored at.
# Neither is rewritten as a different number: the zero becomes `0` and the rest lose only
# trailing zeros, down to two decimal places, so the printed value is the value. Both are
# alternated with a URL for the same reason as above.
_STORED_ZERO: Final = re.compile(r"(?P<url>https?://[^\s)\]]+)|(?P<zero>\b0E-\d+\b)")
_STORED_SCALE: Final = re.compile(
    r"(?P<url>https?://[^\s)\]]+)|\b(?P<whole>\d+)\.(?P<keep>\d{2}\d*?)0+\b"
)


def neutralise(text: str) -> str:
    """One document, in the shape both sides share.

    Order matters, and every step of it is load-bearing. The narration goes first so
    nothing downstream sees it; then the header block and the disclaimers; then the
    machinery, whose module-path rule must run before the bare-identifier rule or a path
    is half-rewritten into prose; then the stored scales; then the identifiers; and the
    markers renumber last, so the two numbering schemes end up as one sequence.

    The markers are renumbered rather than removed. A judge under the sceptic's lens is
    asked whether it can get from a figure to a filing, and stripping the references
    would decide that question for it — which is the same mistake as leaving them in
    their two distinguishable styles, made in the other direction.

    Measured on the recorded corpus: no document loses more than 4% of its length, every
    URL survives, and no money or percentage figure in any of the six changes.
    """
    body = _after_the_title(text)
    body = _HEADER_LINE.sub("", body)
    body = _without_disclaimers(body)
    body = _SECTION_NUMBER.sub(r"\1 ", body)
    for pattern, neutral in (*_HOUSE_WORDS, *_MACHINERY):
        body = pattern.sub(neutral, body)
    body = _without_stored_scale(body)
    body = _IDENTIFIER.sub(_in_words, body)
    body = _renumbered(body)
    return _tidied(body)


def _in_words(found: re.Match[str]) -> str:
    """One identifier as the words it already is, or a URL left exactly as it was."""
    url = found.group("url")
    return url if url is not None else found.group("ident").replace("_", " ")


def _without_stored_scale(text: str) -> str:
    """Decimal's own rendering, as a number rather than as a storage detail.

    Value-preserving by construction: the zero pattern matches only an exact zero, and the
    other strips *trailing* zeros alone and never below two decimal places, so `0.005` keeps
    its third digit and `1.068` keeps its third. Nothing here rounds, and nothing here
    touches a significant digit — a blinding that quietly moved a figure would be corrupting
    the evidence in order to hide who assembled it. What it therefore cannot fix is
    :data:`LEFT_STANDING`.
    """
    text = _STORED_ZERO.sub(lambda f: f.group("url") or "0", text)
    return _STORED_SCALE.sub(
        lambda f: f.group("url") or f"{f.group('whole')}.{f.group('keep')}", text
    )


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
