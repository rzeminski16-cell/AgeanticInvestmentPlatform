"""Errors raised by the fetch layer.

Each one is a distinct decision someone might need to audit later, which is why they are
separate classes with stable codes rather than one ``FetchError`` with a message. "The
run stopped because robots.txt disallowed the path" and "the run stopped because the host
resolved to a private address" call for entirely different responses, and a caller should
not have to match on prose to tell them apart.
"""

from __future__ import annotations

from typing import Any, ClassVar

from aer.errors import AerError, ExternalServiceError

__all__ = [
    "CircuitOpenError",
    "ContentTypeMismatchError",
    "FetchTooLargeError",
    "RobotsDisallowedError",
    "SsrfBlockedError",
    "UrlNotAllowedError",
]


class SsrfBlockedError(AerError):
    """A URL resolved to an address this platform must never connect to.

    Always a refusal, never a warning. The addresses being blocked are the ones that reach
    the machine itself, the local network and the cloud metadata service — the targets
    that make server-side request forgery worth attempting in the first place.
    """

    code: ClassVar[str] = "ssrf_blocked"
    http_status: ClassVar[int] = 400


class UrlNotAllowedError(AerError):
    """The host is not on the allowlist, or is explicitly blocked.

    An allowlist rather than a blocklist: this platform knows which publishers it reads,
    and anything else arriving in a URL is either a mistake or an attempt.
    """

    code: ClassVar[str] = "url_not_allowed"
    http_status: ClassVar[int] = 400


class RobotsDisallowedError(AerError):
    """The publisher's robots.txt forbids this path for our user agent.

    A hard refusal. The user's stated constraint is that this platform must not breach a
    site's terms of use, and robots.txt is the machine-readable form of exactly that. A
    warning that is logged and then ignored would be a breach with a paper trail.
    """

    code: ClassVar[str] = "robots_disallowed"
    http_status: ClassVar[int] = 403


class FetchTooLargeError(AerError):
    """The response exceeded the byte cap and was abandoned mid-stream."""

    code: ClassVar[str] = "fetch_too_large"
    http_status: ClassVar[int] = 413


class ContentTypeMismatchError(AerError):
    """What arrived is not the kind of document that was asked for.

    Decided by sniffing the bytes, not by trusting ``Content-Type``. A server that labels
    an HTML error page as ``application/pdf`` is common; a parser handed that page
    produces confident nonsense rather than an error.
    """

    code: ClassVar[str] = "content_type_mismatch"
    http_status: ClassVar[int] = 422


class CircuitOpenError(ExternalServiceError):
    """A provider has failed repeatedly, so requests are being refused for a cooldown.

    Failing fast rather than continuing to hammer a service that is already struggling.
    Politeness and self-interest agree here: a provider that rate-limits or bans us is
    worse for the run than a pause.
    """

    code: ClassVar[str] = "circuit_open"
    http_status: ClassVar[int] = 503

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retry_after_seconds: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {"retry_after_seconds": retry_after_seconds}
        merged.update(context or {})
        super().__init__(message, provider=provider, retryable=True, context=merged)
        self.retry_after_seconds = retry_after_seconds
