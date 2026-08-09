"""Prices for a run, and the beta that comes out of them.

Gap B3. What is under test is mostly the *absence* paths, because they are the ones a real
machine takes: the subscription is optional, the exchange may not be one this platform
regresses against, and a newly listed company has no five-year beta. Each of those has to
produce a sentence rather than an exception, and none of them may produce a number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus, SourceTier, UserRole
from aer.db.models import (
    Assumption,
    Company,
    Job,
    PriceBar,
    ResearchRequest,
    Security,
    User,
)
from aer.fetch.client import FetchResult
from aer.services.calculations import new_context
from aer.services.price_acquisition import BETA_WINDOW_YEARS, acquire_prices
from aer.services.prices import BETA_ASSUMPTION
from aer.sources.eodhd import api
from aer.sources.eodhd.client import ActionsResponse, PriceResponse, SharesResponse
from aer.storage.local import LocalArtefactStore

pytestmark = pytest.mark.integration

AS_OF = date(2024, 6, 28)


async def _stored_fetch(store: LocalArtefactStore, url: str, payload: bytes) -> FetchResult:
    """A fetch result describing bytes that are genuinely in the store.

    Stored first, exactly as the real fetch layer archives before it returns. A stub that
    invented a digest would have the provenance recorder claim a source document for bytes
    nobody holds, which `aer.services.artefacts` refuses outright — and rightly: that row
    is the whole basis for citing the thing later.
    """
    stored = await store.put_bytes(payload)
    return _fetch(url, stored.sha256, size=stored.size_bytes)


def _fetch(url: str, sha: str, *, size: int = 64) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        sha256=sha,
        size_bytes=size,
        media_type="application/json",
        declared_media_type="application/json",
        headers={"content-type": "application/json"},
        redirect_chain=(),
        elapsed_ms=1.0,
        attempts=1,
        licence_note="Licensed market data; internal use only.",
        robots_allowed=True,
    )


def _bars(symbol: str, *, months: int, start: Decimal, step: Decimal) -> tuple[api.BarRow, ...]:
    """A monthly ladder, so the beta regression has paired observations to work with."""
    rows: list[api.BarRow] = []
    price = start
    for index in range(months):
        year = 2019 + index // 12
        month = index % 12 + 1
        price = price + step
        rows.append(
            api.BarRow(
                on=date(year, month, 28),
                open=price,
                high=price,
                low=price,
                close=price,
                adjusted_close=price,
                volume=1_000,
            )
        )
    return tuple(rows)


class StubPriceClient:
    """The vendor, without the vendor. Records what it was asked for."""

    def __init__(
        self,
        store: LocalArtefactStore,
        *,
        months: int = 72,
        shares: Decimal | None = Decimal("100"),
    ) -> None:
        self._store = store
        self._months = months
        self._shares = shares
        self.bar_calls: list[str] = []
        self.action_calls: list[str] = []
        self.share_calls: list[str] = []

    @property
    def licence_note(self) -> str:
        return "Licensed market data; internal use only."

    async def fetch_bars(
        self, symbol: str, *, as_of: date, since: date | None = None
    ) -> PriceResponse:
        self.bar_calls.append(symbol)
        # The proxy moves differently from the subject, so the regression is not degenerate.
        step = Decimal("0.5") if symbol.endswith(".INDX") else Decimal("1.25")
        return PriceResponse(
            symbol=symbol,
            as_of=as_of,
            bars=_bars(symbol, months=self._months, start=Decimal("100"), step=step),
            discarded_after_as_of=0,
            fetch=await _stored_fetch(
                self._store,
                f"https://eodhd.test/{symbol}",
                f"bars for {symbol} to {as_of.isoformat()}".encode(),
            ),
        )

    async def fetch_actions(
        self, symbol: str, *, as_of: date, since: date | None = None
    ) -> ActionsResponse:
        self.action_calls.append(symbol)
        url = f"https://eodhd.test/actions/{symbol}"
        return ActionsResponse(
            symbol=symbol,
            as_of=as_of,
            splits=(),
            dividends=(),
            splits_fetch=await _stored_fetch(self._store, url, f"splits {symbol}".encode()),
            dividends_fetch=await _stored_fetch(
                self._store, url + "/div", f"dividends {symbol}".encode()
            ),
        )

    async def fetch_shares_outstanding(self, symbol: str, *, as_of: date) -> SharesResponse:
        self.share_calls.append(symbol)
        if self._shares is None:
            message = "no dated share count"
            raise AssertionError(message)
        return SharesResponse(
            symbol=symbol,
            as_of=as_of,
            shares=api.SharesOutstanding(shares=self._shares, as_reported_on=date(2024, 3, 31)),
            fetch=await _stored_fetch(
                self._store, f"https://eodhd.test/fundamentals/{symbol}", b"fundamentals"
            ),
        )


@pytest.fixture
async def scene(db_session: AsyncSession, tmp_path: Any) -> dict[str, Any]:
    user = User(email="prices@example.invalid", display_name="P", role=UserRole.OWNER)
    db_session.add(user)
    await db_session.flush()

    request = ResearchRequest(
        user_id=user.id,
        company_name="Contoso Corporation",
        ticker="CTSO",
        exchange="NASDAQ",
        as_of_date=AS_OF,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    company = Company(
        name="Contoso Corporation", ticker="CTSO", exchange="NASDAQ", cik="0000000009"
    )
    db_session.add_all([request, company])
    await db_session.flush()

    # A real job row: `source_documents.job_id` is a foreign key, and provenance attributed
    # to a run that does not exist is exactly what that constraint is there to refuse.
    job = Job(
        request_id=request.id,
        workflow_version="test",
        code_version="abc",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    settings_root = tmp_path / "artefacts"
    return {
        "session": db_session,
        "request": request,
        "company": company,
        "store": LocalArtefactStore(settings_root, max_bytes=50_000_000),
        "job_id": job.id,
    }


async def _acquire(scene: dict[str, Any], client: Any) -> Any:
    return await acquire_prices(
        scene["session"],
        client,
        scene["store"],
        request=scene["request"],
        company=scene["company"],
        job_id=scene["job_id"],
        context=new_context(),
    )


class TestWithoutASubscription:
    async def test_no_client_is_a_sentence_not_a_failure(self, scene: dict[str, Any]) -> None:
        # ADR 0030 treats the feed as a capability the platform works without. A machine
        # with no key runs every step and says which figures it could not compute.
        outcome = await _acquire(scene, None)

        assert outcome.acquired is False
        assert "no market-data subscription" in outcome.reason.lower()
        assert outcome.market_capitalisation is None
        assert outcome.beta_proposed is False

    async def test_nothing_is_written(self, scene: dict[str, Any]) -> None:
        await _acquire(scene, None)

        assert list(await scene["session"].scalars(select(Security))) == []
        assert list(await scene["session"].scalars(select(PriceBar))) == []


class TestAnUndocumentedExchange:
    async def test_it_refuses_rather_than_regressing_against_the_wrong_market(
        self, scene: dict[str, Any]
    ) -> None:
        scene["request"].exchange = "TSX"
        await scene["session"].flush()

        outcome = await _acquire(scene, StubPriceClient(scene["store"]))

        assert outcome.acquired is False
        assert "No market index is documented" in outcome.reason

    async def test_it_spends_no_calls(self, scene: dict[str, Any]) -> None:
        # The refusal happens before the first fetch, so an unusable exchange costs nothing
        # against the daily weighted allowance.
        scene["request"].exchange = "TSX"
        await scene["session"].flush()
        client = StubPriceClient(scene["store"])

        await _acquire(scene, client)

        assert client.bar_calls == []


class TestAcquiringTheSubjectAndItsMarket:
    async def test_both_listings_are_fetched(self, scene: dict[str, Any]) -> None:
        client = StubPriceClient(scene["store"])

        await _acquire(scene, client)

        # The vendor's key for a US venue is `.US`, not the exchange code.
        assert client.bar_calls == ["CTSO.US", "GSPC.INDX"]

    async def test_the_index_is_not_asked_for_corporate_actions(
        self, scene: dict[str, Any]
    ) -> None:
        # An index has none, and asking spends a call against the allowance to be told so.
        client = StubPriceClient(scene["store"])

        await _acquire(scene, client)

        assert client.action_calls == ["CTSO.US"]

    async def test_the_bars_are_stored_against_the_security(self, scene: dict[str, Any]) -> None:
        outcome = await _acquire(scene, StubPriceClient(scene["store"]))

        assert outcome.acquired is True
        assert outcome.bars > 0
        stored = list(await scene["session"].scalars(select(Security)))
        assert {row.provider_symbol for row in stored} == {"CTSO.US", "GSPC.INDX"}

    async def test_the_provenance_records_the_licensed_tier(self, scene: dict[str, Any]) -> None:
        # The licence note travels on the source document; the containment that keeps the
        # series off an export reads the same policy.
        from aer.db.models import SourceDocument  # noqa: PLC0415

        await _acquire(scene, StubPriceClient(scene["store"]))

        documents = list(await scene["session"].scalars(select(SourceDocument)))
        assert documents
        assert all(row.source_tier is SourceTier.T4_LICENSED_MARKET for row in documents)

    async def test_a_market_capitalisation_is_computed(self, scene: dict[str, Any]) -> None:
        outcome = await _acquire(scene, StubPriceClient(scene["store"]))

        assert outcome.market_capitalisation is not None
        assert outcome.market_capitalisation.value > 0

    async def test_the_run_reports_the_figure_and_not_the_series(
        self, scene: dict[str, Any]
    ) -> None:
        # ADR 0030 route 2 as amended: a derived figure may be published, the series may
        # not. The step output is what a report reads, so it carries the one and not the
        # other.
        outcome = await _acquire(scene, StubPriceClient(scene["store"]))
        payload = outcome.as_dict()

        assert payload["market_capitalisation"] is not None
        assert "bars" in payload
        assert isinstance(payload["bars"], int), "a count, never the prices themselves"
        assert not any(isinstance(value, list) for value in payload.values())


class TestTheBeta:
    async def test_it_is_proposed_from_the_regression(self, scene: dict[str, Any]) -> None:
        outcome = await _acquire(scene, StubPriceClient(scene["store"]))

        assert outcome.beta_proposed is True
        row = await scene["session"].scalar(
            select(Assumption).where(Assumption.name == BETA_ASSUMPTION)
        )
        assert row is not None

    async def test_it_is_never_confirmed(self, scene: dict[str, Any]) -> None:
        # A regression is evidence for a beta, not a decision about one.
        await _acquire(scene, StubPriceClient(scene["store"]))

        row = await scene["session"].scalar(
            select(Assumption).where(Assumption.name == BETA_ASSUMPTION)
        )
        assert row is not None
        assert row.approved is False

    async def test_the_justification_names_the_proxy(self, scene: dict[str, Any]) -> None:
        # A beta quoted without what it was measured against is not reproducible, and the
        # operator confirming it is agreeing to a measurement they cannot see the terms of.
        await _acquire(scene, StubPriceClient(scene["store"]))

        row = await scene["session"].scalar(
            select(Assumption).where(Assumption.name == BETA_ASSUMPTION)
        )
        assert row is not None
        assert "S&P 500" in row.justification

    async def test_too_short_a_history_proposes_nothing_and_does_not_raise(
        self, scene: dict[str, Any]
    ) -> None:
        # A newly listed company has no five-year beta. That is a fact about the company,
        # not a failure of the run, and the operator enters one by hand.
        outcome = await _acquire(scene, StubPriceClient(scene["store"], months=3))

        assert outcome.acquired is True
        assert outcome.beta_proposed is False
        assert (
            await scene["session"].scalar(
                select(Assumption).where(Assumption.name == BETA_ASSUMPTION)
            )
            is None
        )


class TestTheWindow:
    def test_it_reaches_past_the_regression_window(self) -> None:
        # A monthly return needs the month before it, so a window starting exactly five
        # years back yields fifty-nine returns rather than sixty.
        from aer.services.price_acquisition import _window_start  # noqa: PLC0415

        start = _window_start(date(2024, 6, 28))
        assert start.year == 2024 - BETA_WINDOW_YEARS - 1
