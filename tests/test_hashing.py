"""Canonical serialisation and hash chaining.

Pure functions, no database. Tested thoroughly because everything downstream trusts them:
the audit chain, artefact addressing, and the hash of what was shown at an approval gate.
A canonicalisation bug here would not announce itself — it would surface much later as a
chain that "just stopped verifying", and the natural reaction to that is to distrust the
verifier rather than the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from aer.core.hashing import (
    GENESIS_HASH,
    canonical_json,
    chain_hash,
    find_chain_break,
    sha256_hex,
    verify_chain,
)


@dataclass
class Link:
    """Minimal ChainLink implementation for testing the verifier in isolation."""

    prev_hash: str | None
    this_hash: str
    payload: Any


def build_chain(payloads: list[Any]) -> list[Link]:
    links: list[Link] = []
    previous: str | None = None
    for payload in payloads:
        digest = chain_hash(previous, payload)
        links.append(Link(prev_hash=previous, this_hash=digest, payload=payload))
        previous = digest
    return links


class TestCanonicalJson:
    def test_key_order_does_not_affect_output(self):
        # The property everything else depends on. Python dicts preserve insertion order,
        # so without sorting these two would hash differently despite being equal.
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_nested_key_order_does_not_affect_output(self):
        left = {"outer": {"z": 1, "a": {"q": 2, "b": 3}}}
        right = {"outer": {"a": {"b": 3, "q": 2}, "z": 1}}
        assert canonical_json(left) == canonical_json(right)

    def test_output_has_no_incidental_whitespace(self):
        assert canonical_json({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'

    def test_non_ascii_is_preserved_not_escaped(self):
        assert canonical_json({"name": "Nestlé"}) == '{"name":"Nestlé"}'

    def test_list_order_is_significant(self):
        # Lists are ordered data; reordering them is a different value, unlike dict keys.
        assert canonical_json([1, 2]) != canonical_json([2, 1])

    def test_decimal_serialises_exactly_without_passing_through_float(self):
        # str(Decimal) not float(Decimal): 0.1 has no exact binary representation, and a
        # money value that changes when hashed is worse than useless.
        assert canonical_json({"v": Decimal("0.1")}) == '{"v":"0.1"}'
        assert canonical_json({"v": Decimal("1.10")}) == '{"v":"1.10"}'

    def test_datetime_and_date_serialise_as_iso8601(self):
        moment = datetime(2026, 7, 27, 12, 30, tzinfo=UTC)
        assert canonical_json({"t": moment}) == '{"t":"2026-07-27T12:30:00+00:00"}'
        assert canonical_json({"d": date(2026, 7, 27)}) == '{"d":"2026-07-27"}'

    def test_uuid_serialises_as_its_string_form(self):
        identifier = UUID("12345678-1234-5678-1234-567812345678")
        assert canonical_json({"id": identifier}) == '{"id":"12345678-1234-5678-1234-567812345678"}'

    def test_sets_serialise_deterministically(self):
        assert canonical_json({"s": {"b", "a"}}) == canonical_json({"s": {"a", "b"}})

    def test_unserialisable_type_raises_rather_than_being_silently_coerced(self):
        class Opaque:
            pass

        with pytest.raises(TypeError, match="cannot canonicalise"):
            canonical_json({"x": Opaque()})


class TestSha256Hex:
    def test_known_vector(self):
        # The published SHA-256 test vector for "abc" (FIPS 180-4). A known-answer test
        # rather than a self-consistency one: comparing our hash to our own hash would
        # pass just as happily against a broken implementation.
        expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        assert sha256_hex("abc") == expected

    def test_string_and_utf8_bytes_agree(self):
        assert sha256_hex("Nestlé") == sha256_hex("Nestlé".encode())

    def test_digest_is_64_hex_characters(self):
        digest = sha256_hex("anything")
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


class TestChainHash:
    def test_is_deterministic(self):
        payload = {"a": 1}
        assert chain_hash(None, payload) == chain_hash(None, payload)

    def test_none_predecessor_uses_the_genesis_sentinel(self):
        # An explicit sentinel, so "no predecessor" and "predecessor hashed to nothing"
        # cannot collide.
        assert chain_hash(None, {"a": 1}) == chain_hash(GENESIS_HASH, {"a": 1})

    def test_changing_the_payload_changes_the_hash(self):
        assert chain_hash(None, {"a": 1}) != chain_hash(None, {"a": 2})

    def test_changing_the_predecessor_changes_the_hash(self):
        payload = {"a": 1}
        assert chain_hash("0" * 63 + "1", payload) != chain_hash("0" * 63 + "2", payload)

    def test_equal_payloads_written_differently_hash_identically(self):
        assert chain_hash(None, {"a": 1, "b": 2}) == chain_hash(None, {"b": 2, "a": 1})


class TestChainVerification:
    def test_an_intact_chain_verifies(self):
        chain = build_chain([{"n": i} for i in range(5)])
        assert verify_chain(chain)
        assert find_chain_break(chain) is None

    def test_an_empty_chain_verifies(self):
        assert verify_chain([])

    def test_a_single_link_verifies(self):
        assert verify_chain(build_chain([{"only": True}]))

    def test_an_edited_payload_is_detected_at_its_own_index(self):
        chain = build_chain([{"n": i} for i in range(5)])
        chain[2].payload = {"n": 999}
        assert not verify_chain(chain)
        assert find_chain_break(chain) == 2

    def test_editing_the_first_record_is_detected(self):
        chain = build_chain([{"n": i} for i in range(3)])
        chain[0].payload = {"n": "tampered"}
        assert find_chain_break(chain) == 0

    def test_editing_the_last_record_is_detected(self):
        chain = build_chain([{"n": i} for i in range(3)])
        chain[-1].payload = {"n": "tampered"}
        assert find_chain_break(chain) == 2

    def test_a_removed_record_is_detected(self):
        chain = build_chain([{"n": i} for i in range(5)])
        del chain[2]
        # Index 2 is now the record whose prev_hash points at the removed one.
        assert find_chain_break(chain) == 2

    def test_reordered_records_are_detected(self):
        chain = build_chain([{"n": i} for i in range(4)])
        chain[1], chain[2] = chain[2], chain[1]
        assert not verify_chain(chain)

    def test_an_inserted_record_is_detected(self):
        chain = build_chain([{"n": i} for i in range(3)])
        forged_payload = {"n": "inserted"}
        forged = Link(
            prev_hash=chain[0].this_hash,
            this_hash=chain_hash(chain[0].this_hash, forged_payload),
            payload=forged_payload,
        )
        chain.insert(1, forged)
        # The forgery itself is self-consistent, but the record after it is not.
        assert find_chain_break(chain) == 2

    def test_recomputing_hashes_after_tampering_still_fails_without_a_full_rewrite(self):
        # An attacker who edits one record and fixes only its own hash still leaves every
        # subsequent prev_hash pointing at the old value. Rewriting the whole tail is the
        # minimum required, which is exactly the property the chain is meant to provide.
        chain = build_chain([{"n": i} for i in range(5)])
        chain[1].payload = {"n": "tampered"}
        chain[1].this_hash = chain_hash(chain[1].prev_hash, chain[1].payload)
        assert find_chain_break(chain) == 2
