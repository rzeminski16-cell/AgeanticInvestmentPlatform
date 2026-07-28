"""Assembling the application's services, in one place.

The worker and the web process need the same bundle — a provider, an artefact store, a
fetcher, a SEC client — built from the same settings. Two constructions would drift, and
the way they would drift is that one of them would quietly use a different model or a
different artefact root.

**Nothing here decides policy.** It reads settings and constructs objects. The decision
that an unset API key is fatal belongs to the provider; the decision that a run costs at
most £2.50 belongs to the request.

**The provider is not silently substituted.** In production it is the Anthropic provider,
which refuses to construct without a key. In tests it is
:class:`~aer.providers.fake.FakeProvider`, *injected* — a factory that quietly swapped in a
fake when a key was missing would let a deployment run happily and produce nothing real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import structlog
from redis.asyncio import Redis

from aer.config import Settings
from aer.core.enums import Provider
from aer.errors import AerError
from aer.fetch.client import SafeFetcher
from aer.fetch.limits import CircuitBreaker, RateLimiter
from aer.fetch.robots import RobotsCache, RobotsFetcher
from aer.providers.protocol import LLMProvider
from aer.providers.router import Router
from aer.sources.sec.client import SecEdgarClient
from aer.storage.local import LocalArtefactStore
from aer.storage.protocol import ArtefactStore

__all__ = ["ServiceBundle", "build_provider", "build_services"]

_log = structlog.get_logger("aer.runtime")


@dataclass(slots=True)
class ServiceBundle:
    """Everything a workflow step might need."""

    settings: Settings
    provider: LLMProvider
    router: Router
    store: ArtefactStore
    sec_client: SecEdgarClient
    fetcher: SafeFetcher

    def as_mapping(self) -> dict[str, Any]:
        """The form the workflow engine passes to steps."""
        return {
            "settings": self.settings,
            "provider": self.provider,
            "router": self.router,
            "store": self.store,
            "sec_client": self.sec_client,
            "fetcher": self.fetcher,
        }


def build_provider(settings: Settings) -> LLMProvider:
    """The configured model provider.

    Raises:
        ExternalServiceError: If no API key is set. Deliberately fatal rather than falling
            back to a fake: a deployment that ran happily and produced nothing real would
            be far worse than one that refused to start.
    """
    from aer.providers.anthropic import AnthropicProvider  # noqa: PLC0415

    return AnthropicProvider(api_key=settings.require_secret("anthropic_api_key"))


def build_services(
    settings: Settings,
    *,
    redis: Redis,
    provider: LLMProvider | None = None,
    store: ArtefactStore | None = None,
) -> ServiceBundle:
    """Construct the service bundle.

    Args:
        provider: Injected by tests. ``None`` builds the configured one, which requires a
            key.
        store: Injected by tests wanting a temporary directory.
    """
    artefact_store = store or LocalArtefactStore(
        settings.artefact_root, max_bytes=settings.max_artefact_bytes
    )

    # One limiter and one breaker, shared between both fetchers below. Retrieving
    # robots.txt is a request against the same publisher and must count against the same
    # rate as everything else.
    limiter = RateLimiter(redis)
    breaker = CircuitBreaker(redis)

    # Two fetchers, and the order is forced. A robots cache needs something to retrieve
    # robots.txt with, and that something cannot be a fetcher which consults the robots
    # cache: you do not check robots.txt before fetching robots.txt.
    bare = SafeFetcher(
        settings, store=artefact_store, limiter=limiter, breaker=breaker, robots=None
    )
    fetcher = SafeFetcher(
        settings,
        store=artefact_store,
        limiter=limiter,
        breaker=breaker,
        robots=RobotsCache(
            redis,
            _robots_fetcher(bare, artefact_store),
            user_agent=settings.http_user_agent,
        ),
    )

    return ServiceBundle(
        settings=settings,
        provider=provider or build_provider(settings),
        router=Router(settings),
        store=artefact_store,
        sec_client=SecEdgarClient(fetcher, store=artefact_store),
        fetcher=fetcher,
    )


def _robots_fetcher(fetcher: SafeFetcher, store: ArtefactStore) -> RobotsFetcher:
    """A callable that retrieves one robots.txt, for the robots cache to use.

    Goes through the ordinary pipeline, so it is rate-limited, size-capped and archived
    like anything else — but through the fetcher with **no** robots cache attached,
    because consulting the cache in order to populate the cache does not terminate.

    A robots.txt that cannot be retrieved returns ``None``, which the cache reads as "no
    robots.txt" — the same thing a 404 means. Treating a brief outage as a blanket refusal
    would be a stricter reading than the standard supports, and would make an unreachable
    file look like a publisher's policy.
    """

    async def retrieve(robots_url: str) -> str | None:
        try:
            result = await fetcher.fetch(
                robots_url,
                provider=Provider.WEB_SEARCH,
                # The origin's own robots.txt, admitted for this request only. The standing
                # allowlist is not widened: a robots file is fetched from exactly the host
                # whose rules it states.
                extra_hosts=(_host_of(robots_url),),
            )
        except AerError as exc:
            _log.info("robots.unavailable", url=robots_url, error=exc.code)
            return None

        if not result.ok:
            return None

        # Read back from the store, for the same reason the SEC client does: the artefact
        # is the authoritative copy of what arrived.
        return (await store.read(result.sha256)).decode("utf-8", "replace")

    return retrieve


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
