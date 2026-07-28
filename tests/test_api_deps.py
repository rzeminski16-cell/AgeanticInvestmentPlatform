"""Request dependencies.

``get_current_user`` has no caller yet — the request routes arrive in the next task — so
it is exercised here directly. A dependency written now and first run in three weeks'
time is a dependency that fails in three weeks' time, in someone else's feature.
"""

from __future__ import annotations

import pytest

from aer.api.deps import get_current_user
from aer.core.enums import UserRole
from aer.db.models import User
from aer.errors import ConfigError

pytestmark = pytest.mark.integration


async def _add_user(session, email: str) -> User:
    user = User(email=email, display_name=email.split("@", 1)[0], role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    return user


class TestCurrentUser:
    async def test_returns_the_single_user(self, db_session):
        created = await _add_user(db_session, "only@example.invalid")

        assert (await get_current_user(db_session)).id == created.id

    async def test_no_user_raises_an_error_that_names_the_remedy(self, db_session):
        # The MVP has no sign-up flow, so "no user" is a setup mistake rather than an
        # authentication failure. The error has to say how to fix it or the only way to
        # find out is to read the source.
        with pytest.raises(ConfigError) as excinfo:
            await get_current_user(db_session)

        assert "seed-user" in str(excinfo.value)
        assert excinfo.value.context["remedy"] == "aer seed-user"

    async def test_the_oldest_user_wins_when_several_exist(self, db_session):
        # Deterministic rather than arbitrary. Without an ORDER BY, which account "the
        # current user" resolves to would depend on physical row order, and ownership
        # checks would silently change answer after a VACUUM.
        first = await _add_user(db_session, "first@example.invalid")
        await _add_user(db_session, "second@example.invalid")

        assert (await get_current_user(db_session)).id == first.id
