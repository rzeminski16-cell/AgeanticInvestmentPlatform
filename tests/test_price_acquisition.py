"""Prices for a run, and the beta that comes out of them.

Gap B3. What is under test is mostly the *absence* paths, because they are the ones a real
machine takes: the subscription is optional, the exchange may not be one this platform
regresses against, and a newly listed company has no five-year beta. Each of those has to
produce a sentence rather than an exception, and none of them may produce a number.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.enums import FactBasis, JobStatus, Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    Assumption,
    Company,
    FinancialFact,
    Job,
    PriceBar,
    Security,
    SourceDocument,
    User,
)
from aer.fetch.client import FetchResult
from aer.services.calculations import new_context
from aer.services.price_acquisition import BETA_WINDOW_YEARS, acquire_prices
from aer.services.prices import BETA_ASSUMPTION
from aer.sources.eodhd import api
from aer.sources.eodhd.client import ActionsResponse, PriceResponse, SharesResponse
from aer.storage.local import LocalArtefactStore
from aer.workflow.workflows.vertical_slice_v1 import _filed_share_count
from tests.request_fixtures import research_request

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

    request = research_request(
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
        work_order_id=request.id,
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


def _step_context(scene: dict[str, Any]) -> Any:
    """The slice of a `StepContext` `_filed_share_count` actually touches: a session."""
    return SimpleNamespace(session=scene["session"])


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
        # The reason travels on the step's record, in the regression's own words. The
        # confirmation run asked the operator for a beta and said nothing about why: the
        # reason was in a log line, and `aer diagnose acquire_prices` showed only `False`.
        assert "observation" in outcome.beta_reason
        assert outcome.as_dict()["beta_reason"] == outcome.beta_reason

    async def test_a_regressed_beta_carries_no_reason(self, scene: dict[str, Any]) -> None:
        outcome = await _acquire(scene, StubPriceClient(scene["store"]))

        assert outcome.beta_proposed is True
        assert outcome.beta_reason == ""


class TestTheWindow:
    def test_it_reaches_past_the_regression_window(self) -> None:
        # A monthly return needs the month before it, so a window starting exactly five
        # years back yields fifty-nine returns rather than sixty.
        from aer.services.price_acquisition import _window_start  # noqa: PLC0415

        start = _window_start(date(2024, 6, 28))
        assert start.year == 2024 - BETA_WINDOW_YEARS - 1


# ==========================================================================================
# Where the share count comes from — gap A47
# ==========================================================================================


async def _share_document(scene: dict[str, Any]) -> Any:
    """The filing a share count came from. A fact without one is refused by the schema,
    which is the provenance invariant doing its job."""
    held = scene.get("share_document")
    if held is not None:
        return held
    artefact = Artefact(
        sha256="d" * 64, size_bytes=10, media_type="application/json", storage_key="dd/d"
    )
    scene["session"].add(artefact)
    await scene["session"].flush()
    document = SourceDocument(
        work_order_id=scene["request"].id,
        artefact_id=artefact.id,
        url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000009.json",
        provider=Provider.SEC_EDGAR,
        source_tier=SourceTier.T1_REGULATORY,
        title="Contoso XBRL company facts",
        retrieved_at=datetime.now(UTC),
    )
    scene["session"].add(document)
    await scene["session"].flush()
    scene["share_document"] = document
    return document


async def _seed_share_fact(
    scene: dict[str, Any], *, on: date, filed: date, shares: str
) -> FinancialFact:
    """A cover-page share count, as the concept map stores it.

    `dei:EntityCommonStockSharesOutstanding` is dated the day the annual report was signed,
    which is why it is the freshest count a filing carries and why it is an instant.
    """
    document = await _share_document(scene)
    fact = FinancialFact(
        company_id=scene["company"].id,
        source_document_id=document.id,
        concept="shares_outstanding",
        raw_concept="EntityCommonStockSharesOutstanding",
        taxonomy="dei",
        value=Decimal(shares),
        unit="shares",
        period_start=None,
        period_end=on,
        fiscal_year=on.year,
        fiscal_period="FY",
        filed_date=filed,
        form="10-K",
        accession="0000000000-00-000009",
        basis=FactBasis.AS_REPORTED,
    )
    scene["session"].add(fact)
    await scene["session"].flush()
    return fact


class TestTheFiledShareCountIsPreferred:
    """The vendor's fundamentals document is a ten-weight request for one number, and on the
    operator's subscription it is a feed the account does not carry at all. The count is on
    the cover of every annual report, and the run already holds it."""

    async def test_the_vendor_is_not_asked_when_the_filings_answer(
        self, scene: dict[str, Any]
    ) -> None:
        client = StubPriceClient(scene["store"], shares=None)

        outcome = await acquire_prices(
            scene["session"],
            client,
            scene["store"],
            request=scene["request"],
            company=scene["company"],
            job_id=scene["job_id"],
            context=new_context(),
            shares_outstanding=Quantity.of(
                Decimal("1000000"),
                Unit.base("shares"),
                source=SourceRef.security(uuid.uuid4(), label="shares outstanding"),
            ),
        )

        assert outcome.acquired
        assert client.share_calls == [], "the filings answered; the vendor was asked anyway"

    async def test_the_step_reads_the_count_the_run_stored(self, scene: dict[str, Any]) -> None:
        """The wiring that was missing: `acquire_prices` has always preferred a filed count
        and nothing ever passed one, so every run fell through to the vendor."""
        await _seed_share_fact(
            scene, on=date(2024, 1, 24), filed=date(2024, 2, 1), shares="1234567"
        )

        found = await _filed_share_count(
            _step_context(scene), company_id=scene["company"].id, request=scene["request"]
        )

        assert found is not None
        assert found.value == Decimal("1234567")
        assert found.unit.symbol == "shares"

    async def test_the_newest_count_wins(self, scene: dict[str, Any]) -> None:
        """A market capitalisation wants the count as it stands, not the oldest on file."""
        await _seed_share_fact(
            scene, on=date(2023, 1, 25), filed=date(2023, 2, 1), shares="1000000"
        )
        await _seed_share_fact(
            scene, on=date(2024, 1, 24), filed=date(2024, 2, 1), shares="1234567"
        )

        found = await _filed_share_count(
            _step_context(scene), company_id=scene["company"].id, request=scene["request"]
        )

        assert found is not None
        assert found.value == Decimal("1234567")

    async def test_a_count_filed_after_the_as_of_date_is_not_read(
        self, scene: dict[str, Any]
    ) -> None:
        """Point-in-time applies to a share count exactly as it does to a fact."""
        await _seed_share_fact(
            scene, on=date(2026, 1, 24), filed=date(2026, 2, 1), shares="9999999"
        )

        found = await _filed_share_count(
            _step_context(scene), company_id=scene["company"].id, request=scene["request"]
        )

        assert found is None

    async def test_no_filed_count_is_nothing_rather_than_a_guess(
        self, scene: dict[str, Any]
    ) -> None:
        found = await _filed_share_count(
            _step_context(scene), company_id=scene["company"].id, request=scene["request"]
        )

        assert found is None
