"""Server-side request forgery protection: resolve, validate, then pin.

**The attack.** Something persuades this platform to fetch a URL of an attacker's
choosing. On a developer's laptop that reaches the application's own API, the database,
Redis. On a cloud host it reaches ``169.254.169.254`` and returns the instance's IAM
credentials. The request looks entirely ordinary in every log; the damage is in where it
went.

**The naive defence, and why it fails.** Checking whether the *hostname* looks internal
catches nothing: an attacker controls DNS for their own domain, so
``fetch.evil.test`` can resolve to ``127.0.0.1``. Checking the resolved address and then
letting the HTTP client resolve the name again is barely better — that is a
time-of-check/time-of-use gap, and DNS rebinding exists precisely to exploit it: the first
lookup answers with a public address, the second, moments later, answers with a private
one.

**The defence used here.** Resolve the hostname once, validate *every* address the
resolver returned, then connect to one of those validated addresses directly, carrying the
original hostname in the ``Host`` header and in the TLS SNI so the request and the
certificate check remain correct. The client never performs its own lookup, so there is no
second answer to differ from the first.

Two further rules that are easy to omit and expensive to omit:

* **Every returned address must pass, not just the one that gets used.** A resolver
  answering with one public and one private address is either a misconfiguration or an
  attack; picking the public one and proceeding would make the outcome depend on
  ordering.
* **Every redirect hop is re-validated from scratch.** A public URL that answers ``302
  Location: http://169.254.169.254/`` is the single most common way this protection is
  bypassed, because the check ran against the URL that was asked for rather than the one
  that was fetched.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from aer.fetch.credentials import redact_credentials
from aer.fetch.errors import SsrfBlockedError

__all__ = [
    "ALLOWED_SCHEMES",
    "ResolvedHost",
    "address_refusal_reason",
    "is_public_address",
    "resolve_and_validate",
    "validate_url",
]

ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"https", "http"})

# Ports worth allowing at all. A URL naming port 22 or 6379 is not a document request; it
# is someone using this platform as a port scanner or a protocol-confusion gadget.
_ALLOWED_PORTS: Final[frozenset[int]] = frozenset({80, 443, 8443})

_MAX_HOSTNAME_LENGTH: Final = 253


@dataclass(frozen=True, slots=True)
class ResolvedHost:
    """A hostname and the validated addresses it resolved to."""

    hostname: str
    port: int
    addresses: tuple[str, ...]

    @property
    def primary(self) -> str:
        """The address to connect to. Any of them is safe; all of them were validated."""
        return self.addresses[0]


# Ordered, and the order is load-bearing: link-local is checked before private because a
# link-local address *is* private, and "the cloud metadata service" is a far more useful
# thing to tell an operator than "a private address". A table rather than a chain of ifs
# so that adding a rule means adding a row.
_ADDRESS_RULES: Final[tuple[tuple[str, str], ...]] = (
    (
        "is_link_local",
        "link-local, which on a cloud host is the instance metadata service and holds credentials",
    ),
    ("is_loopback", "loopback, which is this machine itself"),
    ("is_private", "a private network address, reachable only from inside this network"),
    ("is_multicast", "multicast"),
    ("is_reserved", "reserved"),
    ("is_unspecified", "unspecified (0.0.0.0 or ::)"),
)


def address_refusal_reason(address: str) -> str | None:
    """Why this IP address must not be connected to, or ``None`` if it is fine.

    Returns a reason rather than a boolean so a refusal can say *which* rule it broke.
    "Blocked" tells an operator nothing; "this is the cloud metadata address" tells them
    exactly what they are looking at.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "not a valid IP address"

    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # ::ffff:127.0.0.1 is a loopback address wearing different notation, and the IPv6
        # attribute checks do not see through the mapping. Unwrapped first so the real
        # address is what gets judged.
        mapped = address_refusal_reason(str(ip.ipv4_mapped))
        return None if mapped is None else f"an IPv4-mapped address that is {mapped}"

    for attribute, reason in _ADDRESS_RULES:
        if getattr(ip, attribute, False):
            return reason

    if not ip.is_global:
        # Catches everything the named rules miss, including IPv6 unique local addresses
        # and future reservations. Failing closed on an address nobody classified is the
        # only safe default here.
        return "not a globally routable address"

    return None


def is_public_address(address: str) -> bool:
    """Whether an address is safe to connect to."""
    return address_refusal_reason(address) is None


def validate_url(url: str, *, allow_insecure_http: bool) -> tuple[str, str, int]:
    """Check a URL's scheme, host and port. Returns ``(scheme, hostname, port)``.

    Performs no name resolution: this is the cheap structural check that runs before
    anything touches DNS.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme not in ALLOWED_SCHEMES:
        message = (
            f"Only {' and '.join(sorted(ALLOWED_SCHEMES))} URLs may be fetched. "
            "A file:, gopher: or ftp: URL is not a document request."
        )
        raise SsrfBlockedError(message, context={"scheme": scheme})

    if scheme == "http" and not allow_insecure_http:
        message = (
            "Plain HTTP is refused. An unencrypted response can be altered in transit, "
            "and evidence that may have been altered is not evidence. Set "
            "AER_ALLOW_INSECURE_HTTP only for a local test server."
        )
        raise SsrfBlockedError(message, context={"url": redact_credentials(url)})

    hostname = parts.hostname
    if not hostname:
        message = "The URL has no host."
        raise SsrfBlockedError(message, context={"url": redact_credentials(url)})

    if len(hostname) > _MAX_HOSTNAME_LENGTH:
        message = "The hostname is longer than a hostname can legally be."
        raise SsrfBlockedError(message, context={"hostname_length": len(hostname)})

    # Credentials embedded in a URL are refused outright. Everything before the "@" is
    # ignored by the client and read as the hostname by a person, which makes the form a
    # classic way to disguise where a URL actually points -- and this platform has no use
    # for it in any case.
    if parts.username or parts.password:
        message = "A URL carrying credentials is refused."
        raise SsrfBlockedError(message, context={"host": hostname})

    port = parts.port or (443 if scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        message = (
            f"Port {port} is not fetched. A URL naming an arbitrary port is a port scan "
            "or a protocol-confusion attempt, not a document request."
        )
        raise SsrfBlockedError(message, context={"port": port, "host": hostname})

    return scheme, hostname, port


def resolve_and_validate(
    url: str,
    *,
    allow_insecure_http: bool = False,
    resolver: object = None,
) -> ResolvedHost:
    """Resolve a URL's hostname and refuse it unless **every** address is public.

    Args:
        url: The URL about to be fetched.
        allow_insecure_http: Permit ``http://``. For a local test server only.
        resolver: Optional replacement for :func:`socket.getaddrinfo`, so tests can
            simulate a hostile DNS answer without one.

    Raises:
        SsrfBlockedError: If the URL is structurally unacceptable, cannot be resolved, or
            resolves to any address that is not globally routable.
    """
    scheme, hostname, port = validate_url(url, allow_insecure_http=allow_insecure_http)

    # A literal IP in the URL still goes through the same address rules; it simply skips
    # the lookup. Treating a literal as pre-approved would be an obvious hole.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        addresses = _lookup(hostname, port, resolver)
    else:
        addresses = (hostname,)

    if not addresses:
        message = f"{hostname} did not resolve to any address."
        raise SsrfBlockedError(message, context={"host": hostname})

    refusals = {
        address: reason
        for address in addresses
        if (reason := address_refusal_reason(address)) is not None
    }
    if refusals:
        # Refused even if some addresses were fine. A mixed answer is either a
        # misconfiguration or a rebinding attempt, and choosing the public one would make
        # the outcome depend on resolver ordering.
        address, reason = next(iter(refusals.items()))
        message = (
            f"{hostname} resolves to {address}, which is {reason}. This platform only "
            "fetches from publicly routable addresses."
        )
        raise SsrfBlockedError(
            message,
            context={
                "host": hostname,
                "scheme": scheme,
                "refused": sorted(refusals),
                "resolved_count": len(addresses),
            },
        )

    return ResolvedHost(hostname=hostname, port=port, addresses=tuple(addresses))


def _lookup(hostname: str, port: int, resolver: object) -> tuple[str, ...]:
    getaddrinfo = resolver if callable(resolver) else socket.getaddrinfo
    try:
        results = getaddrinfo(hostname, port, 0, socket.SOCK_STREAM)
    except OSError as exc:
        message = f"{hostname} could not be resolved."
        raise SsrfBlockedError(message, context={"host": hostname, "detail": str(exc)}) from exc

    addresses: list[str] = []
    for entry in results:
        sockaddr = entry[4]
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)
