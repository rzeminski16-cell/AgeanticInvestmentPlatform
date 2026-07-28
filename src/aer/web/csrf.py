"""CSRF protection for the server-rendered forms.

Wires :mod:`aer.api.security` into the request/response cycle: issue a token into a
cookie and into the form, then require both on submission and check they agree.

**Why this matters on loopback.** The application runs on ``127.0.0.1`` with no
authentication, which feels safe and is not. Any page in any browser tab can POST to
``http://127.0.0.1:8000``. The browser sends the request, the server sees something
entirely ordinary, and a research run has been commissioned — spending real money — by a
page the operator merely visited. Same-origin policy stops the attacker reading the
response; it has never stopped the request.
"""

from __future__ import annotations

from typing import Final

from starlette.requests import Request
from starlette.responses import Response

from aer.api.security import (
    CSRF_COOKIE_NAME,
    CSRF_FIELD_NAME,
    CSRF_HEADER_NAME,
    CSRF_TOKEN_MAX_AGE_SECONDS,
    issue_csrf_token,
    tokens_match,
    verify_csrf_token,
)
from aer.config import Settings

__all__ = ["CSRF_FIELD_NAME", "csrf_is_valid", "new_csrf_token", "set_csrf_cookie"]

_SAME_SITE: Final = "strict"


def new_csrf_token(settings: Settings) -> str:
    """Mint a token for this form.

    Separate from :func:`set_csrf_cookie` because the token has to exist *before* the
    template renders — it goes into a hidden input — while the cookie can only be set on
    the response that rendering produces.
    """
    return issue_csrf_token(settings.signing_key)


def set_csrf_cookie(response: Response, token: str) -> None:
    """Put ``token`` in the cookie half of the double submit."""
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=CSRF_TOKEN_MAX_AGE_SECONDS,
        # Not HttpOnly. The double-submit pattern needs the value readable so a script can
        # copy it into a header; the protection comes from the signature and from
        # same-origin, not from hiding the cookie.
        httponly=False,
        # Strict, not Lax: Lax still sends the cookie on a top-level cross-site GET, and
        # there is no cross-site navigation into this application worth supporting.
        samesite=_SAME_SITE,
        # Not Secure: this is served over plain HTTP on loopback, and a Secure cookie
        # would simply never be sent, silently breaking every form. Revisit with TLS.
        secure=False,
        path="/",
    )


def csrf_is_valid(request: Request, submitted: str | None, settings: Settings) -> bool:
    """Whether the submission carries a matching, validly signed token.

    Both halves must be present, both must verify against this server's key, and both
    must be the same value. A cross-origin page can cause the cookie to be *sent*, but it
    cannot read it to copy the value into the body, and it cannot forge the signature.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    provided = submitted or request.headers.get(CSRF_HEADER_NAME)

    if not tokens_match(cookie_token, provided):
        return False
    return verify_csrf_token(settings.signing_key, provided)
