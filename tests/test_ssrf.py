"""Server-side request forgery protection.

The most security-critical tests in the codebase. Every case here is an address or a
redirect that must never be connected to, and the acceptance criterion for this task is
that none of them escapes.

The parametrised address list is deliberately long and deliberately boring. Each entry is
one thing an attacker would try, and the value of the list is that it is exhaustive rather
than interesting — a gap in it is a gap in the guard.
"""

from __future__ import annotations

import pytest

from aer.fetch.errors import SsrfBlockedError
from aer.fetch.ssrf import (
    address_refusal_reason,
    is_public_address,
    resolve_and_validate,
    validate_url,
)
from tests.fetch_fixtures import public_resolver

pytestmark = pytest.mark.usefixtures("no_real_sockets")

PRIVATE_ADDRESSES = [
    pytest.param("127.0.0.1", id="ipv4-loopback"),
    pytest.param("127.0.0.53", id="ipv4-loopback-resolver"),
    pytest.param("0.0.0.0", id="unspecified"),  # noqa: S104
    pytest.param("10.0.0.1", id="rfc1918-10"),
    pytest.param("172.16.5.4", id="rfc1918-172"),
    pytest.param("192.168.1.1", id="rfc1918-192"),
    pytest.param("169.254.169.254", id="cloud-metadata"),
    pytest.param("169.254.1.1", id="link-local"),
    pytest.param("100.64.0.1", id="carrier-grade-nat"),
    pytest.param("192.0.2.1", id="test-net-1"),
    pytest.param("224.0.0.1", id="multicast"),
    pytest.param("240.0.0.1", id="reserved"),
    pytest.param("::1", id="ipv6-loopback"),
    pytest.param("::", id="ipv6-unspecified"),
    pytest.param("fe80::1", id="ipv6-link-local"),
    pytest.param("fd00::1", id="ipv6-unique-local"),
    pytest.param("fc00::1", id="ipv6-unique-local-fc"),
    pytest.param("::ffff:127.0.0.1", id="ipv4-mapped-loopback"),
    pytest.param("::ffff:169.254.169.254", id="ipv4-mapped-metadata"),
    pytest.param("::ffff:10.0.0.1", id="ipv4-mapped-private"),
]

PUBLIC_ADDRESSES = [
    pytest.param("8.8.8.8", id="google-dns"),
    pytest.param("1.1.1.1", id="cloudflare-dns"),
    pytest.param("104.16.0.1", id="ordinary-public-v4"),
    pytest.param("2606:4700::1", id="ordinary-public-v6"),
]


class TestAddressClassification:
    @pytest.mark.parametrize("address", PRIVATE_ADDRESSES)
    def test_a_private_address_is_refused(self, address):
        reason = address_refusal_reason(address)

        assert reason is not None
        assert not is_public_address(address)

    @pytest.mark.parametrize("address", PUBLIC_ADDRESSES)
    def test_a_public_address_is_permitted(self, address):
        assert address_refusal_reason(address) is None
        assert is_public_address(address)

    def test_the_metadata_address_says_what_it_is(self):
        # "Blocked" tells an operator nothing. The single most valuable SSRF target
        # deserves a message that names it.
        reason = address_refusal_reason("169.254.169.254")

        assert reason is not None
        assert "metadata" in reason
        assert "credentials" in reason

    def test_an_ipv4_mapped_address_is_judged_on_the_address_it_wraps(self):
        # ::ffff:127.0.0.1 is loopback in different notation, and none of the IPv6
        # attribute checks see through the mapping.
        reason = address_refusal_reason("::ffff:127.0.0.1")

        assert reason is not None
        assert "loopback" in reason

    def test_nonsense_is_refused_rather_than_raising(self):
        assert address_refusal_reason("not-an-address") is not None
        assert address_refusal_reason("") is not None


class TestUrlValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://example.test/",
            "ftp://example.test/x",
            "data:text/html,<script>",
            "jar:https://example.test/!/x",
        ],
    )
    def test_a_non_http_scheme_is_refused(self, url):
        with pytest.raises(SsrfBlockedError):
            validate_url(url, allow_insecure_http=True)

    def test_plain_http_is_refused_by_default(self):
        # An unencrypted response can be altered in transit, and evidence that may have
        # been altered is not evidence.
        with pytest.raises(SsrfBlockedError, match="altered in transit"):
            validate_url("http://example.test/x", allow_insecure_http=False)

    def test_plain_http_can_be_permitted_deliberately(self):
        scheme, host, port = validate_url("http://example.test/x", allow_insecure_http=True)

        assert (scheme, host, port) == ("http", "example.test", 80)

    @pytest.mark.parametrize("port", [22, 25, 3306, 5432, 6379, 9200, 11211])
    def test_an_unexpected_port_is_refused(self, port):
        # A URL naming port 22 or 6379 is a port scan or a protocol-confusion attempt,
        # not a document request.
        with pytest.raises(SsrfBlockedError, match="not fetched"):
            validate_url(f"https://example.test:{port}/x", allow_insecure_http=False)

    def test_credentials_in_a_url_are_refused(self):
        # A classic way to disguise the real host from a human reading the URL: everything
        # before the @ is ignored by the browser and read as the hostname by the person.
        hostile = "https://user:pass@example.test/x"  # pragma: allowlist secret
        with pytest.raises(SsrfBlockedError, match="credentials"):
            validate_url(hostile, allow_insecure_http=False)

    def test_a_url_with_no_host_is_refused(self):
        with pytest.raises(SsrfBlockedError, match="no host"):
            validate_url("https:///path", allow_insecure_http=False)


class TestResolution:
    @pytest.mark.parametrize("address", PRIVATE_ADDRESSES)
    def test_a_hostname_resolving_to_a_private_address_is_refused(self, address):
        # The case a hostname-based blocklist misses entirely: an attacker controls DNS
        # for their own domain, so a perfectly ordinary-looking name can point anywhere.
        with pytest.raises(SsrfBlockedError) as excinfo:
            resolve_and_validate(
                "https://totally-normal.test/filing.htm", resolver=public_resolver(address)
            )

        assert excinfo.value.context["host"] == "totally-normal.test"

    @pytest.mark.parametrize("address", PRIVATE_ADDRESSES)
    def test_a_literal_private_address_in_the_url_is_refused(self, address):
        # A literal skips the lookup but not the rules. Treating it as pre-approved would
        # be an obvious hole.
        host = f"[{address}]" if ":" in address else address
        with pytest.raises(SsrfBlockedError):
            resolve_and_validate(f"https://{host}/x")

    def test_a_public_hostname_is_permitted(self):
        resolved = resolve_and_validate(
            "https://www.sec.gov/filing.htm", resolver=public_resolver("104.16.0.1")
        )

        assert resolved.hostname == "www.sec.gov"
        assert resolved.port == 443
        assert resolved.primary == "104.16.0.1"

    def test_a_mixed_answer_is_refused_even_though_one_address_is_public(self):
        # The rule that is easy to get wrong. Picking the public address and proceeding
        # would make the outcome depend on resolver ordering, and a mixed answer is either
        # a misconfiguration or a rebinding attempt — neither is worth connecting to.
        with pytest.raises(SsrfBlockedError) as excinfo:
            resolve_and_validate(
                "https://mixed.test/x", resolver=public_resolver("104.16.0.1", "127.0.0.1")
            )

        assert excinfo.value.context["refused"] == ["127.0.0.1"]
        assert excinfo.value.context["resolved_count"] == 2

    def test_every_public_address_is_kept(self):
        resolved = resolve_and_validate(
            "https://many.test/x", resolver=public_resolver("104.16.0.1", "104.16.0.2")
        )

        assert resolved.addresses == ("104.16.0.1", "104.16.0.2")

    def test_a_host_that_does_not_resolve_is_refused(self):
        def failing(*_args, **_kwargs):
            message = "Name or service not known"
            raise OSError(message)

        with pytest.raises(SsrfBlockedError, match="could not be resolved"):
            resolve_and_validate("https://nowhere.test/x", resolver=failing)

    def test_an_empty_answer_is_refused(self):
        with pytest.raises(SsrfBlockedError, match="did not resolve"):
            resolve_and_validate("https://empty.test/x", resolver=public_resolver())

    def test_the_refusal_names_the_address_and_the_reason(self):
        # An operator seeing this in a log needs to know both what it resolved to and why
        # that is a problem, without reading the source.
        with pytest.raises(SsrfBlockedError) as excinfo:
            resolve_and_validate(
                "https://sneaky.test/x", resolver=public_resolver("169.254.169.254")
            )

        assert "169.254.169.254" in str(excinfo.value)
        assert "metadata" in str(excinfo.value)
