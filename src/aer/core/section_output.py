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
calculation. The rule is deliberately strict about years and percentages alike: a numeral
the platform cannot trace is a numeral the report cannot carry, whatever it denotes.

Pure and ``mypy --strict``: dictionaries in, problem strings out, nothing else consulted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Final

__all__ = [
    "NUMERAL_EXEMPT_KEYS",
    "contract_violations",
    "numerals_in",
    "unsourced_numerals",
]

# Keys whose numeric values are section metadata rather than assertions about the world.
# ``confidence`` is the renderer's own metadata key; nothing else is exempt.
NUMERAL_EXEMPT_KEYS: Final[frozenset[str]] = frozenset({"confidence"})

# A numeral as a reader meets one: digits, optional thousands separators and decimals,
# an optional trailing per-cent sign. Word-bounded so "10-K" and "FY22Q4" do not shed
# fragments, but "grew 34%" and "$198,270 million" both surface their figures. The
# trailing guard refuses only a *mid-decimal* stop (".<digit>"), so a numeral ending a
# sentence — "in 2022." — still counts.
_NUMERAL: Final[re.Pattern[str]] = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)%?(?!\w)(?!\.\d)")

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


def numerals_in(text: str) -> frozenset[str]:
    """Every numeral token in a piece of text, normalised (separators stripped)."""
    return frozenset(match.replace(",", "") for match in _NUMERAL.findall(text))


def unsourced_numerals(content: dict[str, Any], covered_by: Iterable[str]) -> list[str]:
    """Numerals in the content that no numeric claim accounts for, with where they sit.

    Args:
        covered_by: The statements of the section's *numeric* claims — each of which, by
            schema, names exactly one stored fact or recorded calculation. A numeral is
            covered when it appears in at least one of them; anything else in the content
            is a figure with no lineage, which is the §2.12 validation failure.
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
        found = numerals_in(value)
        return [(path, found)] if found else []
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [(path, numerals_in(repr(value)))]
    if isinstance(value, dict):
        collected: list[tuple[str, frozenset[str]]] = []
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
