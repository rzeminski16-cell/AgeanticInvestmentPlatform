"""Which tier a question needs, decided from its words and what the record holds (F6).

ADR 0130 §2. Resolution is deterministic and spends nothing: a classifier that guesses is a
classifier that will eventually answer a tier-3 question from stale tier-2 material and not
say so, and a resolver that called a model would spend before the approval the third tier
exists to demand. So this module is pure — the question's text and a :class:`HeldRecord`
in, a :class:`Resolution` out — and it may err in one direction only. Every rule below
resolves *upward* when unsure, and a property test holds the whole function to it: no edit
that removes something the record holds ever lowers the tier.

**Tier 1 is a match, not a judgement.** The question names one of the registered
recomputable inputs — an assumption the run confirmed, or the discount rate the grids
already vary — and a value with a unit that input is measured in. *Half a point higher* is
a value; *higher* alone is not, and a parameter with no value is not tier 1.

**Tier 2 is a scope question.** Everything the question names is held: a year within the
record's years, a kind of document whose title the record carries, a quarter where the
record holds quarterly figures, the subject by its own name. A name the record does not
hold, a period it does not reach, or a temporal reference it cannot satisfy — *since the
last results*, *has anything changed* — is not tier 2.

**Everything else is tier 3**, the only tier that asks permission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Final

from aer.core.assumption_scales import ASSUMPTION_WORDS

__all__ = [
    "DISCOUNT_RATE",
    "PARAMETERS",
    "Change",
    "HeldRecord",
    "Parameter",
    "Resolution",
    "Tier",
    "empty_record",
    "resolve",
    "subject_tokens",
]


class Tier(IntEnum):
    """The three tiers, ordered: a higher tier costs more and asks more."""

    RECOMPUTE = 1
    RE_READ = 2
    RESEARCH = 3

    @property
    def spoken(self) -> str:
        return _TIER_WORDS[self]


_TIER_WORDS: Final[dict[Tier, str]] = {
    Tier.RECOMPUTE: "Recompute",
    Tier.RE_READ: "Re-read",
    Tier.RESEARCH: "Research",
}


# The discount rate is not a confirmed assumption — it is computed from three that are
# (ADR 0046) — but the sensitivity grids vary it directly, so a question may too.
DISCOUNT_RATE: Final = "wacc"


@dataclass(frozen=True, slots=True)
class Parameter:
    """One input a stored model can be re-run with, and the words a question calls it by."""

    name: str
    phrases: tuple[str, ...]
    # ``fraction`` is measured in percentages and points; ``multiple`` in times.
    measure: str

    @property
    def words(self) -> str:
        return self.phrases[0]


# Longest phrase first within a parameter, so "operating margin" is tried before "margin".
# Across parameters the order below is the tie-break: a question naming two is resolved to
# the first, which is deliberate — the answer says which input moved, and a reader who
# meant the other asks again.
PARAMETERS: Final[tuple[Parameter, ...]] = (
    Parameter(
        DISCOUNT_RATE,
        ("discount rate", "cost of capital", "weighted average cost of capital", "wacc"),
        "fraction",
    ),
    Parameter(
        "terminal_growth",
        ("terminal growth rate", "terminal growth", "perpetual growth", "long-term growth"),
        "fraction",
    ),
    Parameter("exit_multiple", ("exit multiple", "terminal multiple"), "multiple"),
    Parameter("tax_rate", ("tax rate",), "fraction"),
    Parameter(
        "revenue_growth",
        ("revenue growth", "sales growth", "top-line growth", "growth rate", "growth"),
        "fraction",
    ),
    Parameter("ebit_margin", ("ebit margin", "operating margin", "margin"), "fraction"),
    Parameter("capex_intensity", ("capex intensity", "capital intensity", "capex"), "fraction"),
    Parameter("depreciation_intensity", ("depreciation intensity", "depreciation"), "fraction"),
    Parameter(
        "working_capital_intensity",
        ("working capital intensity", "working capital"),
        "fraction",
    ),
)


@dataclass(frozen=True, slots=True)
class Change:
    """What a tier-1 question asks to move: which input, to what or by how much."""

    parameter: str
    # ``absolute`` sets the input to ``value``; ``relative`` adds ``value`` to it.
    kind: str
    value: Decimal
    # How the question said it, for the rationale and the ledger label.
    stated: str

    @property
    def words(self) -> str:
        return _parameter_words(self.parameter)


@dataclass(frozen=True, slots=True)
class HeldRecord:
    """What the record holds for a company, as code read it. Every field lowercased."""

    subject_names: frozenset[str]
    # The inputs a stored model can be re-run with: the confirmed assumption names the
    # current report's run holds, and the discount rate where it reached a valuation.
    variable_inputs: frozenset[str]
    document_titles: tuple[str, ...]
    years: frozenset[int]
    has_quarterly: bool
    # Other companies the record holds documents for — a peer whose filing was fetched.
    other_names: frozenset[str] = frozenset()
    # The capitalised words the held titles and excerpts carry — a product, a segment, a
    # person — so a question naming one of them is naming something the store's contents
    # can answer. The store's contents are the answer (mechanisms §2.2), not a guess.
    vocabulary: frozenset[str] = frozenset()

    @property
    def has_documents(self) -> bool:
        return bool(self.document_titles)

    def knows(self, tokens: frozenset[str]) -> bool:
        """Whether every token names the subject, a held company, or a held word."""
        return tokens <= (self.subject_names | self.other_names | self.vocabulary)


def empty_record() -> HeldRecord:
    return HeldRecord(
        subject_names=frozenset(),
        variable_inputs=frozenset(),
        document_titles=(),
        years=frozenset(),
        has_quarterly=False,
    )


@dataclass(frozen=True, slots=True)
class Resolution:
    """The tier, the sentence that says why, and what the resolver found."""

    tier: Tier
    rationale: str
    change: Change | None = None
    # What the question named that the record does not hold. Empty below tier 3.
    missing: tuple[str, ...] = ()
    # What the question named that the record does hold, for the tier-2 sentence.
    held: tuple[str, ...] = ()


def resolve(question: str, record: HeldRecord) -> Resolution:
    """The tier a question needs, erring upward only."""
    text = _normalised(question)
    if not text:
        return Resolution(Tier.RESEARCH, "An empty question names nothing the record could answer.")

    change = _tier_one(text, record)
    if change is not None:
        return Resolution(
            Tier.RECOMPUTE,
            f"The stored model can be struck again with the {change.words} "
            f"{_change_words(change)}. Nothing is fetched and nothing is spent.",
            change=change,
        )

    missing, held = _scope(question, text, record)
    if not record.has_documents:
        missing = ("no document at all: nothing has been fetched for this company", *missing)
    if missing:
        return Resolution(
            Tier.RESEARCH,
            "This needs new material. The question names " + _listed(missing) + ".",
            missing=missing,
            held=held,
        )
    named = f" — {_listed(held)} —" if held else ""
    return Resolution(
        Tier.RE_READ,
        f"Everything this question names{named} is in the record. One pass over what is "
        "held, and nothing is fetched.",
        held=held,
    )


# -- Tier 1 -----------------------------------------------------------------------------------

_DECIMAL: Final = r"(?P<number>\d+(?:\.\d+)?)"
_PERCENT: Final = re.compile(_DECIMAL + r"\s*(?:%|per\s?cent\b|percent\b)")
_POINTS: Final = re.compile(_DECIMAL + r"\s*(?:percentage\s+points?|pp|points?)\b")
_BASIS_POINTS: Final = re.compile(_DECIMAL + r"\s*(?:bps?|basis\s+points?)\b")
_MULTIPLE: Final = re.compile(_DECIMAL + r"\s*(?:x|times|turns?)\b")

# The analyst's own units, in words. "Half a point" is the comparison a sensitivity grid
# is built on, and a resolver that could not read it would send the commonest tier-1
# question to tier 2.
_WORDED_POINTS: Final[tuple[tuple[re.Pattern[str], Decimal], ...]] = (
    (re.compile(r"\bhalf\s+a\s+(?:percentage\s+)?point\b"), Decimal("0.5")),
    (re.compile(r"\ba\s+quarter\s+(?:of\s+a\s+)?(?:percentage\s+)?point\b"), Decimal("0.25")),
    (re.compile(r"\b(?:a|one)\s+(?:full\s+)?(?:percentage\s+)?point\b"), Decimal(1)),
    (re.compile(r"\btwo\s+(?:percentage\s+)?points\b"), Decimal(2)),
    (re.compile(r"\bthree\s+(?:percentage\s+)?points\b"), Decimal(3)),
)
_WORDED_TURNS: Final[tuple[tuple[re.Pattern[str], Decimal], ...]] = (
    (re.compile(r"\bhalf\s+a\s+turn\b"), Decimal("0.5")),
    (re.compile(r"\b(?:a|one)\s+turn\b"), Decimal(1)),
    (re.compile(r"\btwo\s+turns\b"), Decimal(2)),
)

_UP: Final = re.compile(
    r"\b(?:higher|up|more|rises?|rose|increases?d?|increasing|raised?|above|added|plus)\b"
)
_DOWN: Final = re.compile(
    r"\b(?:lower|down|less|falls?|fell|decreases?d?|decreasing|cut|below|reduced?|minus)\b"
)


def _tier_one(text: str, record: HeldRecord) -> Change | None:
    """A registered input, a value and a unit — or nothing."""
    parameter = _named_parameter(text, record)
    if parameter is None:
        return None
    return _change_for(parameter, text)


def _named_parameter(text: str, record: HeldRecord) -> Parameter | None:
    for parameter in PARAMETERS:
        if parameter.name not in record.variable_inputs:
            continue
        if any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in parameter.phrases):
            return parameter
    return None


def _change_for(parameter: Parameter, text: str) -> Change | None:
    direction = _direction(text)
    if parameter.measure == "multiple":
        amount = _worded(text, _WORDED_TURNS)
        stated = ""
        if amount is None:
            found = _MULTIPLE.search(text)
            if found is None:
                return None
            amount = Decimal(found["number"])
            stated = found.group(0)
        else:
            stated = f"{amount.normalize():f} turn"
        return _change(parameter, amount, direction, stated or f"{amount}x")

    worded = _worded(text, _WORDED_POINTS)
    if worded is not None:
        return _change(parameter, worded / 100, direction, _points_words(worded))
    for pattern, divisor, unit in (
        (_BASIS_POINTS, Decimal(10_000), "basis points"),
        (_POINTS, Decimal(100), "percentage points"),
        (_PERCENT, Decimal(100), "%"),
    ):
        found = pattern.search(text)
        if found is None:
            continue
        number = Decimal(found["number"])
        # A percentage with a direction is read as points — "1% higher" on a rate of 8.5%
        # is 9.5%, which is what an analyst means and what the grid's steps are — and the
        # rationale says so in the unit it read.
        stated = (
            f"{number.normalize():f} {unit}"
            if unit != "%"
            else (f"{number.normalize():f} percentage points" if direction else f"{number}%")
        )
        return _change(parameter, number / divisor, direction, stated)
    return None


def _change(parameter: Parameter, amount: Decimal, direction: int, stated: str) -> Change:
    if direction == 0:
        return Change(parameter.name, "absolute", amount, stated)
    return Change(parameter.name, "relative", amount * direction, stated)


def _direction(text: str) -> int:
    """+1 for higher, -1 for lower, 0 for an absolute value."""
    up = _UP.search(text)
    down = _DOWN.search(text)
    if up is None and down is None:
        return 0
    if down is None:
        return 1
    if up is None:
        return -1
    return 1 if up.start() < down.start() else -1


def _worded(text: str, table: tuple[tuple[re.Pattern[str], Decimal], ...]) -> Decimal | None:
    for pattern, amount in table:
        if pattern.search(text):
            return amount
    return None


def _points_words(points: Decimal) -> str:
    plain = f"{points.normalize():f}"
    return f"{plain} percentage point" + ("" if points == 1 else "s")


def _change_words(change: Change) -> str:
    if change.kind == "absolute":
        return f"at {change.stated}"
    return f"{change.stated} {'higher' if change.value > 0 else 'lower'}"


def _parameter_words(name: str) -> str:
    if name == DISCOUNT_RATE:
        return "discount rate"
    return ASSUMPTION_WORDS.get(name, name.replace("_", " "))


# -- Tier 2 -----------------------------------------------------------------------------------

_YEAR: Final = re.compile(r"\b(?:fy\s?)?(?P<year>(?:19[89]|20[0-4])\d)\b")
_SHORT_YEAR: Final = re.compile(r"\bfy\s?(?P<year>\d{2})\b")
_QUARTER: Final = re.compile(r"\b(?:q[1-4]|quarter(?:ly|s)?|h[12]\b|half[- ]year|interim)\b")

# Temporal references the store cannot satisfy: each is a claim about time after the record
# was fetched, and only fetching again can answer it. Generous on purpose — a question sent
# to tier 3 needlessly costs an approval; one kept at tier 2 wrongly is answered from
# material that does not contain the answer.
_UNSATISFIABLE: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\bsince\b"), "what has happened since"),
    (re.compile(r"\bchanged?\b"), "what has changed"),
    (re.compile(r"\brecent(?:ly)?\b"), "what is recent"),
    (re.compile(r"\b(?:now|today|yesterday|currently|current)\b"), "the present"),
    (re.compile(r"\bthis\s+(?:week|month|quarter|year)\b"), "the present period"),
    (re.compile(r"\blast\s+(?:week|month|results|quarter)\b"), "the latest period"),
    (re.compile(r"\bnews\b"), "the news"),
    (re.compile(r"\bannounce(?:d|ment|s)?\b"), "an announcement"),
    (re.compile(r"\b(?:share|stock)\s+price\b"), "the share price"),
)

# A kind of document, and the words a held title would carry for it.
_DOCUMENT_KINDS: Final[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]] = (
    ("the 10-K", ("10-k", "annual report"), ("10-k", "annual report", "annual accounts")),
    ("the 10-Q", ("10-q", "quarterly report"), ("10-q",)),
    ("an 8-K", ("8-k",), ("8-k",)),
    ("the 20-F", ("20-f",), ("20-f",)),
    ("a 6-K", ("6-k",), ("6-k",)),
    ("the proxy statement", ("proxy", "def 14a"), ("def 14a", "proxy")),
    (
        "a transcript",
        ("transcript", "earnings call", "conference call", "prepared remarks"),
        ("transcript", "earnings call", "prepared remarks"),
    ),
    ("a press release", ("press release",), ("press release", "ex-99", "exhibit 99")),
    ("the annual accounts", ("annual accounts", "accounts"), ("accounts", "annual report")),
    ("a prospectus", ("prospectus",), ("prospectus",)),
    (
        "an investor presentation",
        ("investor presentation", "presentation", "slides"),
        ("presentation", "slides"),
    ),
)

# Capitalised words that name no company. The financial vocabulary a question is written
# in, and the words a question starts with when it does not start with the subject.
_NOT_A_NAME: Final[frozenset[str]] = frozenset(
    {
        "i",
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "by",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "what",
        "why",
        "how",
        "when",
        "where",
        "which",
        "who",
        "if",
        "would",
        "could",
        "should",
        "can",
        "will",
        "ebit",
        "ebitda",
        "eps",
        "roe",
        "roic",
        "wacc",
        "dcf",
        "fcf",
        "npv",
        "irr",
        "capex",
        "opex",
        "gaap",
        "ifrs",
        "sec",
        "fy",
        "q1",
        "q2",
        "q3",
        "q4",
        "h1",
        "h2",
        "usd",
        "gbp",
        "eur",
        "us",
        "uk",
        "eu",
        "ceo",
        "cfo",
        "coo",
        "r&d",
        "ai",
        "m&a",
        "ipo",
        "esg",
        "ltm",
        "ttm",
        "yoy",
        "cagr",
        "pe",
        "ev",
        "adr",
        "plc",
        "inc",
        "ltd",
        "corp",
        "co",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }
)

# A run of capitalised words. Not the "K" of "10-K" or the "Q" of "Q3": a letter after a
# digit or a hyphen is part of a code, and the digits stay in the token so "Q3" reads as
# the quarter it is.
_CAPITALISED: Final = re.compile(
    r"(?<![\w\-])[A-Z][A-Za-z0-9&'\.\-]*(?:\s+[A-Z][A-Za-z0-9&'\.\-]*)*"
)

_CORPORATE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "corporation",
        "corp",
        "inc",
        "incorporated",
        "plc",
        "ltd",
        "limited",
        "company",
        "co",
        "group",
        "holdings",
        "holding",
        "the",
    }
)


def subject_tokens(*names: str) -> frozenset[str]:
    """The words a company is known by, lowercased, without the corporate furniture."""
    found: set[str] = set()
    for name in names:
        for token in re.findall(r"[a-z0-9&'\.\-]+", name.lower()):
            cleaned = token.strip(".'-")
            if cleaned and cleaned not in _CORPORATE_SUFFIXES:
                found.add(cleaned)
    return frozenset(found)


def _scope(question: str, text: str, record: HeldRecord) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What the question names that the record does not hold, and what it does."""
    missing: list[str] = []
    held: list[str] = []

    for pattern, meaning in _UNSATISFIABLE:
        if pattern.search(text):
            missing.append(f"{meaning}, which the record cannot say")
            break

    for spoken, asked, carried in _DOCUMENT_KINDS:
        if not any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in asked):
            continue
        _sort(
            spoken,
            held=any(marker in title for title in record.document_titles for marker in carried),
            into=(held, missing),
            absent="which the record does not hold",
        )

    for year in _years_in(text):
        _sort(
            str(year) if year in record.years else f"the year {year}",
            held=year in record.years,
            into=(held, missing),
            absent="which the record does not reach",
        )

    if _QUARTER.search(text):
        _sort(
            "quarterly figures" if record.has_quarterly else "a quarter",
            held=record.has_quarterly,
            into=(held, missing),
            absent="and the record holds annual figures only",
        )

    missing.extend(
        f"{name}, which this record holds nothing about" for name in _names_in(question, record)
    )

    return tuple(dict.fromkeys(missing)), tuple(dict.fromkeys(held))


def _sort(item: str, *, held: bool, into: tuple[list[str], list[str]], absent: str) -> None:
    """One named thing, into the held list or the missing list with its reason."""
    if held:
        into[0].append(item)
    else:
        into[1].append(f"{item}, {absent}")


def _years_in(text: str) -> tuple[int, ...]:
    found = [int(match["year"]) for match in _YEAR.finditer(text)]
    found.extend(2000 + int(match["year"]) for match in _SHORT_YEAR.finditer(text))
    return tuple(dict.fromkeys(found))


def _names_in(question: str, record: HeldRecord) -> tuple[str, ...]:
    """Capitalised names that are neither the subject nor anything the record holds.

    The first word of a question is capitalised because it is first; every other
    capitalised run is a name until shown otherwise, and a name the record does not hold
    resolves upward. A question about *Oracle* over Microsoft's record is a tier-3 question
    even when the reader would happily guess.
    """
    found: list[str] = []
    for match in _CAPITALISED.finditer(question):
        if match.start() == 0:
            continue
        tokens = subject_tokens(match.group(0)) - _NOT_A_NAME
        if not tokens or record.knows(tokens):
            continue
        found.append(match.group(0).strip(".'-"))
    return tuple(dict.fromkeys(found))


def _normalised(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def _listed(items: tuple[str, ...]) -> str:
    if len(items) == 1:
        return items[0]
    return "; ".join(items[:-1]) + "; and " + items[-1]
