"""Signed CSRF tokens.

Written before any form exists, and tested as thoroughly as if one did. A CSRF helper
that is "obviously correct" and never exercised is how you end up with a signature check
that passes on an empty signature, or an expiry that a negative age slips through.

Every test here states the attack it prevents, because a test named
``test_verify_returns_false`` tells a future reader nothing about why the branch matters.
"""

from __future__ import annotations

import time

import pytest

from aer.api.security import (
    CSRF_TOKEN_MAX_AGE_SECONDS,
    issue_csrf_token,
    tokens_match,
    verify_csrf_token,
)

KEY = b"a-test-signing-key-of-adequate-length"
OTHER_KEY = b"a-different-signing-key-entirely-here"


class TestIssuing:
    def test_a_fresh_token_verifies(self):
        assert verify_csrf_token(KEY, issue_csrf_token(KEY))

    def test_tokens_are_unique(self):
        # Issued within the same second, so only the nonce distinguishes them. Identical
        # tokens would make one captured token reusable against every session.
        issued = {issue_csrf_token(KEY) for _ in range(100)}
        assert len(issued) == 100

    def test_the_token_has_three_dotted_parts(self):
        assert len(issue_csrf_token(KEY).split(".")) == 3


class TestSignatureForgery:
    def test_a_token_from_another_key_is_rejected(self):
        # The property that makes this a *signed* double submit: an attacker who can set
        # a cookie on the origin still cannot mint a value this server will accept.
        assert not verify_csrf_token(KEY, issue_csrf_token(OTHER_KEY))

    def test_an_altered_nonce_is_rejected(self):
        nonce, stamp, signature = issue_csrf_token(KEY).split(".")
        assert not verify_csrf_token(KEY, f"{nonce}x.{stamp}.{signature}")

    def test_an_altered_timestamp_is_rejected(self):
        # Otherwise expiry is decorative: anyone could push a stale token's clock forward.
        nonce, stamp, signature = issue_csrf_token(KEY).split(".")
        forged = int(stamp) + 10_000
        assert not verify_csrf_token(KEY, f"{nonce}.{forged}.{signature}")

    def test_an_altered_signature_is_rejected(self):
        nonce, stamp, signature = issue_csrf_token(KEY).split(".")
        flipped = ("0" if signature[0] != "0" else "1") + signature[1:]
        assert not verify_csrf_token(KEY, f"{nonce}.{stamp}.{flipped}")

    @pytest.mark.parametrize(
        "malformed",
        [
            "",
            "...",
            "onlyonepart",
            "two.parts",
            "a.b.c.d",
            "nonce.notanumber.abc",
            ".123.abc",
            "nonce.123.",
        ],
    )
    def test_malformed_input_is_rejected_without_raising(self, malformed):
        # A parse error must be a rejection, never a 500. An exception here would turn a
        # junk cookie into a denial of service on every form.
        assert verify_csrf_token(KEY, malformed) is False

    def test_none_is_rejected(self):
        assert not verify_csrf_token(KEY, None)


class TestExpiry:
    def test_a_token_within_its_lifetime_verifies(self):
        now = int(time.time())
        token = issue_csrf_token(KEY, issued_at=now - 60)
        assert verify_csrf_token(KEY, token, now=now)

    def test_a_token_past_its_lifetime_is_rejected(self):
        now = int(time.time())
        token = issue_csrf_token(KEY, issued_at=now - CSRF_TOKEN_MAX_AGE_SECONDS - 1)
        assert not verify_csrf_token(KEY, token, now=now)

    def test_the_boundary_is_inclusive(self):
        now = int(time.time())
        token = issue_csrf_token(KEY, issued_at=now - CSRF_TOKEN_MAX_AGE_SECONDS)
        assert verify_csrf_token(KEY, token, now=now)

    def test_a_token_from_the_future_is_rejected(self):
        # A negative age means a clock that moved or a payload that was tampered with.
        # Accepting it would let a forged far-future timestamp never expire.
        now = int(time.time())
        token = issue_csrf_token(KEY, issued_at=now + 600)
        assert not verify_csrf_token(KEY, token, now=now)


class TestDoubleSubmit:
    def test_matching_halves_pass(self):
        token = issue_csrf_token(KEY)
        assert tokens_match(token, token)

    def test_two_separately_valid_tokens_do_not_match(self):
        # Both halves must be the *same* token. Accepting any two valid tokens would let
        # an attacker who obtains one valid token from anywhere pair it with the victim's
        # cookie.
        assert not tokens_match(issue_csrf_token(KEY), issue_csrf_token(KEY))

    @pytest.mark.parametrize(
        ("cookie", "submitted"),
        [(None, "x"), ("x", None), (None, None), ("", "x"), ("x", "")],
    )
    def test_a_missing_half_fails(self, cookie, submitted):
        assert not tokens_match(cookie, submitted)


class TestKeyRotation:
    def test_restarting_with_a_new_key_invalidates_old_tokens(self):
        # The documented consequence of the ephemeral development key. Asserted so the
        # behaviour is a known trade rather than a surprise.
        token = issue_csrf_token(KEY)
        assert not verify_csrf_token(OTHER_KEY, token)
