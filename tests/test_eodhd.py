"""The market-data adapter: the clamp, the ledger, and the key that must not escape.

Three claims are load-bearing here and each has its own class.

**A bar after the as-of date never comes back.** Task 29 asks for the clamp to be in the
adapter rather than in the caller, so it is asserted on the adapter: the URL carries the
bound, and the parser drops anything the provider sent anyway. The fixture deliberately
contains a bar dated after the as-of date, because a test whose fixture is already clean
proves only that the fixture is clean.

**The daily weighted allowance is a cap, not a warning.** A fundamentals request costs ten
calls where a price series costs one, so a rate limiter counting requests cannot see it.

**The API key reaches nothing.** Not the log, not the recorded URL, not the artefact. EODHD
takes it as a query parameter, so it is in the URL of every request, and the URL is the thing
this platform records and shows people.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

import httpx
import pytest
import respx

from aer.config import Settings
from aer.core.enums import Provider, SourceTier
from aer.errors import ConfigError, ExternalServiceError, ValidationError
from aer.fetch.client import SafeFetcher
from aer.fetch.credentials import redact_credentials
from aer.fetch.errors import CircuitOpenError
from aer.fetch.policy import DEFAULT_POLICIES, RetentionClass
from aer.logging import configure_logging
from aer.sources.eodhd import api
from aer.sources.eodhd.budget import (
    DAILY_WEIGHTED_CALLS,
    WEIGHTS,
    EndpointCost,
    WeightedCallBudget,
)
from aer.sources.eodhd.client import EodhdClient
from tests.fetch_fixtures import public_resolver
from tests.log_helpers import structlog_events

pytestmark = pytest.mark.usefixtures("no_real_sockets")

FIXTURES: Final = Path(__file__).parent / "fixtures" / "eodhd"
API_KEY: Final = "test-eodhd-key-not-a-real-one"  # pragma: allowlist secret
AS_OF: Final = date(2024, 6, 28)
MSFT: Final = "MSFT.US"
BARC: Final = "BARC.LSE"


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


@pytest.fixture
def eodhd_settings(fetch_settings: Settings, settings_env) -> Settings:
    settings_env.setenv("AER_EODHD_API_KEY", API_KEY)
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def eodhd_fetcher(eodhd_settings, artefact_store, limiter, breaker, sleeper) -> SafeFetcher:
    """A fetcher wired to respx, as `test_macro.py` does it and for the same reasons."""
    return SafeFetcher(
        eodhd_settings,
        store=artefact_store,
        limiter=limiter,
        breaker=breaker,
        robots=None,
        sleep=sleeper,
        resolver=public_resolver("104.16.0.1"),
        transport_factory=httpx.AsyncHTTPTransport,
    )


@pytest.fixture
def budget(redis_client) -> WeightedCallBudget:
    return WeightedCallBudget(redis_client, clock=lambda: datetime(2024, 6, 28, 9, 0, tzinfo=UTC))


@pytest.fixture
def client(eodhd_fetcher, artefact_store, eodhd_settings, budget) -> EodhdClient:
    return EodhdClient(eodhd_fetcher, artefact_store, settings=eodhd_settings, budget=budget)


# -- URLs ------------------------------------------------------------------------------------


class TestTheClampIsInTheUrl:
    def test_every_bars_url_carries_the_as_of_date(self):
        url = api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF)
        assert "to=2024-06-28" in url

    def test_there_is_no_way_to_build_one_without_it(self):
        """`as_of` is keyword-only and has no default. This is the whole guarantee."""
        with pytest.raises(TypeError):
            api.bars_url(MSFT, api_token=API_KEY)  # type: ignore[call-arg]

    def test_the_splits_and_dividends_urls_carry_it_too(self):
        assert "to=2024-06-28" in api.splits_url(MSFT, api_token=API_KEY, as_of=AS_OF)
        assert "to=2024-06-28" in api.dividends_url(MSFT, api_token=API_KEY, as_of=AS_OF)

    def test_a_window_running_backwards_is_refused(self):
        with pytest.raises(ValidationError):
            api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF, since=date(2025, 1, 1))

    def test_a_symbol_with_a_slash_cannot_walk_out_of_the_path(self):
        url = api.bars_url("../../admin", api_token=API_KEY, as_of=AS_OF)
        assert "/api/eod/..%2F..%2Fadmin?" in url

    def test_the_fundamentals_url_says_it_has_no_bound(self):
        """It has none to give, so the clamp moves to the parser. Asserted so a future
        edit that adds `to=` and drops the parser's check is visible."""
        url = api.fundamentals_url(MSFT, api_token=API_KEY)
        assert "to=" not in url


# -- Parsing ---------------------------------------------------------------------------------


class TestABarAfterTheAsOfDateIsAbsent:
    """Task 29's point-in-time criterion, asserted on the adapter."""

    def test_the_parser_drops_what_the_provider_should_not_have_sent(self):
        parsed = api.parse_bars(fixture("msft_eod"), symbol=MSFT, as_of=AS_OF)

        assert [row.on for row in parsed.rows] == [
            date(2024, 6, 24),
            date(2024, 6, 25),
            date(2024, 6, 26),
            date(2024, 6, 27),
            date(2024, 6, 28),
        ]
        assert all(row.on <= AS_OF for row in parsed.rows)

    def test_it_counts_them_rather_than_dropping_them_silently(self):
        """A provider ignoring `to` is worth knowing about, not worth quietly correcting."""
        parsed = api.parse_bars(fixture("msft_eod"), symbol=MSFT, as_of=AS_OF)
        assert parsed.discarded_after_as_of == 1

    def test_an_earlier_as_of_date_returns_less(self):
        parsed = api.parse_bars(fixture("msft_eod"), symbol=MSFT, as_of=date(2024, 6, 25))
        assert [row.on for row in parsed.rows] == [date(2024, 6, 24), date(2024, 6, 25)]

    def test_a_dividend_after_the_as_of_date_is_absent(self):
        dividends = api.parse_dividends(fixture("msft_div"), symbol=MSFT, as_of=AS_OF)
        assert [row.ex_date for row in dividends] == [date(2024, 2, 14), date(2024, 5, 15)]

    def test_a_split_after_the_as_of_date_is_absent(self):
        """The one that matters most: a split restates every price before it.

        The fixture carries a 2025 split deliberately. Without one, the splits parser's clamp
        was exercised by nothing at all — which a sabotage pass found by deleting the clamp
        and watching every test still pass.
        """
        splits = api.parse_splits(fixture("msft_splits"), symbol=MSFT, as_of=AS_OF)

        assert [row.ex_date for row in splits] == [date(1999, 3, 29), date(2003, 2, 18)]
        assert all(row.ex_date <= AS_OF for row in splits)

    def test_a_later_as_of_date_sees_that_split(self):
        """So the absence above is the clamp working, not the fixture being empty."""
        splits = api.parse_splits(fixture("msft_splits"), symbol=MSFT, as_of=date(2025, 12, 31))

        assert [row.ex_date for row in splits][-1] == date(2025, 3, 14)

    def test_bars_come_back_in_date_order(self):
        parsed = api.parse_bars(fixture("msft_eod"), symbol=MSFT, as_of=AS_OF)
        assert list(parsed.rows) == sorted(parsed.rows, key=lambda row: row.on)


class TestASplitIsAPairNotANumber:
    def test_a_two_for_one_becomes_two(self):
        splits = api.parse_splits(fixture("msft_splits"), symbol=MSFT, as_of=AS_OF)
        assert [row.ratio for row in splits] == [Decimal(2), Decimal(2)]

    def test_splits_come_back_in_date_order(self):
        splits = api.parse_splits(fixture("msft_splits"), symbol=MSFT, as_of=AS_OF)
        assert list(splits) == sorted(splits, key=lambda row: row.ex_date)

    def test_a_one_for_ten_consolidation_becomes_a_tenth(self):
        """Read as a whole number instead, every historical price is ten times wrong."""
        splits = api.parse_splits(fixture("consolidation_splits"), symbol=MSFT, as_of=AS_OF)
        assert splits[0].ratio == Decimal("0.1")

    def test_the_vendor_string_is_kept(self):
        splits = api.parse_splits(fixture("msft_splits"), symbol=MSFT, as_of=AS_OF)
        assert splits[0].raw == "2.000000/1.000000"

    @pytest.mark.parametrize("value", ["", "not a ratio", "2/0", "0/1", "-2/1", None, []])
    def test_an_unreadable_ratio_is_refused(self, value):
        payload = json.dumps([{"date": "2020-01-02", "split": value}]).encode()

        with pytest.raises(ExternalServiceError):
            api.parse_splits(payload, symbol=MSFT, as_of=AS_OF)


class TestADividendKnowsItsCurrency:
    def test_the_stated_currency_wins(self):
        dividends = api.parse_dividends(fixture("barc_div"), symbol=BARC, as_of=AS_OF)
        assert dividends[0].currency == "GBX"

    def test_a_foreign_dividend_keeps_its_own_currency(self):
        """A London listing in pence paying in dollars. The quote currency must not overwrite
        it, because the adjustment would then be wrong by the exchange rate."""
        dividends = api.parse_dividends(
            fixture("foreign_div"), symbol=BARC, as_of=AS_OF, default_currency="GBX"
        )
        assert dividends[0].currency == "USD"

    def test_a_row_with_no_currency_and_no_default_is_refused(self):
        with pytest.raises(ExternalServiceError) as excinfo:
            api.parse_dividends(fixture("undated_div"), symbol=BARC, as_of=AS_OF)
        assert "no currency" in str(excinfo.value)

    def test_the_default_fills_in_only_when_the_row_is_silent(self):
        dividends = api.parse_dividends(
            fixture("undated_div"), symbol=BARC, as_of=AS_OF, default_currency="GBX"
        )
        assert dividends[0].currency == "GBX"

    def test_the_administrative_dates_are_kept(self):
        dividends = api.parse_dividends(fixture("msft_div"), symbol=MSFT, as_of=AS_OF)
        latest = dividends[-1]
        assert latest.record_date == date(2024, 5, 16)
        assert latest.pay_date == date(2024, 6, 13)


class TestTheShareCountIsDated:
    def test_it_takes_the_most_recent_count_at_or_before_the_as_of_date(self):
        shares = api.parse_shares_outstanding(
            fixture("msft_fundamentals"), symbol=MSFT, as_of=AS_OF
        )
        assert shares.as_reported_on == date(2024, 3, 31)
        assert shares.shares == Decimal(7_432_000_000)

    def test_a_later_count_is_never_used(self):
        """The quiet look-ahead: a correct price and next quarter's share count."""
        shares = api.parse_shares_outstanding(
            fixture("msft_fundamentals"), symbol=MSFT, as_of=AS_OF
        )
        assert shares.as_reported_on <= AS_OF

    def test_an_earlier_as_of_date_reaches_further_back(self):
        shares = api.parse_shares_outstanding(
            fixture("msft_fundamentals"), symbol=MSFT, as_of=date(2023, 12, 31)
        )
        assert shares.as_reported_on == date(2023, 6, 30)

    def test_the_undated_headline_figure_is_never_used(self):
        """`SharesStats.SharesOutstanding` is today's, whatever the as-of date says."""
        with pytest.raises(ExternalServiceError) as excinfo:
            api.parse_shares_outstanding(fixture("fundamentals_undated"), symbol=MSFT, as_of=AS_OF)
        assert "no dated share count" in str(excinfo.value)

    def test_an_as_of_date_before_the_first_count_is_refused(self):
        with pytest.raises(ExternalServiceError):
            api.parse_shares_outstanding(
                fixture("msft_fundamentals"), symbol=MSFT, as_of=date(2000, 1, 1)
            )


class TestAShapeChangeStopsRatherThanGuessing:
    def test_an_object_where_a_list_belongs_is_refused(self):
        with pytest.raises(ExternalServiceError) as excinfo:
            api.parse_bars(fixture("not_a_list"), symbol=MSFT, as_of=AS_OF)
        assert "returns a list" in str(excinfo.value)

    def test_html_reaching_the_parser_is_a_provider_outage(self):
        with pytest.raises(ExternalServiceError) as excinfo:
            api.parse_bars(b"<html><body>502</body></html>", symbol=MSFT, as_of=AS_OF)
        assert "not JSON" in str(excinfo.value)

    def test_a_bar_missing_a_close_is_refused(self):
        payload = json.dumps([{"date": "2024-06-28", "open": 1, "high": 2, "low": 1}]).encode()

        with pytest.raises(ExternalServiceError) as excinfo:
            api.parse_bars(payload, symbol=MSFT, as_of=AS_OF)
        assert "'close'" in str(excinfo.value)

    def test_a_row_with_no_date_is_refused(self):
        payload = json.dumps([{"open": 1, "high": 2, "low": 1, "close": 2}]).encode()

        with pytest.raises(ExternalServiceError):
            api.parse_bars(payload, symbol=MSFT, as_of=AS_OF)

    def test_a_price_goes_through_its_repr_not_through_a_float(self):
        """`Decimal(0.1)` is not one tenth. The figure stored is the one printed."""
        payload = json.dumps(
            [{"date": "2024-06-28", "open": 0.1, "high": 0.1, "low": 0.1, "close": 0.1}]
        ).encode()

        parsed = api.parse_bars(payload, symbol=MSFT, as_of=AS_OF)
        assert parsed.rows[0].close == Decimal("0.1")


# -- The weighted ledger -----------------------------------------------------------------------


class TestTheDailyAllowanceIsACap:
    async def test_a_price_request_costs_one_call(self, budget):
        state = await budget.reserve(EndpointCost.EOD)
        assert state.spent == 1

    async def test_a_fundamentals_request_costs_ten(self, budget):
        """Which is the whole reason a request-rate limiter cannot do this job."""
        state = await budget.reserve(EndpointCost.FUNDAMENTALS)
        assert state.spent == WEIGHTS[EndpointCost.FUNDAMENTALS] == 10

    async def test_spend_accumulates_across_calls(self, budget):
        await budget.reserve(EndpointCost.EOD)
        await budget.reserve(EndpointCost.SPLITS)
        state = await budget.reserve(EndpointCost.FUNDAMENTALS)
        assert state.spent == 12

    async def test_exceeding_it_refuses_rather_than_warning(self, redis_client):
        small = WeightedCallBudget(redis_client, allowance=5)
        await small.reserve(EndpointCost.EOD, count=5)

        with pytest.raises(CircuitOpenError) as excinfo:
            await small.reserve(EndpointCost.EOD)
        assert "allowance is spent" in str(excinfo.value)

    async def test_a_refused_reservation_is_refunded(self, redis_client):
        """Otherwise the counter climbs on every refusal and the day never recovers."""
        small = WeightedCallBudget(redis_client, allowance=5)
        await small.reserve(EndpointCost.EOD, count=5)

        with pytest.raises(CircuitOpenError):
            await small.reserve(EndpointCost.FUNDAMENTALS)

        assert (await small.state()).spent == 5

    async def test_the_refusal_says_when_the_counter_resets(self, redis_client):
        small = WeightedCallBudget(
            redis_client,
            allowance=1,
            clock=lambda: datetime(2024, 6, 28, 18, 0, tzinfo=UTC),
        )
        await small.reserve(EndpointCost.EOD)

        with pytest.raises(CircuitOpenError) as excinfo:
            await small.reserve(EndpointCost.EOD)
        assert excinfo.value.context["retry_after_seconds"] == 6 * 3600

    async def test_the_counter_is_keyed_on_the_provider_s_day(self, redis_client):
        """UTC, because that is when the provider's counter resets."""
        today = WeightedCallBudget(
            redis_client, clock=lambda: datetime(2024, 6, 28, 23, 30, tzinfo=UTC)
        )
        tomorrow = WeightedCallBudget(
            redis_client, clock=lambda: datetime(2024, 6, 29, 0, 30, tzinfo=UTC)
        )

        await today.reserve(EndpointCost.FUNDAMENTALS)

        assert (await today.state()).spent == 10
        assert (await tomorrow.state()).spent == 0

    async def test_the_provider_s_own_count_wins(self, budget):
        """The ledger is a model of the provider's counter and drifts from it."""
        await budget.reserve(EndpointCost.EOD)

        state = await budget.reconcile({"X-RateLimit-Remaining": "97500"})

        assert state is not None
        assert state.spent == DAILY_WEIGHTED_CALLS - 97_500

    async def test_a_response_with_no_header_teaches_nothing_and_says_so(self, budget):
        assert await budget.reconcile({"content-type": "application/json"}) is None

    async def test_the_header_lookup_is_case_insensitive(self, budget):
        assert await budget.reconcile({"x-ratelimit-remaining": "99000"}) is not None
        assert await budget.reconcile({"X-RATELIMIT-REMAINING": "99000"}) is not None

    async def test_an_unreadable_header_leaves_the_estimate_alone(self, budget):
        await budget.reserve(EndpointCost.EOD)
        assert await budget.reconcile({"x-ratelimit-remaining": "lots"}) is None
        assert (await budget.state()).spent == 1

    async def test_an_unlisted_endpoint_costs_one_rather_than_failing(self, budget):
        state = await budget.reserve(EndpointCost.BULK)
        assert state.spent == WEIGHTS[EndpointCost.BULK]


# -- The client ---------------------------------------------------------------------------------


class TestTheClientNeverTakesAUrl:
    @respx.mock
    async def test_it_retrieves_bars_by_symbol(self, client):
        respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_eod"), headers={"content-type": "application/json"}
            )
        )

        response = await client.fetch_bars(MSFT, as_of=AS_OF)

        assert len(response.bars) == 5
        assert response.tier is SourceTier.T4_LICENSED_MARKET

    @respx.mock
    async def test_a_bar_after_the_as_of_date_never_reaches_the_caller(self, client):
        respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_eod"), headers={"content-type": "application/json"}
            )
        )

        response = await client.fetch_bars(MSFT, as_of=AS_OF)

        assert all(row.on <= AS_OF for row in response.bars)
        assert response.discarded_after_as_of == 1

    @respx.mock
    async def test_it_retrieves_splits_and_dividends_together(self, client):
        respx.get(url__startswith=f"{api.API_ROOT}/splits/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_splits"), headers={"content-type": "application/json"}
            )
        )
        respx.get(url__startswith=f"{api.API_ROOT}/div/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_div"), headers={"content-type": "application/json"}
            )
        )

        response = await client.fetch_actions(MSFT, as_of=AS_OF)

        assert len(response.splits) == 2
        assert len(response.dividends) == 2

    @respx.mock
    async def test_it_spends_the_allowance_it_reserved(self, client, budget):
        respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_eod"), headers={"content-type": "application/json"}
            )
        )

        await client.fetch_bars(MSFT, as_of=AS_OF)
        assert (await budget.state()).spent == 1

    @respx.mock
    async def test_an_exhausted_allowance_stops_the_request_being_made(
        self, eodhd_fetcher, artefact_store, eodhd_settings, redis_client
    ):
        """Reserved *before* the request, so an over-budget call never reaches the wire."""
        route = respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(200, content=b"[]")
        )
        spent = WeightedCallBudget(redis_client, allowance=1)
        await spent.reserve(EndpointCost.EOD)
        client = EodhdClient(eodhd_fetcher, artefact_store, settings=eodhd_settings, budget=spent)

        with pytest.raises(CircuitOpenError):
            await client.fetch_bars(MSFT, as_of=AS_OF)
        assert not route.calls

    @respx.mock
    async def test_the_licence_note_states_what_may_not_be_done(self, client):
        note = client.licence_note
        assert "prohibited" in note
        assert "no derived-data exemption" in note
        assert "deleted within one month" in note


class TestAMissingSubscriptionFailsByName:
    @pytest.fixture
    def keyless(self, fetch_settings, artefact_store, limiter, breaker, sleeper, budget):
        fetcher = SafeFetcher(
            fetch_settings,
            store=artefact_store,
            limiter=limiter,
            breaker=breaker,
            robots=None,
            sleep=sleeper,
            resolver=public_resolver("104.16.0.1"),
            transport_factory=httpx.AsyncHTTPTransport,
        )
        return EodhdClient(fetcher, artefact_store, settings=fetch_settings, budget=budget)

    async def test_it_names_the_environment_variable(self, keyless):
        with pytest.raises(ConfigError) as excinfo:
            await keyless.fetch_bars(MSFT, as_of=AS_OF)
        assert "AER_EODHD_API_KEY" in str(excinfo.value)

    @respx.mock
    async def test_it_does_not_return_an_empty_series(self, keyless):
        """Which is indistinguishable from a company that has never traded."""
        route = respx.get(url__startswith=api.API_ROOT).mock(
            return_value=httpx.Response(200, content=b"[]")
        )

        with pytest.raises(ConfigError):
            await keyless.fetch_actions(MSFT, as_of=AS_OF)
        assert not route.calls


class TestTheApiKeyReachesNothing:
    def test_the_url_carries_it_because_the_provider_requires_that(self):
        assert API_KEY in api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF)

    def test_redaction_removes_it(self):
        url = api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF)
        assert API_KEY not in redact_credentials(url)
        assert "api_token=REDACTED" in redact_credentials(url)

    def test_redaction_leaves_the_rest_of_the_url_intact(self):
        """The URL is what makes a fetch reproducible; only the key must go."""
        cleaned = redact_credentials(api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF))
        assert "to=2024-06-28" in cleaned
        assert "MSFT.US" in cleaned

    @respx.mock
    async def test_it_is_absent_from_the_recorded_url(self, client):
        """`source_documents.url` is permanent and appears in a report's sources appendix."""
        respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_eod"), headers={"content-type": "application/json"}
            )
        )

        response = await client.fetch_bars(MSFT, as_of=AS_OF)

        assert API_KEY not in response.fetch.url
        assert API_KEY not in response.fetch.final_url
        assert "api_token=REDACTED" in response.fetch.url

    @respx.mock
    async def test_it_is_absent_from_every_log_line(self, client, caplog):
        """The leak this redaction was written for: `SafeFetcher` logs the URL itself.

        Before the fetch layer redacted, `fetch.completed` and `fetch.retrying` carried the
        key in full — and `aer.logging` did not catch it, because it masks by field *name*
        and by value *shape*, and `url` is not a sensitive name while `api_token=abc` is not
        a credential shape.
        """
        respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_eod"), headers={"content-type": "application/json"}
            )
        )

        with caplog.at_level(logging.DEBUG):
            await client.fetch_bars(MSFT, as_of=AS_OF)

        assert caplog.records, "nothing was logged, so this test asserts nothing"
        events = list(structlog_events(caplog.records))
        assert API_KEY not in str(events)

    @respx.mock
    async def test_a_third_party_library_cannot_leak_it_either(self, client, capsys):
        """`httpx` logs the whole request line at INFO, and this codebase does not control it.

        Which is why the credential-parameter pattern is in `aer.logging` rather than only in
        the fetch layer: field-name matching never sees inside a plain string, and the string
        here is written by somebody else's library.
        """
        respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_eod"), headers={"content-type": "application/json"}
            )
        )

        configure_logging(level="INFO", json_output=True)
        await client.fetch_bars(MSFT, as_of=AS_OF)

        # The real shipped path: `configure_logging` puts every record, including foreign
        # ones, through `redact_secrets` on the way out. `caplog` attaches its own handler
        # with its own formatter and bypasses that, so asserting on `caplog.text` would be
        # asserting about pytest rather than about this platform.
        captured = capsys.readouterr().out
        assert "eodhd.com" in captured, "nothing was logged, so this test asserts nothing"
        assert API_KEY not in captured

    @respx.mock
    async def test_it_is_absent_from_the_archived_bytes(self, client, artefact_store):
        """The body is the provider's, so the key is not in it — asserted so a future edit
        that archives the request rather than the response is caught."""
        respx.get(url__startswith=f"{api.API_ROOT}/eod/").mock(
            return_value=httpx.Response(
                200, content=fixture("msft_eod"), headers={"content-type": "application/json"}
            )
        )

        response = await client.fetch_bars(MSFT, as_of=AS_OF)

        assert API_KEY.encode() not in await artefact_store.read(response.fetch.sha256)

    def test_it_redacts_whatever_the_key_looks_like(self):
        """Matched on the parameter, so a rotated key is still hidden in an old log line."""
        assert redact_credentials("https://x.test/?api_token=anything&b=1") == (
            "https://x.test/?api_token=REDACTED&b=1"
        )

    @pytest.mark.parametrize(
        "parameter", ["api_key", "api_token", "apikey", "token", "access_token", "secret"]
    )
    def test_every_conventional_credential_parameter_is_covered(self, parameter):
        """A per-provider opt-in would be right until somebody added an adapter and forgot."""
        assert "REDACTED" in redact_credentials(f"https://x.test/?{parameter}=hunter2")

    def test_a_bare_key_parameter_is_left_alone(self):
        """`key` is a legitimate non-secret parameter in several APIs; hiding it would hide
        something a reader needs while protecting nothing."""
        assert redact_credentials("https://x.test/?key=GDP") == "https://x.test/?key=GDP"


class TestTheProviderIsWiredUpCorrectly:
    def test_the_client_reports_the_licensed_market_tier(self, client):
        assert client.source_tier is SourceTier.T4_LICENSED_MARKET
        assert client.provider is Provider.EODHD

    def test_the_api_root_is_on_the_allowlist(self):
        policy = DEFAULT_POLICIES[Provider.EODHD]
        assert any(host in api.API_ROOT for host in policy.allowed_hosts)

    def test_the_payloads_are_purgeable(self):
        """The subscription obliges deletion within a month of it ending. ADR 0031."""
        assert DEFAULT_POLICIES[Provider.EODHD].retention is RetentionClass.LICENSED


class TestTheWindowIsAWindow:
    def test_a_since_date_narrows_the_request(self):
        url = api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF, since=date(2019, 6, 28))
        assert "from=2019-06-28" in url
        assert "to=2024-06-28" in url

    def test_omitting_it_asks_for_everything_up_to_the_bound(self):
        url = api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF)
        assert "from=" not in url
        assert "to=2024-06-28" in url

    def test_five_years_back_is_expressible(self):
        since = AS_OF - timedelta(days=5 * 365)
        url = api.bars_url(MSFT, api_token=API_KEY, as_of=AS_OF, since=since)
        assert f"from={since.isoformat()}" in url
