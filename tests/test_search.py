"""The search bar on every page (02 §3's command bar; ADR 0112, amended 28 September 2026).

Two promises, in order: it jumps to a company the account's record names exactly, and every
answer ends with the request form for what was typed. Between them it lists the companies a
query might mean, from the Companies list's own record — so it finds what that page shows,
and nothing another operator researched.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import UserRole
from aer.db.models import Company, User, WatchlistEntry
from aer.web.companies.pages import research_href
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.request_fixtures import research_request

AS_OF = date(2026, 6, 30)


def _researched(user: User, company: Company) -> Any:
    """A request that resolved to ``company``: how a company enters an account's record."""
    request = research_request(
        user_id=user.id,
        company_name=company.name,
        ticker=company.ticker,
        exchange=company.exchange,
        as_of_date=AS_OF,
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
        portfolio_context={},
    )
    request.company_id = company.id
    return request


@pytest.fixture
async def scene(db_engine: Any) -> Any:
    """Two researched companies, one followed listing, and a stranger's company.

    The stranger is committed in a transaction of its own, after the operator's, so the
    operator is unambiguously the first user and the one every request is made as.
    """
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        inc = Company(name="Fabrikam Inc", ticker="FBRK", exchange="NYSE", cik="0000000009")
        holdings = Company(
            name="Fabrikam Holdings", ticker="FBKH", exchange="NYSE", cik="0000000010"
        )
        session.add_all([user, inc, holdings])
        await session.flush()
        session.add_all(
            [
                _researched(user, inc),
                _researched(user, holdings),
                WatchlistEntry(
                    user_id=user.id,
                    company_name="Wingtip Toys",
                    ticker="WING",
                    exchange="NASDAQ",
                    why="A toy maker with a moat, maybe.",
                    cadence="quarterly",
                ),
            ]
        )
        await session.commit()
    async with factory() as session:
        stranger = User(
            email="stranger@example.invalid", display_name="Stranger", role=UserRole.OWNER
        )
        northwind = Company(
            name="Northwind Traders", ticker="NWND", exchange="NASDAQ", cik="0000000011"
        )
        session.add_all([stranger, northwind])
        await session.flush()
        session.add(_researched(stranger, northwind))
        await session.commit()
    yield {"inc": inc, "holdings": holdings, "northwind": northwind}
    await delete_all(db_engine)


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, scene: dict[str, Any]) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


@pytest.mark.integration
class TestItJumpsToACompanyItNames:
    async def test_a_ticker_opens_the_company(self, api: Any, scene: dict[str, Any]) -> None:
        answer = await api.get("/search", params={"q": "FBRK"}, follow_redirects=False)

        assert answer.status_code == 303
        assert answer.headers["location"] == f"/companies/{scene['inc'].id}"

    async def test_a_whole_name_in_any_case_opens_the_company(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        answer = await api.get("/search", params={"q": "  fabrikam   INC "}, follow_redirects=False)

        assert answer.status_code == 303
        assert answer.headers["location"] == f"/companies/{scene['inc'].id}"


@pytest.mark.integration
class TestItListsWhatAQueryMightMean:
    async def test_part_of_a_name_lists_every_company_it_is_in(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/search", params={"q": "fabrikam"})

        assert page.status_code == 200
        assert f'href="/companies/{scene["inc"].id}"' in page.text
        assert f'href="/companies/{scene["holdings"].id}"' in page.text
        assert "2 companies might be “fabrikam”" in page.text

    async def test_a_listing_the_platform_never_resolved_is_listed_not_jumped_to(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        # There is no company page to jump to, so the answer is the row that knows it.
        page = await api.get("/search", params={"q": "WING"}, follow_redirects=False)

        assert page.status_code == 200
        assert "Wingtip Toys" in page.text
        assert 'href="/watchlist"' in page.text

    async def test_another_operators_company_is_not_in_the_answer(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/search", params={"q": "NWND"}, follow_redirects=False)

        assert page.status_code == 200
        assert "Nothing in your record is called “NWND”" in page.text
        assert f"/companies/{scene['northwind'].id}" not in page.text


@pytest.mark.integration
class TestEveryAnswerEndsWithTheRequestForm:
    async def test_a_ticker_nobody_knows_is_offered_as_a_ticker(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/search", params={"q": "FTNT"})

        assert _research_link(page.text) == "/requests/new?ticker=FTNT"

    async def test_a_name_nobody_knows_is_offered_as_a_name(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/search", params={"q": "Contoso Pharmaceuticals"})

        assert _research_link(page.text) == "/requests/new?company_name=Contoso+Pharmaceuticals"

    async def test_an_empty_search_offers_the_blank_form(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/search")

        assert page.status_code == 200
        assert _research_link(page.text) == "/requests/new"

    async def test_the_form_arrives_filled_in(self, api: Any, scene: dict[str, Any]) -> None:
        page = (await api.get("/requests/new", params={"ticker": "FTNT"})).text

        assert re.search(r'name="ticker"\s+type="text"\s+value="FTNT"', page)

    async def test_only_the_company_can_be_filled_in(self, api: Any, scene: dict[str, Any]) -> None:
        # A link may say which company; it may not set what the report is allowed to cost.
        page = (await api.get("/requests/new", params={"max_cost_gbp": "999"})).text

        assert 'value="999"' not in page

    async def test_what_is_filled_in_is_text_and_never_markup(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = (
            await api.get("/requests/new", params={"company_name": '"><script>x()</script>'})
        ).text

        assert "<script>x()</script>" not in page
        assert "&lt;script&gt;" in page


class TestTheRequestLink:
    @pytest.mark.parametrize(
        ("query", "href"),
        [
            ("", "/requests/new"),
            ("BRK.B", "/requests/new?ticker=BRK.B"),
            ("rr.", "/requests/new?ticker=rr."),
            ("Rolls-Royce Holdings", "/requests/new?company_name=Rolls-Royce+Holdings"),
            ("A&B", "/requests/new?company_name=A%26B"),
        ],
    )
    def test_the_query_goes_where_the_operator_would_have_typed_it(
        self, query: str, href: str
    ) -> None:
        assert research_href(query) == href


def _research_link(html: str) -> str:
    found = re.search(r'href="([^"]+)"\s+id="research-query"', html)
    assert found is not None, "the page offered no request form"
    return found.group(1).replace("&amp;", "&")
