"""Companies House: the UK register, its accounts, and the two ways to get a company wrong.

The register lists **companies**, not securities. It knows nothing about tickers or listings, so
resolving a name to a company number is a search followed by a judgement — and the judgement this
adapter makes is to **refuse an ambiguous match**. Picking one by search rank would put another
business's accounts under this company's name, and nothing downstream would notice: every figure
would be internally consistent and about the wrong firm. :class:`TestResolvingACompany` is most
of this file for that reason.

The other easy mistake is the credential. It is attached by provider inside the fetcher, so
:class:`TestTheCredential` checks it reaches Companies House and nowhere else.
"""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path
from typing import Final

import httpx
import pytest

from aer.core.enums import Provider, SourceTier
from aer.errors import ValidationError
from aer.fetch.client import SafeFetcher
from aer.fetch.policy import policy_for
from aer.logging import is_sensitive_name, redact_secrets
from aer.sources.base import ResolvedEntity
from aer.sources.uk.companies_house import (
    ACCOUNTS_CATEGORIES,
    API_ROOT,
    DOCUMENT_ROOT,
    CompaniesHouseClient,
    basic_auth_header,
    document_url,
    normalise_company_number,
    parse_company_profile,
    parse_filing_history,
    parse_search_results,
)
from tests.fetch_fixtures import public_resolver

pytestmark = pytest.mark.usefixtures("no_real_sockets")

FIXTURES: Final = Path(__file__).parent / "fixtures" / "uk"
COMPANY_NUMBER: Final = "00102498"
API_KEY: Final = "test-companies-house-key"  # pragma: allowlist secret

PROFILE_URL: Final = f"{API_ROOT}/company/{COMPANY_NUMBER}"
HISTORY_URL: Final = f"{API_ROOT}/company/{COMPANY_NUMBER}/filing-history"
SEARCH_URL: Final = f"{API_ROOT}/search/companies"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _json(name: str) -> httpx.Response:
    return httpx.Response(200, content=fixture(name), headers={"content-type": "application/json"})


@pytest.fixture
def credentialled_fetcher(fetch_settings, artefact_store, limiter, breaker, sleeper):
    return SafeFetcher(
        fetch_settings,
        store=artefact_store,
        limiter=limiter,
        breaker=breaker,
        robots=None,
        sleep=sleeper,
        resolver=public_resolver("104.16.0.1"),
        transport_factory=httpx.AsyncHTTPTransport,
        credentials={Provider.COMPANIES_HOUSE: basic_auth_header(API_KEY)},
    )


@pytest.fixture
def client(credentialled_fetcher, artefact_store):
    return CompaniesHouseClient(credentialled_fetcher, store=artefact_store)


# -- Identifiers ---------------------------------------------------------------------------------


class TestCompanyNumbers:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("102498", "00102498"),
            ("00102498", "00102498"),
            (" 00102498 ", "00102498"),
            ("sc123456", "SC123456"),
            ("NI 123456", "NI123456"),
        ],
    )
    def test_a_number_is_normalised_to_the_form_the_register_answers_to(
        self, given: str, expected: str
    ) -> None:
        """`102498` and `00102498` are the same company and only the padded form resolves —
        a detail that is a 404 the first time it is missed."""
        assert normalise_company_number(given) == expected

    @pytest.mark.parametrize("given", ["", "   ", "not-a-number", "123456789", "../etc/passwd"])
    def test_something_that_is_not_a_company_number_is_refused(self, given: str) -> None:
        """It goes into a URL path, so it is checked rather than trusted."""
        with pytest.raises(ValidationError):
            normalise_company_number(given)

    def test_a_document_url_is_built_from_the_identifier(self) -> None:
        built = document_url("AbCdEf1234")  # pragma: allowlist secret

        assert built == f"{DOCUMENT_ROOT}/document/AbCdEf1234/content"

    def test_a_document_identifier_is_escaped_into_the_path(self) -> None:
        """The identifier comes out of a response body. Escaping it is what stops a crafted one
        climbing out of the path it belongs in."""
        built = document_url("../../company/99999999")

        assert "/document/" in built
        assert built.count("/company/") == 0


# -- Parsing -------------------------------------------------------------------------------------


class TestParsingTheProfile:
    def test_it_reads_the_company(self) -> None:
        profile = parse_company_profile(fixture("ch_profile.json"))

        assert profile.company_number == COMPANY_NUMBER
        assert profile.name == "ACME HOLDINGS PLC"
        assert profile.is_active
        assert profile.incorporated_on == date(1909, 4, 14)

    def test_it_reads_the_accounting_reference_date(self) -> None:
        """Which is what says when this company's financial year ends, and therefore which
        filing covers which period."""
        profile = parse_company_profile(fixture("ch_profile.json"))

        assert profile.accounts_reference_date == "30/06"

    @pytest.mark.parametrize(
        "payload",
        [b"<html>error</html>", b"{}", b'{"errors": [{"error": "company-profile-not-found"}]}'],
        ids=["an html error page", "an empty object", "a not-found body"],
    )
    def test_a_response_that_is_not_a_profile_raises(self, payload: bytes) -> None:
        """A 404 body or a rate-limit page must not read as a company with no name."""
        with pytest.raises(ValidationError):
            parse_company_profile(payload)


class TestParsingTheFilingHistory:
    def test_it_reads_every_filing(self) -> None:
        history = parse_filing_history(
            fixture("ch_filing_history.json"), company_number=COMPANY_NUMBER
        )

        assert len(history.filings) == 4
        assert history.total == 4

    def test_it_extracts_the_document_identifier_from_the_link(self) -> None:
        """The identifier is the only thing that names a retrievable document, and it is buried
        in a URL. Taken apart rather than used as a URL — see the module docstring."""
        history = parse_filing_history(
            fixture("ch_filing_history.json"), company_number=COMPANY_NUMBER
        )

        newest = history.filings[0]
        assert newest.document_id == "AbCdEf1234"
        assert newest.filed_on == date(2022, 10, 14)

    def test_a_filing_with_no_document_is_marked_unfetchable(self) -> None:
        """Older entries are index records with nothing behind them. Saying so beats building a
        URL that 404s and recording the failure as provenance."""
        history = parse_filing_history(
            fixture("ch_filing_history.json"), company_number=COMPANY_NUMBER
        )

        old = next(f for f in history.filings if f.filed_on.year == 1998)
        assert not old.is_fetchable
        assert old.document_id is None

    def test_only_accounts_are_offered_for_acquisition(self) -> None:
        """A filing history is mostly officer appointments and confirmation statements. Real
        records, and not ones a research report cites."""
        history = parse_filing_history(
            fixture("ch_filing_history.json"), company_number=COMPANY_NUMBER
        )

        accounts = history.accounts()
        assert len(accounts) == 2
        assert all(f.category in ACCOUNTS_CATEGORIES for f in accounts)
        assert all(f.is_fetchable for f in accounts)

    def test_the_point_in_time_filter_is_applied_at_discovery(self) -> None:
        history = parse_filing_history(
            fixture("ch_filing_history.json"), company_number=COMPANY_NUMBER
        )

        early = history.filed_on_or_before(date(2022, 1, 1))

        assert all(f.filed_on <= date(2022, 1, 1) for f in early)
        assert len(early) < len(history.filings)

    def test_a_filing_on_the_as_of_date_is_kept(self) -> None:
        history = parse_filing_history(
            fixture("ch_filing_history.json"), company_number=COMPANY_NUMBER
        )

        assert any(
            f.filed_on == date(2022, 10, 14) for f in history.filed_on_or_before(date(2022, 10, 14))
        )

    def test_a_malformed_entry_is_skipped_rather_than_fatal(self) -> None:
        broken = fixture("ch_filing_history.json").replace(b'"date": "2021-10-08"', b'"x": 0')

        history = parse_filing_history(broken, company_number=COMPANY_NUMBER)

        assert len(history.filings) == 3

    def test_a_response_with_no_items_raises(self) -> None:
        with pytest.raises(ValidationError, match="items"):
            parse_filing_history(b'{"total_count": 0}', company_number=COMPANY_NUMBER)


# -- Resolving a company -------------------------------------------------------------------------


class TestResolvingACompany:
    """The judgement that matters, and the one nothing downstream could correct."""

    async def test_an_unambiguous_search_resolves(self, client, respx_mock) -> None:
        respx_mock.get(url__startswith=SEARCH_URL).mock(return_value=_json("ch_search_single.json"))

        entity = await client.resolve_entity("ACME HOLDINGS PLC")

        assert entity.identifier == COMPANY_NUMBER
        assert entity.name == "ACME HOLDINGS PLC"

    async def test_a_dissolved_company_does_not_count_as_a_match(self, client, respx_mock) -> None:
        """The single-match fixture contains a dissolved pension trustee alongside the plc. If
        dissolved companies counted, this would be ambiguous and refuse."""
        respx_mock.get(url__startswith=SEARCH_URL).mock(return_value=_json("ch_search_single.json"))

        entity = await client.resolve_entity("ACME HOLDINGS")

        assert entity.identifier == COMPANY_NUMBER

    async def test_an_ambiguous_search_is_refused(self, client, respx_mock) -> None:
        """**The test this adapter exists to pass.** Two active companies with similar names,
        and choosing by rank would attribute one business's accounts to another. Every figure
        downstream would be internally consistent and about the wrong firm."""
        respx_mock.get(url__startswith=SEARCH_URL).mock(
            return_value=_json("ch_search_ambiguous.json")
        )

        with pytest.raises(ValidationError, match="matches 2 active companies") as raised:
            await client.resolve_entity("ACME HOLDINGS")

        assert "ACME HOLDINGS PLC (00102498)" in str(raised.value)
        assert "Supply the company number" in str(raised.value)

    async def test_no_match_is_refused_with_an_explanation(self, client, respx_mock) -> None:
        """The register lists companies rather than securities, so a ticker is often simply not
        the registered name. The message says so rather than reporting an empty result."""
        respx_mock.get(url__startswith=SEARCH_URL).mock(
            return_value=httpx.Response(
                200, content=b'{"items": []}', headers={"content-type": "application/json"}
            )
        )

        with pytest.raises(ValidationError, match="No active company") as raised:
            await client.resolve_entity("XYZ")

        assert "lists companies rather than securities" in str(raised.value)

    def test_search_results_parse_into_candidates(self) -> None:
        found = parse_search_results(fixture("ch_search_ambiguous.json"))

        assert len(found) == 3
        assert sum(1 for c in found if c.is_active) == 2

    async def test_an_empty_query_is_refused_before_a_request(self, client) -> None:
        with pytest.raises(ValidationError, match="needs a query"):
            await client.search_companies("   ")


# -- Through the network -------------------------------------------------------------------------


class TestFetchingThroughTheClient:
    async def test_it_fetches_and_parses_a_profile(self, client, respx_mock) -> None:
        respx_mock.get(PROFILE_URL).mock(return_value=_json("ch_profile.json"))

        profile = await client.fetch_profile("102498")

        assert profile.company_number == COMPANY_NUMBER

    async def test_discovery_returns_only_fetchable_accounts(self, client, respx_mock) -> None:
        respx_mock.get(url__startswith=HISTORY_URL).mock(
            return_value=_json("ch_filing_history.json")
        )

        refs = await client.discover_documents(
            ResolvedEntity(identifier=COMPANY_NUMBER, name="ACME HOLDINGS PLC")
        )

        assert len(refs) == 2
        assert all(ref.url.startswith(f"{DOCUMENT_ROOT}/document/") for ref in refs)
        assert all(ref.publication_date <= date(2022, 10, 14) for ref in refs)

    async def test_discovery_filters_by_the_as_of_date(self, client, respx_mock) -> None:
        respx_mock.get(url__startswith=HISTORY_URL).mock(
            return_value=_json("ch_filing_history.json")
        )

        refs = await client.discover_documents(
            ResolvedEntity(identifier=COMPANY_NUMBER, name="ACME HOLDINGS PLC"),
            as_of_date=date(2022, 1, 1),
        )

        assert all(ref.publication_date <= date(2022, 1, 1) for ref in refs)

    async def test_the_history_query_asks_the_register_to_filter(self, client, respx_mock) -> None:
        """One request instead of several pages of officer appointments."""
        route = respx_mock.get(url__startswith=HISTORY_URL).mock(
            return_value=_json("ch_filing_history.json")
        )

        await client.fetch_filing_history(COMPANY_NUMBER)

        assert "category=accounts" in str(route.calls[0].request.url)

    def test_the_adapter_declares_its_provider_and_tier(self, client) -> None:
        assert client.provider is Provider.COMPANIES_HOUSE
        assert client.source_tier is SourceTier.T1_REGULATORY


class TestTheCredential:
    """A secret that goes to the wrong host is a leaked secret."""

    def test_the_header_is_basic_auth_with_an_empty_password(self) -> None:
        """The scheme Companies House documents: the key as the username, nothing as the
        password."""
        header = basic_auth_header(API_KEY)

        assert header.startswith("Basic ")
        decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
        assert decoded == f"{API_KEY}:"

    async def test_it_is_sent_to_companies_house(self, credentialled_fetcher, respx_mock) -> None:
        route = respx_mock.get(PROFILE_URL).mock(return_value=_json("ch_profile.json"))

        await credentialled_fetcher.fetch(PROFILE_URL, provider=Provider.COMPANIES_HOUSE)

        assert route.calls[0].request.headers["authorization"] == basic_auth_header(API_KEY)

    async def test_it_is_not_sent_to_another_provider(
        self, credentialled_fetcher, respx_mock
    ) -> None:
        """**The control.** The credential is attached by provider, so a key for one publisher
        cannot travel to another's host — which is what would happen if it lived in a header
        dictionary applied to every request."""
        sec_url = "https://www.sec.gov/files/company_tickers_exchange.json"
        route = respx_mock.get(sec_url).mock(
            return_value=httpx.Response(
                200, content=b"{}", headers={"content-type": "application/json"}
            )
        )

        await credentialled_fetcher.fetch(sec_url, provider=Provider.SEC_EDGAR)

        assert "authorization" not in route.calls[0].request.headers

    async def test_a_fetcher_with_no_credentials_sends_none(
        self, fetch_settings, artefact_store, limiter, breaker, sleeper, respx_mock
    ) -> None:
        plain = SafeFetcher(
            fetch_settings,
            store=artefact_store,
            limiter=limiter,
            breaker=breaker,
            robots=None,
            sleep=sleeper,
            resolver=public_resolver("104.16.0.1"),
            transport_factory=httpx.AsyncHTTPTransport,
        )
        route = respx_mock.get(PROFILE_URL).mock(return_value=_json("ch_profile.json"))

        await plain.fetch(PROFILE_URL, provider=Provider.COMPANIES_HOUSE)

        assert "authorization" not in route.calls[0].request.headers

    def test_the_credential_is_not_in_the_policy_table(self) -> None:
        """It lives on the fetcher instance. `FetchPolicy` is a module constant that gets
        logged, repr'd and imported by anything wanting a rate limit."""
        policy = policy_for(Provider.COMPANIES_HOUSE)

        assert "authorization" not in {k.lower() for k in policy.extra_headers}

    def test_authorization_is_redacted_by_the_logger(self) -> None:
        """The backstop. Nothing should put a credential in log context, and if something does
        it does not reach the log."""
        assert is_sensitive_name("Authorization")

        redacted = redact_secrets(None, "info", {"authorization": basic_auth_header(API_KEY)})
        assert API_KEY not in str(redacted)
