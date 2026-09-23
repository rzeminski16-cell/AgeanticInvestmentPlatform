"""The three surfaces that were absent (page specification §3, §4, §5).

Companies, the company page and the position page, opened against a committed scene the
application's own connections can see: one listing held and never researched, one company
researched, declined with a reason and never owned, and one listing followed that the
platform has never resolved.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import DecisionAction, JobStatus, TransactionKind, UserRole
from aer.db.models import (
    Company,
    Job,
    Portfolio,
    PriceBar,
    Report,
    Security,
    User,
    WatchlistEntry,
)
from aer.services import decisions as decision_service
from aer.services import theses as thesis_service
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.portfolio_fixtures import trade
from tests.request_fixtures import research_request

AS_OF = date(2026, 6, 30)
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
FOLLOWED_ON = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
async def scene(db_engine: Any) -> Any:
    """The record on a session of its own, so what it commits reaches the app."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="owner@example.invalid", display_name="Owner", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        book = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
        contoso = Company(
            name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
        )
        fabrikam = Company(name="Fabrikam Inc", ticker="FBRK", exchange="NYSE", cik="0000000009")
        session.add_all([book, contoso, fabrikam])
        await session.flush()
        # Held and never researched, quoted in pounds so the book needs no rate.
        security = Security(
            company_id=contoso.id,
            ticker="CTSO",
            exchange="LSE",
            provider_symbol="CTSO.LSE",
            name="Contoso plc",
            quote_currency="GBP",
        )
        session.add(security)
        await session.flush()
        session.add_all(
            [
                PriceBar(
                    security_id=security.id,
                    bar_date=AS_OF - timedelta(days=1),
                    open=Decimal("12"),
                    high=Decimal("12"),
                    low=Decimal("12"),
                    close=Decimal("12"),
                ),
                PriceBar(
                    security_id=security.id,
                    bar_date=AS_OF,
                    open=Decimal("12.4"),
                    high=Decimal("12.6"),
                    low=Decimal("12.3"),
                    close=Decimal("12.5"),
                ),
            ]
        )
        holdings = {"portfolio": book, "document": None}
        await trade(
            session,
            holdings,
            kind=TransactionKind.DEPOSIT,
            quantity="5000",
            price=None,
            currency="GBP",
            on=date(2026, 6, 1),
        )
        await trade(
            session,
            holdings,
            security=security,
            quantity="100",
            price="10",
            currency="GBP",
            on=date(2026, 6, 15),
        )
        # Researched, declined with a reason, never owned.
        request = research_request(
            user_id=user.id,
            company_name="Fabrikam Inc",
            ticker="FBRK",
            exchange="NYSE",
            as_of_date=AS_OF,
            base_currency="USD",
            investment_horizon_months=12,
            max_cost_gbp="2.50",
            portfolio_context={},
        )
        request.company_id = fabrikam.id
        session.add(request)
        await session.flush()
        job = Job(
            work_order_id=request.id,
            workflow_version="vertical_slice_v1",
            code_version="pagesseed12345",
            status=JobStatus.SUCCEEDED,
            started_at=NOW - timedelta(days=2),
            finished_at=NOW - timedelta(days=2, hours=-1),
            total_cost_gbp=Decimal("3.20"),
        )
        session.add(job)
        await session.flush()
        report = Report(
            job_id=job.id,
            request_id=request.id,
            company_id=fabrikam.id,
            as_of_date=AS_OF,
            content={"markdown": "approved"},
            content_hash="c" * 64,
            approved_at=NOW - timedelta(days=1),
            immutable=True,
        )
        session.add(report)
        thesis = await thesis_service.write_thesis(
            session, user=user, company=fabrikam, title="Fabrikam is priced for perfection"
        )
        await decision_service.record_decision(
            session,
            actor=user,
            thesis=thesis,
            action=DecisionAction.PASS,
            statement="Pass at this price.",
            basis="Priced for perfection.",
            decided_at=NOW - timedelta(hours=1),
        )
        # Followed, and never resolved by a run: known by its listing alone.
        wingtip = WatchlistEntry(
            user_id=user.id,
            company_name="Wingtip Toys",
            ticker="WING",
            exchange="NASDAQ",
            why="A toy maker with a moat, maybe.",
            cadence="quarterly",
            followed_at=FOLLOWED_ON,
            next_check_at=FOLLOWED_ON + timedelta(days=92),
        )
        session.add(wingtip)
        await session.commit()
        yield {
            "factory": factory,
            "user": user,
            "book": book,
            "contoso": contoso,
            "fabrikam": fabrikam,
            "security": security,
            "report": report,
            "thesis": thesis,
            "wingtip": wingtip,
        }
    await delete_all(db_engine)


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, scene: dict[str, Any]) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return str(found.group(1))


async def _close_the_position(scene: dict[str, Any]) -> None:
    async with scene["factory"]() as session:
        book = await session.get(Portfolio, scene["book"].id)
        security = await session.get(Security, scene["security"].id)
        await trade(
            session,
            {"portfolio": book, "document": None},
            kind=TransactionKind.SELL,
            security=security,
            quantity="-100",
            price="12.5",
            currency="GBP",
            on=AS_OF,
            at_hour=16,
        )
        await session.commit()


# -- Companies (§4) --------------------------------------------------------------------------


class TestCompanies:
    async def test_it_keeps_the_populations_apart_and_the_neglected_first(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/companies")

        assert page.status_code == 200
        body = page.text
        assert body.count('data-population="held"') == 1
        assert body.count('data-population="researched_not_owned"') == 2
        # Never looked at sorts first, then the listing followed in May, then the company
        # decided on an hour ago.
        assert body.index("Contoso plc") < body.index("Wingtip Toys") < body.index("Fabrikam Inc")
        assert ">Never<" in body or "Never" in body
        assert 'id="add-to-watch"' in body
        assert 'data-filter="researched"' in body
        # The unresolved listing opens on the watchlist, since no company page exists for it.
        assert 'href="/watchlist"' in body
        assert f'href="/companies/{scene["contoso"].id}"' in body

    async def test_the_filters_narrow_the_list_and_nonsense_shows_everything(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        held = (await api.get("/companies", params={"show": "held"})).text
        assert "Contoso plc" in held
        assert "Fabrikam Inc" not in held

        unresearched = (await api.get("/companies", params={"show": "no_report"})).text
        assert "Contoso plc" in unresearched
        assert "Wingtip Toys" in unresearched
        assert "Fabrikam Inc" not in unresearched

        everything = (await api.get("/companies", params={"show": "nonsense"})).text
        assert all(name in everything for name in ("Contoso plc", "Wingtip Toys", "Fabrikam Inc"))

    async def test_a_row_changes_its_cadence(self, api: Any, scene: dict[str, Any]) -> None:
        token = _csrf((await api.get("/companies")).text)
        entry_id = scene["wingtip"].id

        changed = await api.post(
            f"/companies/{entry_id}/cadence", data={"csrf_token": token, "cadence": "monthly"}
        )
        assert changed.status_code == 303
        assert changed.headers["location"] == "/companies"
        async with scene["factory"]() as session:
            entry = await session.scalar(
                select(WatchlistEntry).where(WatchlistEntry.id == entry_id)
            )
            assert entry is not None
            assert entry.cadence == "monthly"

        refused = await api.post(
            f"/companies/{entry_id}/cadence", data={"csrf_token": token, "cadence": "weekly"}
        )
        assert refused.status_code == 422
        assert "monthly or quarterly" in refused.text

        no_token = await api.post(f"/companies/{entry_id}/cadence", data={"cadence": "monthly"})
        assert no_token.status_code == 403

    async def test_an_empty_record_is_one_card_with_the_request_form_as_its_action(
        self, api_settings: Any, db_engine: Any, fake_redis: Any
    ) -> None:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            session.add(User(email="new@example.invalid", display_name="N", role=UserRole.OWNER))
            await session.commit()
        try:
            async for client in client_for(
                build_app(api_settings, engine=db_engine, redis=fake_redis)
            ):
                page = await client.get("/companies")
                assert page.status_code == 200
                assert 'id="nothing-watched"' in page.text
                assert 'href="/requests/new"' in page.text
        finally:
            await delete_all(db_engine)


# -- Company (§5) ----------------------------------------------------------------------------


class TestTheCompanyPage:
    async def test_a_held_listing_leads_with_belief_then_the_holding(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get(f"/companies/{scene['contoso'].id}")

        assert page.status_code == 200
        body = page.text
        # §5.1: the state, the weight, and a price block that says the close and the move.
        assert 'id="company-header"' in body
        assert "held" in body
        assert 'data-field="close">£12.50<' in body
        assert 'data-field="move">+4.2% on the day<' in body
        # §5.2 above §5.3, and no thesis is a warning with the one action that fixes it.
        assert 'id="no-thesis"' in body
        assert f'href="/theses?company={scene["contoso"].id}"' in body
        assert body.index('id="believe"') < body.index('id="hold"')
        assert 'id="what-you-hold"' in body
        assert 'data-field="quantity">100 shares<' in body
        assert 'data-field="average">£10.00<' in body
        assert 'data-field="value">£1,250.00<' in body
        assert f'href="/portfolio/positions/{scene["security"].id}"' in body
        # §5.4 and §5.6: never researched, nothing to ask over, and the actions that exist.
        assert "Never researched" in body
        assert 'id="ask-needs-a-record"' in body
        assert 'id="refresh-refused"' in body
        assert 'href="/decisions?security=CTSO.LSE"' in body
        assert 'id="workbook-absent"' in body
        # The history sheets are still here.
        assert 'id="report-timeline"' in body
        assert 'id="no-approved-reports"' in body

    async def test_a_researched_company_says_why_it_is_not_held(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get(f"/companies/{scene['fabrikam'].id}")

        assert page.status_code == 200
        body = page.text
        assert 'data-field="no-price">No price series<' in body
        assert 'id="what-you-believe"' in body
        assert "Fabrikam is priced for perfection" in body
        assert 'id="not-held"' in body
        assert "Not held. Researched" in body
        assert "you declined on" in body
        assert "because: Priced for perfection." in body
        assert "Current &mdash; approved" in body or "Current — approved" in body
        assert "£3.20" in body
        assert 'id="ask-form"' in body
        assert f'name="company_id" value="{scene["fabrikam"].id}"' in body
        assert 'id="refresh-form"' in body
        assert f'action="/reports/{scene["report"].id}/refresh"' in body
        assert f'href="/theses/{scene["thesis"].id}"' in body
        # The timeline pins the one approved report exactly once.
        assert body.count("as of 2") == 1

    async def test_a_company_outside_the_record_is_not_shown(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get(f"/companies/{uuid.uuid4()}")
        assert page.status_code == 404
        assert "not in your record" in page.text


# -- Position (§3) ---------------------------------------------------------------------------


class TestThePositionPage:
    async def test_the_book_links_each_holding_to_its_position(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        book = await api.get("/portfolio")
        assert book.status_code == 200
        assert f'href="/portfolio/positions/{scene["security"].id}"' in book.text

    async def test_it_shows_how_it_was_built_and_what_it_does_to_the_book(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get(f"/portfolio/positions/{scene['security'].id}")

        assert page.status_code == 200
        body = page.text
        assert 'id="ledger"' in body
        assert body.count("data-trade=") == 1
        assert 'data-field="average"' in body
        assert "£10.00" in body
        assert 'data-field="value">£1,250.00' in body
        assert 'data-field="unrealised">£250.00' in body
        assert 'id="what-it-does"' in body
        assert 'data-field="concentration"' in body
        assert 'id="record-transaction"' in body
        assert f'href="/companies/{scene["contoso"].id}"' in body
        assert 'id="write-thesis"' in body
        assert 'id="partly-closed"' not in body

    async def test_a_closed_position_says_what_it_made(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        await _close_the_position(scene)

        page = await api.get(f"/portfolio/positions/{scene['security'].id}")

        assert page.status_code == 200
        body = page.text
        assert "Closed" in body
        assert 'data-field="realised"' in body
        assert "£250.00" in body
        assert 'id="nothing-to-the-book"' in body
        assert body.count("data-trade=") == 2

    async def test_a_listing_the_book_never_dealt_is_not_a_position(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get(f"/portfolio/positions/{uuid.uuid4()}")
        assert page.status_code == 404
        assert "not a position in your book" in page.text


# -- The shell and the doors between the pages ------------------------------------------------


class TestTheDoors:
    async def test_companies_sits_beside_the_watchlist_in_the_menu(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        body = (await api.get("/companies")).text
        assert 'href="/companies"' in body
        assert 'href="/watchlist"' in body
        assert "Companies" in body

    async def test_writing_a_thesis_from_the_company_page_arrives_with_it_chosen(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/theses", params={"company": str(scene["contoso"].id)})
        assert page.status_code == 200
        assert f'value="{scene["contoso"].id}" selected' in page.text
