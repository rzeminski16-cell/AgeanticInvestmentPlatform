"""Liveness and readiness.

The interesting cases are the failures. A readiness probe that only ever gets tested
while everything is up is a probe nobody has tested: the branch that matters is the one
that runs at three in the morning, and the thing it must do is say *which* dependency is
down, not merely that something is.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from aer.db.models import JobCancellation
from aer.version import version
from tests.api_fixtures import build_app, client_for


@pytest.fixture
async def healthy_client(api_settings, api_engine, fake_redis):
    async for client in client_for(build_app(api_settings, engine=api_engine, redis=fake_redis)):
        yield client


class TestLiveness:
    async def test_healthz_is_ok(self, api_settings, broken_engine, broken_redis):
        # Built deliberately on top of dependencies that are down. Liveness answers "is
        # this process alive", and a process that gets restarted because Postgres blinked
        # is a process in a restart loop.
        app = build_app(api_settings, engine=broken_engine, redis=broken_redis)
        async for client in client_for(app):
            response = await client.get("/healthz")

            assert response.status_code == 200
            assert response.json()["status"] == "ok"
            assert response.json()["version"] == version()

    async def test_healthz_carries_a_request_id(self, api_settings, broken_engine, broken_redis):
        app = build_app(api_settings, engine=broken_engine, redis=broken_redis)
        async for client in client_for(app):
            response = await client.get("/healthz")
            assert response.headers["x-request-id"]


@pytest.mark.integration
class TestReadinessWhenHealthy:
    async def test_returns_200(self, healthy_client):
        response = await healthy_client.get("/readyz")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["failing"] == []

    async def test_reports_every_dependency(self, healthy_client):
        checks = (await healthy_client.get("/readyz")).json()["checks"]

        assert set(checks) == {"database", "redis", "schema"}
        for result in checks.values():
            assert result["status"] == "ok"
            assert result["latency_ms"] >= 0


class TestReadinessWhenDegraded:
    async def test_a_broken_database_returns_503_and_names_it(
        self, api_settings, broken_engine, fake_redis
    ):
        app = build_app(api_settings, engine=broken_engine, redis=fake_redis)
        async for client in client_for(app):
            response = await client.get("/readyz")

            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "unavailable"
            assert body["failing"] == ["database"]
            assert body["checks"]["database"]["status"] == "error"
            assert body["checks"]["redis"]["status"] == "ok"

    @pytest.mark.integration
    async def test_a_broken_redis_returns_503_and_names_it(
        self, api_settings, api_engine, broken_redis
    ):
        app = build_app(api_settings, engine=api_engine, redis=broken_redis)
        async for client in client_for(app):
            response = await client.get("/readyz")

            assert response.status_code == 503
            assert response.json()["failing"] == ["redis"]

    async def test_both_down_names_both(self, api_settings, broken_engine, broken_redis):
        app = build_app(api_settings, engine=broken_engine, redis=broken_redis)
        async for client in client_for(app):
            body = (await client.get("/readyz")).json()

            assert body["failing"] == ["database", "redis"]

    async def test_a_failure_does_not_echo_the_connection_string(
        self, api_settings, broken_engine, fake_redis
    ):
        # /readyz is unauthenticated. A driver error that quotes the DSN would hand a
        # username, host and port to anyone who can reach the port.
        app = build_app(api_settings, engine=broken_engine, redis=fake_redis)
        async for client in client_for(app):
            raw = (await client.get("/readyz")).text

            assert "nothing" not in raw
            assert "nobody" not in raw


@pytest.mark.integration
class TestReadinessWhenTheSchemaIsBehind:
    """The failure this check was added for.

    A database two migrations behind answers ``SELECT 1`` perfectly, so connectivity alone
    reported "ready" and the first page touching the new table returned an opaque 500 whose
    only clue was in a stack trace. The probe now answers "have you run the migrations?"
    before anything has to find out the hard way.
    """

    @pytest.fixture
    async def missing_a_table(self, api_engine):
        """Drop a table, run the probe, put it back.

        Dropped rather than simulated. The point is that the *inspector* sees what
        PostgreSQL actually has, and a fake inspector would only prove the assertion agrees
        with itself.
        """
        async with api_engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS job_cancellations CASCADE"))
        try:
            yield
        finally:
            async with api_engine.begin() as connection:
                await connection.run_sync(JobCancellation.__table__.create, checkfirst=True)

    async def test_it_is_not_ready_and_names_the_schema(self, healthy_client, missing_a_table):
        response = await healthy_client.get("/readyz")

        assert response.status_code == 503
        assert response.json()["failing"] == ["schema"]

    async def test_it_names_the_missing_table_and_the_command_that_fixes_it(
        self, healthy_client, missing_a_table
    ):
        # "Not ready" without the reason sends you to read logs for something the probe
        # already knew. Both halves matter: which object, and what to type.
        detail = (await healthy_client.get("/readyz")).json()["checks"]["schema"]["detail"]

        assert "job_cancellations" in detail
        assert "alembic upgrade head" in detail

    async def test_the_database_check_still_passes(self, healthy_client, missing_a_table):
        # Connectivity and schema are different problems with different fixes. Reporting a
        # stale schema as a database outage sends the operator to restart a container that
        # was working perfectly.
        checks = (await healthy_client.get("/readyz")).json()["checks"]

        assert checks["database"]["status"] == "ok"

    async def test_the_schema_is_not_probed_when_the_database_is_down(
        self, api_settings, broken_engine, fake_redis
    ):
        # Nothing can be said about the schema of a database that is not there, and saying
        # it anyway would report two failures for one cause.
        app = build_app(api_settings, engine=broken_engine, redis=fake_redis)
        async for client in client_for(app):
            body = (await client.get("/readyz")).json()

            assert body["failing"] == ["database"]
            assert "schema" not in body["checks"]
