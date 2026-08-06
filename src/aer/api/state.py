"""Process-lifetime resources shared by every request.

In its own module rather than in ``app.py`` because the dependency functions need the
type and the application factory needs the dependency functions. Putting the dataclass
where both can import it breaks that cycle without either of them reaching for a
deferred import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aer.config import Settings

if TYPE_CHECKING:
    from aer.providers.protocol import LLMProvider
    from aer.storage.protocol import ArtefactStore

__all__ = ["AppState"]


@dataclass(slots=True)
class AppState:
    """Everything a handler might need that outlives a single request."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis

    # **The web process holds a provider for exactly one thing: a skill dry run.**
    # Everything else that spends is enqueued to the worker, and that separation is
    # deliberate — a request handler that can start a research run is a request handler
    # that can time out halfway through one. A dry run is a single bounded call whose
    # whole point is that the author waits for it.
    #
    # Built on first use rather than at start-up (see `aer.api.deps.get_provider`), so a
    # deployment with no key still serves every page that does not need one; injected
    # whole by tests, which is why it is a field rather than a lazily cached global.
    provider: LLMProvider | None = None
    store: ArtefactStore | None = None
