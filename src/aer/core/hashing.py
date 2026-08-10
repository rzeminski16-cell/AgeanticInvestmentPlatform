"""Canonical serialisation and hash chaining.

Pure functions, no I/O. Used for the tamper-evident audit log, and later for artefact
addressing and for hashing the exact payload shown at an approval gate.

**Canonicalisation is the whole game.** A hash is only useful if the same logical value
always produces the same bytes. ``json.dumps`` does not guarantee that by default: key
order follows insertion order, and whitespace and unicode escaping vary. Two records with
identical content would then hash differently, every chain verification would fail, and
the natural reaction would be to stop trusting the verifier rather than the data — which
is the worst possible outcome for an integrity control.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Protocol
from uuid import UUID

__all__ = [
    "GENESIS_HASH",
    "ChainLink",
    "canonical_json",
    "chain_hash",
    "find_chain_break",
    "sha256_hex",
    "verify_chain",
]

# The predecessor of the first event in a chain. An explicit sentinel rather than an empty
# string, so "no previous event" and "previous event hashed to nothing" cannot be confused.
GENESIS_HASH: Final = "0" * 64


def _encode_unsupported(value: Any) -> str:
    """Render types JSON cannot express, deterministically.

    Each conversion is lossless and unambiguous within its type, which is what canonical
    hashing requires. ``Decimal`` becomes its exact string form rather than a float,
    because passing through binary floating point would make 0.1 unstable.
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, set | frozenset):
        return json.dumps(sorted(map(str, value)))
    message = f"cannot canonicalise value of type {type(value).__name__!r}"
    raise TypeError(message)


def canonical_json(payload: Any) -> str:
    """Serialise ``payload`` to a stable, canonical JSON string.

    Keys are sorted, separators are minimal, and non-ASCII characters are preserved as
    themselves rather than escaped, so the output depends only on the value.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_encode_unsupported,
    )


def sha256_hex(data: str | bytes) -> str:
    """Return the hex-encoded SHA-256 digest of ``data``."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def chain_hash(previous_hash: str | None, payload: Any) -> str:
    """Compute the hash linking a record to its predecessor.

    ``sha256(previous_hash || canonical_json(payload))``. Including the predecessor is
    what makes the chain tamper-evident: altering any record changes its hash, which
    invalidates every record after it, so a single edit cannot be made to look consistent
    without rewriting the entire remainder of the log.
    """
    previous = previous_hash if previous_hash is not None else GENESIS_HASH
    return sha256_hex(previous + canonical_json(payload))


class ChainLink(Protocol):
    """The minimum a record must expose to be chain-verifiable."""

    @property
    def prev_hash(self) -> str | None: ...

    @property
    def this_hash(self) -> str: ...

    @property
    def payload(self) -> Any: ...


def find_chain_break(
    links: Sequence[ChainLink], *, expected_previous: str | None = None
) -> int | None:
    """Return the index of the first record that fails verification, or ``None``.

    Two failure modes are checked, and both matter:

    * a record whose ``this_hash`` does not match its own content — the record was edited;
    * a record whose ``prev_hash`` does not match its predecessor's ``this_hash`` — a
      record was inserted, removed or reordered.

    Returning the index rather than a bare boolean means an operator can be told *where*
    the log stopped being trustworthy, which is the difference between an actionable
    alert and an unfalsifiable one.

    Args:
        expected_previous: The ``this_hash`` the first record must link back to, for a
            caller verifying a *slice* of a longer chain. A log too large to hold in memory
            has to be read in pages, and without this the join between two pages is the one
            place a break cannot be seen: the first record of a page has no predecessor in
            its own sequence, so its ``prev_hash`` would go unchecked and a record deleted
            exactly at a page boundary would verify. Left ``None`` for the true start of a
            chain, where there is no predecessor to demand.
    """
    for index, link in enumerate(links):
        if index == 0:
            if expected_previous is not None and link.prev_hash != expected_previous:
                return index
        elif link.prev_hash != expected_previous:
            return index
        if link.this_hash != chain_hash(link.prev_hash, link.payload):
            return index
        expected_previous = link.this_hash
    return None


def verify_chain(links: Sequence[ChainLink]) -> bool:
    """Whether every record in ``links`` verifies against its predecessor."""
    return find_chain_break(links) is None
