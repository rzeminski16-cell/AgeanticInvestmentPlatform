"""The portfolio's third door: a never-seen ticker verified once, at first sight.

Roadmap §3.1 under ADR 0093. What must hold: a verified listing is a real acquisition —
the series hashed and stored, the source document rooted on the act's own work order, the
bars pointing at that document (invariant 1) — and every refusal names its reason while
the act's ``FAILED`` order stays on the record. No network: the vendor is a stub whose
bytes genuinely land in the artefact store, exactly as the real fetch layer archives
before it returns.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import RequestStatus, UserRole
from aer.db.models import Portfolio, PriceBar, SourceDocument, User, WorkOrder
from aer.errors import ExternalServiceError
from aer.fetch.client import FetchResult
from aer.services.listings import NO_SUBSCRIPTION, add_listing
from aer.sources.eodhd import api
from aer.sources.eodhd.client import PriceResponse
from aer.storage.local import LocalArtefactStore

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path: Any) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=1_000_000)


@pytest.fixture
async def book(db_session: AsyncSession) -> Portfolio:
    user = User(email="book@example.invalid", display_name="B", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()
    portfolio = Portfolio(user_id=user.id, name="My portfolio", base_currency="GBP")
    db_session.add(portfolio)
    await db_session.flush()
    return portfolio


class _Vendor:
    """The vendor, without the vendor. Only the one call the third door makes."""

    def __init__(self, store: LocalArtefactStore, *, days: int = 5, refuse: str = "") -> None:
        self._store = store
        self._days = days
        self._refuse = refuse
        self.asked: list[str] = []

    @property
    def licence_note(self) -> str:
        return "Licensed market data; internal use only."

    async def fetch_bars(
        self, symbol: str, *, as_of: date, since: date | None = None
    ) -> PriceResponse:
        self.asked.append(symbol)
        if self._refuse:
            raise ExternalServiceError(self._refuse, provider="eodhd", retryable=False)
        stored = await self._store.put_bytes(f"bars for {symbol}".encode())
        bars = tuple(
            api.BarRow(
                on=as_of - timedelta(days=self._days - index),
                open=Decimal(10) + Decimal(index),
                high=Decimal(10) + Decimal(index),
                low=Decimal(10) + Decimal(index),
                close=Decimal(10) + Decimal(index),
                adjusted_close=Decimal(10) + Decimal(index),
                volume=100,
            )
            for index in range(self._days)
        )
        return PriceResponse(
            symbol=symbol,
            as_of=as_of,
            bars=bars,
            discarded_after_as_of=0,
            fetch=FetchResult(
                url=f"https://eodhd.test/{symbol}",
                final_url=f"https://eodhd.test/{symbol}",
                status_code=200,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type="application/json",
                declared_media_type="application/json",
                headers={"content-type": "application/json"},
                redirect_chain=(),
                elapsed_ms=1.0,
                attempts=1,
                licence_note=self.licence_note,
                robots_allowed=True,
            ),
        )

    async def fetch_actions(self, symbol: str, *, as_of: date, since: date | None = None) -> Any:
        message = "the third door fetches no actions"
        raise AssertionError(message)

    async def fetch_shares_outstanding(self, symbol: str, *, as_of: date) -> Any:
        message = "the third door fetches no share count"
        raise AssertionError(message)


async def _the_orders(session: AsyncSession) -> list[WorkOrder]:
    rows = await session.scalars(select(WorkOrder).where(WorkOrder.tool == "portfolio"))
    return list(rows)


class TestAVerifiedListing:
    async def test_it_becomes_dealable_with_the_vendor_key_and_the_venue_currency(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        added = await add_listing(
            db_session,
            store,
            portfolio=book,
            ticker="tsla",
            exchange="nasdaq",
            client=_Vendor(store),
        )

        assert added.is_dealable
        assert added.security is not None
        assert added.security.ticker == "TSLA"
        assert added.security.exchange == "NASDAQ"
        assert added.security.provider_symbol == "TSLA.US"
        assert added.security.quote_currency == "USD"

    async def test_the_act_is_a_work_order_of_the_books_own_kind(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        """ADR 0093 on the table: tool and subject_kind distinguish it, the clock is the
        day of the act, point-in-time is off because today's close is the point, and the
        cap is the zero that refuses every model call under it."""
        await add_listing(
            db_session,
            store,
            portfolio=book,
            ticker="TSLA",
            exchange="NASDAQ",
            client=_Vendor(store),
        )

        (order,) = await _the_orders(db_session)
        assert order.subject_kind == "portfolio"
        assert order.subject_id == book.id
        assert order.as_of_date == datetime.now(UTC).date()
        assert order.point_in_time is False
        assert order.max_cost_gbp == 0
        assert order.status is RequestStatus.COMPLETED

    async def test_the_series_is_evidence_with_a_hash_behind_it(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        """Invariant 1, which is the reason the third door needed a decision at all: the
        fetched series is hashed and stored, its provenance row roots on the act's own work
        order rather than on a research request that does not exist, and every bar points at
        that document rather than at nothing."""
        added = await add_listing(
            db_session,
            store,
            portfolio=book,
            ticker="TSLA",
            exchange="NASDAQ",
            client=_Vendor(store),
        )

        (order,) = await _the_orders(db_session)
        document = await db_session.scalar(
            select(SourceDocument).where(SourceDocument.work_order_id == order.id)
        )
        assert document is not None
        assert document.company_id is None
        assert document.licence_note == "Licensed market data; internal use only."
        assert not document.quarantined

        assert added.security is not None
        bars = list(
            await db_session.scalars(
                select(PriceBar).where(PriceBar.security_id == added.security.id)
            )
        )
        assert bars
        assert all(bar.source_document_id == document.id for bar in bars)

    async def test_verifying_the_same_listing_twice_keeps_one_security_row(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        first = await add_listing(
            db_session,
            store,
            portfolio=book,
            ticker="TSLA",
            exchange="NASDAQ",
            client=_Vendor(store),
        )
        second = await add_listing(
            db_session,
            store,
            portfolio=book,
            ticker="TSLA",
            exchange="NASDAQ",
            client=_Vendor(store),
        )

        assert first.security is not None
        assert second.security is not None
        assert second.security.id == first.security.id


class TestEveryRefusalNamesItsReason:
    async def test_no_subscription_refuses_before_any_act_exists(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        added = await add_listing(
            db_session, store, portfolio=book, ticker="TSLA", exchange="NASDAQ", client=None
        )

        assert not added.is_dealable
        assert added.refusal == NO_SUBSCRIPTION
        assert await _the_orders(db_session) == []

    async def test_an_undocumented_exchange_is_refused_with_the_known_ones(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        vendor = _Vendor(store)
        added = await add_listing(
            db_session, store, portfolio=book, ticker="SAP", exchange="XETRA", client=vendor
        )

        assert not added.is_dealable
        assert "XETRA" in added.refusal
        assert "LSE" in added.refusal
        assert vendor.asked == [], "a symbol was guessed for an undocumented venue"
        (order,) = await _the_orders(db_session)
        assert order.status is RequestStatus.FAILED

    async def test_a_symbol_the_vendor_returns_nothing_for_is_refused(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        added = await add_listing(
            db_session,
            store,
            portfolio=book,
            ticker="NOPE",
            exchange="NASDAQ",
            client=_Vendor(store, days=0),
        )

        assert not added.is_dealable
        assert "no prices for NOPE.US" in added.refusal
        (order,) = await _the_orders(db_session)
        assert order.status is RequestStatus.FAILED

    async def test_a_vendor_refusal_travels_with_its_message(
        self, db_session: AsyncSession, store: LocalArtefactStore, book: Portfolio
    ) -> None:
        added = await add_listing(
            db_session,
            store,
            portfolio=book,
            ticker="TSLA",
            exchange="NASDAQ",
            client=_Vendor(store, refuse="the subscription does not cover this feed"),
        )

        assert not added.is_dealable
        assert "the subscription does not cover this feed" in added.refusal
        (order,) = await _the_orders(db_session)
        assert order.status is RequestStatus.FAILED
