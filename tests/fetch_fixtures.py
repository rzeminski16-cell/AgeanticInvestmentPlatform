"""Fixtures for the fetch layer, including the one that proves no test touches the network.

``no_real_sockets`` is the load-bearing one. Every other test in this directory would pass
just as happily against code that quietly opened a real connection — respx intercepts at
the transport layer, so anything bypassing httpx would slip through unnoticed. Replacing
``socket.socket`` turns that from an assumption into a failure.

Loopback is left open, because Redis is a real dependency of the rate limiter and the
robots cache, and testing those against a stub would be testing the stub.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fakeredis import aioredis

from aer.config import Settings, load_settings
from aer.fetch.limits import CircuitBreaker, RateLimiter
from aer.storage.local import LocalArtefactStore

USER_AGENT = "Ageiantic Research Test test@example.invalid"

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class _BlockedSocket(socket.socket):
    """A socket that refuses to connect anywhere but loopback."""

    def connect(self, address):  # type: ignore[no-untyped-def]
        _refuse_if_remote(address)
        return super().connect(address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        _refuse_if_remote(address)
        return super().connect_ex(address)


def _refuse_if_remote(address: object) -> None:
    host = address[0] if isinstance(address, tuple) and address else address
    if str(host) not in _LOOPBACK:
        message = (
            f"A test opened a real network connection to {host!r}. Every fetch test must "
            "run against respx; a test that reaches the internet is slow, flaky, and "
            "proves nothing about the code under test."
        )
        raise AssertionError(message)


@pytest.fixture
def no_real_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if it opens a socket to a remote address.

    Requested per module via ``pytestmark = pytest.mark.usefixtures("no_real_sockets")``
    rather than made autouse. Autouse here would reach the whole suite and break the
    database and browser tests, which legitimately open sockets — and a guard that has to
    be disabled somewhere is a guard nobody trusts anywhere.
    """
    monkeypatch.setattr(socket, "socket", _BlockedSocket)


@pytest.fixture
def fetch_settings(settings_env, tmp_path) -> Settings:
    settings_env.setenv("AER_HTTP_USER_AGENT", USER_AGENT)
    settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
    settings_env.setenv("AER_MAX_ARTEFACT_BYTES", "8192")
    return load_settings()


@pytest.fixture
def artefact_store(tmp_path: Path) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=8192)


@pytest.fixture
def redis_client():
    """An in-process Redis.

    A real implementation of the protocol, including Lua scripting, so the token bucket's
    atomicity is genuinely exercised rather than assumed.
    """
    return aioredis.FakeRedis(decode_responses=True)


class FakeClock:
    """A clock the test moves by hand.

    "Does the eleventh request wait?" is a question about elapsed time. Answering it by
    sleeping makes a slow test that still only proves one timing; moving the clock proves
    the arithmetic exactly.
    """

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSleeper:
    """Records what it was asked to wait for, and returns immediately.

    Optionally advances a clock, so a test can assert both "it slept" and "the bucket
    refilled while it slept" without any real delay.
    """

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self._clock = clock

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._clock is not None:
            self._clock.advance(seconds)

    @property
    def total(self) -> float:
        return sum(self.calls)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sleeper(clock: FakeClock) -> RecordingSleeper:
    return RecordingSleeper(clock)


@pytest.fixture
def limiter(redis_client, clock: FakeClock) -> RateLimiter:
    return RateLimiter(redis_client, clock=clock)


@pytest.fixture
def breaker(redis_client, clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(redis_client, clock=clock)


def public_resolver(*addresses: str):
    """A stand-in for :func:`socket.getaddrinfo` returning fixed addresses.

    Injected rather than mocked globally, so a hostile DNS answer can be simulated without
    controlling a domain and without touching the real resolver.
    """

    def resolve(host, port, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses
        ]

    return resolve
