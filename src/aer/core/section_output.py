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
calculation. One carve-out, decided by the operator and recorded in ADR 0054: a numeral
inside a recognisable date or document reference — "March 2026", "Q3 2025", "Item 2.02",
a labelled CIK — denotes no quantity, and refusing it was provenance tripping the rule
that exists to protect provenance. The exemption is by *span*, never by value: those
exact characters are excused, and the same digits anywhere else still need lineage.

Pure and ``mypy --strict``: dictionaries in, problem strings out, nothing else consulted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Final

from aer.core.schemas.skill import RESERVED_OUTPUT_FIELDS

__all__ = [
    "NUMERAL_EXEMPT_KEYS",
    "contract_violations",
    "numerals_in",
    "reserved_fields_in",
    "unsourced_numerals",
    "without_document_references",
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
            # A year in temporal company: "in 2026", "since 2024", "mid-2025", and the
            # paired form "between 2019 and 2024". The anchors deliberately exclude
            # "of", "to" and "for", each of which reads naturally in front of a
            # quantity.
            r"\b(?:in|by|since|until|through|throughout|during|before|after|between|"
            r"around|calendar|year|early|late|mid)[\s-](?:19|20)\d{2}"
            r"(?:\s*(?:and|to|[-\u2013\u2014])\s*(?:19|20)\d{2})?\b",
            # A year-to-year range on its own: "2019-2024", hyphen or en/em dash.
            r"\b(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(?:19|20)\d{2}\b",
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
        found = numerals_in(without_document_references(value))
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
