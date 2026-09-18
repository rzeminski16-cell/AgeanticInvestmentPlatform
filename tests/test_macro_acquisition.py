"""The risk-free rate is fetched, recorded and proposed — never typed when it can be read.

Phase 1.6. The macro stack was complete and had no caller, so every run reached the
assumptions gate asking the operator for a government yield the platform could have fetched.
Three things are under test here.

**What the acquisition records.** One series at the run's own as-of vintage, the observation
this run will stand on, and the percentage-to-fraction conversion as a step in the ledger.

**What it says when it cannot.** No client, no key, a currency with no documented series, a
reading too old to be the rate on the as-of date: each is a sentence, never a failure of the
run, because the gate has to be able to say why it is asking.

**That the gate proposes it.** A run whose macro step acquired the ten-year Treasury lists
the rate among its proposals, under a proposer that says what it is, and names nothing
outstanding for it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select, text

from aer.errors import ConfigError
from aer.fetch.client import SafeFetcher
from aer.services import calculations as calculation_service
from aer.services.macro_acquisition import (
    PROPOSED_BY,
    STALE_AFTER_DAYS,
    RiskFreeAcquisition,
    acquire_risk_free,
)
from aer.sources.macro import fred
from aer.sources.macro.client import MacroClient
from aer.sources.macro.series import series_for
from tests.fetch_fixtures import public_resolver
from tests.macro_fixtures import StubMacroClient as _StubMacroClient

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_real_sockets")]

FIXTURES = Path(__file__).parent / "fixtures" / "macro"
TEN_YEAR = series_for("us_treasury_10y")
AS_OF = date(2024, 6, 28)
API_KEY = "test-fred-key-not-a-real-one"  # pragma: allowlist secret

# Every stub here answers at the fixture's vintage unless a test says otherwise.
StubMacroClient = partial(_StubMacroClient, vintage=AS_OF)


@pytest.fixture
async def clean(db_session: Any) -> None:
    await db_session.execute(text("TRUNCATE macro_series RESTART IDENTITY CASCADE"))


@pytest.fixture
def macro_fetcher(fetch_settings, artefact_store, limiter, breaker, sleeper):  # type: ignore[no-untyped-def]
    """A fetcher wired to respx, as `test_macro.py` builds one and for the same reasons."""
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


class TestAcquiringTheRate:
    async def test_a_usd_run_records_the_series_and_converts_the_yield(
        self, db_session: Any, clean: None
    ) -> None:
        client = StubMacroClient({date(2024, 6, 27): "4.29", AS_OF: "4.36"})
        ledger = calculation_service.new_context()

        acquired = await acquire_risk_free(
            db_session, client, currency="usd", as_of=AS_OF, context=ledger
        )

        assert acquired.acquired
        assert acquired.currency == "USD"
        assert acquired.series_key == "us_treasury_10y"
        assert acquired.observed_on == AS_OF
        assert acquired.vintage == AS_OF
        assert acquired.quoted == Decimal("4.36")
        assert acquired.rate == Decimal("0.0436")
        assert acquired.observation_id is not None
        assert acquired.reason == ""
        # Asked at the run's own vintage, and only for the documented series.
        assert client.asked == [("us_treasury_10y", AS_OF)]

    async def test_the_observations_are_stored_and_the_conversion_is_in_the_ledger(
        self, db_session: Any, clean: None
    ) -> None:
        from aer.db.models import MacroObservationRow  # noqa: PLC0415 -- only this test

        client = StubMacroClient({date(2024, 6, 27): "4.29", AS_OF: "4.36"})
        ledger = calculation_service.new_context()

        await acquire_risk_free(db_session, client, currency="USD", as_of=AS_OF, context=ledger)

        stored = list(await db_session.scalars(select(MacroObservationRow)))
        assert {(row.observed_on, row.value) for row in stored} == {
            (date(2024, 6, 27), Decimal("4.29")),
            (AS_OF, Decimal("4.36")),
        }
        # ADR 0027: the division is a traced step, never an inline `/ 100`.
        assert [record.function_ref for record in ledger.records] == [
            "aer.calc.wacc:rate_from_percent"
        ]

    async def test_the_justification_names_instrument_date_vintage_and_publisher(
        self, db_session: Any, clean: None
    ) -> None:
        client = StubMacroClient({AS_OF: "4.36"})

        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="USD",
            as_of=AS_OF,
            context=calculation_service.new_context(),
        )

        assert "4.36%" in acquired.justification
        assert "2024-06-28" in acquired.justification
        assert TEN_YEAR.originator in acquired.justification
        assert TEN_YEAR.identifier in acquired.justification
        assert "0.0436" in acquired.justification

    async def test_the_record_survives_the_step_output(self, db_session: Any, clean: None) -> None:
        """Written as strings into the step's record and read back as itself."""
        client = StubMacroClient({AS_OF: "4.36"})
        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="USD",
            as_of=AS_OF,
            context=calculation_service.new_context(),
        )

        assert RiskFreeAcquisition.from_dict(acquired.as_dict()) == acquired
        assert RiskFreeAcquisition.from_dict({}) == RiskFreeAcquisition(currency="")

    async def test_the_reading_is_the_newest_the_vintage_admits(
        self, db_session: Any, clean: None
    ) -> None:
        """A run standing on a Sunday reads Friday's yield: the newest period not after it."""
        friday, sunday = date(2024, 6, 28), date(2024, 6, 30)
        client = StubMacroClient({date(2024, 6, 27): "4.29", friday: "4.36"}, vintage=sunday)

        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="USD",
            as_of=sunday,
            context=calculation_service.new_context(),
        )

        assert acquired.acquired
        assert acquired.observed_on == friday
        assert acquired.quoted == Decimal("4.36")


class TestWhenThereIsNoRate:
    async def test_no_client_is_a_sentence_not_a_failure(
        self, db_session: Any, clean: None
    ) -> None:
        acquired = await acquire_risk_free(
            db_session, None, currency="USD", as_of=AS_OF, context=calculation_service.new_context()
        )

        assert not acquired.acquired
        assert acquired.series_key == "us_treasury_10y"
        assert "No macro client" in acquired.reason
        assert "say which instrument and date" in acquired.reason

    async def test_a_currency_with_no_documented_series_is_refused_in_words(
        self, db_session: Any, clean: None
    ) -> None:
        """Not defaulted to the US yield: that error is the whole rate differential."""
        client = StubMacroClient({AS_OF: "4.36"})

        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="JPY",
            as_of=AS_OF,
            context=calculation_service.new_context(),
        )

        assert not acquired.acquired
        assert "No risk-free series is documented for JPY" in acquired.reason
        assert client.asked == []

    async def test_a_sterling_run_is_told_which_rate_to_enter(
        self, db_session: Any, clean: None
    ) -> None:
        """**The sentence the gate shows is the one the operator acts on.** Sterling's proxy
        is settled — the ten-year gilt yield — and only its retrieval is closed (ADR 0026), so
        the refusal names the instrument rather than leaving a valuation with a blank in it.
        Nothing is asked of the macro client: there is no series for it to fetch."""
        client = StubMacroClient({AS_OF: "4.36"})

        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="GBP",
            as_of=AS_OF,
            context=calculation_service.new_context(),
        )

        assert not acquired.acquired
        assert "ten-year gilt yield" in acquired.reason
        assert "which date you took it from" in acquired.reason
        assert client.asked == []

    async def test_a_missing_key_is_the_clients_own_sentence(
        self, db_session: Any, clean: None
    ) -> None:
        from aer.db.models import MacroObservationRow  # noqa: PLC0415 -- only this test

        refusal = ConfigError("AER_FRED_API_KEY is not set.", context={"setting": "fred_api_key"})
        client = StubMacroClient(raises=refusal)

        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="USD",
            as_of=AS_OF,
            context=calculation_service.new_context(),
        )

        assert not acquired.acquired
        assert "could not be fetched" in acquired.reason
        assert "AER_FRED_API_KEY" in acquired.reason
        assert list(await db_session.scalars(select(MacroObservationRow))) == []

    async def test_a_stale_reading_is_not_the_rate_on_the_as_of_date(
        self, db_session: Any, clean: None
    ) -> None:
        """The table outlives the request (ADR 0084): an old reading is still a reading, and
        must not be taken for a fresh one."""
        old = AS_OF - timedelta(days=STALE_AFTER_DAYS + 1)
        client = StubMacroClient({old: "4.10"})
        ledger = calculation_service.new_context()

        acquired = await acquire_risk_free(
            db_session, client, currency="USD", as_of=AS_OF, context=ledger
        )

        assert not acquired.acquired
        assert "days earlier" in acquired.reason
        assert old.isoformat() in acquired.reason
        assert not ledger.records

    async def test_a_reading_within_the_window_stands(self, db_session: Any, clean: None) -> None:
        recent = AS_OF - timedelta(days=STALE_AFTER_DAYS)
        client = StubMacroClient({recent: "4.10"})

        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="USD",
            as_of=AS_OF,
            context=calculation_service.new_context(),
        )

        assert acquired.acquired
        assert acquired.observed_on == recent

    async def test_an_archive_with_nothing_published_yet_says_so(
        self, db_session: Any, clean: None
    ) -> None:
        client = StubMacroClient({})

        acquired = await acquire_risk_free(
            db_session,
            client,
            currency="USD",
            as_of=AS_OF,
            context=calculation_service.new_context(),
        )

        assert not acquired.acquired
        assert "holds no" in acquired.reason


class TestThroughTheRealClient:
    """The archive replayed: the fixture is ALFRED's answer for DGS10 as at 28 June 2024."""

    @respx.mock
    async def test_the_fixture_vintage_yields_the_days_close(
        self, db_session: Any, clean: None, macro_fetcher: Any, artefact_store: Any
    ) -> None:
        route = respx.get(url__startswith=f"{fred.API_ROOT}/series/observations").mock(
            return_value=httpx.Response(
                200,
                content=(FIXTURES / "dgs10_vintage.json").read_bytes(),
                headers={"content-type": "application/json"},
            )
        )
        client = MacroClient(macro_fetcher, artefact_store, fred_api_key=API_KEY)
        ledger = calculation_service.new_context()

        acquired = await acquire_risk_free(
            db_session, client, currency="USD", as_of=AS_OF, context=ledger
        )

        assert acquired.acquired
        assert acquired.quoted == Decimal("4.36")
        assert acquired.rate == Decimal("0.0436")
        assert acquired.observed_on == AS_OF
        requested = str(route.calls[0].request.url)
        assert "series_id=DGS10" in requested
        assert "realtime_start=2024-06-28" in requested
        # The proposer the gate will record for it.
        assert PROPOSED_BY == "aer.services.macro"
