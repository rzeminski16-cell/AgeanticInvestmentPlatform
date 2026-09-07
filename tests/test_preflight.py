"""`aer preflight`: everything a paid run depends on, checked in one readout at no cost.

The runbook's stage 1 is seven checks in seven commands, and the confirmation run still
lost a night to the one that is easiest to skip. These tests hold the readout to what each
check means for the run: a failure the run cannot survive is a FAIL, one it survives is a
WARN, a check an earlier failure made impossible is a SKIP that names it, and none of them
can stop the others from being reported.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.cli import _print_preflight
from aer.config import Settings, load_settings
from aer.core.enums import UserRole
from aer.db.models import User
from aer.queue import HEALTH_CHECK_INTERVAL_SECONDS, HEALTH_CHECK_KEY
from aer.services import preflight as preflight_module
from aer.services.preflight import Check, CheckStatus, Preflight, run_preflight
from tests.db_cleanup import delete_all

pytestmark = pytest.mark.anyio

_RECORD = "Sep-06 10:41:03 j_complete=3 j_failed=0 j_retried=0 j_ongoing=0 queued=0"
_LIFETIME_MS = (HEALTH_CHECK_INTERVAL_SECONDS + 1) * 1000
_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings(settings_env: pytest.MonkeyPatch, tmp_path: Any) -> Settings:
    """A configured platform: a model key, no price feed, the default caps."""
    settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
    settings_env.setenv("AER_ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    settings_env.delenv("AER_EODHD_API_KEY", raising=False)
    return load_settings()


@pytest.fixture
async def factory(db_engine: Any) -> Any:
    """A session factory over the migrated test database, emptied first so the user
    check answers for what this test wrote and not for an earlier module's rows."""
    await delete_all(db_engine)
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


async def _with_a_user(factory: Any) -> None:
    async with factory() as session:
        session.add(User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER))
        await session.commit()


def _check(readout: Preflight, name: str) -> Check:
    return next(check for check in readout.checks if check.name == name)


async def _preflight(settings: Settings, factory: Any, redis: Any) -> Preflight:
    return await run_preflight(settings, session_factory=factory, redis=redis, now=_NOW)


# -- The whole readout -------------------------------------------------------------------------


class TestAReadyPlatform:
    async def test_everything_answers_and_it_is_ready(
        self, settings: Settings, factory: Any, fake_redis: Any
    ) -> None:
        await _with_a_user(factory)
        await fake_redis.psetex(HEALTH_CHECK_KEY, _LIFETIME_MS - 2_000, _RECORD)

        readout = await _preflight(settings, factory, fake_redis)

        assert readout.ok
        assert [c.name for c in readout.checks] == [
            "provider_key",
            "database",
            "schema",
            "user",
            "run_cap",
            "monthly_room",
            "redis",
            "worker",
            "price_feed",
            "wire_contract",
        ]
        for name in ("provider_key", "database", "schema", "user", "redis", "worker"):
            assert _check(readout, name).status is CheckStatus.PASS, name
        assert _check(readout, "worker").detail.startswith("A worker is alive and idle")
        assert _check(readout, "user").detail.startswith("owner@example.invalid")

    async def test_the_wire_contract_is_named_not_performed(
        self, settings: Settings, factory: Any, fake_redis: Any
    ) -> None:
        """Preflight spends nothing; the one check that costs money is pointed at."""
        readout = await _preflight(settings, factory, fake_redis)

        contract = _check(readout, "wire_contract")
        assert contract.status is CheckStatus.SKIP
        assert "just test-live" in contract.detail


class TestWhatTheRunCannotSurvive:
    async def test_no_worker_fails_and_says_what_to_start(
        self, settings: Settings, factory: Any, fake_redis: Any
    ) -> None:
        await _with_a_user(factory)

        readout = await _preflight(settings, factory, fake_redis)

        worker = _check(readout, "worker")
        assert worker.status is CheckStatus.FAIL
        assert "just worker" in worker.detail
        assert not readout.ok

    async def test_no_user_fails_and_says_how_to_make_one(
        self, settings: Settings, factory: Any, fake_redis: Any
    ) -> None:
        readout = await _preflight(settings, factory, fake_redis)

        user = _check(readout, "user")
        assert user.status is CheckStatus.FAIL
        assert "seed-user" in user.detail

    async def test_no_model_key_fails_before_anything_is_spent(
        self, settings_env: pytest.MonkeyPatch, factory: Any, fake_redis: Any, tmp_path: Any
    ) -> None:
        settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
        settings_env.delenv("AER_ANTHROPIC_API_KEY", raising=False)

        readout = await _preflight(load_settings(), factory, fake_redis)

        key = _check(readout, "provider_key")
        assert key.status is CheckStatus.FAIL
        assert "first model call" in key.detail
        assert "sk-" not in key.detail

    async def test_a_database_that_does_not_answer_skips_what_needs_it(
        self, settings: Settings, broken_engine: Any, fake_redis: Any
    ) -> None:
        """One cause, one failure: the schema, the user and the caps say they were not
        checked rather than each failing on the same refused connection."""
        factory = async_sessionmaker(bind=broken_engine, expire_on_commit=False)
        await fake_redis.psetex(HEALTH_CHECK_KEY, _LIFETIME_MS - 2_000, _RECORD)

        readout = await _preflight(settings, factory, fake_redis)

        assert _check(readout, "database").status is CheckStatus.FAIL
        for name in ("schema", "user", "run_cap", "monthly_room"):
            skipped = _check(readout, name)
            assert skipped.status is CheckStatus.SKIP, name
            assert "database" in skipped.detail
        # The other half is still read: Redis and the worker are answered for.
        assert _check(readout, "redis").status is CheckStatus.PASS
        assert _check(readout, "worker").status is CheckStatus.PASS

    async def test_a_redis_that_does_not_answer_skips_the_worker(
        self, settings: Settings, factory: Any, broken_redis: Any
    ) -> None:
        readout = await _preflight(settings, factory, broken_redis)

        assert _check(readout, "redis").status is CheckStatus.FAIL
        worker = _check(readout, "worker")
        assert worker.status is CheckStatus.SKIP
        assert "Redis" in worker.detail

    async def test_a_failure_is_redacted_and_bounded(
        self, settings: Settings, broken_engine: Any, fake_redis: Any
    ) -> None:
        factory = async_sessionmaker(bind=broken_engine, expire_on_commit=False)

        readout = await _preflight(settings, factory, fake_redis)

        detail = _check(readout, "database").detail
        assert len(detail) < 400
        assert "aer_local_dev" not in detail


class TestWhatTheRunSurvivesButShouldKnow:
    async def test_no_price_feed_is_a_warning_that_names_the_consequence(
        self, settings: Settings, factory: Any, fake_redis: Any
    ) -> None:
        readout = await _preflight(settings, factory, fake_redis)

        feed = _check(readout, "price_feed")
        assert feed.status is CheckStatus.WARN
        assert "comparables" in feed.detail

    async def test_a_price_feed_is_reported_with_the_workers_caveat(
        self, settings_env: pytest.MonkeyPatch, factory: Any, fake_redis: Any, tmp_path: Any
    ) -> None:
        """This process seeing the key says nothing about the worker, which read .env
        when it started; the row says so rather than pretending to know."""
        settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
        settings_env.setenv("AER_ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
        settings_env.setenv("AER_EODHD_API_KEY", "not-a-real-eodhd-key")

        readout = await _preflight(load_settings(), factory, fake_redis)

        feed = _check(readout, "price_feed")
        assert feed.status is CheckStatus.PASS
        assert "worker" in feed.detail
        assert "not-a-real-eodhd-key" not in feed.detail

    async def test_a_busy_worker_is_a_warning_not_a_failure(
        self, settings: Settings, factory: Any, fake_redis: Any
    ) -> None:
        await fake_redis.psetex(
            HEALTH_CHECK_KEY, _LIFETIME_MS - 2_000, _RECORD.replace("j_ongoing=0", "j_ongoing=1")
        )

        readout = await _preflight(settings, factory, fake_redis)

        worker = _check(readout, "worker")
        assert worker.status is CheckStatus.WARN
        assert "waits its turn" in worker.detail


class TestTheCapsAgainstTheRecord:
    @pytest.fixture
    def last_run_cost(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        """What the last run that spent cost, without driving a run to find out."""

        def costing(amount: str | None) -> None:
            async def recent(session: Any, *, limit: int = 20) -> list[tuple[Any, Decimal]]:
                if amount is None:
                    return []
                job = type("Job", (), {"id": "9d0d4e2e-0000-4000-8000-000000000001"})()
                return [(job, Decimal(amount))]

            monkeypatch.setattr(preflight_module, "recent_runs", recent)

        return costing

    @pytest.fixture
    def month_spent(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        def spending(amount: str) -> None:
            async def this_month(session: Any, *, now: datetime) -> Decimal:
                return Decimal(amount)

            monkeypatch.setattr(preflight_module, "spend_this_month", this_month)

        return spending

    async def test_a_cap_with_room_for_retries_passes(
        self, settings: Settings, factory: Any, fake_redis: Any, last_run_cost: Any
    ) -> None:
        last_run_cost("8.00")

        cap = _check(await _preflight(settings, factory, fake_redis), "run_cap")

        assert cap.status is CheckStatus.PASS
        assert "£12.00" in cap.detail
        assert "£8.00" in cap.detail

    async def test_a_cap_within_a_retrys_worth_of_the_last_run_warns(
        self, settings: Settings, factory: Any, fake_redis: Any, last_run_cost: Any
    ) -> None:
        """The confirmation run: £10.82 against £12.00. Every refused section costs
        another attempt, and a run with a few more pauses at the cap for a decision."""
        last_run_cost("10.82")

        cap = _check(await _preflight(settings, factory, fake_redis), "run_cap")

        assert cap.status is CheckStatus.WARN
        assert "£10.82" in cap.detail
        assert "AER_PER_RUN_BUDGET_GBP" in cap.detail

    async def test_a_cap_below_the_last_run_warns_that_the_run_pauses(
        self, settings: Settings, factory: Any, fake_redis: Any, last_run_cost: Any
    ) -> None:
        last_run_cost("13.50")

        cap = _check(await _preflight(settings, factory, fake_redis), "run_cap")

        assert cap.status is CheckStatus.WARN
        assert "more than the ceiling" in cap.detail

    async def test_no_earlier_run_is_nothing_to_compare_with(
        self, settings: Settings, factory: Any, fake_redis: Any, last_run_cost: Any
    ) -> None:
        last_run_cost(None)

        cap = _check(await _preflight(settings, factory, fake_redis), "run_cap")

        assert cap.status is CheckStatus.PASS
        assert "No earlier run" in cap.detail

    async def test_a_month_with_room_passes_with_the_figures(
        self, settings: Settings, factory: Any, fake_redis: Any, month_spent: Any
    ) -> None:
        month_spent("21.50")

        room = _check(await _preflight(settings, factory, fake_redis), "monthly_room")

        assert room.status is CheckStatus.PASS
        assert "£21.50 of the month's £80.00" in room.detail
        assert "£58.50 remains" in room.detail

    async def test_a_month_with_less_room_than_a_run_warns(
        self, settings: Settings, factory: Any, fake_redis: Any, month_spent: Any
    ) -> None:
        month_spent("70.00")

        room = _check(await _preflight(settings, factory, fake_redis), "monthly_room")

        assert room.status is CheckStatus.WARN
        assert "less than one run's ceiling" in room.detail

    async def test_a_spent_month_fails(
        self, settings: Settings, factory: Any, fake_redis: Any, month_spent: Any
    ) -> None:
        month_spent("80.00")

        room = _check(await _preflight(settings, factory, fake_redis), "monthly_room")

        assert room.status is CheckStatus.FAIL
        assert "refused" in room.detail


# -- The readout -------------------------------------------------------------------------------


def test_the_readout_says_each_verdict_and_whether_to_go(
    capsys: pytest.CaptureFixture[str],
) -> None:
    readout = Preflight(
        at=_NOW,
        checks=(
            Check("provider_key", CheckStatus.PASS, "AER_ANTHROPIC_API_KEY is set."),
            Check("worker", CheckStatus.FAIL, "No worker has reported in the last 31 s."),
            Check("price_feed", CheckStatus.WARN, "AER_EODHD_API_KEY is not set."),
            Check("wire_contract", CheckStatus.SKIP, "not checked here."),
        ),
    )

    _print_preflight(readout)
    out = capsys.readouterr().out

    assert "Preflight at 2026-09-06 12:00Z" in out
    assert "[PASS] provider_key — AER_ANTHROPIC_API_KEY is set." in out
    assert "[FAIL] worker — No worker has reported in the last 31 s." in out
    assert "[WARN] price_feed" in out
    assert "[SKIP] wire_contract" in out
    assert "Not ready: 1 check(s) failed." in out


def test_the_readout_of_a_ready_platform_counts_its_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    readout = Preflight(
        at=_NOW,
        checks=(
            Check("database", CheckStatus.PASS, "PostgreSQL answers."),
            Check("price_feed", CheckStatus.WARN, "AER_EODHD_API_KEY is not set."),
        ),
    )

    _print_preflight(readout)
    out = capsys.readouterr().out

    assert "Ready to run, with 1 warning(s) to weigh." in out
