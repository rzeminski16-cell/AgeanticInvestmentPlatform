"""robots.txt compliance.

The operator's stated constraint is that this platform must not breach a site's terms of
use, and robots.txt is the machine-readable form of exactly that. So the assertion that
matters most here is not "the check runs" but **"a disallow stops the fetch"** — a warning
that is logged and then ignored is a breach with a paper trail, which is worse than no
check at all.
"""

from __future__ import annotations

import pytest

from aer.fetch.errors import RobotsDisallowedError
from aer.fetch.policy import DEFAULT_POLICIES, refusal_for
from aer.fetch.robots import RobotsCache, robots_url_for

pytestmark = pytest.mark.usefixtures("no_real_sockets")

USER_AGENT = "Ageiantic Research Test test@example.invalid"

PERMISSIVE = """
User-agent: *
Disallow: /private/
Allow: /
"""

BLANKET_REFUSAL = """
User-agent: *
Disallow: /
"""

NAMED_REFUSAL = f"""
User-agent: {USER_AGENT}
Disallow: /filings/

User-agent: *
Disallow:
"""

# The Bank of England's robots.txt, read at source on 2026-08-05 and recorded verbatim in
# ADR 0026. Pinned here because the determination that closed that ADR rests on it: the
# Bank's own documented CSV handler for the Interactive Statistical Database is on this
# list, and that is the whole reason this platform does not retrieve UK rates.
BANK_OF_ENGLAND = """User-agent: *
Disallow: /boeapps/database/ShowChart.asp
Disallow: /boeapps/database/_iadb-FromShowColumns.asp
Disallow: /boeapps/iadb
Disallow: /boeapps/titan
Disallow: /error
Disallow: /forms
Disallow: /mfsd
Disallow: /search
Disallow: /test-folder

Sitemap: https://www.bankofengland.co.uk/_api/sitemap/getsitemap
Host: www.bankofengland.co.uk
"""


class RecordingFetcher:
    """A robots fetcher that returns canned text and counts its calls."""

    def __init__(self, body: str | None) -> None:
        self.body = body
        self.calls: list[str] = []

    async def __call__(self, robots_url: str) -> str | None:
        self.calls.append(robots_url)
        return self.body


def cache_for(redis_client, body: str | None) -> tuple[RobotsCache, RecordingFetcher]:
    fetcher = RecordingFetcher(body)
    return RobotsCache(redis_client, fetcher, user_agent=USER_AGENT), fetcher


class TestRobotsUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.sec.gov/a/b/c.htm", "https://www.sec.gov/robots.txt"),
            ("https://www.sec.gov/", "https://www.sec.gov/robots.txt"),
            ("https://www.sec.gov/x?y=1#z", "https://www.sec.gov/robots.txt"),
            ("https://www.sec.gov:8443/x", "https://www.sec.gov:8443/robots.txt"),
        ],
    )
    def test_it_is_per_origin(self, url, expected):
        # Scheme, host and port. Two paths on one host share a robots.txt; a different
        # port does not, because it is a different server.
        assert robots_url_for(url) == expected


class TestDecisions:
    async def test_a_permitted_path_is_allowed(self, redis_client):
        cache, _ = cache_for(redis_client, PERMISSIVE)

        decision = await cache.decide("https://example.test/public/filing.htm")

        assert decision.allowed is True

    async def test_a_disallowed_path_is_refused(self, redis_client):
        cache, _ = cache_for(redis_client, PERMISSIVE)

        decision = await cache.decide("https://example.test/private/filing.htm")

        assert decision.allowed is False
        assert "disallows" in decision.reason

    async def test_a_blanket_refusal_blocks_everything(self, redis_client):
        cache, _ = cache_for(redis_client, BLANKET_REFUSAL)

        assert (await cache.decide("https://example.test/")).allowed is False
        assert (await cache.decide("https://example.test/anything")).allowed is False

    async def test_a_rule_naming_our_user_agent_is_the_one_applied(self, redis_client):
        # A site may permit a named crawler and forbid everything else, or the reverse.
        # Checking under "*" when we send a specific identity answers a question nobody
        # asked.
        cache, _ = cache_for(redis_client, NAMED_REFUSAL)

        assert (await cache.decide("https://example.test/filings/x.htm")).allowed is False
        assert (await cache.decide("https://example.test/other/x.htm")).allowed is True

    async def test_a_site_with_no_robots_txt_permits_everything(self, redis_client):
        # The standard reading: no robots.txt expresses no restriction.
        cache, _ = cache_for(redis_client, None)

        decision = await cache.decide("https://example.test/anything")

        assert decision.allowed is True
        assert "no robots.txt" in decision.reason


class TestEnforcement:
    async def test_require_allowed_raises_on_a_disallow(self, redis_client):
        # The assertion this whole module exists for. A disallow is a refusal, not a
        # warning: a warning that is logged and ignored is a deliberate breach.
        cache, _ = cache_for(redis_client, BLANKET_REFUSAL)

        with pytest.raises(RobotsDisallowedError) as excinfo:
            await cache.require_allowed("https://example.test/filing.htm")

        assert excinfo.value.context["robots_url"] == "https://example.test/robots.txt"
        assert excinfo.value.context["user_agent"] == USER_AGENT

    async def test_the_refusal_records_why(self, redis_client):
        cache, _ = cache_for(redis_client, BLANKET_REFUSAL)

        with pytest.raises(RobotsDisallowedError) as excinfo:
            await cache.require_allowed("https://example.test/filing.htm")

        assert "does not fetch what a publisher has asked it not to" in str(excinfo.value)

    async def test_require_allowed_returns_the_decision_when_permitted(self, redis_client):
        cache, _ = cache_for(redis_client, PERMISSIVE)

        decision = await cache.require_allowed("https://example.test/public/x.htm")

        assert decision.allowed is True


class TestCaching:
    async def test_robots_is_fetched_once_per_origin(self, redis_client):
        cache, fetcher = cache_for(redis_client, PERMISSIVE)

        for path in ("a", "b", "c"):
            await cache.decide(f"https://example.test/{path}")

        assert fetcher.calls == ["https://example.test/robots.txt"]

    async def test_a_different_origin_is_fetched_separately(self, redis_client):
        cache, fetcher = cache_for(redis_client, PERMISSIVE)

        await cache.decide("https://one.test/x")
        await cache.decide("https://two.test/x")

        assert len(fetcher.calls) == 2

    async def test_the_absence_of_robots_is_cached_too(self, redis_client):
        # "This site has no robots.txt" is an answer worth remembering. Without caching
        # it, every document on a site without one costs an extra request.
        cache, fetcher = cache_for(redis_client, None)

        await cache.decide("https://example.test/a")
        await cache.decide("https://example.test/b")

        assert len(fetcher.calls) == 1

    async def test_the_second_decision_reports_that_it_came_from_cache(self, redis_client):
        cache, _ = cache_for(redis_client, PERMISSIVE)

        first = await cache.decide("https://example.test/a")
        second = await cache.decide("https://example.test/b")

        assert first.from_cache is False
        assert second.from_cache is True

    async def test_invalidating_forces_a_refetch(self, redis_client):
        # A publisher changing their mind must be respected without waiting for the TTL.
        cache, fetcher = cache_for(redis_client, PERMISSIVE)
        await cache.decide("https://example.test/a")

        await cache.invalidate("https://example.test/a")
        await cache.decide("https://example.test/a")

        assert len(fetcher.calls) == 2


class TestTheBankOfEnglandDetermination:
    """ADR 0026, closed. The enforcement is this check, not a note in a document.

    The Bank's legal terms carry **no** blanket prohibition on automated access — unlike the
    FCA's, which is why `bankofengland.co.uk` is not in `REFUSED_HOSTS` and the rest of the
    site remains fetchable. What its robots.txt disallows is the Interactive Statistical
    Database, including `_iadb-FromShowColumns.asp`, which is the Bank's own documented
    handler for parameterised CSV downloads.

    So the download route is documented *and* disallowed at the same time, and this platform
    resolves that against itself: no UK rate is retrieved. These tests pin both halves, so
    the day somebody adds a Bank of England adapter the suite says why it cannot work.
    """

    async def test_the_documented_csv_handler_is_disallowed(self, redis_client):
        cache, _ = cache_for(redis_client, BANK_OF_ENGLAND)

        decision = await cache.decide(
            "https://www.bankofengland.co.uk/boeapps/database/"
            "_iadb-FromShowColumns.asp?csv.x=yes&SeriesCodes=XUDLUSS"
        )

        assert decision.allowed is False

    async def test_the_database_root_is_disallowed_too(self, redis_client):
        cache, _ = cache_for(redis_client, BANK_OF_ENGLAND)

        decision = await cache.decide("https://www.bankofengland.co.uk/boeapps/iadb/index.asp")

        assert decision.allowed is False

    async def test_the_viewer_path_is_not_a_way_round_it(self, redis_client):
        """It is not on the list, and that is not permission.

        Prefix matching leaves `/boeapps/database/fromshowcolumns.asp` — the plain viewer —
        permitted, and third-party clients use it to reach the same data. Fetching it to get
        at a handler robots.txt disallows would be circumvention of a stated restriction,
        which this project's constraints forbid regardless of what the parser returns. So the
        test records what the file actually says and the refusal lives one layer up, in the
        absence of any Bank of England provider, adapter or allowlisted host.
        """
        cache, _ = cache_for(redis_client, BANK_OF_ENGLAND)

        decision = await cache.decide(
            "https://www.bankofengland.co.uk/boeapps/database/fromshowcolumns.asp?csv.x=yes"
        )

        assert decision.allowed is True

    async def test_the_rest_of_the_site_is_fetchable(self, redis_client):
        """The refusal is a path, not a publisher. Speeches and reports are still readable."""
        cache, _ = cache_for(redis_client, BANK_OF_ENGLAND)

        decision = await cache.decide(
            "https://www.bankofengland.co.uk/financial-stability-report/2026/july-2026"
        )

        assert decision.allowed is True

    def test_no_provider_is_configured_for_the_bank(self):
        """The determination in code: nothing can fetch from the Bank because nothing may.

        Not a `REFUSED_HOSTS` entry, which would assert that the Bank's *terms* forbid
        automated access. They do not. The absence of a provider is the accurate statement.
        """
        hosts = {host for policy in DEFAULT_POLICIES.values() for host in policy.allowed_hosts}
        assert not any("bankofengland" in host for host in hosts)
        assert refusal_for("www.bankofengland.co.uk") is None
