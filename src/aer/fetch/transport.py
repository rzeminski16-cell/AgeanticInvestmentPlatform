"""An HTTP transport that connects only to addresses already validated.

This is the piece that closes the DNS-rebinding gap. Validating a hostname's addresses and
then handing the *hostname* to an HTTP client leaves the client free to resolve it again —
and a resolver under an attacker's control can answer differently the second time. The
check passes, the connection goes somewhere else, and nothing in the logs looks wrong.

So the resolution is done once, up front, and the connection is pinned to an address that
passed. The mechanism is a custom :mod:`httpcore` network backend: ``connect_tcp`` is
handed the validated IP instead of the hostname, while everything above it — the request
line, the ``Host`` header, the TLS SNI and therefore the certificate check — still uses the
real hostname. The server sees an ordinary request; the client never performs a second
lookup.

The alternative usually reached for — rewriting the URL to the IP and setting ``Host`` by
hand — breaks SNI and certificate validation, so it trades an SSRF hole for a
man-in-the-middle one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import httpcore
import httpx

__all__ = ["PinnedAddressTransport", "PinnedResolutionBackend"]


class PinnedResolutionBackend(httpcore.AsyncNetworkBackend):
    """Delegates to the real backend, but substitutes a pre-validated address.

    A hostname with no pin is refused rather than resolved. Failing closed matters here:
    the whole point is that nothing connects to an address the SSRF guard has not seen, and
    a fallback to ordinary resolution would silently restore the behaviour being prevented.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend, pins: Mapping[str, str]) -> None:
        self._inner = inner
        self._pins = pins

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        address = self._pins.get(host)
        if address is None:
            message = (
                f"No validated address is pinned for {host}. Every destination must pass "
                "the SSRF checks before a connection is opened."
            )
            raise httpcore.ConnectError(message)

        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        _path: str,
        _timeout: float | None = None,
        _socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        # There is no legitimate reason for this platform to speak HTTP over a Unix
        # socket, and permitting it would reach services the address rules exist to keep
        # out of range.
        message = "Connecting to a Unix socket is not permitted."
        raise httpcore.ConnectError(message)

    async def sleep(self, seconds: float) -> None:  # pragma: no cover -- pass-through
        await self._inner.sleep(seconds)


class PinnedAddressTransport(httpx.AsyncHTTPTransport):
    """An httpx transport whose connections go only to pinned addresses.

    ``pins`` is mutated by the fetcher as redirects are followed, because each new hop is
    validated before it is added. A hop whose host never gets pinned cannot be connected
    to, so a redirect that escapes validation fails at the socket rather than reaching
    anything.
    """

    def __init__(self, pins: dict[str, str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pins = pins
        # Replacing the pool's backend rather than the pool itself: everything else about
        # httpx's transport -- connection reuse, HTTP/2, proxy handling, retries -- stays
        # exactly as configured.
        self._pool._network_backend = PinnedResolutionBackend(self._pool._network_backend, pins)

    @property
    def pins(self) -> dict[str, str]:
        return self._pins
