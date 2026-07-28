"""robots.txt compliance.

The operator's stated constraint is that this platform must not breach a site's terms of
use. ``robots.txt`` is the machine-readable form of exactly that, so **a disallow is a
refusal, not a warning**. A warning that is logged and then ignored is a breach with a
paper trail, which is worse than no check at all — it proves the breach was deliberate.

Parsing is delegated to :mod:`urllib.robotparser`. Writing another robots parser would be
a fresh source of bugs in a component whose whole job is to be conservative, and the
stdlib one already handles the awkward parts: wildcards, ``$`` anchors, longest-match
precedence between ``Allow`` and ``Disallow``, and per-user-agent sections.

**Fetch failures do not mean permission.** A robots.txt that returns 500 leaves us unable
to tell whether the path is permitted, and "we could not check" is not "we may proceed".
The one exception is 404: a site with no robots.txt has expressed no restriction, which is
the standard reading and the one the stdlib parser assumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import structlog
from redis.asyncio import Redis

from aer.fetch.errors import RobotsDisallowedError

__all__ = ["RobotsCache", "RobotsDecision", "robots_url_for"]

_log = structlog.get_logger("aer.fetch.robots")

_KEY_PREFIX: Final = "aer:fetch:robots"

# Long enough that a run does not re-fetch it for every document, short enough that a
# publisher changing their mind is respected the same day.
DEFAULT_TTL_SECONDS: Final = 6 * 60 * 60

# A robots.txt larger than this is not a robots.txt. Google stops at 500 KiB; this is
# more generous than any real file and still bounded.
MAX_ROBOTS_BYTES: Final = 512 * 1024

_MISSING = "__missing__"
"""Cache marker for "this site has no robots.txt", which is different from "not cached"."""


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """Whether a path may be fetched, and how that was decided."""

    allowed: bool
    reason: str
    robots_url: str
    from_cache: bool = False


class RobotsFetcher(Protocol):
    """Fetches a robots.txt. Returns its text, or ``None`` if the site has none.

    A Protocol so that the caller supplies the fetch. This module must not open its own
    connection — every outbound request goes through the same SSRF, allowlist and rate
    limiting controls, and a robots fetch that quietly bypassed them would be a hole in
    the exact component meant to close them.
    """

    async def __call__(self, robots_url: str) -> str | None: ...


def robots_url_for(url: str) -> str:
    """The robots.txt URL governing ``url``.

    Per-origin: scheme, host and port. ``https://example.test/a/b`` and
    ``https://example.test/c`` share one robots.txt; ``http://`` and a different port do
    not.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


class RobotsCache:
    """Fetches, caches and consults robots.txt for each origin."""

    def __init__(
        self,
        redis: Redis,
        fetcher: RobotsFetcher,
        *,
        user_agent: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._fetch = fetcher
        self._user_agent = user_agent
        self._ttl = ttl_seconds

    async def decide(self, url: str) -> RobotsDecision:
        """Whether ``url`` may be fetched, according to its origin's robots.txt."""
        robots_url = robots_url_for(url)
        key = f"{_KEY_PREFIX}:{robots_url}"

        cached = await self._redis.get(key)
        from_cache = cached is not None

        if cached is None:
            body = await self._fetch(robots_url)
            cached = _MISSING if body is None else body
            await self._redis.set(key, cached, ex=self._ttl)
        elif isinstance(cached, bytes):  # pragma: no cover -- depends on decode_responses
            cached = cached.decode("utf-8", errors="replace")

        if cached == _MISSING:
            return RobotsDecision(
                allowed=True,
                reason="the site publishes no robots.txt, which expresses no restriction",
                robots_url=robots_url,
                from_cache=from_cache,
            )

        parser = RobotFileParser()
        parser.parse(cached.splitlines())

        # The configured User-Agent, not "*". A site may permit a named crawler and
        # forbid everything else, or the reverse; checking under a different identity
        # than the one we send would answer a question nobody asked.
        allowed = parser.can_fetch(self._user_agent, url)
        return RobotsDecision(
            allowed=allowed,
            reason=(
                "permitted by robots.txt"
                if allowed
                else f"robots.txt disallows this path for {self._user_agent!r}"
            ),
            robots_url=robots_url,
            from_cache=from_cache,
        )

    async def require_allowed(self, url: str) -> RobotsDecision:
        """Consult robots.txt and raise unless the path is permitted.

        Raises:
            RobotsDisallowedError: If the publisher forbids this path.
        """
        decision = await self.decide(url)
        if not decision.allowed:
            _log.warning("fetch.robots_disallowed", url=url, robots_url=decision.robots_url)
            message = (
                f"robots.txt at {decision.robots_url} disallows this path for our user "
                "agent. This platform does not fetch what a publisher has asked it not "
                "to, so the source is refused rather than retrieved and flagged."
            )
            raise RobotsDisallowedError(
                message,
                context={
                    "url": url,
                    "robots_url": decision.robots_url,
                    "user_agent": self._user_agent,
                },
            )
        return decision

    async def invalidate(self, url: str) -> None:
        """Drop the cached robots.txt for a URL's origin."""
        await self._redis.delete(f"{_KEY_PREFIX}:{robots_url_for(url)}")
