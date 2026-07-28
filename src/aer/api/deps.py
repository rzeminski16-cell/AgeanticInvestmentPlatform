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

__all__ = [
    "CurrentUser",
    "DbSession",
    "RedisClient",
    "SettingsDep",
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
