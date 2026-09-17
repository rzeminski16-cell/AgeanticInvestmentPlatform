"""How a figure's name appears inside a sentence, and which period that sentence is about.

The vocabulary half of ADR 0125's negative-assertion scan. A stored figure is named
``value_per_share`` or ``operating_cash_flow``; a section writing about it writes "value per
share" and "operating cash flow", and a section *denying* it writes "operating cash flow is
not among the figures available here". Deciding whether those are the same figure is a
string question, and it lives here so that it is pure, testable without a database, and in
one place rather than one per caller.

**Everything here errs towards saying no.** The scan it feeds is specified to refuse a run
once two live runs come back clean, so a phrase that fires on prose that is right is far
more expensive than a denial that goes unrecorded. Three rules follow from that, and each
is a deliberate under-report:

* **Two words minimum.** ``revenue``, ``assets``, ``beta``, ``margin`` and ``growth`` are
  words every sentence in a research report is entitled to use. "No evidence of revenue
  growth by segment" is not a denial that revenue exists. A phrase of one word is dropped
  rather than matched, so those figures are never the subject of a recorded denial.
* **Whole words only.** "interest cover" does not match "interest coverage"; where prose
  genuinely uses both, the alias table carries both, so the decision is written down rather
  than inferred by a looser matcher that would also match things nobody chose.
* **A name with no readable phrase matches nothing.** ``pp_and_e_net`` reads as "pp and e
  net", which no sentence contains, so it is silently absent. That is the safe direction:
  the check misses a denial rather than inventing one.

Punctuation is not part of a name. Both sides are normalised the same way — lowercased,
with every run of non-alphanumerics collapsed to one space — so "risk-free rate",
"risk free rate" and "Risk-Free Rate" are one phrase, and a matcher that knew about hyphens
in one direction only cannot exist.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "EVIDENCE_WORDS",
    "FIGURE_ALIASES",
    "MIN_PHRASE_WORDS",
    "NARROWING_WORDS",
    "NEGATORS",
    "clauses",
    "denial_span",
    "denies",
    "mentions",
    "narrows",
    "normalised",
    "opens_with",
    "periods_named",
    "phrases_for",
]

# See the module docstring. Below this a phrase is a word the whole report is entitled to.
MIN_PHRASE_WORDS: Final = 2

# The names whose own spelling is not how a sentence says them, and the variants prose
# genuinely uses. Deliberately short: a map of every figure is a map somebody maintains for
# ever and which is wrong the first time the vocabulary grows, and an absent alias costs a
# missed denial rather than a false one. Each entry is here because a report the readiness
# audit read used the words, or because the name alone is a single word and would otherwise
# be unreachable.
FIGURE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "wacc": ("weighted average cost of capital", "cost of capital"),
    "capex": ("capital expenditure", "capital expenditures"),
    "fcf": ("free cash flow",),
    "eps_basic": ("basic earnings per share",),
    "eps_diluted": ("diluted earnings per share",),
    "earnings_per_share_basic": ("basic earnings per share",),
    "earnings_per_share_diluted": ("diluted earnings per share",),
    "interest_cover": ("interest cover", "interest coverage"),
    "sg_and_a": ("selling general and administrative expense",),
    "pp_and_e_net": ("property plant and equipment",),
    "rnd_expense": ("research and development expense",),
    "sga_expense": ("selling general and administrative expense",),
}

_NOT_A_WORD: Final[re.Pattern[str]] = re.compile(r"[^0-9a-z]+")

# A period as a sentence names one, in the order the alternation has to try them: a quarter
# before the fiscal year it contains, and both before a bare year, so "Q3 FY2025" is read
# once as a quarter rather than three times as its parts. A two-digit year ("FY25") is
# deliberately unread — it is ambiguous against a figure and unreading it only costs a
# denial that goes unbound.
_PERIOD: Final[re.Pattern[str]] = re.compile(
    r"\bQ(?P<quarter>[1-4])\s*(?:FY\s*)?(?P<quarter_year>(?:19|20)\d{2})\b"
    r"|\bFY\s*(?P<fiscal_year>(?:19|20)\d{2})\b"
    r"|\b(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)


def normalised(text: str) -> str:
    """Lowercased, with every run of non-alphanumerics reduced to one space.

    The one transform both sides of a match go through. A phrase and a sentence normalised
    differently is the bug this function exists to make impossible.
    """
    return _NOT_A_WORD.sub(" ", text.casefold()).strip()


def phrases_for(name: str) -> tuple[str, ...]:
    """Every phrase a sentence could use for this figure, or empty where none qualifies.

    The name's own spelling with the underscores taken out, plus its aliases. Anything
    shorter than :data:`MIN_PHRASE_WORDS` words is dropped — including, deliberately, the
    name itself for a single-word figure. Deduplicated, in a stable order, so a caller
    building an index gets the same list every time.
    """
    candidates = (name, *FIGURE_ALIASES.get(name, ()))
    phrases: list[str] = []
    for candidate in candidates:
        phrase = normalised(candidate)
        if len(phrase.split()) < MIN_PHRASE_WORDS or phrase in phrases:
            continue
        phrases.append(phrase)
    return tuple(phrases)


def mentions(sentence: str, phrase: str) -> bool:
    """Whether ``sentence`` contains ``phrase`` as whole words.

    ``phrase`` is expected already normalised — :func:`phrases_for` returns them that way —
    and padding both with spaces is what makes the containment a word-boundary test without
    compiling a pattern per phrase.
    """
    if not phrase:
        return False
    return f" {phrase} " in f" {normalised(sentence)} "


# A sentence denies a figure when it negates and then speaks about the evidence, in that
# order. Two lists rather than a list of phrases, because the phrasings a report actually
# uses put an arbitrary distance between the two halves.
#
# The words that are both halves at once: each negates *and* speaks about the record, so a
# sentence needs nothing after it. "Interest cover is unavailable" has no second half to
# find, and a rule that demanded one would refuse the plainest denial in the language.
_SELF_NEGATING: Final[tuple[str, ...]] = (
    "absent",
    "missing",
    "silent",
    "unavailable",
    "undisclosed",
    "unreported",
)

NEGATORS: Final[tuple[str, ...]] = (
    "no",
    "not",
    "never",
    "neither",
    "nor",
    "none",
    "cannot",
    "insufficient",
    *_SELF_NEGATING,
)

# Deliberately **not** a negator: "without". The dry run over the committed azn2 record read
# "One external reference point is available without leaving primary disclosure" as a denial
# of the cost of capital the same sentence goes on to quote. A sentence that genuinely needs
# it — "without a disclosed revenue figure the margin cannot be computed" — carries "cannot"
# as well, so nothing is lost that the rest of the list does not already hold.

# What the second half is *about*: whether the platform holds the figure. Every one of these
# is a word about the record rather than about the company, which is what keeps "interest
# cover is not a concern" out of the set — "concern" is not on it, and no general adjective
# is.
EVIDENCE_WORDS: Final[tuple[str, ...]] = (
    "available",
    "disclose",
    "discloses",
    "disclosed",
    "disclosure",
    "report",
    "reports",
    "reported",
    "provide",
    "provides",
    "provided",
    "state",
    "states",
    "stated",
    "establish",
    "establishes",
    "established",
    "compute",
    "computes",
    "computed",
    "computable",
    "determine",
    "determines",
    "determined",
    "record",
    "records",
    "recorded",
    "evidence",
    "figures",
    "data",
    "sit",
    "sits",
    "filings",
    *_SELF_NEGATING,
)

# Deliberately **not** on that list: "present". "No interest-cover figure is present" is a
# denial and "we do not present the $3.21tn equity value as a downside anchor" is an author
# declining to lead with a figure it goes on to print, and one word carries both. The dry run
# over the committed msft2 record found the second, which is why it is named here rather than
# left out silently.

# Where one clause ends and the next begins. A denial belongs to its clause, not to its
# sentence: the msft2 record contains "…short-term investments of $55.9 billion sit against
# those borrowings, and no netted leverage figure is recorded here", which names one figure
# and denies a different one. Read whole, it reports a contradiction that is not there.
#
# Enumeration commas are **not** boundaries — "No discounted cash flow, cost of equity, …,
# value per share or peer multiple sits on this record" is one clause and must stay one, or
# the denial and the figure it denies end up on opposite sides of a split.
_CLAUSE: Final[re.Pattern[str]] = re.compile(
    r";\s+"
    r"|\s+[—–]\s+"  # noqa: RUF001 -- an em or en dash, by code point
    r"|,\s+(?:and|but|though|although|while|whereas|yet|so|however|because|since)\s+",
    re.IGNORECASE,
)


# Words that narrow a figure to a part of itself. A report legitimately says "no
# segment-level capital expenditure was disclosed" in a document that prints consolidated
# capital expenditure, and "no quarterly capital expenditure cadence is available" beside an
# annual one: the denial and the figure are about different things, exactly as a dimensioned
# fact and its consolidated parent are (`aer.services.consistency`'s fact pass keys on the
# dimension for the same reason). A clause carrying any of these is left alone, which
# under-reports by refusing a denial that happens to mention one.
NARROWING_WORDS: Final[tuple[str, ...]] = (
    "segment",
    "segments",
    "segmental",
    "segmented",
    "divisional",
    "division",
    "geographic",
    "geographical",
    "regional",
    "region",
    "product",
    "quarterly",
    "monthly",
    "interim",
    "cadence",
)


def narrows(clause: str) -> bool:
    """Whether this clause denies a *part* of a figure rather than the figure.

    See :data:`NARROWING_WORDS`. Measured against the re-seeded corpus, where three of
    eighteen recorded denials were a segment-level or quarterly absence read as a denial of
    the consolidated figure the same report prints.
    """
    return any(word in normalised(clause).split() for word in NARROWING_WORDS)


def clauses(sentence: str) -> list[str]:
    """One sentence split where a denial's reach ends. See :data:`_CLAUSE`.

    The unit both halves of the scan read: a clause denies, and the figure it denies is
    named in the same clause. Applying either test to a whole sentence reports a
    contradiction between two clauses that are each correct.
    """
    return [part for part in (piece.strip() for piece in _CLAUSE.split(sentence)) if part]


def denies(sentence: str) -> bool:
    """Whether this sentence says a figure is not here (ADR 0125).

    **A negator, and then a word about the evidence, in that order.** Not a phrase list and
    not a proximity window, and the reason is one sentence from the readiness audit:

        "No discounted cash flow, cost of equity, weighted average cost of capital,
        terminal value, value per share or peer multiple sits on this record."

    Nineteen words of enumeration stand between the "No" and the "sits", so any window
    narrow enough to be safe would miss the instance the check exists for. The order is the
    rule instead, and the narrowing is done elsewhere: the caller reads one :func:`clauses`
    clause at a time and requires that same clause to name a figure this report publishes,
    by a phrase of at least two words, for a period this report publishes it at. On its own
    this predicate is loose; it is never used on its own.

    The vocabulary grows when a live run shows a phrasing it missed. That is how
    ``aer.core.section_output``'s own gap phrases were built, and it is the honest way to
    build a list of how people write: this one already carries "sits on this record", which
    no amount of thinking produced and one report did.
    """
    words = normalised(sentence).split()
    negated = next((index for index, word in enumerate(words) if word in NEGATORS), None)
    if negated is None:
        return False
    # From the negator itself, not from the word after it, so a self-negating word satisfies
    # both halves on its own — see `_SELF_NEGATING`.
    return any(word in EVIDENCE_WORDS for word in words[negated:])


def denial_span(clause: str) -> str:
    """The part of a clause the negation reaches: from the negator to the end, normalised.

    **Which noun a negator governs is the last question this scan has to answer**, and the
    re-seeded corpus asked it three times with the same sentence. `aer.calc.wacc` writes
    *"Book equity was used as the equity weight because no market capitalisation was
    available"* into every run with no market price. It denies the market capitalisation and
    *uses* the equity weight, and read whole it looked like a report denying a figure it
    prints — on three of four runs.

    The negation reaches forwards, so a figure named before it is not what is denied. A
    figure named before it can still be the subject of the denial — "operating cash flow is
    not among the figures available here" — and that case is :func:`opens_with`, which asks
    whether the figure *begins* the clause rather than merely appearing somewhere in it.

    **It ends at the word about the record, not at the end of the clause.** "Where no cloud
    margin is disclosed, the consolidated operating income line is the fallback" denies the
    cloud margin and then goes on to *use* the operating income, and a span running to the
    end of the clause reads the second as denied too — the last false positive the corpus
    held. The exception is a negator that is itself a word about the record, where there is
    nothing between the two to end at: "the evidence is silent on interest cover" negates and
    speaks about the record in one word, and its subject follows.

    Empty where nothing negates, which a caller should already have ruled out with
    :func:`denies`.
    """
    words = normalised(clause).split()
    negated = next((index for index, word in enumerate(words) if word in NEGATORS), None)
    if negated is None:
        return ""
    if words[negated] in EVIDENCE_WORDS:
        return " ".join(words[negated:])
    ends = next(
        (index for index in range(negated + 1, len(words)) if words[index] in EVIDENCE_WORDS),
        len(words) - 1,
    )
    return " ".join(words[negated : ends + 1])


# What may stand in front of a subject without displacing it. Determiners and possessives
# only: "a value per share is not available here" is the same subject as "value per share is
# not available here", while "book equity was used as the equity weight because no …" opens
# with a different subject entirely and must keep doing so.
_DETERMINERS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "the",
        "its",
        "their",
        "our",
        "this",
        "that",
        "these",
        "those",
        "any",
        "each",
        "every",
        "such",
        "no",
    }
)


def opens_with(clause: str, phrase: str) -> bool:
    """Whether the clause's subject is this figure: "operating cash flow is not …".

    The other half of :func:`denial_span`. Deliberately the *opening* rather than anywhere
    before the negator: "book equity was used as the equity weight because no …" names a
    figure early and denies a different one, and only the opening distinguishes the two.

    A leading determiner is stepped over, because "a value per share is not available here"
    is the same sentence as "value per share is not available here" and a rule that read the
    article as the subject would miss the commoner of the two.
    """
    if not phrase:
        return False
    words = normalised(clause).split()
    while words and words[0] in _DETERMINERS:
        words = words[1:]
    opening = " ".join(words)
    return opening == phrase or opening.startswith(f"{phrase} ")


def periods_named(sentence: str) -> frozenset[str]:
    """The reporting periods this sentence names, as a stored figure labels them.

    ``"FY2024"``, ``"Q3 FY2025"``. A bare year reads as that fiscal year: a sentence saying
    "the 2021 accounts do not disclose free cash flow" is about the same period a figure
    labelled ``FY2021`` is about, and treating the two as different would let the commonest
    phrasing of a denial escape the period bound entirely.

    Empty means the sentence named no period, which the scan reads as an **unqualified**
    denial — the msft1 case, where an executive summary denied a discounted cash flow
    outright rather than for a year.
    """
    found: set[str] = set()
    for match in _PERIOD.finditer(sentence):
        if match["quarter"] is not None:
            found.add(f"Q{match['quarter']} FY{match['quarter_year']}")
        elif match["fiscal_year"] is not None:
            found.add(f"FY{match['fiscal_year']}")
        else:
            found.add(f"FY{match['year']}")
    return frozenset(found)
