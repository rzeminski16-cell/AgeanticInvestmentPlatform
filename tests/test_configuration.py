"""Settings an operator may change, and the ones no form may reach.

Gap B6/B11. The risk in a settings screen is not that a field fails to save — it is that
the screen becomes a way to change something it was never meant to. So the first test here
is structural: it walks `Settings` for every secret field and asserts none of them is
overridable, which keeps holding when somebody adds a credential a year from now.

The second risk is subtler. Configuration read per step rather than per run would let a
routing change land halfway through, and the run's own record would then describe two
different platforms.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.config import HouseStyle, ModelRoute, Settings
from aer.core.enums import UserRole
from aer.db.models import User
from aer.errors import ValidationError
from aer.services import configuration
from aer.services.configuration import (
    OVERRIDABLE,
    current_overrides,
    effective_settings,
    save_override,
    secret_presence,
)
from aer.web.csrf import CSRF_FIELD_NAME
from tests.api_fixtures import build_app, client_for


@pytest.fixture
async def committed_user(db_engine: Any) -> Any:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="settings@example.invalid", display_name="S", role=UserRole.OWNER)
        session.add(user)
        await session.commit()
        yield user
        await session.delete(user)
        await session.commit()


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, committed_user: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _settings() -> Settings:
    return Settings(http_user_agent="Tracework Test test@example.invalid")


async def _actor(session: AsyncSession) -> User:
    user = User(email="ops@example.invalid", display_name="Ops", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    return user


class TestNoSecretIsEverEditable:
    """The property worth more than every other test in this file."""

    def test_no_secret_field_appears_in_the_allowlist(self) -> None:
        """Walks the model rather than naming today's four keys.

        A hard-coded list would pass forever while a credential added next year quietly
        became editable from a web form — and from there into `pg_dump` output and every
        backup. This asserts the shape, so the guard survives the schema growing.
        """
        overridable = {item.key for item in OVERRIDABLE}

        secrets = {
            name
            for name, field in Settings.model_fields.items()
            if configuration._is_secret(field.annotation)
        }

        assert secrets, "the model has no secret fields; this test is no longer meaningful"
        assert not (secrets & overridable)

    def test_both_ways_of_declaring_a_secret_are_recognised(self) -> None:
        """The walk above cannot exercise this, and that is the point.

        `Settings` declares every credential as `SecretStr | None` today, so only the
        parametrised branch of `_is_secret` ever runs — the bare-annotation branch is dead
        code that nothing would notice breaking. A required credential added later as a
        plain `SecretStr` would then slip past both this allowlist and `secret_presence`
        with no test failing. Checked directly, against the schema as it will be rather
        than as it happens to be.
        """
        assert configuration._is_secret(SecretStr)
        assert configuration._is_secret(SecretStr | None)
        assert not configuration._is_secret(str)
        assert not configuration._is_secret(Decimal | None)

    async def test_a_post_naming_a_secret_is_refused(self, db_session: AsyncSession) -> None:
        """The allowlist is enforced at the write, not only in the template that renders it."""
        actor = await _actor(db_session)

        with pytest.raises(ValidationError, match="not a setting that may be changed"):
            await save_override(db_session, key="anthropic_api_key", raw="sk-ant-nope", actor=actor)

    def test_presence_is_reported_without_the_value(self) -> None:
        settings = _settings().model_copy(
            update={"anthropic_api_key": SecretStr("sk-ant-secret-value")}
        )

        presence = secret_presence(settings)

        assert presence["anthropic_api_key"] is True
        assert "sk-ant-secret-value" not in json.dumps(presence)

    async def test_the_page_never_renders_a_key(self, api: Any) -> None:
        page = await api.get("/settings")

        assert page.status_code == 200
        assert "sk-ant" not in page.text


@pytest.mark.integration
class TestWhatOverridingDoes:
    async def test_nothing_stored_returns_the_base_object_untouched(
        self, db_session: AsyncSession
    ) -> None:
        base = _settings()

        assert await effective_settings(db_session, base) is base

    async def test_a_budget_override_takes_effect(self, db_session: AsyncSession) -> None:
        actor = await _actor(db_session)
        await save_override(db_session, key="per_run_budget_gbp", raw="7.50", actor=actor)

        effective = await effective_settings(db_session, _settings())

        assert effective.per_run_budget_gbp == Decimal("7.50")

    async def test_a_routing_override_reaches_the_router(self, db_session: AsyncSession) -> None:
        """The largest lever on cost, and the reason this screen exists."""
        actor = await _actor(db_session)
        table = {"planner": {"model": "claude-haiku-4-5", "effort": "low"}}
        await save_override(db_session, key="model_routes", raw=json.dumps(table), actor=actor)

        effective = await effective_settings(db_session, _settings())

        assert effective.model_routes["planner"] == ModelRoute(
            model="claude-haiku-4-5", effort="low"
        )

    async def test_saving_twice_replaces_rather_than_duplicates(
        self, db_session: AsyncSession
    ) -> None:
        actor = await _actor(db_session)
        await save_override(db_session, key="monthly_budget_gbp", raw="40", actor=actor)
        await save_override(db_session, key="monthly_budget_gbp", raw="55", actor=actor)

        stored = await current_overrides(db_session)

        assert stored["monthly_budget_gbp"] == "55"

    async def test_a_change_is_recorded_in_the_audit_trail(self, db_session: AsyncSession) -> None:
        """Correlating a routing change against a month's spend needs the change on record."""
        from sqlalchemy import select  # noqa: PLC0415

        from aer.db.models import AuditEvent  # noqa: PLC0415

        actor = await _actor(db_session)
        await save_override(db_session, key="per_run_budget_gbp", raw="3.00", actor=actor)

        latest = await db_session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()))
        assert latest is not None
        assert latest.event_type == "settings.changed"
        assert latest.payload["key"] == "per_run_budget_gbp"

    async def test_a_value_that_does_not_parse_is_refused_not_clamped(
        self, db_session: AsyncSession
    ) -> None:
        """A budget silently corrected is a budget the operator does not know they have."""
        actor = await _actor(db_session)

        with pytest.raises(ValidationError):
            await save_override(db_session, key="per_run_budget_gbp", raw="-5", actor=actor)

    async def test_a_malformed_routing_table_is_refused(self, db_session: AsyncSession) -> None:
        actor = await _actor(db_session)

        with pytest.raises(ValidationError, match="not valid JSON"):
            await save_override(db_session, key="model_routes", raw="{not json", actor=actor)

    async def test_a_house_style_override_takes_effect(self, db_session: AsyncSession) -> None:
        """A partial object restyles only what it names; the rest keeps its default."""
        actor = await _actor(db_session)
        await save_override(
            db_session,
            key="house_style",
            raw=json.dumps({"prose_money": "millions"}),
            actor=actor,
        )

        effective = await effective_settings(db_session, _settings())

        assert effective.house_style.prose_money == "millions"
        assert effective.house_style.voice == HouseStyle().voice

    async def test_an_unknown_voice_is_refused_with_the_field_named(
        self, db_session: AsyncSession
    ) -> None:
        actor = await _actor(db_session)

        with pytest.raises(ValidationError, match="voice"):
            await save_override(
                db_session,
                key="house_style",
                raw=json.dumps({"voice": "royal_we"}),
                actor=actor,
            )

    async def test_the_stored_style_round_trips_through_jsonb(
        self, db_session: AsyncSession
    ) -> None:
        """The Decimal threshold must survive storage exactly, not as a float's idea of it."""
        actor = await _actor(db_session)
        await save_override(
            db_session,
            key="house_style",
            raw=json.dumps({"billions_from": "2500000000"}),
            actor=actor,
        )

        stored = await current_overrides(db_session)
        effective = await effective_settings(db_session, _settings())

        assert stored["house_style"]["billions_from"] == "2500000000"
        assert effective.house_style.billions_from == Decimal("2500000000")

    async def test_a_stored_value_gone_bad_is_ignored_rather_than_fatal(
        self, db_session: AsyncSession
    ) -> None:
        """A platform that will not start because of a settings row is the worse failure.

        This is reachable without anybody editing the database by hand: a value valid under
        one release can stop validating under the next.
        """
        from aer.db.models import SettingsOverride  # noqa: PLC0415

        db_session.add(
            SettingsOverride(
                key="budget_warn_ratio", value="not a number", updated_by="ops@example.invalid"
            )
        )
        await db_session.flush()

        effective = await effective_settings(db_session, _settings())

        assert effective.budget_warn_ratio == _settings().budget_warn_ratio


@pytest.mark.integration
class TestThePage:
    async def test_it_renders_the_current_values(self, api: Any) -> None:
        page = await api.get("/settings")

        assert page.status_code == 200
        assert 'id="form-model_routes"' in page.text
        assert "claude-opus-5" in page.text

    async def test_a_post_without_a_token_changes_nothing(self, api: Any) -> None:
        refused = await api.post("/settings", data={"key": "per_run_budget_gbp", "value": "9"})

        assert refused.status_code == 403

    async def test_a_refused_value_rerenders_with_the_reason(self, api: Any) -> None:
        page = await api.get("/settings")
        token = _hidden(page.text)

        rejected = await api.post(
            "/settings",
            data={CSRF_FIELD_NAME: token, "key": "per_run_budget_gbp", "value": "-1"},
        )

        assert rejected.status_code == 400
        assert 'id="error"' in rejected.text


def _hidden(html: str) -> str:
    marker = f'name="{CSRF_FIELD_NAME}" value="'
    return html.split(marker, 1)[1].split('"', 1)[0]
