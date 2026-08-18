"""Deterministic checks over what a custom section produced.

Two questions, both answered by code because both are the kind a model answers
optimistically:

**Does the content satisfy its contract?** The projected ``output_contract`` (task 36) is
a minimal JSON Schema — declared properties, required names, scalar types. The check here
is exact and closed: every required field present, no field the contract did not declare,
declared scalars carrying the declared type. Closed matters most — an undeclared key is
how a field the author never wrote (or one the platform reserves) would otherwise ride
into a report inside a dict nobody validates.

**Does any numeral stand on nothing?** `docs/PLAN.md` §2.12: a custom section may only
reference facts and calculations by id, and *"a section that emits a bare numeral not
resolvable to one is a validation failure"*. The scan walks every string and number in the
content and demands that each numeral token also appears in the text of a numeric claim —
which, by schema and by database constraint, names exactly one stored fact or recorded
calculation. Three carve-outs, each decided by the operator and each recorded: a numeral
inside a recognisable date or document reference — "March 2026", "Q3 2025", "Item 2.02",
a labelled CIK (ADR 0054); a plain count of the prose's own nouns (ADR 0057); and the
number inside a product name — "Microsoft 365", "Windows 11" (ADR 0060). Each was
provenance tripping the rule that exists to protect provenance. Every exemption is by
*span*, never by value: those exact characters are excused, and the same digits anywhere
else still need lineage.

Pure and ``mypy --strict``: dictionaries in, problem strings out, nothing else consulted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from copy import deepcopy
from typing import Any, Final

from aer.core.concepts import CANONICAL_CONCEPTS
from aer.core.schemas.skill import RESERVED_OUTPUT_FIELDS

__all__ = [
    "MAX_GAP_SENTENCES",
    "NUMERAL_EXEMPT_KEYS",
    "contract_violations",
    "gap_sentences",
    "numerals_in",
    "prose_word_count",
    "reserved_fields_in",
    "trimmed_to_word_count",
    "unsourced_numerals",
    "without_document_references",
    "without_plain_counts",
    "without_product_names",
    "without_unsourced_numeral_sentences",
]

# Keys whose values are section metadata rather than assertions about the world.
# ``confidence`` is the renderer's own metadata key; the citation keys carry ids, and a
# UUID whose hyphen-delimited group happens to be all digits would otherwise surface a
# "numeral" no claim could ever cover — provenance tripping the rule that exists to
# protect provenance. These are exactly the keys the renderer treats as metadata too.
NUMERAL_EXEMPT_KEYS: Final[frozenset[str]] = frozenset(
    {"confidence", "calculation_id", "source_document_id", "extraction_id", "financial_fact_id"}
)

# A numeral as a reader meets one: digits, optional thousands separators and decimals,
# an optional trailing per-cent sign. Word-bounded so "10-K" and "FY22Q4" do not shed
# fragments, but "grew 34%" and "$198,270 million" both surface their figures. The
# trailing guard refuses only a *mid-decimal* stop (".<digit>"), so a numeral ending a
# sentence — "in 2022." — still counts.
_NUMERAL: Final[re.Pattern[str]] = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)%?(?!\w)(?!\.\d)")

_MONTHS: Final = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)

# What a reference looks like when a reader meets one. Every alternative is anchored by
# context a quantity does not have — a month name, a fiscal marker, a temporal word, a
# filing label — so "revenue of 2,026 million" anchors to none of them and still needs
# lineage. ADR 0054 records the decision and the trade.
_REFERENCE: Final[re.Pattern[str]] = re.compile(
    "|".join(
        (
            # A calendar date in either order, day optional: "15 March 2026",
            # "March 15, 2026", "March 2026".
            rf"\b(?:\d{{1,2}}\s+)?(?:{_MONTHS})(?:\s+\d{{1,2}})?(?:,?\s+\d{{4}})?\b",
            # An ISO date is reference-shaped all by itself.
            r"\b\d{4}-\d{2}-\d{2}\b",
            # Fiscal markers: "Q3 2026", "H1 2026", "FY2026", "FY26", "fiscal 2026",
            # "fiscal year 2026", "the fourth quarter of 2026".
            r"\b(?:Q[1-4]|H[12])\s?(?:19|20)\d{2}\b",
            r"\bFY\s?\d{2,4}\b",
            r"\bfiscal(?:\s+year)?\s+(?:19|20)\d{2}\b",
            r"\b(?:first|second|third|fourth)\s+quarter\s+of\s+(?:19|20)\d{2}\b",
            # A year in temporal company: "in 2026", "since 2024", "mid-2025", and any
            # list or range the anchor opens \u2014 "between 2019 and 2024", "in 2014, 2019
            # and 2024". The whole list is excused, not just the anchored head: a live
            # section died because the head of its year list was erased and the tail was
            # flagged. The anchors deliberately exclude "of", "to" and "for", each of
            # which reads naturally in front of a quantity.
            r"\b(?:in|by|since|until|through|throughout|during|before|after|between|"
            r"around|calendar|year|early|late|mid)[\s-](?:19|20)\d{2}(?:/\d{2,4})?"
            r"(?:\s*(?:,|and|or|to|[-\u2013\u2014])\s*(?:19|20)\d{2}(?:/\d{2,4})?)*\b",
            # A year-to-year range on its own: "2019-2024", hyphen or en/em dash.
            r"\b(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(?:19|20)\d{2}\b",
            # A fiscal split year is reference-shaped by itself: "2014/15", "2024/2025".
            # No quantity is written with a slash and a two-digit tail, and the live rule
            # flagged the "15" of "2014/15" as an unsourced figure.
            r"\b(?:19|20)\d{2}/\d{2,4}\b",
            # A bare pair or list of years \u2014 "2014 and 2024", "2019, 2021 and 2023" \u2014
            # where every element is year-shaped. A money amount cannot back into this
            # form: written with separators ("2,014") it does not match the year atom,
            # and a single bare year is deliberately not excused.
            r"\b(?:19|20)\d{2}(?:\s*(?:,|and|or|to)\s*(?:19|20)\d{2})+\b",
            # A year the sentence itself marks as one: "the 2026 fiscal year".
            r"\b(?:19|20)\d{2}\s+(?:fiscal|financial|calendar)\b",
            # Filing references, where the label is the anchor and an enumeration keeps
            # its cover: "Item 2.02", "Items 2.02 and 9.01", "Exhibit 99.1", "Form 4".
            r"\b(?:Item|Exhibit|Note|Form|Rule|Section)s?\s+\d+(?:\.\d+)?[A-Za-z]?"
            r"(?:\s*(?:,|and|&|through|to)\s*\d+(?:\.\d+)?[A-Za-z]?)*",
            # The bare form types themselves — "the 10-K", "a 10-Q", "an 8-K" — which is
            # how a writer names a filing far more often than "Form 10-K". The letter is
            # the anchor, and the closed list keeps "2-for-1" and its kin out of scope.
            r"\b(?:10-K|10-Q|8-K|20-F|40-F|6-K|11-K|S-1|S-3|S-4|13D|13G|14A)(?:/A)?\b",
            # A statute's year is its name: "the Securities Exchange Act of 1934".
            r"\bAct\s+of\s+(?:18|19|20)\d{2}\b",
            # A labelled CIK, and an accession number whose 10-2-6 shape is its own label.
            r"\bCIK\s*(?:No\.?\s*|Number\s*)?#?\d+\b",
            r"\b\d{10}-\d{2}-\d{6}\b",
        )
    ),
    re.IGNORECASE,
)

# Words that make the number before them a measured figure rather than a count. A small
# integer followed by one of these — "3 percent", "13 million", "5 basis points" — still
# needs lineage; followed by anything else — "three segments", "13 quarters" — it is the
# writer counting the nouns of its own prose, which no stored fact could ever cover.
_MEASURE_WORDS: Final = (
    "million|billion|trillion|thousand|hundred|percent|per|basis|bps|times|x|"
    "usd|gbp|eur|dollars?|pounds?|euros?|cents?|pence|p"
)

# A plain count: a bare integer under one hundred, no separators, no decimals, not
# preceded by a currency sign or attached to a larger token, followed by an ordinary
# lowercase word. Both sections the live run lost died here — Business Overview on the
# "13" of a market count, Catalysts on the "3" of its own list — figures in no ledger
# because they are not figures at all. ADR 0057 records the trade.
_PLAIN_COUNT: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w.,$£€])\d{{1,2}}(?=\s+(?!(?:{_MEASURE_WORDS})\b)[a-z])"
)

# The financial vocabulary, derived from the concept map rather than listed here. A word
# the platform already knows as a line item cannot be the head of a product name, and
# deriving the set means it grows when the vocabulary does instead of drifting behind it.
_CONCEPT_WORDS: Final[frozenset[str]] = frozenset(
    word for concept in CANONICAL_CONCEPTS for word in concept.split("_")
)

# The prose finance terms the machine vocabulary does not spell, because no filer tags
# them: a reader writes "EBITDA" and "margin" where a taxonomy writes `operating_income`.
_PROSE_FINANCE_WORDS: Final[frozenset[str]] = frozenset(
    {"ebitda", "ebit", "eps", "margin", "margins", "sales", "capex", "opex", "fcf", "arr"}
)

_FINANCIAL_WORDS: Final[frozenset[str]] = _CONCEPT_WORDS | _PROSE_FINANCE_WORDS

# A number that is part of a name: "Microsoft 365", "Windows 11", "Boeing 737". Matched as
# the pair, so the guards below can read the word that owns the number — a bare regex
# cannot tell "Microsoft 365" from "Revenue 365", and only one of those is a product.
_NAMED_NUMBER: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w.,$£€])([A-Za-z][A-Za-z'\u2019&.\-]*)(\s)(\d{{1,4}})(?!\s*%)"
    rf"(?!\s+(?:{_MEASURE_WORDS})\b)(?![\w.,])"
)

_JSON_SCALARS: Final[dict[str, type | tuple[type, ...]]] = {
    "string": str,
    "number": (int, float),
    "boolean": bool,
}


def contract_violations(content: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    """Where the content fails its contract. Empty means it satisfies it.

    Closed-world on purpose: a key the contract does not declare is a violation, not a
    bonus. The reserved-field rule (task 35) makes ``rating`` undeclarable in a contract;
    this check is what makes it therefore unwritable in content.
    """
    properties = contract.get("properties")
    declared: dict[str, Any] = properties if isinstance(properties, dict) else {}
    required = contract.get("required")
    needed: list[str] = [str(name) for name in required] if isinstance(required, list) else []

    problems: list[str] = []
    for name in needed:
        if name not in content:
            problems.append(f"The required field {name!r} is missing from the content.")
    for name in content:
        if name not in declared:
            problems.append(
                f"The field {name!r} is not declared by this section's output contract. "
                "Undeclared fields are refused, not carried."
            )

    for name, subschema in declared.items():
        if name not in content or not isinstance(subschema, dict):
            continue
        expected = _JSON_SCALARS.get(str(subschema.get("type", "")))
        if expected is None:
            continue
        value = content[name]
        # bool is an int in Python; a boolean where a number was declared is a mistake,
        # not a number.
        if isinstance(value, bool) and expected is not _JSON_SCALARS["boolean"]:
            problems.append(f"The field {name!r} must be a {subschema['type']}, not a boolean.")
        elif not isinstance(value, expected):
            problems.append(
                f"The field {name!r} must be a {subschema['type']}, not {type(value).__name__}."
            )
    return problems


def reserved_fields_in(contract: dict[str, Any]) -> frozenset[str]:
    """The reserved output fields a projected contract declares. Empty is the only good answer.

    Task 35 refuses these names at authoring and task 36 projects only validated
    contracts, so a non-empty result means a contract reached execution around the
    service layer. The execution boundary refuses such a section unrun, and the
    adversarial corpus (task 42) scores this function directly — it is the same check,
    not a copy of it.
    """
    properties = contract.get("properties")
    declared = set(properties) if isinstance(properties, dict) else set()
    return frozenset(declared) & RESERVED_OUTPUT_FIELDS


def numerals_in(text: str) -> frozenset[str]:
    """Every numeral token in a piece of text, normalised (separators stripped)."""
    return frozenset(_canonical_numeral(match.replace(",", "")) for match in _NUMERAL.findall(text))


def _canonical_numeral(token: str) -> str:
    """One spelling per number, so lineage survives a round trip through the contract.

    A skill declaring ``{"type": "number"}`` gets a Python ``float`` once the reply is
    validated, and an integral one reprs as ``8.0`` while the claim that sources it says
    "8 years". Comparing the spellings rather than the numbers made that pair look like two
    different figures, and refused a section whose figure was properly sourced.

    Trailing zeros in the fractional part are the whole of the difference, so they are the
    whole of what is stripped: ``8.0`` and ``8.00`` become ``8``, ``8.10`` becomes ``8.1``,
    and ``8.05`` is left exactly as it is.
    """
    if "." not in token:
        return token
    whole, _, fraction = token.partition(".")
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def without_document_references(text: str) -> str:
    """The text with recognised date and document-reference spans removed.

    Applied to content before the numeral scan, never to the claims that provide cover —
    so it can only narrow what the scan flags, and a draft that passed before this
    function existed still passes. A span-based erasure rather than a value-based
    allowlist, because the same four digits are a year in "in 2026" and a quantity in
    "2,026 million", and only the surrounding characters can tell them apart.
    """
    return _REFERENCE.sub(" ", text)


def _erased(text: str) -> str:
    """Every one-way erasure, composed once.

    Written here rather than spelled out at each scan site: the numeral scan and the
    salvage that removes offending sentences must agree exactly about what counts as a
    figure, and they drifted apart the moment there were three erasers instead of two.
    """
    return without_plain_counts(without_product_names(without_document_references(text)))


def without_product_names(text: str) -> str:
    """The text with the numbers that belong to product names removed.

    "Microsoft 365" is not a figure, and a live report lost five sections to it: the same
    three digits were flagged in the executive summary, the business overview, the
    financial analysis and the catalysts, in prose that never once asserted a quantity.
    ADR 0060 records the decision and the trade.

    **The word that owns the number decides.** A regex alone cannot separate "Microsoft
    365" from "Revenue 365", so the pair is matched and the head word is read against
    three tests, all of which it must pass:

    * **It is capitalised mid-sentence.** That is the proper-noun signal. Capitalisation
      at the *start* of a sentence says nothing — every sentence has it — and trusting
      it excused "Shipped 240 units", which an existing test caught. The cost is that a
      product name opening a sentence keeps its figure; the alternative was a rule that
      lets a real quantity through whenever a sentence begins with a verb.
    * **It is not a word the platform knows as a line item.** That denylist is *derived*
      from :data:`~aer.core.concepts.CANONICAL_CONCEPTS`, so it grows with the vocabulary
      rather than drifting behind it: "Revenue 365", "Cash 500" and "Goodwill 365" stay
      figures, as does "EBITDA 1234" through the prose terms no filer tags.
    * **It carries a capital at all**, which the mid-sentence test implies but which is
      checked plainly because it is the cheapest way to say what a name looks like.

    The number itself is bounded to four bare digits with no separator, no decimal and no
    per-cent sign, and must not be followed by a measure word: "Azure 12 million" is a
    measurement whatever precedes it. A product name carries none of those.

    The same one-way contract as the erasers beside it: applied to content before the
    scan, never to the claims that provide cover, so it can only narrow what gets flagged.
    """

    def erase(match: re.Match[str]) -> str:
        name = match.group(1)
        if not any(character.isupper() for character in name):
            return match.group(0)
        if name.strip(".'\u2019&-").lower() in _FINANCIAL_WORDS:
            return match.group(0)
        preceding = text[: match.start(1)].rstrip()
        if not preceding or preceding[-1] in ".!?":
            # Sentence-initial: the capital is grammar rather than a name.
            return match.group(0)
        return f"{name}{match.group(2)}"

    return _NAMED_NUMBER.sub(erase, text)


def without_plain_counts(text: str) -> str:
    """The text with plain counts removed — see :data:`_PLAIN_COUNT` and ADR 0057.

    The same one-way contract as the reference eraser: applied to content before the
    scan, never to claims, so it can only narrow what gets flagged.
    """
    return _PLAIN_COUNT.sub(" ", text)


def unsourced_numerals(content: dict[str, Any], covered_by: Iterable[str]) -> list[str]:
    """Numerals in the content that nothing accounts for, with where they sit.

    Numerals inside recognised date and document-reference spans are not figures and are
    not scanned — see :func:`without_document_references` and ADR 0054. For the rest, a
    numeral has lineage two ways, and either satisfies the rule:

    * it appears in a numeric claim's statement (``covered_by``) — each of which, by
      schema, names exactly one stored fact or recorded calculation; or
    * it sits inside an object that itself names its figure by ``calculation_id`` or
      ``financial_fact_id`` — the figure-row convention every built-in section has used
      since Phase 1, and the one the renderer turns into a footnote. **The named id is
      not taken on trust here**: the execution boundary separately refuses any content id
      the call's evidence does not hold, so a fabricated id fails there rather than
      passing as cover.

    Anything else is a figure with no lineage, which is the §2.12 validation failure.
    """
    covered: set[str] = set()
    for statement in covered_by:
        covered.update(numerals_in(statement))

    problems: list[str] = []
    for path, found in sorted(_numerals_by_path(content, path="content")):
        uncovered = sorted(found - covered)
        if uncovered:
            listed = ", ".join(uncovered)
            problems.append(
                f"{path} contains the numeral(s) {listed} which no numeric claim "
                "resolves to a stored fact or recorded calculation."
            )
    return problems


def _numerals_by_path(value: Any, *, path: str) -> list[tuple[str, frozenset[str]]]:
    """Walk the content and collect numerals with the path they were found at.

    Numbers count as well as digits inside strings: a JSON number in a field is as much a
    figure as one spelt out in prose, and the contract's ``number`` type is not a licence
    to assert one without lineage.
    """
    if isinstance(value, str):
        found = numerals_in(_erased(value))
        return [(path, found)] if found else []
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [(path, numerals_in(repr(value)))]
    if isinstance(value, dict):
        collected: list[tuple[str, frozenset[str]]] = []
        if not _names_its_figure(value):
            for key, item in value.items():
                if str(key) in NUMERAL_EXEMPT_KEYS:
                    continue
                collected.extend(_numerals_by_path(item, path=f"{path}.{key}"))
        return collected
    if isinstance(value, list):
        collected = []
        for index, item in enumerate(value):
            collected.extend(_numerals_by_path(item, path=f"{path}[{index}]"))
        return collected
    return []


# The keys whose presence makes an object a figure row: it names the stored figure its
# numerals came from. `source_document_id` is deliberately not on this list — a document
# reference says where prose came from, not which figure a numeral is.
_FIGURE_NAMING_KEYS: Final[frozenset[str]] = frozenset({"calculation_id", "financial_fact_id"})


def _names_its_figure(row: dict[Any, Any]) -> bool:
    return any(isinstance(row.get(key), str) and row.get(key) for key in _FIGURE_NAMING_KEYS)


# Sentence boundaries for the salvage below. Deterministic and deliberately simple: a
# terminal mark followed by whitespace. Prose that defeats it merely salvages a larger
# span, which errs towards removing more of the offending text rather than less.
_SENTENCES: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")

# The vocabulary of a sentence about missing evidence rather than about the company.
# Phrases, not a model: the question is whether the sentence's subject is the disclosure
# rather than the business, and these are the words that make it so.
_GAP_PHRASES: Final[tuple[str, ...]] = (
    "not disclosed",
    "no disclosure",
    "does not disclose",
    "not available",
    "is unavailable",
    "were not provided",
    "not reported",
    "does not report",
    "cannot be computed",
    "cannot compute",
    "could not be computed",
    "could not be determined",
    "insufficient evidence",
    "no evidence",
    "the evidence does not",
    "evidence is silent",
    "no segment data",
    "data is missing",
    "is not stated",
)

# How many sentences a section may spend on its own gaps. One: rule 6 of the drafting
# prompt says "in one clause and move on", and a live report spent a third of its prose
# describing absence because nothing enforced it (gap R4). Advisory rules drift; budgets
# refused in code do not.
MAX_GAP_SENTENCES: Final = 1


def prose_word_count(content: dict[str, Any]) -> int:
    """How many words the content's prose runs to, metadata ids excluded.

    The count behind the section word budget (gap O4): whitespace-delimited tokens over
    every string the reader will meet. Deliberately blunt — a budget needs a count both
    sides can predict, not a typographer's opinion.
    """
    words = 0

    def walk(value: Any) -> None:
        nonlocal words
        if isinstance(value, str):
            words += len(value.split())
        elif isinstance(value, dict):
            for key, item in value.items():
                if str(key) not in NUMERAL_EXEMPT_KEYS:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(content)
    return words


def gap_sentences(content: dict[str, Any]) -> list[str]:
    """Every sentence in the content's prose whose subject is missing evidence.

    Deterministic and phrase-based, like the numeral rule beside it. A sentence matching
    none of the phrases is about the company however hedged its verbs; matching one, it
    is about the disclosure — which the reader needs once, not per paragraph.
    """
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for sentence in _SENTENCES.split(value):
                lowered = sentence.lower()
                if any(phrase in lowered for phrase in _GAP_PHRASES):
                    found.append(sentence.strip())
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(content)
    return found


def without_unsourced_numeral_sentences(
    content: dict[str, Any], covered_by: Iterable[str]
) -> dict[str, Any] | None:
    """The content with every sentence carrying an unsourced numeral removed, or ``None``.

    The salvage behind ADR 0057. Two of a live report's sections were discarded whole —
    a billed draft each — over a single flagged token, when removing the one offending
    clause would have kept everything the rule had no quarrel with. This is the same
    move the plan salvage makes: code narrowing model output, never adding to it.

    ``None`` — salvage declined — whenever removal is not the repair: an unsourced
    numeral in a non-string field (a JSON number cannot be narrowed, only dropped, and
    dropping a field is a contract decision, not a salvage), or a string a removal would
    empty (a field wholly built of unsourced figures should fail loudly, not render
    blank). Declining leaves the caller exactly where it was: refusing the draft.

    The result must be revalidated in full by the caller. Removal can only narrow the
    numeral scan, but a shorter text can still break its contract's other rules.
    """
    covered: set[str] = set()
    for statement in covered_by:
        covered.update(numerals_in(statement))

    def scrub_text(value: str) -> str:
        sentences = _SENTENCES.split(value)
        kept = [sentence for sentence in sentences if numerals_in(_erased(sentence)) <= covered]
        if len(kept) == len(sentences):
            return value
        narrowed = " ".join(kept).strip()
        if not narrowed:
            raise _SalvageDeclinedError
        return narrowed

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            return scrub_text(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if numerals_in(repr(value)) <= covered:
                return value
            raise _SalvageDeclinedError
        if isinstance(value, dict) and not _names_its_figure(value):
            return {
                key: (item if str(key) in NUMERAL_EXEMPT_KEYS else scrub(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    try:
        narrowed_content = {key: scrub(item) for key, item in content.items()}
    except _SalvageDeclinedError:
        return None
    if narrowed_content == content:
        return None
    return narrowed_content


class _SalvageDeclinedError(Exception):
    """Raised inside the scrub walk when removal is not the repair."""


def trimmed_to_word_count(content: dict[str, Any], ceiling: int) -> dict[str, Any] | None:
    """The content shortened to ``ceiling`` words by dropping trailing sentences, or ``None``.

    The length half of ADR 0057's salvage. Nine of one live report's sixteen sections were
    refused for overrunning their budget — several for *nothing else* — and a section
    refused only for length is a fully cited, contract-conforming draft thrown away for
    being long. That is the worst trade in the pipeline: the evidence work is done and paid
    for, and the remedy is an edit.

    **Trailing sentences, longest field first.** The refusal tells the writer to "keep the
    analysis, drop the restatement and the narration", and in a section that overruns, both
    sit at the end. Taking from the longest remaining field each time spreads the cut rather
    than gutting one field to save another, and ties break on document order so the same
    draft always trims the same way.

    **It removes as little as it can.** The target is the *ceiling* the validator refuses
    above, not the budget the prompt asks for: trimming further would discard prose the
    rule has no quarrel with, and this function has no opinion about prose it is not
    obliged to remove.

    Two things it will not do, both of which return ``None`` — declining, and leaving the
    caller exactly where it was:

    * **Empty a field.** Every string keeps its first sentence, so a one-sentence
      ``lead_in`` is never removed and no field renders blank.
    * **Reach the ceiling by other means.** Dropping list items or whole fields is a
      contract decision rather than an edit, so a draft that cannot fit by shedding
      trailing sentences fails as it did before.

    The result must be revalidated in full by the caller: a shorter text satisfies the word
    budget by construction, and can still break any other rule its contract carries.
    """
    if ceiling <= 0 or prose_word_count(content) <= ceiling:
        return None

    narrowed = deepcopy(content)
    while prose_word_count(narrowed) > ceiling:
        slots = [slot for slot in _prose_slots(narrowed) if len(_SENTENCES.split(slot[2])) > 1]
        if not slots:
            # Nothing left that can shed a sentence without emptying a field.
            return None
        container, key, text, _ = max(slots, key=lambda slot: (len(slot[2].split()), slot[3]))
        kept = _SENTENCES.split(text)[:-1]
        container[key] = " ".join(kept).strip()

    return narrowed


def _prose_slots(value: Any, *, order: int = 0) -> list[tuple[Any, Any, str, int]]:
    """Every string the reader meets, with the container and key that hold it.

    Container and key rather than a path, so the caller can replace one string in place.
    The walk mirrors :func:`prose_word_count` exactly — the same keys skipped, the same
    strings counted — because a trim that removed text the count does not measure would
    shorten the section without ever satisfying the budget.
    """
    collected: list[tuple[Any, Any, str, int]] = []

    def walk(node: Any, container: Any, key: Any) -> None:
        nonlocal order
        if isinstance(node, str):
            order += 1
            collected.append((container, key, node, order))
        elif isinstance(node, dict):
            for item_key, item in node.items():
                if str(item_key) not in NUMERAL_EXEMPT_KEYS:
                    walk(item, node, item_key)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, node, index)

    walk(value, None, None)
    return [slot for slot in collected if slot[0] is not None]
