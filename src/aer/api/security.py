"""Signed CSRF tokens.

No form posts here yet — the request form arrives in the next task. This exists now
because retrofitting CSRF protection means auditing every handler written in the
meantime, and the one that gets missed is always a state-changing one.

**The threat.** This application runs on ``127.0.0.1`` with no authentication, which
feels safe and is not. Any page in any browser tab can issue a cross-origin ``POST`` to
``http://127.0.0.1:8000``. The browser sends it, the request looks entirely ordinary to
the server, and the attacker cannot read the response — but by then a research run has
been started, a report deleted, or an approval gate passed. Same-origin policy prevents
reading, never sending.

**The defence.** Double-submit with a signed token. A token is issued into a cookie and
the same value is rendered into the form. On submission both must be present, identical,
and carry a valid signature. A cross-origin page can cause the cookie to be sent, but it
cannot read the cookie to copy the value into the form body, and it cannot forge the
signature without the server's key.

Signing on top of double-submit matters: an unsigned double-submit is defeated by anyone
who can set a cookie on the origin, which on a shared machine or via a subdomain is not a
high bar. The signature means only this server can mint an acceptable value.
"""

from __future__ import annotations

import hmac
import secrets
import time
from hashlib import sha256
from typing import Final

__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_FIELD_NAME",
    "CSRF_HEADER_NAME",
    "CSRF_TOKEN_MAX_AGE_SECONDS",
    "issue_csrf_token",
    "tokens_match",
    "verify_csrf_token",
]

CSRF_COOKIE_NAME: Final = "aer_csrf"
CSRF_FIELD_NAME: Final = "csrf_token"
CSRF_HEADER_NAME: Final = "X-CSRF-Token"

# Long enough that a form left open over lunch still submits; short enough that a token
# captured from a stale page is not usable indefinitely.
CSRF_TOKEN_MAX_AGE_SECONDS: Final = 8 * 60 * 60

_NONCE_BYTES: Final = 16
_SEPARATOR: Final = "."
_EXPECTED_PARTS: Final = 3


def _signature(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode("utf-8"), sha256).hexdigest()


def issue_csrf_token(key: bytes, *, issued_at: int | None = None) -> str:
    """Mint a token of the form ``<nonce>.<issued_at>.<signature>``.

    The timestamp is inside the signed payload rather than tracked server-side, so
    expiry needs no storage and survives a restart of everything except the key itself.
    """
    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    stamp = int(time.time()) if issued_at is None else issued_at
    payload = f"{nonce}{_SEPARATOR}{stamp}"
    return f"{payload}{_SEPARATOR}{_signature(key, payload)}"


def verify_csrf_token(
    key: bytes,
    token: str | None,
    *,
    max_age_seconds: int = CSRF_TOKEN_MAX_AGE_SECONDS,
    now: int | None = None,
) -> bool:
    """Whether ``token`` was minted by this key and has not expired.

    Returns a bool rather than raising: the caller decides the response, and every
    rejection reason is deliberately indistinguishable to the client. Telling an attacker
    *which* check failed is free information about the key and the clock.
    """
    if not token:
        return False

    parts = token.split(_SEPARATOR)
    if len(parts) != _EXPECTED_PARTS:
        return False

    nonce, stamp, provided = parts
    if not nonce:
        return False

    try:
        issued_at = int(stamp)
    except ValueError:
        return False

    expected = _signature(key, f"{nonce}{_SEPARATOR}{stamp}")
    if not hmac.compare_digest(expected, provided):
        return False

    current = int(time.time()) if now is None else now
    age = current - issued_at
    # A token from the future is rejected as firmly as an expired one: it means either a
    # clock that moved or a payload that was tampered with, and neither should pass.
    return 0 <= age <= max_age_seconds


def tokens_match(cookie_token: str | None, submitted_token: str | None) -> bool:
    """Constant-time equality for the two halves of the double submit."""
    if not cookie_token or not submitted_token:
        return False
    return hmac.compare_digest(cookie_token, submitted_token)
