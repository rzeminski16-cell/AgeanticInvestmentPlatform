"""The fetch pipeline end to end, against respx.

Two things here are worth reading rather than skimming.

**The redirect-to-private test.** A public URL that answers ``302 Location:
http://169.254.169.254/`` is the single most common way SSRF protection is bypassed,
because the check ran against the URL that was asked for rather than the one that was
actually fetched. It has its own test and its own reason to exist.

**Archiving on failure.** ``fetch()`` stores the body of a 404 and a 500 exactly as it
stores a 200. A run whose failures left no trace cannot be audited, and "the server
returned a page saying the filing was withdrawn" is sometimes the most informative thing
that happened.

Everything runs through respx, and ``no_real_sockets`` fails the test if anything opens a
connection anyway — respx intercepts at the transport layer, so code bypassing httpx would
otherwise slip through unnoticed.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket as socket_module

import httpx
import pytest
import respx

from aer.config import load_settings
from aer.core.enums import Provider
from aer.errors import ExternalServiceError
from aer.fetch.client import MAX_ATTEMPTS, SafeFetcher, sniff_media_type
from aer.fetch.errors import (
    CircuitOpenError,
    ContentTypeMismatchError,
    FetchTooLargeError,
    RobotsDisallowedError,
    SsrfBlockedError,
    UrlNotAllowedError,
)
from aer.fetch.policy import policy_for_url
from aer.fetch.robots import RobotsCache
from tests.fetch_fixtures import (
    NetworkAccessInTestError,
    RecordingSleeper,
    public_resolver,
)

pytestmark = pytest.mark.usefixtures("no_real_sockets")

FILING_URL = "https://www.sec.gov/Archives/edgar/data/789019/msft-10k.htm"
FILING_HTML = b"<html><body>Microsoft Corporation 10-K. Revenue 245,122.</body></html>"


@pytest.fixture
def fetcher(fetch_settings, artefact_store, limiter, breaker, sleeper):
    """A fetcher wired to respx, with a public DNS answer and no robots check.

    The transport is httpx's default so respx can intercept it. The pinned-address
    backend has its own test; mixing the two would mean testing respx's plumbing rather
    than the pipeline.
    """
    return SafeFetcher(
        fetch_settings,
        store=artefact_store,
        limiter=limiter,
        breaker=breaker,
        robots=None,
        sleep=sleeper,
        resolver=public_resolver("104.16.0.1"),
        transport_factory=httpx.AsyncHTTPTransport,
    )


class TestSniffing:
    @pytest.mark.parametrize(
        ("head", "expected"),
        [
            (b"%PDF-1.7\n...", "application/pdf"),
            (b"PK\x03\x04rest", "application/zip"),
            (b"<!DOCTYPE html><html>", "text/html"),
            (b"  \n<html>", "text/html"),
            (b'<?xml version="1.0"?>', "application/xml"),
            (b'{"a": 1}', "application/json"),
            (b"[1, 2]", "application/json"),
            (b"\x89PNG\r\n", "image/unknown"),
        ],
    )
    def test_content_is_identified_from_its_bytes(self, head, expected):
        assert sniff_media_type(head, declared=None) == expected

    def test_the_bytes_beat_a_lying_header(self):
        # A server labelling an HTML error page as a PDF is common, and a PDF parser
        # handed that page produces confident nonsense rather than failing.
        assert sniff_media_type(b"<html>Not found</html>", "application/pdf") == "text/html"

    def test_the_declared_type_is_used_when_the_bytes_say_nothing(self):
        assert sniff_media_type(b"col1,col2\n1,2", "text/csv; charset=utf-8") == "text/csv"

    def test_unidentifiable_content_with_no_header_is_generic(self):
        assert sniff_media_type(b"\x00\x01\x02", None) == "application/octet-stream"


class TestSuccessfulFetch:
    @respx.mock
    async def test_it_returns_the_content_address(self, fetcher, artefact_store):
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=FILING_HTML))

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert result.ok
        assert result.status_code == 200
        assert result.sha256 == hashlib.sha256(FILING_HTML).hexdigest()
        assert result.size_bytes == len(FILING_HTML)
        assert await artefact_store.exists(result.sha256)

    @respx.mock
    async def test_the_body_is_archived(self, fetcher, artefact_store):
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=FILING_HTML))

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert await artefact_store.read(result.sha256) == FILING_HTML

    @respx.mock
    async def test_the_user_agent_identifies_the_operator(self, fetcher, fetch_settings):
        # The SEC makes a descriptive User-Agent a condition of access, so this is a
        # licence requirement rather than a nicety.
        route = respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"<html>"))

        await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert route.calls.last.request.headers["user-agent"] == fetch_settings.http_user_agent

    @respx.mock
    async def test_the_licence_note_travels_with_the_result(self, fetcher):
        # Answerable now, much harder to reconstruct from a URL a year later.
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"<html>"))

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert "US government work" in result.licence_note

    @respx.mock
    async def test_an_expected_media_type_is_accepted(self, fetcher):
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=FILING_HTML))

        result = await fetcher.fetch(
            FILING_URL, provider=Provider.SEC_EDGAR, expected_media_types=frozenset({"text/html"})
        )

        assert result.media_type == "text/html"


class TestSsrfThroughTheFetcher:
    @respx.mock
    async def test_a_host_resolving_to_a_private_address_is_refused(
        self, fetch_settings, artefact_store, limiter, breaker, sleeper
    ):
        hostile = SafeFetcher(
            fetch_settings,
            store=artefact_store,
            limiter=limiter,
            breaker=breaker,
            robots=None,
            sleep=sleeper,
            resolver=public_resolver("127.0.0.1"),
            transport_factory=httpx.AsyncHTTPTransport,
        )
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"<html>"))

        with pytest.raises(SsrfBlockedError):
            await hostile.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

    @respx.mock
    async def test_a_redirect_to_a_private_address_is_refused(
        self, fetch_settings, artefact_store, limiter, breaker, sleeper
    ):
        # The bypass this design exists to close. The first URL is entirely legitimate;
        # the Location header is not, and a check that ran only on the original URL would
        # never see it.
        def resolve(host, port, *_args, **_kwargs):
            address = "169.254.169.254" if "169.254" in host else "104.16.0.1"
            return [(socket_module.AF_INET, socket_module.SOCK_STREAM, 6, "", (address, port))]

        redirecting = SafeFetcher(
            fetch_settings,
            store=artefact_store,
            limiter=limiter,
            breaker=breaker,
            robots=None,
            sleep=sleeper,
            resolver=resolve,
            transport_factory=httpx.AsyncHTTPTransport,
        )
        respx.get(FILING_URL).mock(
            return_value=httpx.Response(302, headers={"location": "https://169.254.169.254/"})
        )

        with pytest.raises((SsrfBlockedError, UrlNotAllowedError)):
            await redirecting.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

    @respx.mock
    async def test_a_redirect_off_the_allowlist_is_refused(self, fetcher):
        # Even a public destination is refused if it is not a host this provider serves.
        respx.get(FILING_URL).mock(
            return_value=httpx.Response(302, headers={"location": "https://evil.test/x"})
        )

        with pytest.raises(UrlNotAllowedError):
            await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

    @respx.mock
    async def test_a_redirect_into_a_refused_host_is_refused(self, fetcher):
        # The refusal has to hold on the hop that is actually fetched, not only on the URL
        # somebody typed. A publisher that redirects to the FCA would otherwise fetch it.
        respx.get(FILING_URL).mock(
            return_value=httpx.Response(
                302, headers={"location": "https://data.fca.org.uk/nsm/document/abc"}
            )
        )

        with pytest.raises(UrlNotAllowedError, match="not fetched by this platform"):
            await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

    @respx.mock
    async def test_a_permitted_redirect_is_followed_and_recorded(self, fetcher):
        final = "https://www.sec.gov/Archives/final.htm"
        respx.get(FILING_URL).mock(return_value=httpx.Response(301, headers={"location": final}))
        respx.get(final).mock(return_value=httpx.Response(200, content=FILING_HTML))

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert result.final_url == final
        assert result.redirect_chain == (FILING_URL,)

    @respx.mock
    async def test_an_endless_redirect_chain_is_refused(self, fetcher):
        respx.get(url__regex=r"https://www\.sec\.gov/loop.*").mock(
            return_value=httpx.Response(302, headers={"location": "https://www.sec.gov/loop2"})
        )

        with pytest.raises(SsrfBlockedError, match="redirects"):
            await fetcher.fetch("https://www.sec.gov/loop", provider=Provider.SEC_EDGAR)


class TestAllowlist:
    async def test_a_host_off_the_allowlist_is_refused_before_any_request(self, fetcher):
        with pytest.raises(UrlNotAllowedError, match="not on the allowlist"):
            await fetcher.fetch("https://evil.test/x", provider=Provider.SEC_EDGAR)

    async def test_a_lookalike_domain_is_refused(self, fetcher):
        # The classic allowlist mistake: endswith("sec.gov") also accepts "evil-sec.gov",
        # which is a domain an attacker can simply register.
        with pytest.raises(UrlNotAllowedError):
            await fetcher.fetch("https://evil-sec.gov/x", provider=Provider.SEC_EDGAR)

    def test_a_subdomain_of_an_allowed_domain_is_permitted(self):
        """Asked of the allowlist directly, rather than inferred from a fetch failing.

        This was originally written as "fetch it and assert *something* raises, but not
        ``UrlNotAllowedError``" — the something being the socket guard firing on a request
        respx had no route for. That is a proxy for the real question, and it depended on
        the guard behaving identically everywhere. It did not: on Windows the fetch reached
        the network instead of raising, so the test failed while the allowlist was working
        perfectly. Asking the allowlist what it decided has no such dependency.
        """
        assert policy_for_url("https://data.sec.gov/api/x", Provider.SEC_EDGAR)

    def test_a_lookalike_of_a_subdomain_is_still_refused(self):
        """The same question from the other side, so the test above cannot pass vacuously."""
        with pytest.raises(UrlNotAllowedError):
            policy_for_url("https://data.sec.gov.evil.test/api/x", Provider.SEC_EDGAR)

    @respx.mock
    async def test_the_pipeline_really_will_fetch_that_subdomain(self, fetcher):
        """And the allowlist decision is actually reached by the pipeline.

        Routed through respx this time, so the request is answered rather than escaping.
        Without this, `policy_for_url` could be correct and never consulted.
        """
        url = "https://data.sec.gov/api/x"
        respx.get(url).mock(return_value=httpx.Response(200, content=b"{}"))

        result = await fetcher.fetch(url, provider=Provider.SEC_EDGAR)

        assert result.ok


class TestRobots:
    @respx.mock
    async def test_a_disallowed_path_is_never_requested(
        self, fetch_settings, artefact_store, limiter, breaker, sleeper, redis_client
    ):
        async def robots(_url: str) -> str:
            return "User-agent: *\nDisallow: /\n"

        route = respx.get("https://investors.example-plc.com/report.htm").mock(
            return_value=httpx.Response(200, content=b"<html>")
        )
        guarded = SafeFetcher(
            fetch_settings,
            store=artefact_store,
            limiter=limiter,
            breaker=breaker,
            robots=RobotsCache(redis_client, robots, user_agent=fetch_settings.http_user_agent),
            sleep=sleeper,
            resolver=public_resolver("104.16.0.1"),
            transport_factory=httpx.AsyncHTTPTransport,
        )

        # An issuer's own site, which is where robots.txt actually applies: reading a
        # company's website is crawling, whereas the regulator APIs are a documented
        # contract and are configured not to consult it.
        with pytest.raises(RobotsDisallowedError):
            await guarded.fetch(
                "https://investors.example-plc.com/report.htm",
                provider=Provider.ISSUER_IR,
                extra_hosts=("investors.example-plc.com",),
            )

        assert route.call_count == 0


class TestRetries:
    @respx.mock
    async def test_a_500_is_retried_then_returned(self, fetcher, sleeper):
        route = respx.get(FILING_URL).mock(
            return_value=httpx.Response(500, content=b"<html>upstream error</html>")
        )

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert route.call_count == MAX_ATTEMPTS
        assert result.status_code == 500
        assert len(sleeper.calls) == MAX_ATTEMPTS - 1

    @respx.mock
    async def test_a_transient_failure_recovers(self, fetcher):
        respx.get(FILING_URL).mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, content=FILING_HTML),
            ]
        )

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert result.ok
        assert result.attempts == 2

    @respx.mock
    async def test_retry_after_is_honoured(self, fetcher, sleeper):
        # A server that names a delay knows better than our backoff curve does.
        respx.get(FILING_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "7"}),
                httpx.Response(200, content=FILING_HTML),
            ]
        )

        await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert sleeper.calls == [7.0]

    @respx.mock
    async def test_an_absurd_retry_after_is_capped(self, fetcher, sleeper):
        # A server asking us back in a day is not something to sit and wait for inside a
        # request.
        respx.get(FILING_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "86400"}),
                httpx.Response(200, content=FILING_HTML),
            ]
        )

        await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert sleeper.calls == [120.0]

    @respx.mock
    async def test_a_404_is_not_retried(self, fetcher):
        # 4xx other than 429 means the request was wrong, and repeating a wrong request
        # is just load.
        route = respx.get(FILING_URL).mock(
            return_value=httpx.Response(404, content=b"<html>Not found</html>")
        )

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert route.call_count == 1
        assert result.status_code == 404

    @respx.mock
    async def test_repeated_timeouts_raise_after_the_attempt_limit(self, fetcher):
        respx.get(FILING_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

        with pytest.raises(ExternalServiceError) as excinfo:
            await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert excinfo.value.context["attempts"] == MAX_ATTEMPTS
        assert excinfo.value.retryable is True

    @respx.mock
    async def test_backoff_grows_between_attempts(self, fetcher, sleeper):
        # Full jitter, so the exact values vary; what must hold is that the ceiling grows
        # and nothing waits absurdly long.
        respx.get(FILING_URL).mock(return_value=httpx.Response(503))

        await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert len(sleeper.calls) == MAX_ATTEMPTS - 1
        assert all(0 <= delay <= 30 for delay in sleeper.calls)


class TestArchivingFailures:
    @respx.mock
    async def test_a_404_body_is_archived(self, fetcher, artefact_store):
        # A run whose failures left no trace cannot be audited, and the error page often
        # says exactly what went wrong.
        body = b"<html>This filing has been withdrawn.</html>"
        respx.get(FILING_URL).mock(return_value=httpx.Response(404, content=body))

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert await artefact_store.read(result.sha256) == body

    @respx.mock
    async def test_a_500_body_is_archived(self, fetcher, artefact_store):
        respx.get(FILING_URL).mock(return_value=httpx.Response(500, content=b"upstream boom"))

        result = await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert await artefact_store.exists(result.sha256)

    @respx.mock
    async def test_a_mismatched_body_is_archived_before_it_is_rejected(
        self, fetcher, artefact_store
    ):
        # The case where the body is most worth keeping: it is the evidence of what the
        # server actually sent, and raising first would throw it away.
        respx.get(FILING_URL).mock(
            return_value=httpx.Response(
                200, content=b"<html>error</html>", headers={"content-type": "application/pdf"}
            )
        )

        with pytest.raises(ContentTypeMismatchError) as excinfo:
            await fetcher.fetch(
                FILING_URL,
                provider=Provider.SEC_EDGAR,
                expected_media_types=frozenset({"application/pdf"}),
            )

        assert await artefact_store.exists(excinfo.value.context["sha256"])


class TestSizeCap:
    @respx.mock
    async def test_an_oversized_response_is_refused(self, fetcher, fetch_settings):
        oversized = b"x" * (fetch_settings.max_artefact_bytes + 1)
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=oversized))

        with pytest.raises(FetchTooLargeError):
            await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

    @respx.mock
    async def test_nothing_is_archived_when_the_cap_is_exceeded(
        self, fetcher, fetch_settings, artefact_store
    ):
        oversized = b"x" * (fetch_settings.max_artefact_bytes + 1)
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=oversized))

        with pytest.raises(FetchTooLargeError):
            await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        stored = [p for p in artefact_store.root.rglob("*") if p.is_file()]
        assert stored == []

    @respx.mock
    async def test_a_per_request_cap_can_be_smaller(self, fetcher):
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"x" * 100))

        with pytest.raises(FetchTooLargeError):
            await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR, max_bytes=10)


class TestContentType:
    @respx.mock
    async def test_a_mismatch_is_refused(self, fetcher):
        respx.get(FILING_URL).mock(
            return_value=httpx.Response(200, content=b"<html>not a pdf</html>")
        )

        with pytest.raises(ContentTypeMismatchError, match="decided by the bytes"):
            await fetcher.fetch(
                FILING_URL,
                provider=Provider.SEC_EDGAR,
                expected_media_types=frozenset({"application/pdf"}),
            )

    @respx.mock
    async def test_an_error_response_is_not_type_checked(self, fetcher):
        # An error response is an HTML page whatever was requested. Refusing it would
        # lose the message explaining the failure.
        respx.get(FILING_URL).mock(
            return_value=httpx.Response(404, content=b"<html>Not found</html>")
        )

        result = await fetcher.fetch(
            FILING_URL,
            provider=Provider.SEC_EDGAR,
            expected_media_types=frozenset({"application/pdf"}),
        )

        assert result.status_code == 404


class TestCircuitBreaking:
    @respx.mock
    async def test_an_open_circuit_refuses_before_any_request(self, fetcher, breaker):
        route = respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"<html>"))
        for _ in range(5):
            await breaker.record_failure("sec_edgar", threshold=5, cooldown_seconds=60)

        with pytest.raises(CircuitOpenError):
            await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert route.call_count == 0

    @respx.mock
    async def test_a_success_clears_earlier_failures(self, fetcher, breaker):
        await breaker.record_failure("sec_edgar", threshold=5, cooldown_seconds=60)
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"<html>"))

        await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        await breaker.check("sec_edgar")


class TestConstruction:
    def test_an_empty_user_agent_is_refused_at_construction(
        self, settings_env, artefact_store, limiter, breaker, tmp_path
    ):
        # Refused now rather than at the first request: discovering it halfway through a
        # run wastes everything spent up to that point.
        settings_env.setenv("AER_HTTP_USER_AGENT", "   ")
        settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path))

        with pytest.raises(Exception, match="AER_HTTP_USER_AGENT"):
            SafeFetcher(load_settings(), store=artefact_store, limiter=limiter, breaker=breaker)


class TestRateLimitingIsApplied:
    @respx.mock
    async def test_the_bucket_is_drawn_from(self, fetcher, limiter):
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"<html>"))

        before = (await limiter.try_acquire("sec_edgar", rate=8.0, burst=8)).tokens_remaining
        await fetcher.fetch(FILING_URL, provider=Provider.SEC_EDGAR)
        after = (await limiter.try_acquire("sec_edgar", rate=8.0, burst=8)).tokens_remaining

        assert after < before

    @respx.mock
    async def test_an_exhausted_bucket_delays_rather_than_failing(
        self, fetch_settings, artefact_store, limiter, breaker, clock
    ):
        recorded = RecordingSleeper(clock)
        patient = SafeFetcher(
            fetch_settings,
            store=artefact_store,
            limiter=limiter,
            breaker=breaker,
            robots=None,
            sleep=recorded,
            resolver=public_resolver("104.16.0.1"),
            transport_factory=httpx.AsyncHTTPTransport,
        )
        respx.get(FILING_URL).mock(return_value=httpx.Response(200, content=b"<html>"))
        for _ in range(8):
            await limiter.try_acquire("sec_edgar", rate=8.0, burst=8)

        result = await patient.fetch(FILING_URL, provider=Provider.SEC_EDGAR)

        assert result.ok
        assert recorded.calls == [pytest.approx(0.125, abs=1e-6)]


class TestTheNetworkGuard:
    """The guard that makes every other test in this module mean something.

    Worth its own tests because it failed silently once. Patching ``socket.socket`` catches
    synchronous code and nothing else: on Windows the Proactor event loop connects through
    IOCP and never calls ``socket.connect``, so for two months the fetch suite had no
    network guard at all on the platform this project is developed on — and the only symptom
    was one unrelated test failing for a reason that looked like a bug in the allowlist.

    A guard nobody tests is a guard that is working right up until it is not.
    """

    async def test_a_remote_connection_is_refused(self):
        """Through ``create_connection``, which is how anyio, httpcore and httpx connect."""
        loop = asyncio.get_running_loop()

        with pytest.raises(NetworkAccessInTestError, match="real network connection"):
            await loop.create_connection(asyncio.Protocol, "example.com", 80)

    def test_create_connection_is_patched_and_not_only_the_socket(self):
        """A structural check, because a behavioural one cannot tell them apart here.

        On a selector event loop — Linux, macOS — ``create_connection`` builds its socket
        with ``socket.socket`` and connects through it, so the socket patch alone catches
        everything and the test above passes whether or not this patch exists. Only the
        Proactor loop, which is Windows-only and cannot be run here, takes the path that
        needs it.

        So this asserts the patch is *installed* rather than that it fires. That is weaker
        than a behavioural test and it is what is actually verifiable on this platform;
        pretending otherwise is how the guard came to be a no-op on Windows in the first
        place.
        """
        import asyncio.base_events  # noqa: PLC0415 -- read at call time, after patching

        assert (
            asyncio.base_events.BaseEventLoop.create_connection.__qualname__
            != "BaseEventLoop.create_connection"
        ), "the no_real_sockets fixture is not patching create_connection"

    async def test_a_remote_ip_is_refused_as_well_as_a_hostname(self):
        """A resolved address must not be a way round it."""
        loop = asyncio.get_running_loop()

        with pytest.raises(NetworkAccessInTestError):
            await loop.create_connection(asyncio.Protocol, "104.16.0.1", 443)

    async def test_loopback_is_still_permitted(self):
        """Redis is a real dependency; blocking loopback would mean testing a stub.

        Nothing is listening on this port, so the connection is refused by the operating
        system — and that is the point: it got far enough to be refused by the OS rather
        than by the guard.
        """
        loop = asyncio.get_running_loop()

        with pytest.raises((ConnectionRefusedError, OSError)) as excinfo:
            await loop.create_connection(asyncio.Protocol, "127.0.0.1", 9)

        assert not isinstance(excinfo.value, NetworkAccessInTestError)

    def test_the_synchronous_path_is_guarded_too(self):
        """The original check, kept: not everything that opens a socket is async.

        The socket is closed explicitly. The guard raises before the connection is
        attempted, so without the context manager it would be finalised by the garbage
        collector — and ``filterwarnings = ["error"]`` turns that ResourceWarning into a
        failure in whichever test happens to run next.
        """
        with socket_module.socket() as sock, pytest.raises(NetworkAccessInTestError):
            sock.connect(("example.com", 80))
