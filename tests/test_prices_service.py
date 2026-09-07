"""Storing a price series, reading it back adjusted, and the figures that come out of it.

The end-to-end claim of task 29: **a price series is reproducible from the archived response
and the recorded adjustments alone.** `TestTheSeriesIsReproducible` stores a fetched response,
reads it back through the adjustment, and asserts the answer — with nothing held in memory
between the two halves.

The rest is the ways storing could go quietly wrong. A second acquisition over a window
already held must insert nothing rather than duplicating it. A vendor that has *revised* a
bar must collide rather than overwrite, because a figure a report already cited must not
change underneath it. And a pence-quoted listing must not produce a market capitalisation a
hundred times too large.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text

from aer.calc import prices as calc
from aer.calc.engine import CalculationContext
from aer.calc.units import Quantity, SourceRef, Unit
from aer.core.enums import JobStatus, UserRole
from aer.db.models import (
    Company,
    CorporateAction,
    Job,
    PriceBar,
    User,
)
from aer.errors import ValidationError
from aer.fetch.client import FetchResult
from aer.services import assumptions
from aer.services import prices as service
from aer.services.assumptions import UnconfirmedAssumptionError
from aer.services.disagreements import disagreements_for_job
from aer.sources.eodhd import api
from aer.sources.eodhd.client import ActionsResponse, PriceResponse
from tests.request_fixtures import research_request
from tests.workflow_fixtures import AS_OF_DATE

pytestmark = pytest.mark.integration

_TABLES = (
    "companies, securities, research_requests, jobs, users, artefacts, disagreements, assumptions"
)

AS_OF = date(2024, 6, 28)
SOURCE = SourceRef.security("price-series")


def fetch_result() -> FetchResult:
    """A `FetchResult` stand-in. These tests store rows; they do not fetch."""
    return FetchResult(
        url="https://eodhd.com/api/eod/MSFT.US?api_token=REDACTED",
        final_url="https://eodhd.com/api/eod/MSFT.US?api_token=REDACTED",
        status_code=200,
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/json",
        declared_media_type="application/json",
        headers={},
        redirect_chain=(),
        elapsed_ms=1.0,
        attempts=1,
    )


def bars_response(rows: list[api.BarRow], *, as_of: date = AS_OF) -> PriceResponse:
    return PriceResponse(
        symbol="MSFT.US",
        as_of=as_of,
        bars=tuple(rows),
        discarded_after_as_of=0,
        fetch=fetch_result(),
    )


def row(on: date, close: str, *, adjusted: str | None = None) -> api.BarRow:
    value = Decimal(close)
    return api.BarRow(
        on=on,
        open=value,
        high=value,
        low=value,
        close=value,
        adjusted_close=Decimal(adjusted) if adjusted else None,
        volume=1_000,
    )


def actions_response(
    *, splits: list[api.SplitRow] | None = None, dividends: list[api.DividendRow] | None = None
) -> ActionsResponse:
    return ActionsResponse(
        symbol="MSFT.US",
        as_of=AS_OF,
        splits=tuple(splits or []),
        dividends=tuple(dividends or []),
        splits_fetch=fetch_result(),
        dividends_fetch=fetch_result(),
    )


@pytest.fixture
async def security(db_session: Any):
    await db_session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    return await service.upsert_security(
        db_session,
        ticker="MSFT",
        exchange="NASDAQ",
        provider_symbol="MSFT.US",
        quote_currency="USD",
        name="Microsoft Corporation",
    )


@pytest.fixture
async def london(db_session: Any, security):
    """A listing quoted in pence, which is the trap ADR 0032 exists for."""
    return await service.upsert_security(
        db_session,
        ticker="BARC",
        exchange="LSE",
        provider_symbol="BARC.LSE",
        quote_currency="GBX",
    )


@pytest.fixture
def context() -> CalculationContext:
    return CalculationContext(code_version="test")


@pytest.fixture
async def job(db_session: Any, security) -> Job:
    operator = User(email="operator@example.invalid", display_name="Operator", role=UserRole.OWNER)
    db_session.add(operator)
    await db_session.flush()

    request = research_request(
        user_id=operator.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=AS_OF_DATE,
        point_in_time=True,
        base_currency="USD",
        reporting_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.50",
    )
    db_session.add(request)
    await db_session.flush()

    row_ = Job(
        work_order_id=request.id,
        status=JobStatus.RUNNING,
        workflow_version="vertical_slice_v1",
        code_version="test",
        started_at=datetime.now(UTC),
    )
    db_session.add(row_)
    await db_session.flush()
    return row_


# -- Storing ----------------------------------------------------------------------------------


class TestStoringIsIdempotent:
    async def test_the_first_store_inserts_everything(self, db_session, security):
        response = bars_response(
            [row(date(2024, 6, 27), "452.85"), row(date(2024, 6, 28), "446.95")]
        )

        outcome = await service.record_bars(db_session, security=security, response=response)

        assert outcome.inserted == 2
        assert outcome.already_held == 0
        assert await db_session.scalar(select(func.count()).select_from(PriceBar)) == 2

    async def test_a_second_store_of_the_same_window_inserts_nothing(self, db_session, security):
        response = bars_response(
            [row(date(2024, 6, 27), "452.85"), row(date(2024, 6, 28), "446.95")]
        )
        await service.record_bars(db_session, security=security, response=response)

        outcome = await service.record_bars(db_session, security=security, response=response)

        assert outcome.inserted == 0
        assert outcome.already_held == 2
        assert await db_session.scalar(select(func.count()).select_from(PriceBar)) == 2

    async def test_an_overlapping_window_inserts_only_the_new_days(self, db_session, security):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 27), "452.85")]),
        )

        outcome = await service.record_bars(
            db_session,
            security=security,
            response=bars_response(
                [row(date(2024, 6, 27), "452.85"), row(date(2024, 6, 28), "446.95")]
            ),
        )

        assert outcome.inserted == 1
        assert outcome.already_held == 1

    async def test_upserting_a_listing_twice_gives_one_row(self, db_session, security):
        again = await service.upsert_security(
            db_session,
            ticker="MSFT",
            exchange="NASDAQ",
            provider_symbol="MSFT.US",
            quote_currency="USD",
        )
        assert again.id == security.id

    async def test_a_peer_resolved_later_gets_its_company_filled_in(self, db_session, security):
        """A price series can exist before the company does. ADR 0032."""
        assert security.company_id is None
        company = Company(
            name="Microsoft Corporation", cik="0000789019", ticker="MSFT", exchange="NASDAQ"
        )
        db_session.add(company)
        await db_session.flush()

        again = await service.upsert_security(
            db_session,
            ticker="MSFT",
            exchange="NASDAQ",
            provider_symbol="MSFT.US",
            quote_currency="USD",
            company_id=company.id,
        )

        assert again.id == security.id
        assert again.company_id == company.id

    async def test_an_existing_link_is_never_re_pointed(self, db_session, security):
        """Filled in, not overwritten: a peer already attached to a company stays attached."""
        first = Company(name="First", cik="0000000001", ticker="AAA", exchange="NASDAQ")
        second = Company(name="Second", cik="0000000002", ticker="BBB", exchange="NASDAQ")
        db_session.add_all([first, second])
        await db_session.flush()

        security.company_id = first.id
        await db_session.flush()

        again = await service.upsert_security(
            db_session,
            ticker="MSFT",
            exchange="NASDAQ",
            provider_symbol="MSFT.US",
            quote_currency="USD",
            company_id=second.id,
        )
        assert again.company_id == first.id


class TestARevisedBarCollidesRatherThanOverwriting:
    async def test_a_changed_close_is_reported_as_a_conflict(self, db_session, security):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "446.95")]),
        )

        outcome = await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "999.00")]),
        )

        assert outcome.inserted == 0
        assert len(outcome.conflicts) == 1
        assert outcome.conflicts[0].stored_close == Decimal("446.950000")
        assert outcome.conflicts[0].incoming_close == Decimal("999.00")

    async def test_the_stored_figure_is_not_changed(self, db_session, security):
        """A figure a report already cited must not move underneath it."""
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "446.95")]),
        )
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "999.00")]),
        )

        stored = await db_session.scalar(select(PriceBar))
        assert stored.close == Decimal("446.950000")

    async def test_it_reaches_the_disagreement_ladder(self, db_session, security, job):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "446.95")]),
        )
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "999.00")]),
            job_id=job.id,
        )

        recorded = await disagreements_for_job(db_session, job.id)
        assert len(recorded) == 1
        assert "MSFT.US close on 2024-06-28" in recorded[0].topic


class TestStoringActions:
    async def test_splits_and_dividends_are_inserted(self, db_session, security):
        response = actions_response(
            splits=[api.SplitRow(ex_date=date(2024, 5, 1), ratio=Decimal(2), raw="2/1")],
            dividends=[
                api.DividendRow(ex_date=date(2024, 5, 15), amount=Decimal("0.75"), currency="USD")
            ],
        )

        outcome = await service.record_actions(db_session, security=security, response=response)

        assert outcome.splits_inserted == 1
        assert outcome.dividends_inserted == 1

    async def test_a_second_store_inserts_nothing(self, db_session, security):
        response = actions_response(
            splits=[api.SplitRow(ex_date=date(2024, 5, 1), ratio=Decimal(2), raw="2/1")],
            dividends=[
                api.DividendRow(ex_date=date(2024, 5, 15), amount=Decimal("0.75"), currency="USD")
            ],
        )
        await service.record_actions(db_session, security=security, response=response)

        outcome = await service.record_actions(db_session, security=security, response=response)

        assert outcome.splits_inserted == 0
        assert outcome.dividends_inserted == 0
        assert outcome.already_held == 2

    async def test_an_ordinary_and_a_special_dividend_on_one_day_both_store(
        self, db_session, security
    ):
        """The reason a dividend's identity includes its amount. Migration 0018."""
        response = actions_response(
            dividends=[
                api.DividendRow(ex_date=date(2024, 5, 15), amount=Decimal("0.75"), currency="USD"),
                api.DividendRow(ex_date=date(2024, 5, 15), amount=Decimal("2.50"), currency="USD"),
            ]
        )

        outcome = await service.record_actions(db_session, security=security, response=response)

        assert outcome.dividends_inserted == 2
        assert await db_session.scalar(select(func.count()).select_from(CorporateAction)) == 2


# -- Reading back -----------------------------------------------------------------------------


class TestTheSeriesIsReproducible:
    """Task 29's acceptance criterion: the response plus the recorded actions is enough."""

    async def test_a_stored_series_reads_back_adjusted(self, db_session, security):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response(
                [
                    row(date(2024, 6, 24), "100"),
                    row(date(2024, 6, 25), "102"),
                    row(date(2024, 6, 26), "50"),
                ]
            ),
        )
        await service.record_actions(
            db_session,
            security=security,
            response=actions_response(
                splits=[api.SplitRow(ex_date=date(2024, 6, 26), ratio=Decimal(2), raw="2/1")]
            ),
        )

        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)

        assert [bar.split_adjusted_close for bar in series.bars] == [
            Decimal(50),
            Decimal(51),
            Decimal(50),
        ]

    async def test_an_earlier_as_of_date_sees_neither_the_bar_nor_the_split(
        self, db_session, security
    ):
        """The clamp is in the query, and the split had not happened yet."""
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response(
                [
                    row(date(2024, 6, 24), "100"),
                    row(date(2024, 6, 25), "102"),
                    row(date(2024, 6, 26), "50"),
                ]
            ),
        )
        await service.record_actions(
            db_session,
            security=security,
            response=actions_response(
                splits=[api.SplitRow(ex_date=date(2024, 6, 26), ratio=Decimal(2), raw="2/1")]
            ),
        )

        series = await service.adjusted_series_for(db_session, security, as_of=date(2024, 6, 25))

        assert [bar.on for bar in series.bars] == [date(2024, 6, 24), date(2024, 6, 25)]
        assert [bar.split_adjusted_close for bar in series.bars] == [Decimal(100), Decimal(102)]

    async def test_a_dividend_in_a_foreign_currency_stops_the_series(self, db_session, london):
        """A pence-quoted listing paying in dollars. Refused, not converted at a guess."""
        await service.record_bars(
            db_session,
            security=london,
            response=bars_response([row(date(2024, 6, 27), "210"), row(date(2024, 6, 28), "212")]),
        )
        await service.record_actions(
            db_session,
            security=london,
            response=actions_response(
                dividends=[
                    api.DividendRow(
                        ex_date=date(2024, 6, 28), amount=Decimal("0.07"), currency="USD"
                    )
                ]
            ),
        )

        with pytest.raises(calc.CurrencyMismatchError):
            await service.adjusted_series_for(db_session, london, as_of=AS_OF)


class TestTheVendorIsACrossCheckNotAnAnswer:
    async def test_an_agreeing_vendor_produces_no_divergence(self, db_session, security):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response(
                [
                    row(date(2024, 6, 27), "100", adjusted="100"),
                    row(date(2024, 6, 28), "102", adjusted="102"),
                ]
            ),
        )
        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)

        assert service.vendor_divergence(series, vendor={date(2024, 6, 27): Decimal(100)}) == ()

    async def test_a_disagreeing_vendor_is_surfaced(self, db_session, security):
        """Which is impossible if only one of the two figures is stored."""
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "100", adjusted="90")]),
        )
        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)

        divergences = service.vendor_divergence(series, vendor={date(2024, 6, 28): Decimal(90)})

        assert len(divergences) == 1
        assert divergences[0].ours == Decimal(100)
        assert divergences[0].theirs == Decimal(90)

    async def test_rounding_alone_is_within_tolerance(self, db_session, security):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "100.00")]),
        )
        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)

        assert (
            service.vendor_divergence(series, vendor={date(2024, 6, 28): Decimal("100.01")}) == ()
        )


# -- The figures ------------------------------------------------------------------------------


class TestMarketCapitalisation:
    async def test_a_dollar_listing_multiplies_straight_through(
        self, db_session, security, context
    ):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "446.95")]),
        )
        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)
        shares = Quantity.of(Decimal(1_000_000), Unit.base("shares"), source=SOURCE)

        cap = service.market_capitalisation_for(
            context, series=series, shares=shares, price_source=SOURCE
        )

        assert cap.unit == Unit.currency("USD")
        assert cap.value == Decimal("446950000.000000")

    async def test_a_pence_listing_converts_first(self, db_session, london, context):
        """250 pence is £2.50. Skipping this gives a figure a hundred times too large."""
        await service.record_bars(
            db_session, security=london, response=bars_response([row(date(2024, 6, 28), "250")])
        )
        series = await service.adjusted_series_for(db_session, london, as_of=AS_OF)
        shares = Quantity.of(Decimal(1_000_000), Unit.base("shares"), source=SOURCE)

        cap = service.market_capitalisation_for(
            context, series=series, shares=shares, price_source=SOURCE
        )

        assert cap.unit == Unit.currency("GBP")
        assert cap.value == Decimal("2500000.000000")

    async def test_the_conversion_is_a_recorded_step(self, db_session, london, context):
        await service.record_bars(
            db_session, security=london, response=bars_response([row(date(2024, 6, 28), "250")])
        )
        series = await service.adjusted_series_for(db_session, london, as_of=AS_OF)
        shares = Quantity.of(Decimal(1_000_000), Unit.base("shares"), source=SOURCE)

        service.market_capitalisation_for(
            context, series=series, shares=shares, price_source=SOURCE
        )

        assert [record.name for record in context.records] == [
            "price_in_major_units",
            "market_capitalisation",
        ]

    async def test_a_dollar_listing_has_no_conversion_step(self, db_session, security, context):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "446.95")]),
        )
        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)
        shares = Quantity.of(Decimal(1_000_000), Unit.base("shares"), source=SOURCE)

        service.market_capitalisation_for(
            context, series=series, shares=shares, price_source=SOURCE
        )

        assert [record.name for record in context.records] == ["market_capitalisation"]


class TestBeta:
    async def test_a_security_that_tracks_the_index_has_a_beta_of_one(
        self, db_session, security, london, context
    ):
        for listing, factor in ((security, Decimal(1)), (london, Decimal(1))):
            await service.record_bars(
                db_session,
                security=listing,
                response=bars_response(
                    [
                        row(date(2021, 1, 1) + timedelta(days=31 * i), str(100 + i * factor))
                        for i in range(40)
                    ]
                ),
            )

        subject = await service.adjusted_series_for(db_session, security, as_of=AS_OF)
        market = await service.adjusted_series_for(db_session, london, as_of=AS_OF)

        result = service.beta_against(
            context,
            subject=subject,
            market=market,
            subject_source=SOURCE,
            market_source=SourceRef.security("market-series"),
        )
        assert result.value == Decimal(1)

    async def test_two_windows_that_do_not_match_are_refused(self, db_session, security, context):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 27), "100"), row(date(2024, 6, 28), "101")]),
        )
        subject = await service.adjusted_series_for(db_session, security, as_of=AS_OF)
        market = await service.adjusted_series_for(db_session, security, as_of=date(2024, 6, 27))

        with pytest.raises(ValidationError):
            service.beta_against(
                context,
                subject=subject,
                market=market,
                subject_source=SOURCE,
                market_source=SOURCE,
            )

    async def test_too_little_shared_history_is_refused(
        self, db_session, security, london, context
    ):
        for listing in (security, london):
            await service.record_bars(
                db_session,
                security=listing,
                response=bars_response(
                    [row(date(2024, 6, 27), "100"), row(date(2024, 6, 28), "101")]
                ),
            )

        subject = await service.adjusted_series_for(db_session, security, as_of=AS_OF)
        market = await service.adjusted_series_for(db_session, london, as_of=AS_OF)

        with pytest.raises(calc.InsufficientHistoryError):
            service.beta_against(
                context,
                subject=subject,
                market=market,
                subject_source=SOURCE,
                market_source=SOURCE,
            )


class TestThePriceIsPerShare:
    async def test_it_carries_a_per_share_unit(self, db_session, security):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "446.95")]),
        )
        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)

        price = service.price_quantity(series, source=SOURCE)

        assert price.unit == Unit.currency("USD") / Unit.base("shares")

    async def test_a_day_the_market_was_closed_is_refused(self, db_session, security):
        await service.record_bars(
            db_session,
            security=security,
            response=bars_response([row(date(2024, 6, 28), "446.95")]),
        )
        series = await service.adjusted_series_for(db_session, security, as_of=AS_OF)

        with pytest.raises(ValidationError):
            service.price_quantity(series, on=date(2024, 6, 29), source=SOURCE)


class TestBetaIsProposedNotDecided:
    """`docs/archive/phase-3-plan.md`: a first-class assumption with an optional computed
    override."""

    async def _two_series(self, db_session, security, london):
        for listing in (security, london):
            await service.record_bars(
                db_session,
                security=listing,
                response=bars_response(
                    [
                        row(date(2021, 1, 1) + timedelta(days=31 * i), str(100 + i))
                        for i in range(40)
                    ]
                ),
            )
        subject = await service.adjusted_series_for(db_session, security, as_of=AS_OF)
        market = await service.adjusted_series_for(db_session, london, as_of=AS_OF)
        return subject, market

    async def test_it_writes_an_assumption(self, db_session, security, london, context, job):
        subject, market = await self._two_series(db_session, security, london)

        proposed = await service.propose_computed_beta(
            db_session,
            context,
            request_id=job.work_order_id,
            subject=subject,
            market=market,
            subject_source=SOURCE,
            market_source=SourceRef.security("market-series"),
            market_label="FTSE 100",
            job_id=job.id,
        )

        assert proposed.name == service.BETA_ASSUMPTION
        assert proposed.value == Decimal(1)
        assert proposed.unit == "pure"

    async def test_it_is_never_confirmed(self, db_session, security, london, context, job):
        """A regression is evidence for a beta, not a decision about one."""
        subject, market = await self._two_series(db_session, security, london)

        proposed = await service.propose_computed_beta(
            db_session,
            context,
            request_id=job.work_order_id,
            subject=subject,
            market=market,
            subject_source=SOURCE,
            market_source=SourceRef.security("market-series"),
            market_label="FTSE 100",
        )

        assert proposed.approved is False
        with pytest.raises(UnconfirmedAssumptionError):
            assumptions.as_quantity(proposed)

    async def test_the_justification_names_the_proxy_and_the_window(
        self, db_session, security, london, context, job
    ):
        """A beta quoted without those is not reproducible."""
        subject, market = await self._two_series(db_session, security, london)

        proposed = await service.propose_computed_beta(
            db_session,
            context,
            request_id=job.work_order_id,
            subject=subject,
            market=market,
            subject_source=SOURCE,
            market_source=SourceRef.security("market-series"),
            market_label="FTSE 100",
            frequency=calc.Frequency.MONTHLY,
        )

        assert "FTSE 100" in proposed.justification
        assert "monthly" in proposed.justification
        assert "2024-06-28" in proposed.justification
        assert "paired observations" in proposed.justification

    async def test_it_is_attributed_to_code_rather_than_to_a_person(
        self, db_session, security, london, context, job
    ):
        subject, market = await self._two_series(db_session, security, london)

        proposed = await service.propose_computed_beta(
            db_session,
            context,
            request_id=job.work_order_id,
            subject=subject,
            market=market,
            subject_source=SOURCE,
            market_source=SourceRef.security("market-series"),
            market_label="FTSE 100",
        )

        assert proposed.proposed_by == "aer.services.prices"
