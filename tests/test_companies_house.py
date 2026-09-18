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
from aer.sources.base import DocumentRef, ResolvedEntity, SourceAdapter
from aer.sources.uk.companies_house import (
    ACCOUNTS_CATEGORIES,
    API_ROOT,
    DOCUMENT_ROOT,
    CompaniesHouseClient,
    CompanyProfile,
    basic_auth_header,
    document_url,
    looks_like_company_number,
    normalise_company_number,
    parse_company_profile,
    parse_filing_history,
    parse_search_results,
)
from tests.fetch_fixtures import public_resolver
from tests.ixbrl_fixtures import (
    CLEAN_IFRS,
    CLEAN_IFRS_TRUTH,
    EXTENSION_TAG,
    PERIOD_END,
    PERIOD_START,
    REPEATED_FIGURE,
    WITH_EXTENSION,
)

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

    def test_it_reads_the_declared_sic_codes(self) -> None:
        """What a UK company says it does, in UK SIC 2007 (ADR 0121).

        Read from the live register on 18 September 2026: Tesco declares `47110`, retail sale
        in non-specialised stores with food predominating. The profile endpoint is the only
        place the register states it — a search result carries no classification — so without
        this a UK run reaches the sector gate with nothing to propose from.
        """
        profile = parse_company_profile(fixture("ch_profile_tesco.json"))

        assert profile.name == "TESCO PLC"
        assert profile.sic_codes == ("47110",)

    def test_a_profile_with_no_codes_says_so_rather_than_inventing_one(self) -> None:
        assert parse_company_profile(fixture("ch_profile.json")).sic_codes == ()

    @pytest.mark.parametrize(
        ("reference", "expected"),
        [("30/06", "0630"), ("26/02", "0226"), ("1/1", "0101"), (None, None), ("x/y", None)],
    )
    def test_the_accounting_reference_date_becomes_a_fiscal_year_end(
        self, reference, expected
    ) -> None:
        """`fiscal_year_of` reads `MMDD`; the register states a day and a month.

        The same fact in the other order and without the padding that makes it sortable —
        which is the kind of difference that is invisible until a fiscal year is computed
        against a calendar one.
        """
        profile = CompanyProfile(
            company_number=COMPANY_NUMBER, name="ACME", accounts_reference_date=reference
        )

        assert profile.fiscal_year_end == expected

    def test_the_recorded_profile_carries_its_own_year_end(self) -> None:
        assert parse_company_profile(fixture("ch_profile_tesco.json")).fiscal_year_end == "0226"

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

    async def test_the_company_name_is_searched_rather_than_the_ticker(
        self, client, respx_mock
    ) -> None:
        """`TSCO` is Tesco's symbol and no part of `TESCO PLC` (ADR 0121).

        The register knows nothing about listings, so searching the symbol finds the company
        by luck or not at all — and the wrong company is the failure that matters, because
        every figure downstream would be internally consistent and about another business.
        """
        route = respx_mock.get(url__startswith=SEARCH_URL).mock(
            return_value=_json("ch_search_single.json")
        )

        entity = await client.resolve_entity("ACME", name="ACME HOLDINGS PLC")

        assert route.calls.last.request.url.params["q"] == "ACME HOLDINGS PLC"
        assert entity.identifier == COMPANY_NUMBER
        # The symbol is what the operator commissioned and stays on the entity whatever
        # answered: the search query is how the company was found, not what it is called.
        assert entity.ticker == "ACME"

    async def test_a_company_number_is_looked_up_rather_than_searched(
        self, client, respx_mock
    ) -> None:
        """The escape hatch the ambiguity refusal names, and the reason it is not a dead end."""
        profile = respx_mock.get(PROFILE_URL).mock(return_value=_json("ch_profile.json"))
        search = respx_mock.get(url__startswith=SEARCH_URL).mock(
            return_value=_json("ch_search_ambiguous.json")
        )

        entity = await client.resolve_entity("ACME", name=COMPANY_NUMBER)

        assert entity.identifier == COMPANY_NUMBER
        assert entity.name == "ACME HOLDINGS PLC"
        assert profile.called
        assert not search.called

    async def test_a_short_ticker_is_never_padded_into_a_company_number(
        self, client, respx_mock
    ) -> None:
        """`1234` is a company number only if somebody says it is.

        `normalise_company_number` zero-pads, because `102498` and `00102498` are the same
        company. Applying that to a query would turn a numeric symbol into an unrelated
        entity's number and research a company nobody asked about.
        """
        route = respx_mock.get(url__startswith=SEARCH_URL).mock(
            return_value=_json("ch_search_single.json")
        )

        await client.resolve_entity("1234", name="ACME HOLDINGS PLC")

        assert route.called

    async def test_a_listed_company_resolves_by_its_own_registered_name(
        self, client, respx_mock
    ) -> None:
        """Recorded from the live register on 18 September 2026, and it is why this rule exists.

        `TESCO PLC` matches **nine** active companies — the plc and eight subsidiaries, among
        them `TESCO ATRATO (GP) LIMITED`. Refusing all nine would make every large UK group
        unresearchable by name, and taking the top-ranked one would be the guess this adapter
        refuses to make. Exactly one of the nine is *called* TESCO PLC.
        """
        respx_mock.get(url__startswith=SEARCH_URL).mock(return_value=_json("ch_search_tesco.json"))

        entity = await client.resolve_entity("TSCO", exchange="LSE", name="Tesco plc")

        assert entity.identifier == "00445790"
        assert entity.name == "TESCO PLC"
        assert entity.ticker == "TSCO"

    async def test_an_inexact_name_among_several_is_still_refused(self, client, respx_mock) -> None:
        """The rule narrows the refusal; it does not remove it.

        `TESCO` is not the name of any company in that recording, so nine active candidates
        stay nine, and choosing between them is the operator's to do.
        """
        respx_mock.get(url__startswith=SEARCH_URL).mock(return_value=_json("ch_search_tesco.json"))

        with pytest.raises(ValidationError, match="active companies"):
            await client.resolve_entity("TSCO", name="TESCO")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("00102498", True),
            ("SC123456", True),
            ("sc123456", True),
            (" 00102498 ", True),
            ("102498", False),  # A number, but not yet in the register's own form.
            ("1234", False),
            ("TSCO", False),
            ("ACME HOLD", False),
            ("123456789", False),
            ("SCSC1234", False),
        ],
    )
    def test_what_counts_as_a_company_number_already(self, value, expected) -> None:
        assert looks_like_company_number(value) is expected


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

    async def test_discovery_offers_every_account_filing_with_its_date(
        self, client, respx_mock
    ) -> None:
        """The whole history's accounts, each dated by the register (ADR 0113): nothing
        cuts the list at the run's date any more."""
        respx_mock.get(url__startswith=HISTORY_URL).mock(
            return_value=_json("ch_filing_history.json")
        )

        refs = await client.discover_documents(
            ResolvedEntity(identifier=COMPANY_NUMBER, name="ACME HOLDINGS PLC")
        )

        assert len(refs) == 2
        assert all(ref.publication_date is not None for ref in refs)
        assert any(ref.publication_date > date(2022, 1, 1) for ref in refs)

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


# -- The facts, read out of the accounts themselves ----------------------------------------------


ENTITY: Final = ResolvedEntity(identifier=COMPANY_NUMBER, name="ACME HOLDINGS PLC")

# The two fetchable accounts in the filing-history fixture, newest first.
NEWER_DOCUMENT: Final = f"{DOCUMENT_ROOT}/document/AbCdEf1234/content"
OLDER_DOCUMENT: Final = f"{DOCUMENT_ROOT}/document/GhIjKl5678/content"


def _accounts(payload: bytes) -> httpx.Response:
    return httpx.Response(200, content=payload, headers={"content-type": "application/xhtml+xml"})


class TestFetchingFacts:
    """ADR 0121's load-bearing piece: **Companies House publishes no companyfacts.**

    EDGAR gives a registrant's whole tagged history in one JSON document. The UK register
    gives a filing history and, per filing, a document — so a UK fact base is one fetch and
    one arelle parse per accounting period, and every figure is the output of this platform's
    own extractor rather than of the registry's aggregation. That is the boundary the ADR says
    is worth testing hardest, because a parsing error here is a wrong number with a perfect
    audit trail.
    """

    @pytest.fixture
    def history(self, respx_mock) -> None:
        respx_mock.get(url__startswith=HISTORY_URL).mock(
            return_value=_json("ch_filing_history.json")
        )

    async def test_the_accounts_own_figures_come_back_as_facts(
        self, client, respx_mock, history: None
    ) -> None:
        """Against the fixture's truth set, concept for concept and penny for penny — the
        document's own `scale` applied, as a UK report states its figures in thousands."""
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))

        facts = await client.fetch_facts(ENTITY)

        by_concept = {fact.concept: fact.value for fact in facts}
        for concept, expected in CLEAN_IFRS_TRUTH.items():
            assert by_concept[concept] == expected, concept

    async def test_a_figure_two_filings_both_state_is_two_observations(
        self, client, respx_mock, history: None
    ) -> None:
        """An annual report restates the prior year, and both statements are kept.

        The platform's whole selection story is the latest filing's word on a period with the
        rest recorded as superseded (ADR 0113). Collapsing them here would leave nothing to
        select between, and would take the older restatement as often as the newer one.
        """
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))

        facts = await client.fetch_facts(ENTITY)

        revenues = [fact for fact in facts if fact.concept == "revenue"]
        assert len(revenues) == 2
        assert {fact.accession for fact in revenues} == {
            "MzM1NTk4NDI3NmFkaXF6a2N4",
            "MzA5ODc2NTQzMmFkaXF6a2N4",
        }
        assert {fact.filed_date for fact in revenues} == {date(2022, 10, 14), date(2021, 10, 8)}

    async def test_a_figure_tagged_twice_in_one_filing_is_one_observation(
        self, client, respx_mock, history: None
    ) -> None:
        """A total appears in the primary statement and again in the note that analyses it,
        tagged both times. That is one observation stated twice, and two rows would double
        every count resting on it."""
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(REPEATED_FIGURE))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(b"<html></html>"))

        facts = await client.fetch_facts(ENTITY)

        assert len([fact for fact in facts if fact.concept == "revenue"]) == 1

    async def test_each_fact_names_the_filing_it_was_tagged_in(
        self, client, respx_mock, history: None
    ) -> None:
        """Invariant 1 through a different door: a UK fact traces to the accounts document
        it came from, by the register's own transaction id and filing date."""
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(b"<html></html>"))

        facts = await client.fetch_facts(ENTITY)

        assert {fact.accession for fact in facts} == {"MzM1NTk4NDI3NmFkaXF6a2N4"}
        assert {fact.filed_date for fact in facts} == {date(2022, 10, 14)}
        assert {fact.form for fact in facts} == {"accounts"}

    async def test_a_year_long_duration_is_labelled_and_dated_by_its_own_period(
        self, client, respx_mock, history: None
    ) -> None:
        """ADR 0062's rule, reached through the shared join: an inline document states no
        fiscal period, so the span decides, and a year ending June 2022 is FY2022."""
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))

        facts = await client.fetch_facts(ENTITY)

        revenue = next(fact for fact in facts if fact.concept == "revenue")
        assert revenue.period_start == PERIOD_START
        assert revenue.period_end == PERIOD_END
        assert revenue.fiscal_period == "FY"
        assert revenue.fiscal_year == 2022

    async def test_an_instant_is_not_given_a_fiscal_period(
        self, client, respx_mock, history: None
    ) -> None:
        """A balance-sheet line is a fact about a moment. Labelling it FY would invent a
        duration the document does not state."""
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))

        facts = await client.fetch_facts(ENTITY)

        assets = next(fact for fact in facts if fact.concept == "assets")
        assert assets.period_start is None
        assert assets.fiscal_period is None
        assert assets.fiscal_year is None

    async def test_a_filer_extension_comes_back_carrying_its_own_tag(
        self, client, respx_mock, history: None
    ) -> None:
        """The protocol says unfiltered, and this is the case it is for: UK filers extend the
        taxonomy routinely, and an adapter that dropped an extension here would leave no trace
        of what it discarded — the confirmation gate would be asked about nothing."""
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(WITH_EXTENSION))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(WITH_EXTENSION))

        facts = await client.fetch_facts(ENTITY)

        tag = EXTENSION_TAG.split(":", 1)[1]
        invented = next(fact for fact in facts if fact.raw_concept == tag)
        assert invented.concept == tag, "an unmapped tag is its own concept, as EDGAR's are"
        assert invented.value == 91_204_000

    async def test_the_depth_is_a_stated_number(
        self, credentialled_fetcher, artefact_store, respx_mock, history: None
    ) -> None:
        """Because there is no aggregate, cost is linear in the number of filings opened —
        so it is a number this adapter states rather than a function of the company's age."""
        newer = respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))
        older = respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))
        shallow = CompaniesHouseClient(credentialled_fetcher, store=artefact_store, depth=1)

        await shallow.fetch_facts(ENTITY)

        assert newer.called
        assert not older.called, "the second filing is past the stated depth"

    async def test_a_document_that_will_not_parse_costs_only_its_own_facts(
        self, client, respx_mock, history: None
    ) -> None:
        """One bad year must not take the other three with it: the rest are already fetched
        and hashed, and a UK history is exactly where that matters."""
        respx_mock.get(NEWER_DOCUMENT).mock(return_value=_accounts(b"not a document at all"))
        respx_mock.get(OLDER_DOCUMENT).mock(return_value=_accounts(CLEAN_IFRS))

        facts = await client.fetch_facts(ENTITY)

        assert facts, "the readable filing still yielded its figures"
        assert {fact.accession for fact in facts} == {"MzA5ODc2NTQzMmFkaXF6a2N4"}

    async def test_a_company_with_no_fetchable_accounts_yields_nothing(
        self, client, respx_mock
    ) -> None:
        """Not an error. A company can be on the register with its accounts held only as
        index entries, and an empty fact base is the honest answer to that."""
        respx_mock.get(url__startswith=HISTORY_URL).mock(
            return_value=httpx.Response(
                200,
                content=b'{"total_count": 0, "items": []}',
                headers={"content-type": "application/json"},
            )
        )

        assert await client.fetch_facts(ENTITY) == ()

    def test_the_adapter_now_satisfies_the_protocol(self, client) -> None:
        """It had two thirds of `SourceAdapter` and not the third, which is why `acquire`
        could name only the SEC client."""
        assert isinstance(client, SourceAdapter)


class TestAskingForTheTaggedCopy:
    """One filing, two representations, and the register serves the wrong one by default.

    Measured against the live register on 18 September 2026 (ADR 0127). A small company's
    accounts came back as a 20 KB PDF with no extractable text by default, and as 19.6 KB of
    inline XBRL carrying eight facts when asked for by type — so without the header the fact
    extractor was being handed the wrong document every time.
    """

    @pytest.fixture
    def ref(self) -> DocumentRef:
        return DocumentRef(
            url=document_url("abc"),
            title="Accounts — ACME HOLDINGS PLC",
            publication_date=date(2026, 7, 25),
            form="accounts",
            accession="transaction-1",
        )

    async def test_the_tagged_representation_is_asked_for(self, client, respx_mock, ref) -> None:
        route = respx_mock.get(ref.url).mock(
            return_value=httpx.Response(
                200, content=b"<html/>", headers={"content-type": "application/xhtml+xml"}
            )
        )

        await client.fetch_document(ref)

        assert route.calls[0].request.headers["accept"] == "application/xhtml+xml"

    async def test_the_scan_can_still_be_asked_for_explicitly(
        self, client, respx_mock, ref
    ) -> None:
        """A caller that wants the document as filed says so, and gets whatever is served."""
        route = respx_mock.get(ref.url).mock(
            return_value=httpx.Response(
                200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
            )
        )

        await client.fetch_document(ref, tagged=False)

        # httpx sends its own `*/*` when nothing is asked for; what matters is that the
        # request does not narrow itself to a representation this filing may not have.
        assert route.calls[0].request.headers["accept"] == "*/*"

    async def test_a_filing_with_no_tagged_copy_costs_its_facts_and_nothing_else(
        self, client, respx_mock
    ) -> None:
        """406 is the register saying "this filing is not tagged", in one round trip.

        The alternative it is spared: 14 MB of scanned pages that yield nothing. A listed
        company's history is entirely this case, so it must not read as an error.
        """
        respx_mock.get(url__startswith=f"{API_ROOT}/company/").mock(
            return_value=_json("ch_filing_history_tesco.json")
        )
        respx_mock.get(url__startswith=f"{DOCUMENT_ROOT}/document/").mock(
            return_value=httpx.Response(
                406,
                content=b'{"errors":[{"type":"ch:service"}]}',
                headers={"content-type": "application/json"},
            )
        )
        entity = ResolvedEntity(identifier="00445790", name="TESCO PLC", ticker="TSCO")

        assert await client.fetch_facts(entity) == ()


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

    async def test_it_does_not_follow_the_register_to_its_object_store(
        self, credentialled_fetcher, respx_mock
    ) -> None:
        """**The one that was measured rather than imagined** (ADR 0127).

        The register redirects a document download to a pre-signed object-store URL, and the
        header had always been attached by provider — which was sound while every admitted
        host was one of the provider's own. On the first real document fetch the key went to
        Amazon S3, which answered 400 *quoting the header back*, and the fetcher archived the
        reply: a credential in a third party's error log and in this platform's own store.
        """
        content = f"{DOCUMENT_ROOT}/document/abc/content"
        store_url = "https://s3.eu-west-2.amazonaws.com/ch-document-store/abc?X-Amz-Signature=x"
        first = respx_mock.get(content).mock(
            return_value=httpx.Response(302, headers={"location": store_url})
        )
        second = respx_mock.get(store_url).mock(
            return_value=httpx.Response(200, content=b"%PDF-1.7 accounts")
        )

        await credentialled_fetcher.fetch(content, provider=Provider.COMPANIES_HOUSE)

        assert first.calls[0].request.headers["authorization"] == basic_auth_header(API_KEY)
        assert "authorization" not in second.calls[0].request.headers

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
