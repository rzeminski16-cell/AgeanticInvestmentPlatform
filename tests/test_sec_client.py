"""The SEC client: URLs, pacing, and what happens to the bytes.

Everything runs against respx, and ``no_real_sockets`` fails the test if anything opens a
connection anyway. No test here touches sec.gov.

The property worth stating: **no method on this client accepts a URL.** Each takes an
identifier the SEC issued and builds the URL itself. That is what carries the fetch
layer's "no agent-callable tool takes a URL" guarantee up to the adapter, and there is a
test asserting the surface stays that way.
"""

from __future__ import annotations

import inspect
from datetime import date

import httpx
import pytest
import respx

from aer.core.enums import Provider, SourceTier
from aer.errors import ValidationError
from aer.fetch.client import SafeFetcher
from aer.fetch.errors import UrlNotAllowedError
from aer.sources.base import DocumentRef, ResolvedEntity, SourceAdapter
from aer.sources.sec.client import COMPANY_TICKERS_URL, SecEdgarClient
from tests.fetch_fixtures import public_resolver
from tests.sec_fixtures import MSFT_CIK, fixture_bytes

pytestmark = pytest.mark.usefixtures("no_real_sockets")

SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{MSFT_CIK}.json"
COMPANYFACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{MSFT_CIK}.json"


@pytest.fixture
def fetcher(fetch_settings, artefact_store, limiter, breaker, sleeper):
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


@pytest.fixture
def sec_sleeper():
    """Records the client's own inter-request pauses, separately from retry backoff."""

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[float] = []

        async def __call__(self, seconds: float) -> None:
            self.calls.append(seconds)

    return Recorder()


@pytest.fixture
def client(fetcher, artefact_store, sec_sleeper):
    return SecEdgarClient(fetcher, store=artefact_store, sleep=sec_sleeper)


def json_response(name: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=fixture_bytes(name),
        headers={"content-type": "application/json"},
    )


class TestTheAdapterSurface:
    def test_the_client_satisfies_the_source_adapter_protocol(self, client):
        assert isinstance(client, SourceAdapter)

    def test_it_declares_its_provider_and_tier(self, client):
        assert client.provider is Provider.SEC_EDGAR
        assert client.source_tier is SourceTier.T1_REGULATORY

    def test_no_public_method_accepts_a_url(self):
        # The structural answer to prompt injection: a filing whose text says "fetch
        # https://attacker.test/" produces no method call that could carry it out,
        # because no method takes a URL. Asserted rather than assumed, so adding one is a
        # deliberate act that fails a test first.
        offenders = []
        for name, member in inspect.getmembers(SecEdgarClient, inspect.isfunction):
            if name.startswith("_"):
                continue
            parameters = inspect.signature(member).parameters
            offenders.extend(
                f"{name}({parameter})" for parameter in parameters if "url" in parameter.lower()
            )

        assert offenders == []


class TestUrlConstruction:
    @respx.mock
    async def test_the_submissions_url_uses_the_padded_cik(self, client):
        route = respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))

        await client.fetch_submissions("789019")

        assert route.called

    @respx.mock
    async def test_the_companyfacts_url_uses_the_padded_cik(self, client):
        route = respx.get(COMPANYFACTS_URL).mock(
            return_value=json_response("companyfacts_msft.json")
        )

        await client.fetch_company_facts(MSFT_CIK)

        assert route.called

    @respx.mock
    async def test_a_bare_integer_cik_is_padded_before_the_request(self, client):
        # CIK789019.json is a 404 and CIK0000789019.json is Microsoft. Padding at the
        # boundary is what stops each call site getting it wrong separately.
        route = respx.get(COMPANYFACTS_URL).mock(
            return_value=json_response("companyfacts_msft.json")
        )

        await client.fetch_company_facts("789019")

        assert route.called


class TestFetchAndParse:
    @respx.mock
    async def test_resolving_a_ticker_returns_the_cik(self, client):
        respx.get(COMPANY_TICKERS_URL).mock(
            return_value=json_response("company_tickers_exchange.json")
        )

        entity = await client.resolve_entity("MSFT", exchange="NASDAQ")

        assert entity.identifier == MSFT_CIK
        assert entity.name == "MICROSOFT CORP"
        assert entity.exchange == "NASDAQ"

    @respx.mock
    async def test_an_unknown_ticker_fails_with_an_actionable_message(self, client):
        respx.get(COMPANY_TICKERS_URL).mock(
            return_value=json_response("company_tickers_exchange.json")
        )

        with pytest.raises(ValidationError, match="not in the SEC's ticker file"):
            await client.resolve_entity("TSCO", exchange="LSE")

    @respx.mock
    async def test_the_ticker_table_is_fetched_once_per_client(self, client):
        """Gap A56: the live run resolved six peers and fetched the megabyte table six
        times in three seconds. One client serves one run, so the client holds it."""
        route = respx.get(COMPANY_TICKERS_URL).mock(
            return_value=json_response("company_tickers_exchange.json")
        )

        first = await client.fetch_company_tickers()
        second = await client.fetch_company_tickers()

        assert route.call_count == 1
        assert second is first

    @respx.mock
    async def test_resolving_several_tickers_costs_one_table_fetch(self, client):
        """The shape the peer resolver actually has: resolve after resolve on one client."""
        route = respx.get(COMPANY_TICKERS_URL).mock(
            return_value=json_response("company_tickers_exchange.json")
        )

        for _ in range(3):
            await client.resolve_entity("MSFT", exchange="NASDAQ")

        assert route.call_count == 1

    @respx.mock
    async def test_the_response_is_parsed_from_what_was_archived(self, client, artefact_store):
        # Read back from the store by hash rather than kept in memory. If the two could
        # differ, the citation verifier would be checking a different document from the
        # one the facts came from.
        respx.get(COMPANYFACTS_URL).mock(return_value=json_response("companyfacts_msft.json"))

        response = await client.fetch_company_facts(MSFT_CIK)

        assert await artefact_store.exists(response.sha256)
        assert response.data.entity_name == "MICROSOFT CORPORATION"

    @respx.mock
    async def test_the_fetch_result_travels_with_the_parsed_data(self, client):
        respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))

        response = await client.fetch_submissions(MSFT_CIK)

        assert response.fetch.status_code == 200
        assert response.fetch.licence_note.startswith("US government work")
        assert len(response.data.filings) == 5


class TestDiscovery:
    @respx.mock
    async def test_documents_are_filtered_at_acquisition_by_the_as_of_date(self, client):
        # Filtered here, not downstream. A filing accepted after the as-of date never
        # becomes a reference, so no later code path can fetch it by forgetting to check.
        respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))
        entity = ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP")

        refs = await client.discover_documents(entity, as_of_date=date(2021, 1, 1))

        assert all(ref.publication_date <= date(2021, 1, 1) for ref in refs)
        assert "0000789019-22-000010" not in {ref.accession for ref in refs}

    @respx.mock
    async def test_periodic_forms_are_the_default(self, client):
        respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))
        entity = ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP")

        refs = await client.discover_documents(entity)

        # The 8-K in the fixture is not a periodic report.
        assert {ref.form for ref in refs} == {"10-K", "10-Q"}

    @respx.mock
    async def test_a_reference_carries_the_filing_date_as_its_publication_date(self, client):
        respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))
        entity = ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP")

        refs = await client.discover_documents(entity, forms=frozenset({"10-K"}))
        fy2020 = next(r for r in refs if r.accession == "0000789019-20-000039")

        assert fy2020.publication_date == date(2020, 7, 30)

    @respx.mock
    async def test_fetch_facts_returns_everything_unfiltered(self, client):
        # Point-in-time selection happens later, on the complete set, so what was
        # rejected and why stays recoverable.
        respx.get(COMPANYFACTS_URL).mock(return_value=json_response("companyfacts_msft.json"))
        entity = ResolvedEntity(identifier=MSFT_CIK, name="MICROSOFT CORP")

        facts = await client.fetch_facts(entity, as_of_date=date(2021, 1, 1))

        assert any(f.filed_date > date(2021, 1, 1) for f in facts)


class TestPacing:
    @respx.mock
    async def test_no_pause_before_the_first_request(self, client, sec_sleeper):
        respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))

        await client.fetch_submissions(MSFT_CIK)

        assert sec_sleeper.calls == []

    @respx.mock
    async def test_a_pause_between_sequential_requests(self, client, sec_sleeper):
        # The bucket is the ceiling across every worker; this pause keeps one sequential
        # loop from spending the whole allowance in a burst. The SEC blocks rather than
        # throttles, so the margin is worth its cost.
        respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))
        respx.get(COMPANYFACTS_URL).mock(return_value=json_response("companyfacts_msft.json"))

        await client.fetch_submissions(MSFT_CIK)
        await client.fetch_company_facts(MSFT_CIK)

        assert sec_sleeper.calls == [0.1]

    @respx.mock
    async def test_the_delay_is_configurable(self, fetcher, artefact_store, sec_sleeper):
        respx.get(SUBMISSIONS_URL).mock(return_value=json_response("submissions_msft.json"))
        client = SecEdgarClient(
            fetcher, store=artefact_store, sleep=sec_sleeper, inter_request_delay=0.5
        )

        await client.fetch_submissions(MSFT_CIK)
        await client.fetch_submissions(MSFT_CIK)

        assert sec_sleeper.calls == [0.5]


class TestRefusals:
    @respx.mock
    async def test_an_error_status_is_reported_with_the_archived_hash(self, client):
        # The body is still archived: "the server said the filing was withdrawn" is
        # sometimes the most informative thing in a run.
        respx.get(SUBMISSIONS_URL).mock(
            return_value=httpx.Response(404, content=b'{"error": "not found"}')
        )

        with pytest.raises(ValidationError) as excinfo:
            await client.fetch_submissions(MSFT_CIK)

        assert excinfo.value.context["status_code"] == 404
        assert excinfo.value.context["sha256"]

    @respx.mock
    async def test_a_document_outside_sec_gov_is_refused_by_the_allowlist(self, client):
        # The DocumentRef is constructed by this client from an accession EDGAR issued,
        # so this should be impossible -- and the fetch layer checks anyway, because a
        # chain of trusted construction is only as good as its weakest link.
        ref = DocumentRef(
            url="https://attacker.test/filing.htm",
            title="Not a filing",
            publication_date=date(2020, 7, 30),
        )

        with pytest.raises(UrlNotAllowedError):
            await client.fetch_document(ref)
