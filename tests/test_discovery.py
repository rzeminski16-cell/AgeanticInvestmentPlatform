"""Finding more than one document: EDGAR full-text search, and an issuer's own site.

Two adapters with opposite risk profiles, which is why they are tested together.

**EDGAR full-text search** returns identifiers the SEC issued, and the URL is built from them
here. The tests that matter are the ones asserting the response's own strings never become a
URL, and that a hit published after the as-of date is *reported as excluded* rather than
silently missing.

**Issuer-IR discovery** is the first adapter whose candidate URLs come out of untrusted content.
A page can link anywhere, so most of :class:`TestWhatIsRefused` is about links that must not
become fetches — a different host, a lookalike host, a `data:` URI with no host at all, and a
``<base href>`` pointing elsewhere, which is the obvious way to make every relative link on a
page resolve off-domain.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

import httpx
import pytest
import respx

from aer.core.enums import Provider, RequestStatus, SourceTier, UserRole
from aer.db.models import ResearchRequest, User
from aer.errors import ValidationError
from aer.fetch.client import SafeFetcher
from aer.fetch.errors import RobotsDisallowedError, UrlNotAllowedError
from aer.fetch.policy import host_matches, policy_for
from aer.fetch.robots import RobotsCache
from aer.services.acquisition import record_acquisition
from aer.services.sources import PUBLISHED_AFTER_AS_OF
from aer.sources.issuer import PROVIDER, SOURCE_TIER, Rejection, discover_documents
from aer.sources.sec.client import SecEdgarClient
from aer.sources.sec.fulltext import (
    FULL_TEXT_SEARCH_URL,
    build_search_url,
    parse_search_results,
)
from aer.sources.tiering import DocumentKind
from tests.fetch_fixtures import public_resolver
from tests.issuer_fixtures import IR_HOST, IR_PAGE, IR_PAGE_URL, IR_WITH_HOSTILE_BASE
from tests.sec_fixtures import MSFT_CIK, fixture_bytes

pytestmark = pytest.mark.usefixtures("no_real_sockets")

AS_OF = date(2022, 7, 31)


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


def _results():
    return parse_search_results(fixture_bytes("fulltext_msft.json"))


# -- EDGAR full-text search ----------------------------------------------------------------------


class TestParsingSearchResults:
    def test_it_reads_every_usable_hit(self) -> None:
        results = _results()

        assert len(results.hits) == 4
        assert results.total == 137

    def test_the_total_is_the_index_count_not_the_page_size(self) -> None:
        """ "The top four of a hundred and thirty-seven" and "all four of them" are different
        situations, and a reviewer should be able to tell which they are looking at."""
        results = _results()

        assert results.total > len(results.hits)

    def test_the_url_is_built_from_identifiers(self) -> None:
        """The CIK is un-padded in an archive path and the accession has its dashes stripped —
        two conventions that differ from everywhere else EDGAR uses the same identifiers."""
        first = _results().hits[0]

        assert first.url == (
            "https://www.sec.gov/Archives/edgar/data/789019/000078901922000010/"
            "msft-10k_20220630.htm"
        )

    def test_no_url_is_taken_from_the_response(self) -> None:
        """**The control.** The response is untrusted content; a hit carrying its own URL would
        let whatever EDGAR indexed choose what this platform fetches next.

        Asserted by poisoning the response with URL-shaped fields under every plausible name and
        checking none of them reaches the constructed URL.
        """
        poisoned = fixture_bytes("fulltext_msft.json").replace(
            b'"sequence": "1",',
            b'"sequence": "1", "url": "https://attacker.test/x.htm",'
            b' "_url": "https://attacker.test/y.htm",'
            b' "file_url": "https://attacker.test/z.htm",',
        )

        results = parse_search_results(poisoned)

        assert results.hits
        for hit in results.hits:
            assert hit.url.startswith("https://www.sec.gov/Archives/edgar/data/")
            assert "attacker.test" not in hit.url

    def test_a_malformed_hit_is_skipped_not_fatal(self) -> None:
        """EDGAR's index carries entries with fields missing. Failing the whole search over the
        ninth of ten results would lose eight good ones."""
        broken = fixture_bytes("fulltext_msft.json").replace(
            b'"_id": "0000789019-22-000005:msft-10q_20220331.htm"', b'"_id": "no-colon-here"'
        )

        results = parse_search_results(broken)

        assert len(results.hits) == 3

    def test_a_hit_with_no_date_is_skipped(self) -> None:
        """A document with no filing date cannot be point-in-time checked, so it cannot be
        acquired under the rules — dropping it here beats admitting it undated."""
        undated = fixture_bytes("fulltext_msft.json").replace(
            b'"file_date": "2021-07-29"', b'"x": 0'
        )

        assert len(parse_search_results(undated).hits) == 3

    @pytest.mark.parametrize(
        "payload",
        [b"<html>not json</html>", b"{}", b'{"hits": []}', b'{"error": "rate limited"}'],
        ids=["html error page", "empty object", "hits is a list", "an error body"],
    )
    def test_a_response_that_is_not_a_search_result_raises(self, payload: bytes) -> None:
        """Distinct from "no results". An error page, a login redirect or a changed API should
        not look like a company that has never mentioned the phrase."""
        with pytest.raises(ValidationError):
            parse_search_results(payload)

    def test_a_search_with_no_matches_is_not_an_error(self) -> None:
        empty = b'{"hits": {"total": {"value": 0}, "hits": []}}'

        results = parse_search_results(empty)

        assert results.hits == ()
        assert results.total == 0


class TestPointInTimeOnSearchResults:
    def test_a_post_dated_hit_is_excluded_and_reported(self) -> None:
        """**Reported, not dropped.** A search that found relevant material and a search that
        found nothing need different responses: the first means "this exists and you may not use
        it yet", the second means "look elsewhere"."""
        usable, excluded = _results().admissible(AS_OF)

        assert len(usable) == 3
        assert len(excluded) == 1
        assert excluded[0].filed == date(2022, 10, 25)

    def test_nothing_is_excluded_without_an_as_of_date(self) -> None:
        usable, excluded = _results().admissible(None)

        assert len(usable) == 4
        assert excluded == ()

    def test_a_hit_filed_on_the_as_of_date_is_usable(self) -> None:
        """The boundary, again: published *on* the as-of date is admissible."""
        usable, excluded = _results().admissible(date(2022, 7, 28))

        assert any(hit.filed == date(2022, 7, 28) for hit in usable)
        assert all(hit.filed > date(2022, 7, 28) for hit in excluded)

    def test_the_split_keeps_every_hit(self) -> None:
        results = _results()
        usable, excluded = results.admissible(AS_OF)

        assert len(usable) + len(excluded) == len(results.hits)


class TestBuildingTheSearchUrl:
    def test_the_phrase_is_quoted_into_an_exact_match(self) -> None:
        """A bare set of words matches documents containing all of them anywhere, which for a
        filing means nearly every document."""
        url = build_search_url("segment reporting")

        assert "q=%22segment+reporting%22" in url

    def test_it_scopes_to_one_filer(self) -> None:
        """An unscoped search returns other companies' filings, and acquiring one would mean
        citing a competitor's document for this company's figures."""
        url = build_search_url("revenue", cik=MSFT_CIK)

        assert f"ciks={MSFT_CIK}" in url

    def test_it_bounds_the_query_by_date(self) -> None:
        url = build_search_url("revenue", start_date=date(2021, 1, 1), end_date=AS_OF)

        assert "startdt=2021-01-01" in url
        assert "enddt=2022-07-31" in url
        assert "dateRange=custom" in url

    def test_it_filters_by_form(self) -> None:
        url = build_search_url("revenue", forms=["10-K", "10-Q"])

        assert "forms=10-K%2C10-Q" in url

    def test_an_empty_phrase_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="needs a phrase"):
            build_search_url("   ")

    def test_a_backwards_date_range_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="runs backwards"):
            build_search_url("revenue", start_date=AS_OF, end_date=date(2021, 1, 1))

    def test_the_endpoint_is_on_the_sec_allowlist(self) -> None:
        """`efts.sec.gov` is a different host from `www.sec.gov`; both are covered by the
        `.sec.gov` entry, and this is the assertion that says so."""
        host = urlsplit(FULL_TEXT_SEARCH_URL).hostname or ""
        policy = policy_for(Provider.SEC_EDGAR)

        assert any(host_matches(host, pattern) for pattern in policy.allowed_hosts)


@pytest.mark.usefixtures("no_real_sockets")
class TestSearchingThroughTheClient:
    async def test_it_fetches_parses_and_archives(
        self, fetcher: SafeFetcher, artefact_store, sleeper
    ) -> None:
        client = SecEdgarClient(fetcher, store=artefact_store, sleep=sleeper)

        with respx.mock(assert_all_called=True) as mock:
            mock.get(url__startswith=FULL_TEXT_SEARCH_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=fixture_bytes("fulltext_msft.json"),
                    headers={"content-type": "application/json"},
                )
            )
            response = await client.search_full_text("segment revenue", cik=MSFT_CIK)

        assert len(response.data.hits) == 4
        assert response.sha256, "the response was archived like any other fetch"

    async def test_the_as_of_date_bounds_the_query_that_is_sent(
        self, fetcher: SafeFetcher, artefact_store, sleeper
    ) -> None:
        """A courtesy to EDGAR and a saving, not the control — the hits are still checked after
        parsing. Asserted on the request that actually went out."""
        client = SecEdgarClient(fetcher, store=artefact_store, sleep=sleeper)

        with respx.mock(assert_all_called=True) as mock:
            route = mock.get(url__startswith=FULL_TEXT_SEARCH_URL).mock(
                return_value=httpx.Response(
                    200,
                    content=fixture_bytes("fulltext_msft.json"),
                    headers={"content-type": "application/json"},
                )
            )
            await client.search_full_text("revenue", cik=MSFT_CIK, as_of_date=AS_OF)

        assert "enddt=2022-07-31" in str(route.calls[0].request.url)


# -- The issuer's own site -------------------------------------------------------------------------


class TestWhatIsFound:
    def test_it_finds_documents_named_in_the_link_text(self) -> None:
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        titles = {doc.title for doc in found.documents}
        assert "Annual Report 2022" in titles
        assert "Q4 earnings call transcript" in titles

    def test_it_finds_documents_named_only_in_the_url(self) -> None:
        """IR sites split evenly between linking `Annual Report 2022` to `/media/12345.pdf` and
        linking `Download` to `/annual-report-2022.pdf`."""
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert any("interim-report-2022.pdf" in doc.url for doc in found.documents)

    def test_a_bare_pdf_from_an_ir_page_is_a_document(self) -> None:
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert any(doc.url.endswith("/media/8912.pdf") for doc in found.documents)

    def test_relative_links_resolve_against_the_page(self) -> None:
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert all(doc.url.startswith(f"https://{IR_HOST}/") for doc in found.documents)

    def test_a_date_in_the_link_text_is_kept(self) -> None:
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        dated = [doc for doc in found.documents if doc.publication_date is not None]
        assert len(dated) == 1
        assert dated[0].publication_date == date(2022, 7, 28)

    def test_most_documents_have_no_date_and_that_is_recorded(self) -> None:
        """An undated document is quarantined under point-in-time rules. A date invented from a
        URL slug would be worse than none, because it would pass the check."""
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert any(doc.publication_date is None for doc in found.documents)

    def test_the_same_document_twice_is_one_candidate(self) -> None:
        """Two links differing only after the `#` are one document, and fetching both would
        archive the same bytes twice under two provenance rows saying different things."""
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        finals = [doc for doc in found.documents if "ar-final.pdf" in doc.url]
        assert len(finals) == 1
        assert "#" not in finals[0].url

    def test_everything_found_is_tier_two_issuer_material(self) -> None:
        assert PROVIDER is Provider.ISSUER_IR
        assert SOURCE_TIER is SourceTier.T2_ISSUER

    def test_a_press_release_is_classified_as_marketing(self) -> None:
        """Not an accounting document, and treating it as one is how a forward-looking claim
        ends up cited as a fact."""
        page = (
            b'<html><body><a href="/news/partnership-press-release">'
            b"Press release: new partnership</a></body></html>"
        )

        found = discover_documents(page, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert found.documents[0].kind is DocumentKind.ISSUER_MARKETING

    def test_the_candidate_count_is_bounded(self) -> None:
        """A page that yields two hundred links is a site map, and taking all of them would
        spend a run's budget on navigation."""
        many = (
            b"<html><body>"
            + b"".join(
                f'<a href="/media/report-{n}.pdf">Annual Report {n}</a>'.encode()
                for n in range(200)
            )
            + b"</body></html>"
        )

        found = discover_documents(many, page_url=IR_PAGE_URL, allowed_host=IR_HOST, limit=5)

        assert len(found.documents) == 5


class TestWhatIsRefused:
    """The links that must not become fetches.

    An IR page is the first content in the platform whose links become candidate URLs, so this
    is where a mistake turns into a request to somewhere nobody chose.
    """

    def test_a_link_to_another_host_is_refused(self) -> None:
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        off = {link.url for link in found.off_domain()}
        assert "https://cdn.other-domain.test/reports/ar-2022.pdf" in off
        assert all("other-domain.test" not in doc.url for doc in found.documents)

    def test_a_lookalike_host_is_refused(self) -> None:
        """`endswith` on the bare domain admits `evil-investors.example-issuer.test`, which is a
        host an attacker can register."""
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert all(f"evil-{IR_HOST}" not in doc.url for doc in found.documents)
        assert any(f"evil-{IR_HOST}" in link.url for link in found.off_domain())

    @pytest.mark.parametrize("scheme", ["mailto:", "javascript:", "data:"])
    def test_a_link_that_is_not_a_fetch_is_refused(self, scheme: str) -> None:
        """`data:` matters most: it has no host to check, which is the appeal of it."""
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        rejected = {
            link.url for link in found.rejected if link.reason is Rejection.UNSUPPORTED_SCHEME
        }
        assert any(url.startswith(scheme) for url in rejected)
        assert all(not doc.url.startswith(scheme) for doc in found.documents)

    def test_a_base_href_pointing_elsewhere_is_ignored(self) -> None:
        """**The subtle one.** Honouring `<base href>` would make every relative link on the page
        resolve to the attacker's domain, and each would then pass a host check against a host
        the page chose. Resolution uses the URL the fetch actually used.
        """
        found = discover_documents(IR_WITH_HOSTILE_BASE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert found.documents
        assert all(doc.url.startswith(f"https://{IR_HOST}/") for doc in found.documents)
        assert all("attacker.test" not in doc.url for doc in found.documents)

    def test_an_ordinary_page_link_is_not_a_document(self) -> None:
        """Fetching every link would spend a run's budget on a cookie policy."""
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert all("/careers" not in doc.url for doc in found.documents)
        assert all("/governance/board" not in doc.url for doc in found.documents)

    def test_rejections_are_recorded_rather_than_dropped(self) -> None:
        """ "The IR page had nothing on it" and "the IR page linked to forty documents on a CDN
        we may not read" are different situations, and only one is the operator's to fix."""
        found = discover_documents(IR_PAGE, page_url=IR_PAGE_URL, allowed_host=IR_HOST)

        assert {link.reason for link in found.rejected} == {
            Rejection.OFF_DOMAIN,
            Rejection.UNSUPPORTED_SCHEME,
            Rejection.NOT_A_DOCUMENT,
        }


@pytest.mark.usefixtures("no_real_sockets")
class TestTheFetchLayerIsTheControl:
    """Discovery's host check is the cheap one. This is the one that counts.

    Every one of these would still hold with `aer.sources.issuer` deleted, which is the property
    worth having: the allowlist is enforced by the component that owns it, not by whichever
    adapter happened to build the URL.
    """

    async def test_an_issuer_domain_not_supplied_is_refused(self, fetcher: SafeFetcher) -> None:
        """`ISSUER_IR` has an empty standing allowlist, so *everything* is refused unless the
        operator names the host for this request. That default is the whole design: an issuer's
        hosts are not knowable in advance, and an allowlist that guessed would be a blocklist."""
        with pytest.raises(UrlNotAllowedError):
            await fetcher.fetch(
                f"https://{IR_HOST}/media/2022/ar-final.pdf", provider=Provider.ISSUER_IR
            )

    async def test_the_supplied_domain_is_admitted_for_this_request_only(
        self, fetcher: SafeFetcher
    ) -> None:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(f"https://{IR_HOST}/media/2022/ar-final.pdf").mock(
                return_value=httpx.Response(
                    200, content=b"%PDF-1.7\n", headers={"content-type": "application/pdf"}
                )
            )
            result = await fetcher.fetch(
                f"https://{IR_HOST}/media/2022/ar-final.pdf",
                provider=Provider.ISSUER_IR,
                extra_hosts=(IR_HOST,),
            )

        assert result.sha256

        # And naming one host does not widen the allowlist for another.
        with pytest.raises(UrlNotAllowedError):
            await fetcher.fetch(
                "https://cdn.other-domain.test/reports/ar-2022.pdf",
                provider=Provider.ISSUER_IR,
                extra_hosts=(IR_HOST,),
            )

    async def test_a_page_robots_disallows_is_not_fetched(
        self, fetch_settings, artefact_store, limiter, breaker, sleeper, redis_client
    ) -> None:
        """Reading a company's website is crawling, and a company's stated wishes about crawling
        apply — unlike the regulator APIs, where access is by a documented contract."""

        async def robots(_url: str) -> str:
            return "User-agent: *\nDisallow: /media/\n"

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

        with pytest.raises(RobotsDisallowedError):
            await guarded.fetch(
                f"https://{IR_HOST}/media/2022/ar-final.pdf",
                provider=Provider.ISSUER_IR,
                extra_hosts=(IR_HOST,),
            )

    async def test_robots_is_honoured_for_issuers_unlike_the_regulator_apis(self) -> None:
        """Stated as a test because it is a per-provider decision that is easy to get wrong in
        either direction."""
        assert policy_for(Provider.ISSUER_IR).honours_robots
        assert not policy_for(Provider.SEC_EDGAR).honours_robots


# -- The acceptance criterion ----------------------------------------------------------------------


@pytest.fixture
async def research_request(db_session) -> ResearchRequest:
    """A point-in-time request, as-of the date the fixtures are built around."""
    user = User(email="discovery@example.invalid", display_name="Discovery", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    row = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF,
        base_currency="USD",
        investment_horizon_months=36,
        max_cost_gbp="2.00",
        portfolio_context={},
        point_in_time=True,
        status=RequestStatus.DRAFT,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.integration
@pytest.mark.usefixtures("no_real_sockets")
class TestARunAcquiresMoreThanOneDocument:
    """§ *"A run acquires ≥3 documents of ≥2 kinds, all hash-addressed and replayable offline."*

    The whole objective of this task in one test. Before it, a run read a single 10-K.
    """

    async def test_a_run_acquires_three_documents_of_two_kinds(
        self,
        db_session,
        fetcher: SafeFetcher,
        artefact_store,
        research_request: ResearchRequest,
    ) -> None:
        filing_url = (
            "https://www.sec.gov/Archives/edgar/data/789019/000078901922000010/"
            "msft-10k_20220630.htm"
        )
        prior_url = (
            "https://www.sec.gov/Archives/edgar/data/789019/000078901921000007/"
            "msft-10k_20210630.htm"
        )
        ir_url = f"https://{IR_HOST}/media/2022/ar-final.pdf"

        acquisitions = []
        with respx.mock(assert_all_called=True) as mock:
            mock.get(filing_url).mock(
                return_value=httpx.Response(
                    200,
                    content=b"<html><body><p>Revenue was $198,270 million.</p></body></html>",
                    headers={"content-type": "text/html"},
                )
            )
            mock.get(prior_url).mock(
                return_value=httpx.Response(
                    200,
                    content=b"<html><body><p>Revenue was $168,088 million.</p></body></html>",
                    headers={"content-type": "text/html"},
                )
            )
            mock.get(ir_url).mock(
                return_value=httpx.Response(
                    200,
                    content=b"%PDF-1.7\nannual report\n",
                    headers={"content-type": "application/pdf"},
                )
            )

            for url, provider, tier, published in (
                (filing_url, Provider.SEC_EDGAR, SourceTier.T1_REGULATORY, date(2022, 7, 28)),
                (prior_url, Provider.SEC_EDGAR, SourceTier.T1_REGULATORY, date(2021, 7, 29)),
                (ir_url, Provider.ISSUER_IR, SourceTier.T2_ISSUER, date(2022, 7, 28)),
            ):
                extra = (IR_HOST,) if provider is Provider.ISSUER_IR else ()
                result = await fetcher.fetch(url, provider=provider, extra_hosts=extra)
                acquisitions.append(
                    await record_acquisition(
                        db_session,
                        artefact_store,
                        request=research_request,
                        result=result,
                        provider=provider,
                        source_tier=tier,
                        publication_date=published,
                    )
                )

        assert len(acquisitions) >= 3

        # Two kinds, by the tier that decides what each may support.
        tiers = {a.source_document.source_tier for a in acquisitions}
        assert len(tiers) >= 2
        assert {SourceTier.T1_REGULATORY, SourceTier.T2_ISSUER} <= tiers

        # Hash-addressed, and every hash distinct because every document is.
        digests = [a.sha256 for a in acquisitions]
        assert all(digests)
        assert len(set(digests)) == len(digests)

        # None of them refused: all are dated on or before the as-of date.
        assert not any(a.quarantined for a in acquisitions)

    async def test_every_acquired_document_is_replayable_from_the_store(
        self,
        db_session,
        fetcher: SafeFetcher,
        artefact_store,
        research_request: ResearchRequest,
    ) -> None:
        """ "Replayable offline" stated as the property it is: the bytes come back from the
        store by hash, with no network, and the store verifies the digest as it reads."""
        url = f"https://{IR_HOST}/media/2022/ar-final.pdf"
        body = b"%PDF-1.7\nthe annual report\n"

        with respx.mock(assert_all_called=True) as mock:
            mock.get(url).mock(
                return_value=httpx.Response(
                    200, content=body, headers={"content-type": "application/pdf"}
                )
            )
            result = await fetcher.fetch(url, provider=Provider.ISSUER_IR, extra_hosts=(IR_HOST,))

        acquisition = await record_acquisition(
            db_session,
            artefact_store,
            request=research_request,
            result=result,
            provider=Provider.ISSUER_IR,
            source_tier=SourceTier.T2_ISSUER,
            publication_date=date(2022, 7, 28),
        )

        # No respx mock in scope, and `no_real_sockets` is active: this cannot reach a network.
        replayed = await artefact_store.read(acquisition.sha256)

        assert replayed == body

    async def test_a_post_dated_search_result_is_quarantined_rather_than_dropped(
        self,
        db_session,
        fetcher: SafeFetcher,
        artefact_store,
        research_request: ResearchRequest,
    ) -> None:
        """The hit EDGAR returned that the point-in-time rule refuses.

        It is fetched, hashed and recorded — and marked inadmissible. "We saw this and refused
        to use it" is a more useful audit trail than a document that silently never appears,
        and a reviewer asking why a search seemed to find nothing gets an answer.
        """
        _, excluded = _results().admissible(AS_OF)
        assert excluded, "the fixture must contain a post-dated hit"
        hit = excluded[0]

        with respx.mock(assert_all_called=True) as mock:
            mock.get(hit.url).mock(
                return_value=httpx.Response(
                    200,
                    content=b"<html><body>A later quarter.</body></html>",
                    headers={"content-type": "text/html"},
                )
            )
            result = await fetcher.fetch(hit.url, provider=Provider.SEC_EDGAR)

        acquisition = await record_acquisition(
            db_session,
            artefact_store,
            request=research_request,
            result=result,
            provider=Provider.SEC_EDGAR,
            source_tier=SourceTier.T1_REGULATORY,
            publication_date=hit.filed,
        )

        assert acquisition.quarantined
        assert acquisition.source_document.quarantine_reason == PUBLISHED_AFTER_AS_OF
        assert acquisition.sha256, "refused, and still archived"
        assert not acquisition.source_document.is_admissible
