"""The transport that connects only to pre-validated addresses.

This needs its own file because the fetcher tests cannot reach it: they substitute
httpx's ordinary transport so respx can intercept, which means the pinned backend — the
single most security-critical component in the fetch layer — would otherwise have no
coverage at all.

What is being proved is narrow and specific: the connection goes to the *address* the
SSRF guard approved, while everything above it still uses the hostname, and a host with no
approved address cannot be connected to at all.
"""

from __future__ import annotations

import httpcore
import httpx
import pytest

from aer.fetch.transport import PinnedAddressTransport, PinnedResolutionBackend

pytestmark = pytest.mark.usefixtures("no_real_sockets")


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Stands in for the real backend and records what it was asked to connect to."""

    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connections.append((host, port))
        return object()  # type: ignore[return-value]

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("the inner backend should never be reached for a Unix socket")

    async def sleep(self, seconds: float) -> None:
        return None


class TestPinnedResolutionBackend:
    async def test_it_connects_to_the_pinned_address_not_the_hostname(self):
        # The entire point. If the hostname reached the socket layer, the client would
        # resolve it a second time — and a resolver under an attacker's control can answer
        # differently the second time, which is DNS rebinding.
        inner = RecordingBackend()
        backend = PinnedResolutionBackend(inner, {"www.sec.gov": "104.16.0.1"})

        await backend.connect_tcp("www.sec.gov", 443)

        assert inner.connections == [("104.16.0.1", 443)]

    async def test_an_unpinned_host_is_refused_rather_than_resolved(self):
        # Fails closed. A fallback to ordinary resolution would silently restore exactly
        # the behaviour this class exists to prevent.
        inner = RecordingBackend()
        backend = PinnedResolutionBackend(inner, {})

        with pytest.raises(httpcore.ConnectError, match="No validated address is pinned"):
            await backend.connect_tcp("www.sec.gov", 443)

        assert inner.connections == []

    async def test_a_different_host_cannot_borrow_another_hosts_pin(self):
        inner = RecordingBackend()
        backend = PinnedResolutionBackend(inner, {"www.sec.gov": "104.16.0.1"})

        with pytest.raises(httpcore.ConnectError):
            await backend.connect_tcp("evil.test", 443)

    async def test_connection_options_are_passed_through(self):
        # The pin replaces the destination and nothing else; timeouts and socket options
        # must still reach the real backend.
        recorded: dict[str, object] = {}

        class CapturingBackend(RecordingBackend):
            async def connect_tcp(
                self, host, port, timeout=None, local_address=None, socket_options=None
            ):
                recorded.update(
                    {"timeout": timeout, "local_address": local_address, "options": socket_options}
                )
                return await super().connect_tcp(host, port)

        backend = PinnedResolutionBackend(CapturingBackend(), {"a.test": "1.1.1.1"})
        await backend.connect_tcp("a.test", 443, timeout=7.5, local_address="0.0.0.0")  # noqa: S104

        assert recorded["timeout"] == 7.5
        assert recorded["local_address"] == "0.0.0.0"  # noqa: S104

    async def test_a_unix_socket_is_refused(self):
        # There is no legitimate reason for this platform to speak HTTP over a Unix
        # socket, and permitting it would reach services the address rules exist to keep
        # out of range.
        backend = PinnedResolutionBackend(RecordingBackend(), {})

        with pytest.raises(httpcore.ConnectError, match="Unix socket"):
            await backend.connect_unix_socket("/var/run/docker.sock")

    async def test_pins_added_later_take_effect(self):
        # The fetcher pins each redirect hop as it validates it, so the mapping has to be
        # live rather than a snapshot taken at construction.
        inner = RecordingBackend()
        pins: dict[str, str] = {}
        backend = PinnedResolutionBackend(inner, pins)

        with pytest.raises(httpcore.ConnectError):
            await backend.connect_tcp("late.test", 443)

        pins["late.test"] = "8.8.8.8"
        await backend.connect_tcp("late.test", 443)

        assert inner.connections == [("8.8.8.8", 443)]


class TestPinnedAddressTransport:
    def test_it_installs_the_pinned_backend_into_the_pool(self):
        # Asserted directly, because a transport that silently kept httpx's default
        # backend would pass every functional test while providing no protection at all.
        transport = PinnedAddressTransport({"a.test": "1.1.1.1"})

        assert isinstance(transport._pool._network_backend, PinnedResolutionBackend)

    def test_the_pins_it_exposes_are_the_ones_it_uses(self):
        pins = {"a.test": "1.1.1.1"}
        transport = PinnedAddressTransport(pins)

        assert transport.pins is pins

    async def test_a_request_to_an_unpinned_host_fails_to_connect(self):
        # End to end through httpx: an unpinned host does not reach the network, and the
        # failure surfaces as a connection error rather than a successful request.
        transport = PinnedAddressTransport({}, retries=0)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.ConnectError, match="No validated address is pinned"):
                await client.get("https://www.sec.gov/x")
