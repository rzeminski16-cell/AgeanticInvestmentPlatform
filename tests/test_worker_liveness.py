"""Whether anyone is listening to the queue, said wherever a run says "queued".

The confirmation run of 2026-09-05 sat queued overnight because no worker was running, and
the console promised it would begin within a few seconds the whole time. The worker now
records its health to Redis every thirty seconds, and the record's presence is what the
console, `aer diagnose` and `just worker-check` read. These tests hold each surface to the
same record: no record, no worker, and the surface says so.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.cli import worker_words
from aer.config import Settings
from aer.core.enums import JobStatus, UserRole
from aer.db.models import User
from aer.queue import (
    HEALTH_CHECK_INTERVAL_SECONDS,
    HEALTH_CHECK_KEY,
    WorkerHealth,
    worker_health,
)
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.request_fixtures import research_request
from tests.run_fixtures import start_run
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.anyio

# What arq's worker writes, verbatim (`Worker.record_health`).
_RECORD = "Sep-06 10:41:03 j_complete=3 j_failed=1 j_retried=0 j_ongoing=1 queued=2"
_LIFETIME_MS = (HEALTH_CHECK_INTERVAL_SECONDS + 1) * 1000


# -- The record ------------------------------------------------------------------------------


class TestTheHealthRecord:
    async def test_no_record_means_no_worker(self, fake_redis: Any) -> None:
        assert await worker_health(fake_redis) is None

    async def test_the_counters_and_the_age_are_read_from_the_record(self, fake_redis: Any) -> None:
        """The age comes from the record's remaining lifetime — arq sets it to the interval
        plus one second — not from the timestamp, which is the worker's local clock with
        no year in it."""
        await fake_redis.psetex(HEALTH_CHECK_KEY, _LIFETIME_MS - 4_000, _RECORD)

        health = await worker_health(fake_redis)

        assert health is not None
        assert health.ongoing == 1
        assert health.queued == 2
        assert health.completed == 3
        assert health.failed == 1
        # The fake's clock is the real one, so the second can tick over between the write
        # and the read on a loaded machine: four, or the four plus what the machine took.
        assert 4 <= health.reported_seconds_ago <= 6

    async def test_a_record_from_a_longer_interval_is_alive_and_never_negative(
        self, fake_redis: Any
    ) -> None:
        """A worker built before the interval was shortened writes an hour-long record. It
        is alive — that is what the record means — and its age floors at zero rather than
        reading as minus fifty-nine minutes."""
        await fake_redis.psetex(HEALTH_CHECK_KEY, 3_601_000, _RECORD)

        health = await worker_health(fake_redis)

        assert health is not None
        assert health.reported_seconds_ago == 0

    async def test_redis_being_unreachable_is_left_to_the_caller(self, broken_redis: Any) -> None:
        """A different fact from "no worker", and the two must not be conflated: a page
        renders through it, a readout says "unknown"."""
        with pytest.raises(Exception, match="onnect"):
            await worker_health(broken_redis)

    def test_the_interval_is_short_enough_to_be_the_signal(self) -> None:
        """The record's absence is what every "nobody is listening" message rests on, so
        an hour-long record would have said a dead worker was alive for an hour."""
        assert HEALTH_CHECK_INTERVAL_SECONDS <= 60
        assert HEALTH_CHECK_KEY == "arq:queue:health-check"


class TestTheWorkerHonoursIt:
    def test_the_worker_records_at_the_shared_interval(self, settings_env: Any) -> None:
        from arq.worker import get_kwargs  # noqa: PLC0415

        from aer.worker import WorkerSettings  # noqa: PLC0415 -- see test_worker.py

        assert get_kwargs(WorkerSettings)["health_check_interval"] == HEALTH_CHECK_INTERVAL_SECONDS


# -- `aer diagnose` --------------------------------------------------------------------------


class TestTheDiagnoseLine:
    def test_a_queued_run_with_no_worker_says_what_to_start(self) -> None:
        line, colour = worker_words(None, status=JobStatus.QUEUED)

        assert "NONE" in line
        assert f"last {HEALTH_CHECK_INTERVAL_SECONDS + 1} s" in line
        assert "just worker" in line
        assert colour == "red"

    def test_a_running_run_with_no_worker_says_the_process_is_gone(self) -> None:
        line, colour = worker_words(None, status=JobStatus.RUNNING)

        assert "marked running" in line
        assert "gone" in line
        assert colour == "red"

    def test_a_queued_run_behind_a_busy_worker_is_waiting_its_turn(self) -> None:
        health = WorkerHealth(reported_seconds_ago=7, ongoing=1, queued=1, completed=2, failed=0)

        line, colour = worker_words(health, status=JobStatus.QUEUED)

        assert "reported 7 s ago" in line
        assert "busy with 1 job(s)" in line
        assert "waits its turn" in line
        assert colour == "yellow"

    def test_a_queued_run_with_an_idle_worker_is_about_to_start(self) -> None:
        health = WorkerHealth(reported_seconds_ago=2, ongoing=0, queued=1, completed=0, failed=0)

        line, colour = worker_words(health, status=JobStatus.QUEUED)

        assert "idle" in line
        assert colour == "green"

    def test_a_running_run_with_a_live_worker_is_fine(self) -> None:
        health = WorkerHealth(reported_seconds_ago=12, ongoing=1, queued=0, completed=0, failed=0)

        line, colour = worker_words(health, status=JobStatus.RUNNING)

        assert "1 job(s) ongoing" in line
        assert colour == "green"


# -- The console -----------------------------------------------------------------------------


@pytest.fixture
async def queued_run(
    db_engine: Any, api_settings: Settings, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """A run started through the API and left queued, served over the in-process Redis
    the console reads the worker's record from. Enqueueing is recorded rather than done:
    the fake holds no queue, and the run's status is what the page is about."""
    await delete_all(db_engine)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        request = research_request(
            user_id=user.id,
            company_name="Microsoft Corporation",
            ticker="MSFT",
            exchange="NASDAQ",
            as_of_date=AS_OF_DATE,
            point_in_time=True,
            base_currency="USD",
            reporting_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
        )
        session.add(request)
        await session.commit()

    async def record(redis: Any, job_id: uuid.UUID) -> str:
        return f"task-{job_id}"

    monkeypatch.setattr("aer.api.routes.runs.enqueue_run", record)
    monkeypatch.setattr("aer.web.pages.enqueue_run", record)

    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        body = await start_run(client, request.id)
        assert body["status"] == JobStatus.QUEUED.value
        yield client, body["job_id"]
    await delete_all(db_engine)


class TestTheConsoleWhileQueued:
    async def test_with_no_worker_it_says_nobody_is_listening(
        self, queued_run: Any, fake_redis: Any
    ) -> None:
        client, job_id = queued_run

        html = (await client.get(f"/runs/{job_id}")).text

        assert "no worker has reported" in html
        assert "just worker" in html
        assert "normally begins within a few seconds" not in html

    async def test_with_an_idle_worker_it_says_when_it_reported(
        self, queued_run: Any, fake_redis: Any
    ) -> None:
        client, job_id = queued_run
        await fake_redis.psetex(
            HEALTH_CHECK_KEY, _LIFETIME_MS - 3_000, _RECORD.replace("j_ongoing=1", "j_ongoing=0")
        )

        html = (await client.get(f"/runs/{job_id}")).text

        # The seconds are read from the record's remaining lifetime against a real clock,
        # and rendering the page on a loaded runner took the age from 3 to 4 once in CI.
        assert re.search(r"The worker reported [3-9] seconds ago", html)
        assert "normally begins within a few seconds" in html
        assert "no worker has reported" not in html

    async def test_behind_a_busy_worker_it_says_the_run_is_waiting_its_turn(
        self, queued_run: Any, fake_redis: Any
    ) -> None:
        client, job_id = queued_run
        await fake_redis.psetex(HEALTH_CHECK_KEY, _LIFETIME_MS - 3_000, _RECORD)

        html = (await client.get(f"/runs/{job_id}")).text

        assert "Queued behind 1 other job(s)" in html
        assert "one at a time" in html
