"""The single door out to the network.

Every outbound request this platform makes passes through :class:`SafeFetcher`. Nothing
else opens a socket to the internet — that is a rule in ``CLAUDE.md``, and one door is what
makes it checkable.

**No agent can call this with a URL it chose.** There is no agent-callable tool anywhere in
this system that takes an arbitrary URL. An agent asks for a *kind* of source — "the latest
annual report for this company" — and deterministic adapter code decides which URL that
means. Text hidden inside a fetched filing can instruct as loudly as it likes; no tool
exists that would carry the instruction out. That is the structural answer to prompt
injection escalating to exfiltration (threat model T3), and it is a property of what is
*absent* from the tool surface, so it has to be stated somewhere it will be read before
someone adds the missing tool.

**Everything is archived, including failures.** A 404, a 500, an error page: each is a fact
about what happened during a run, and a run whose failures left no trace cannot be audited.
Failed bodies are hashed and stored exactly like successful ones — "the server returned a
page saying the filing was withdrawn" is sometimes the most informative thing in a run.

Redirects are followed by hand rather than by httpx. Automatic following would resolve and
connect to each hop before this code saw it, which is exactly the check that must not be
skipped.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urljoin

import httpx
import structlog

from aer.config import Settings
from aer.core.enums import Provider
from aer.errors import ExternalServiceError
from aer.fetch.errors import ContentTypeMismatchError, FetchTooLargeError, SsrfBlockedError
from aer.fetch.limits import CircuitBreaker, RateLimiter
from aer.fetch.policy import FetchPolicy, policy_for_url
from aer.fetch.robots import RobotsCache
from aer.fetch.ssrf import resolve_and_validate
from aer.fetch.transport import PinnedAddressTransport
from aer.storage.protocol import ArtefactStore

__all__ = ["MAX_ATTEMPTS", "MAX_REDIRECTS", "FetchResult", "SafeFetcher", "sniff_media_type"]

_log = structlog.get_logger("aer.fetch.client")

MAX_REDIRECTS: Final = 3
MAX_ATTEMPTS: Final = 4

_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_REDIRECT_STATUSES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})

# Longest we will honour from a Retry-After header. A server asking us back in an hour is
# not something to sit and wait for inside a request.
MAX_RETRY_AFTER_SECONDS: Final = 120.0

_SNIFF_BYTES: Final = 1024
_HTTP_OK: Final = 200
_HTTP_REDIRECTION: Final = 300
_MAX_BACKOFF_SECONDS: Final = 30.0

Sleeper = Callable[[float], Awaitable[Any]]


@dataclass(slots=True)
class FetchResult:
    """What a fetch produced, whether or not it succeeded."""

    url: str
    final_url: str
    status_code: int
    sha256: str
    size_bytes: int
    media_type: str
    declared_media_type: str | None
    headers: dict[str, str]
    redirect_chain: tuple[str, ...]
    elapsed_ms: float
    attempts: int
    licence_note: str = ""
    robots_allowed: bool | None = None

    # Filled in by whichever service records the provenance. The fetcher archives the
    # bytes but does not touch the database: keeping it free of a session is what lets it
    # be tested, and reused, without one.
    artefact_id: object = None

    @property
    def ok(self) -> bool:
        return _HTTP_OK <= self.status_code < _HTTP_REDIRECTION


# Magic bytes at the very start of the content. "application/zip" also covers .xlsx,
# .docx and every other OOXML container; telling those apart needs the archive's
# contents, which is the extraction layer's job rather than this one's.
_MAGIC_PREFIXES: Final[tuple[tuple[bytes, str], ...]] = (
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x89PNG", "image/unknown"),
    (b"\xff\xd8\xff", "image/unknown"),
    (b"GIF8", "image/unknown"),
)

# Checked after leading whitespace is stripped, lowercased, because a document may open
# with a byte-order mark, a newline or a stray space and still be exactly what it says.
_TEXT_PREFIXES: Final[tuple[tuple[bytes, str], ...]] = (
    (b"<?xml", "application/xml"),
    (b"<!doctype", "text/html"),
    (b"<html", "text/html"),
    (b"{", "application/json"),
    (b"[", "application/json"),
)


def sniff_media_type(head: bytes, declared: str | None) -> str:
    """Identify content from its first bytes, falling back to the declared type.

    Sniffed rather than trusted. A server labelling an HTML error page as
    ``application/pdf`` is common, and a PDF parser handed that page does not fail — it
    produces confident nonsense, which is the failure mode this entire codebase exists to
    prevent.
    """
    sample = head[:_SNIFF_BYTES]

    for prefix, media_type in _MAGIC_PREFIXES:
        if sample.startswith(prefix):
            return media_type

    stripped = sample.lstrip().lower()
    for prefix, media_type in _TEXT_PREFIXES:
        if stripped.startswith(prefix):
            return media_type

    if declared:
        return declared.split(";", 1)[0].strip().lower()
    return "application/octet-stream"


@dataclass(frozen=True, slots=True)
class _Response:
    """One completed HTTP exchange: the metadata, and the body already read."""

    status_code: int
    headers: httpx.Headers
    body: bytes


class SafeFetcher:
    """Fetches URLs through every control this platform requires.

    Args:
        settings: Supplies the User-Agent, the byte cap and the insecure-HTTP switch.
        store: Where every response body is archived, successful or not.
        limiter: Shared token bucket.
        breaker: Shared circuit breaker.
        robots: robots.txt cache. ``None`` skips the check and is for tests only.
        sleep: Injected so retry backoff is assertable without waiting.
        resolver: Injected DNS, so a hostile answer can be simulated without one.
        transport_factory: Injected transport, so tests can mount respx without
            reaching the pinned-address backend.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        store: ArtefactStore,
        limiter: RateLimiter,
        breaker: CircuitBreaker,
        robots: RobotsCache | None = None,
        sleep: Sleeper | None = None,
        resolver: object = None,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        if not settings.http_user_agent.strip():
            # Refused at construction rather than at the first request. The SEC makes a
            # descriptive User-Agent a condition of access, and discovering that halfway
            # through a run wastes everything spent up to that point.
            message = (
                "AER_HTTP_USER_AGENT is empty. Every outbound request must identify the "
                "operator; the SEC makes that a condition of access."
            )
            raise ExternalServiceError(message, provider="fetch", retryable=False)

        self._settings = settings
        self._store = store
        self._limiter = limiter
        self._breaker = breaker
        self._robots = robots
        self._sleep: Sleeper = sleep or asyncio.sleep
        self._resolver = resolver
        self._transport_factory = transport_factory
        self._pins: dict[str, str] = {}

    # -- Public API ----------------------------------------------------------------------

    async def fetch(
        self,
        url: str,
        *,
        provider: Provider,
        expected_media_types: frozenset[str] | None = None,
        max_bytes: int | None = None,
        extra_hosts: tuple[str, ...] = (),
    ) -> FetchResult:
        """Fetch a URL, archive whatever came back, and describe it.

        Raises:
            UrlNotAllowedError: The host is not on the provider's allowlist.
            RobotsDisallowedError: The publisher forbids this path.
            SsrfBlockedError: A hop resolved to an address that must not be reached.
            FetchTooLargeError: The body exceeded the cap and was abandoned mid-stream.
            ContentTypeMismatchError: The sniffed type is not one that was asked for.
            CircuitOpenError: The provider is in its failure cooldown.
            ExternalServiceError: Every attempt failed.
        """
        started = time.perf_counter()
        cap = max_bytes or self._settings.max_artefact_bytes
        policy = policy_for_url(url, provider, extra_hosts=extra_hosts)

        await self._breaker.check(provider.value)

        robots_allowed: bool | None = None
        if policy.honours_robots and self._robots is not None:
            robots_allowed = (await self._robots.require_allowed(url)).allowed

        redirect_chain: list[str] = []
        current = url
        total_attempts = 0

        for hop in range(MAX_REDIRECTS + 1):
            response, attempts = await self._request_with_retries(current, policy, cap)
            total_attempts += attempts

            location = response.headers.get("location")
            if response.status_code in _REDIRECT_STATUSES and location:
                if hop >= MAX_REDIRECTS:
                    message = (
                        f"More than {MAX_REDIRECTS} redirects. A chain this long is either "
                        "a loop or an attempt to walk somewhere the first check refused."
                    )
                    raise SsrfBlockedError(
                        message, context={"url": url, "hops": len(redirect_chain)}
                    )
                redirect_chain.append(current)
                # Resolved against the current URL so a relative Location works, then
                # re-validated from scratch on the next pass: allowlist, SSRF, the lot.
                current = urljoin(current, location)
                policy = policy_for_url(current, provider, extra_hosts=extra_hosts)
                continue

            return await self._finish(
                url=url,
                final_url=current,
                response=response,
                policy=policy,
                redirect_chain=tuple(redirect_chain),
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                attempts=total_attempts,
                robots_allowed=robots_allowed,
                expected_media_types=expected_media_types,
            )

        # Unreachable: the loop returns or raises on every path. Present so that a future
        # edit which makes it reachable fails loudly instead of returning None.
        message = "The redirect loop ended without a response."  # pragma: no cover
        raise SsrfBlockedError(message, context={"url": url})  # pragma: no cover

    # -- Internals -----------------------------------------------------------------------

    async def _finish(
        self,
        *,
        url: str,
        final_url: str,
        response: _Response,
        policy: FetchPolicy,
        redirect_chain: tuple[str, ...],
        elapsed_ms: float,
        attempts: int,
        robots_allowed: bool | None,
        expected_media_types: frozenset[str] | None,
    ) -> FetchResult:
        # Archived before anything can reject it. A content-type mismatch is exactly the
        # case where the body is worth keeping: it is the evidence of what the server
        # actually sent, and raising first would throw it away.
        stored = await self._store.put_bytes(response.body)

        declared = response.headers.get("content-type")
        media_type = sniff_media_type(response.body, declared)
        is_success = _HTTP_OK <= response.status_code < _HTTP_REDIRECTION

        _log.info(
            "fetch.completed",
            url=url,
            final_url=final_url,
            status=response.status_code,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type=media_type,
            attempts=attempts,
            redirects=len(redirect_chain),
            elapsed_ms=elapsed_ms,
        )

        # Only checked on success. An error response is an HTML page whatever was
        # requested, and refusing it here would throw away the message explaining the
        # failure.
        if is_success and expected_media_types and media_type not in expected_media_types:
            expected = sorted(expected_media_types)
            message = (
                f"{final_url} returned {media_type} (declared {declared!r}), but "
                f"{' or '.join(expected)} was expected. The type is decided by the bytes, "
                "not the header, because a mislabelled error page parses into confident "
                "nonsense rather than failing."
            )
            raise ContentTypeMismatchError(
                message,
                context={
                    "url": final_url,
                    "sniffed": media_type,
                    "declared": declared,
                    "expected": expected,
                    "sha256": stored.sha256,
                },
            )

        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=response.status_code,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type=media_type,
            declared_media_type=declared,
            headers=dict(response.headers),
            redirect_chain=redirect_chain,
            elapsed_ms=elapsed_ms,
            attempts=attempts,
            licence_note=policy.licence_note,
            robots_allowed=robots_allowed,
        )

    async def _request_with_retries(
        self, url: str, policy: FetchPolicy, cap: int
    ) -> tuple[_Response, int]:
        """Try one URL up to :data:`MAX_ATTEMPTS` times.

        Returns the response and how many attempts it took. A retryable status on the
        final attempt is *returned* rather than raised, so its body is still archived — a
        503 page sometimes says exactly why.
        """
        last_status: int | None = None
        last_error: Exception | None = None

        for attempt_number in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire(
                policy.provider.value,
                rate=policy.requests_per_second,
                burst=policy.burst,
                sleep=self._sleep,
            )

            response: _Response | None = None
            try:
                response = await self._request_once(url, policy, cap)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error, last_status = exc, None
            else:
                if response.status_code not in _RETRYABLE_STATUSES:
                    await self._breaker.record_success(policy.provider.value)
                    return response, attempt_number
                last_error, last_status = None, response.status_code

            if attempt_number == MAX_ATTEMPTS:
                break

            delay = self._backoff_delay(attempt_number, retry_after=_retry_after(response))
            _log.info(
                "fetch.retrying",
                url=url,
                attempt=attempt_number,
                status=last_status,
                error=type(last_error).__name__ if last_error else None,
                delay_seconds=round(delay, 2),
            )
            await self._sleep(delay)

        await self._record_failure(policy)

        if response is not None:
            # Ran out of attempts on a retryable status. Handed back so the body is
            # archived and the caller sees the real status rather than a generic failure.
            return response, MAX_ATTEMPTS

        message = (
            f"{url} failed after {MAX_ATTEMPTS} attempts "
            f"({type(last_error).__name__ if last_error else 'unknown error'})."
        )
        raise ExternalServiceError(
            message,
            provider=policy.provider.value,
            retryable=True,
            status_code=last_status,
            context={"url": url, "attempts": MAX_ATTEMPTS},
        )

    async def _request_once(self, url: str, policy: FetchPolicy, cap: int) -> _Response:
        """Validate, pin and perform one request, reading the body under a cap."""
        resolved = resolve_and_validate(
            url,
            allow_insecure_http=self._settings.allow_insecure_http,
            resolver=self._resolver,
        )
        # Pinned before the connection is opened, and only ever with an address that just
        # passed validation. The transport refuses any host it has no pin for.
        self._pins[resolved.hostname] = resolved.primary

        headers = {
            "User-Agent": self._settings.http_user_agent,
            "Accept-Encoding": "gzip, deflate",
            **policy.extra_headers,
        }

        async with self._client(policy) as client:
            request = client.build_request("GET", url, headers=headers)
            # follow_redirects is off: every hop is validated by the caller's loop before
            # it is connected to. Letting httpx follow them would resolve and connect
            # before any of this ran.
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                body = await self._read_capped(response, url, cap)
            finally:
                await response.aclose()

            return _Response(status_code=response.status_code, headers=response.headers, body=body)

    async def _read_capped(self, response: httpx.Response, url: str, cap: int) -> bytes:
        """Read a body, abandoning it the moment it exceeds the cap.

        Counted as it arrives rather than read against ``Content-Length``: the header is
        set by the sender, so trusting it means the cap can be bypassed by lying about it.
        """
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > cap:
                message = (
                    f"The response exceeded the {cap:,} byte cap and was abandoned "
                    "part-way. Raise AER_MAX_ARTEFACT_BYTES only if a document this "
                    "large is genuinely expected."
                )
                raise FetchTooLargeError(
                    message, context={"url": url, "max_bytes": cap, "read_bytes": total}
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _client(self, policy: FetchPolicy) -> httpx.AsyncClient:
        transport = (
            self._transport_factory()
            if self._transport_factory is not None
            else PinnedAddressTransport(self._pins, retries=0)
        )
        return httpx.AsyncClient(
            transport=transport,
            timeout=policy.timeout_seconds,
            follow_redirects=False,
        )

    async def _record_failure(self, policy: FetchPolicy) -> None:
        await self._breaker.record_failure(
            policy.provider.value,
            threshold=policy.failure_threshold,
            cooldown_seconds=policy.cooldown_seconds,
        )

    @staticmethod
    def _backoff_delay(attempt: int, *, retry_after: float | None) -> float:
        """Exponential backoff with full jitter, unless the server named a delay.

        Full jitter rather than a fixed multiplier: several workers that failed at the
        same moment would otherwise retry at the same moment, and the provider sees a
        synchronised burst exactly when it is least able to cope.
        """
        if retry_after is not None:
            return retry_after
        ceiling = min(2.0**attempt, _MAX_BACKOFF_SECONDS)
        return random.uniform(0, ceiling)  # noqa: S311 -- jitter, not a security decision


def _retry_after(response: _Response | None) -> float | None:
    """Seconds the server asked us to wait, if it said so and the value is sane."""
    if response is None:
        return None
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        # The HTTP-date form. Rare, and falling back to ordinary backoff is a safe answer
        # whereas mis-parsing a date is not.
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)
