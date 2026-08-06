"""FastAPI dependencies.

Handlers ask for what they need; nothing reaches into module globals for a session or a
settings object. That is what keeps a handler testable in isolation and what makes the
resource lifecycle visible in the signature rather than buried in an import.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from aer.api.state import AppState
from aer.config import Settings
from aer.db.models import User
from aer.errors import ConfigError
from aer.providers.protocol import LLMProvider
from aer.providers.router import Router
from aer.runtime import build_provider
from aer.storage.local import LocalArtefactStore
from aer.storage.protocol import ArtefactStore

__all__ = [
    "CurrentUser",
    "DbSession",
    "ProviderDep",
    "RedisClient",
    "RouterDep",
    "SettingsDep",
    "StoreDep",
    "get_app_state",
    "get_current_user",
    "get_db_session",
    "get_redis",
    "get_settings_dep",
]


def get_app_state(request: Request) -> AppState:
    state: AppState = request.app.state.aer
    return state


StateDep = Annotated[AppState, Depends(get_app_state)]


def get_settings_dep(state: StateDep) -> Settings:
    return state.settings


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


async def get_db_session(state: StateDep) -> AsyncIterator[AsyncSession]:
    """Yield a session for the duration of the request.

    Deliberately does not commit. A handler that raises halfway through must not leave a
    partial write behind, and the only way to guarantee that is for the handler to say
    explicitly when its unit of work is complete.
    """
    async with state.session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_redis(state: StateDep) -> Redis:
    return state.redis


RedisClient = Annotated[Redis, Depends(get_redis)]


async def get_current_user(session: DbSession) -> User:
    """The single local user.

    The MVP has no authentication and exactly one user. This dependency exists anyway so
    that every handler is already written against "the current user" rather than against
    an implicit singleton — when authentication arrives it replaces the body of this
    function and nothing else.
    """
    result = await session.execute(select(User).order_by(User.created_at).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        message = "No user exists. Create one with: uv run aer seed-user --email you@example.com"
        raise ConfigError(message, context={"remedy": "aer seed-user"})
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_provider(state: StateDep) -> LLMProvider:
    """The model provider, built on first use and kept for the process's life.

    Lazily rather than at start-up: only the skill dry run spends from the web process,
    and a deployment with no key should still serve every page that does not. The
    provider itself is what refuses a missing key, loudly, at the point somebody asks for
    something that needs one.
    """
    if state.provider is None:
        state.provider = build_provider(state.settings)
    return state.provider


ProviderDep = Annotated[LLMProvider, Depends(get_provider)]


def get_router(settings: SettingsDep) -> Router:
    """Model routing for this request. Cheap to build and configured wholly by settings."""
    return Router(settings)


RouterDep = Annotated[Router, Depends(get_router)]


def get_store(state: StateDep) -> ArtefactStore:
    """The artefact store, on the same terms as the provider."""
    if state.store is None:
        state.store = LocalArtefactStore(
            state.settings.artefact_root, max_bytes=state.settings.max_artefact_bytes
        )
    return state.store


StoreDep = Annotated[ArtefactStore, Depends(get_store)]
