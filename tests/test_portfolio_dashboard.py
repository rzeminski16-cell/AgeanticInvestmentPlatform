"""The book as a validity dashboard (page specification §2).

What the record says about each holding beside its value: the thesis state from the
company record, the last check from the daily pass, the risk flags from the exposure the
risk page computes; the conviction sort that puts a broken thesis above a losing position;
the filters; the risk summary; and the header sentence the page exists to deliver.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.enums import TransactionKind, UserRole
from aer.db.models import Company, FxRateRow, Portfolio, PriceBar, Security, User
from aer.services import calculations as calculation_service
from aer.services import performance as performance_service
from aer.services import portfolio as portfolio_service
from aer.services import theses as thesis_service
from aer.web.portfolio import dashboard
from aer.web.portfolio.pages import SORT_COOKIE, percent, pounds
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.portfolio_fixtures import AS_OF, book, funded, trade

__all__ = ["book"]

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


# -- The readings ------------------------------------------------------------------------------


@pytest.fixture
async def readings_scene(db_session: Any, book: dict[str, Any]) -> dict[str, Any]:
    """Two holdings: one with a thesis that holds, one with nothing written down."""
    microsoft = Company(name="MICROSOFT CORP", ticker="MSFT", exchange="NASDAQ", cik="0000789019")
    barclays = Company(
        name="BARCLAYS PLC", ticker="BARC", exchange="LSE", company_number="00048839"
    )
    db_session.add_all([microsoft, barclays])
    await db_session.flush()
    book["msft"].company_id = microsoft.id
    book["barc"].company_id = barclays.id
    # The close before the fixture's, on every leg the prior-day valuation needs: both
    # listings and both rate legs, so the day's move has a complete book to move from.
    prior = AS_OF - timedelta(days=1)
    db_session.add_all(
        [
            PriceBar(
                security_id=book["msft"].id,
                bar_date=prior,
                open=Decimal("398"),
                high=Decimal("402"),
                low=Decimal("397"),
                close=Decimal("400"),
            ),
            PriceBar(
                security_id=book["barc"].id,
                bar_date=prior,
                open=Decimal("250"),
                high=Decimal("250"),
                low=Decimal("250"),
                close=Decimal("250"),
            ),
            FxRateRow(
                base="EUR",
                quote="USD",
                observed_on=prior,
                vintage=prior,
                rate=Decimal("1.0705"),
                source_document_id=book["document"].id,
                artefact_sha256="c" * 64,
            ),
            FxRateRow(
                base="EUR",
                quote="GBP",
                observed_on=prior,
                vintage=prior,
                rate=Decimal("0.84645"),
                source_document_id=book["document"].id,
                artefact_sha256="c" * 64,
            ),
        ]
    )
    await funded(db_session, book)
    await trade(db_session, book, security=book["msft"], quantity="100", price="400")
    await trade(
        db_session,
        book,
        security=book["barc"],
        quantity="1000",
        price="250",
        currency="GBX",
        on=date(2026, 6, 10),
    )
    await thesis_service.write_thesis(
        db_session, user=book["user"], company=microsoft, title="Azure keeps compounding"
    )
    await db_session.flush()
    context = calculation_service.new_context()
    view = await portfolio_service.book_as_at(
        db_session, context, portfolio=book["portfolio"], as_of=AS_OF
    )
    exposure = await performance_service.exposure_as_at(
        db_session, context, portfolio=book["portfolio"], as_of=AS_OF, view=view
    )
    return {**book, "view": view, "exposure": exposure, "microsoft": microsoft}


class TestTheReadings:
    async def test_each_holding_is_read_from_the_record(
        self, db_session: Any, readings_scene: dict[str, Any]
    ) -> None:
        readings = await dashboard.validity_for(
            db_session,
            user=readings_scene["user"],
            view=readings_scene["view"],
            exposure=readings_scene["exposure"],
            now=NOW,
        )
        msft = readings[readings_scene["msft"].id]
        barc = readings[readings_scene["barc"].id]

        assert msft.thesis_state == "holds"
        assert msft.thesis_words == "holds"
        # The daily pass has never run, so every holding is unchecked and says *Never*.
        assert msft.checked == "Never"
        assert msft.checked_overdue
        assert msft.conviction == dashboard.CONVICTION["unchecked"]
        assert barc.thesis_state == "none"
        assert barc.thesis_words == "no thesis"
        assert barc.thesis_tone == "warning"
        assert barc.conviction == 0
        # Two holdings: both are among the five largest, so both are flagged.
        assert "concentration" in msft.risk_flags
        assert "concentration" in barc.risk_flags

    async def test_conviction_puts_no_thesis_first_and_ties_break_by_weight(
        self, db_session: Any, readings_scene: dict[str, Any]
    ) -> None:
        readings = await dashboard.validity_for(
            db_session,
            user=readings_scene["user"],
            view=readings_scene["view"],
            exposure=readings_scene["exposure"],
            now=NOW,
        )
        rows = [
            {
                "security_id": row.security.id,
                "ticker": row.security.ticker,
                "name": row.security.ticker,
                "weight_value": row.weight.value if row.weight else None,
                "value_amount": row.value.value if row.value else None,
                "unrealised_amount": row.unrealised.value if row.unrealised else None,
            }
            for row in readings_scene["view"].holdings
        ]

        assert [row["ticker"] for row in dashboard.sort_rows(rows, readings, "conviction")] == [
            "BARC",
            "MSFT",
        ]
        assert [row["ticker"] for row in dashboard.sort_rows(rows, readings, "value")] == [
            "MSFT",
            "BARC",
        ]  # $40,000 against £2,500
        assert [row["ticker"] for row in dashboard.sort_rows(rows, readings, "name")] == [
            "BARC",
            "MSFT",
        ]
        assert dashboard.sort_rows(rows, readings, "nonsense") == dashboard.sort_rows(
            rows, readings, "conviction"
        )

    async def test_the_filters_narrow_and_over_ceiling_can_never_match(
        self, db_session: Any, readings_scene: dict[str, Any]
    ) -> None:
        readings = await dashboard.validity_for(
            db_session,
            user=readings_scene["user"],
            view=readings_scene["view"],
            exposure=readings_scene["exposure"],
            now=NOW,
        )
        rows = [
            {"security_id": row.security.id, "ticker": row.security.ticker, "name": "x"}
            for row in readings_scene["view"].holdings
        ]

        def tickers(show: str) -> list[str]:
            return [row["ticker"] for row in dashboard.filter_rows(rows, readings, show)]

        assert tickers("no_thesis") == ["BARC"]
        assert tickers("in_doubt") == []
        assert set(tickers("overdue")) == {"BARC", "MSFT"}  # never checked
        assert tickers("over_ceiling") == []  # no ceiling exists to be over
        assert set(tickers("all")) == {"BARC", "MSFT"}

    async def test_the_days_move_is_a_change_between_two_valuations(
        self, db_session: Any, readings_scene: dict[str, Any]
    ) -> None:
        view = readings_scene["view"]
        assert view.net_assets is not None
        move = await dashboard.days_move(
            db_session, book=readings_scene["portfolio"], as_of=AS_OF, latest=view.net_assets.value
        )
        # Microsoft closed at 400 the day before and 410 today; the book moved up.
        assert move is not None
        assert move > 0

    async def test_the_risk_summary_has_five_rows_each_with_its_working(
        self, db_session: Any, readings_scene: dict[str, Any]
    ) -> None:
        rows = await dashboard.risk_summary(
            db_session,
            book=readings_scene["portfolio"],
            view=readings_scene["view"],
            exposure=readings_scene["exposure"],
            as_of=AS_OF,
            money=pounds,
            share=lambda value: percent(value).lstrip("+"),
        )
        assert [row.key for row in rows] == [
            "concentration",
            "sector",
            "over_ceiling",
            "shock",
            "cash",
        ]
        by_key = {row.key: row for row in rows}
        assert by_key["concentration"].value.endswith("%")
        assert by_key["over_ceiling"].value == "none"
        assert "No ceiling is stored" in by_key["over_ceiling"].note
        assert by_key["shock"].value == "none stated"
        assert by_key["cash"].value.startswith("GBP ")
        assert all(row.href.startswith("/risk") for row in rows)


# -- The page ----------------------------------------------------------------------------------


@pytest.fixture
async def scene(db_engine: Any) -> Any:
    """A committed book: one holding with a thesis, one with no company record at all."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="dash@example.invalid", display_name="Dash", role=UserRole.OWNER)
        session.add(user)
        await session.flush()
        book = Portfolio(user_id=user.id, name="ISA", base_currency="GBP")
        contoso = Company(
            name="Contoso plc", ticker="CTSO", exchange="LSE", company_number="01234567"
        )
        session.add_all([book, contoso])
        await session.flush()
        contoso_listing = Security(
            company_id=contoso.id,
            ticker="CTSO",
            exchange="LSE",
            provider_symbol="CTSO.LSE",
            name="Contoso plc",
            quote_currency="GBP",
        )
        orphan = Security(
            company_id=None,
            ticker="ORPH",
            exchange="LSE",
            provider_symbol="ORPH.LSE",
            name="Orphan Holdings",
            quote_currency="GBP",
        )
        session.add_all([contoso_listing, orphan])
        await session.flush()
        for listing, close in ((contoso_listing, "12.5"), (orphan, "5")):
            session.add_all(
                [
                    PriceBar(
                        security_id=listing.id,
                        bar_date=AS_OF - timedelta(days=1),
                        open=Decimal(close),
                        high=Decimal(close),
                        low=Decimal(close),
                        close=Decimal(close),
                    ),
                    PriceBar(
                        security_id=listing.id,
                        bar_date=AS_OF,
                        open=Decimal(close),
                        high=Decimal(close),
                        low=Decimal(close),
                        close=Decimal(close),
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
            security=contoso_listing,
            quantity="100",
            price="10",
            currency="GBP",
            on=date(2026, 6, 15),
        )
        await trade(
            session,
            holdings,
            security=orphan,
            quantity="200",
            price="5",
            currency="GBP",
            on=date(2026, 6, 16),
        )
        await thesis_service.write_thesis(
            session, user=user, company=contoso, title="Contoso keeps its pricing power"
        )
        await session.commit()
        yield {"factory": factory, "user": user, "contoso": contoso, "listing": contoso_listing}
    await delete_all(db_engine)


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, scene: dict[str, Any]) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


class TestThePage:
    async def test_it_leads_with_validity_and_sorts_by_conviction(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/portfolio")

        assert page.status_code == 200
        body = page.text
        assert 'id="book-header"' in body
        assert 'data-field="total-value">£5,250.00<' in body  # 3,000 cash + 1,250 + 1,000
        assert 'data-field="positions">2<' in body
        assert "1 of 2 positions have a thesis that currently holds." in body
        # Conviction: the orphan with no thesis sits above Contoso, whose thesis holds.
        assert body.index('data-holding="ORPH.LSE"') < body.index('data-holding="CTSO.LSE"')
        assert 'data-thesis-state="none"' in body
        assert 'data-thesis-state="holds"' in body
        assert "no thesis" in body
        assert 'data-field="checked">Never<' in body
        # The company cell opens the company page; the orphan opens the request form.
        assert f'href="/companies/{scene["contoso"].id}"' in body
        assert 'href="/requests/new"' in body
        assert "No company record" in body
        # The risk summary sits beside the table with its five rows.
        assert 'id="risk-summary"' in body
        for key in ("concentration", "sector", "over_ceiling", "shock", "cash"):
            assert f'data-summary="{key}"' in body, key
        # The last close held is not today's: the warning band says so.
        assert 'id="prices-stale"' in body
        assert "Valued at the close of 2026-06-30." in body

    async def test_the_sort_is_remembered_for_the_session(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        by_name = await api.get("/portfolio", params={"sort": "name"})
        assert by_name.status_code == 200
        assert 'data-sort="name"' in by_name.text
        assert by_name.text.index('data-holding="CTSO.LSE"') < by_name.text.index(
            'data-holding="ORPH.LSE"'
        )
        assert SORT_COOKIE in by_name.cookies

        # The next plain visit remembers it; the cookie is the session's.
        again = await api.get("/portfolio")
        assert 'data-sort="name"' in again.text
        sort_cookie = next(
            part
            for part in by_name.headers.get_list("set-cookie")
            if part.startswith(f"{SORT_COOKIE}=")
        )
        assert re.search(r"max-age|expires", sort_cookie, re.IGNORECASE) is None

    async def test_the_filters_narrow_the_table(self, api: Any, scene: dict[str, Any]) -> None:
        unwritten = (await api.get("/portfolio", params={"show": "no_thesis"})).text
        assert 'data-holding="ORPH.LSE"' in unwritten
        assert 'data-holding="CTSO.LSE"' not in unwritten

        ceiling = (await api.get("/portfolio", params={"show": "over_ceiling"})).text
        assert 'id="no-ceiling"' in ceiling
        assert 'data-holding="CTSO.LSE"' not in ceiling

    async def test_a_date_the_operator_asked_for_is_not_stale(
        self, api: Any, scene: dict[str, Any]
    ) -> None:
        page = await api.get("/portfolio", params={"as_of": "2026-06-30"})
        assert page.status_code == 200
        assert 'id="prices-stale"' not in page.text
