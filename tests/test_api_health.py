"""Liveness and readiness.

The interesting cases are the failures. A readiness probe that only ever gets tested
while everything is up is a probe nobody has tested: the branch that matters is the one
that runs at three in the morning, and the thing it must do is say *which* dependency is
down, not merely that something is.
"""

from __future__ import annotations

import pytest

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

        assert set(checks) == {"database", "redis"}
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
