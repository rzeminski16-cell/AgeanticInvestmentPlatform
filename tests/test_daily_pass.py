"""The daily pass: that it runs, that it records having run, and that a miss is not silent.

F15's done-when is two claims. *"The book is valued at yesterday's close without
intervention for a fortnight"* — which here means the closes are read and stored, because
there is no `positions` table to value into (ADR 0083). And *"a missed run is visible on the
health page rather than silent"*, which is the half that cannot be tested by waiting: the
verdict is a pure function of a timestamp and a clock, so these tests hold the clock still
and assert the sentence at the hour they want.

**Nothing here reaches a vendor.** The price client is a protocol, so the pass takes a stub
that answers from a dict — which also lets one listing raise while the others answer, which
is the case the pass exists to survive.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus, RequestStatus
from aer.db.models import WatchlistEntry, WorkOrder
from aer.services import daily_pass
from tests import portfolio_fixtures
from tests.portfolio_fixtures import AS_OF, funded, trade
from tests.price_fixtures import StubPrices

pytestmark = pytest.mark.integration

book = portfolio_fixtures.book


async def _watch(session: AsyncSession, scene: dict[str, Any], ticker: str, exchange: str) -> None:
    session.add(
        WatchlistEntry(
            user_id=scene["user"].id,
            company_name=ticker,
            ticker=ticker,
            exchange=exchange,
            why="Worth a look.",
        )
    )
    await session.flush()


class TestWhichListingsThePassReads:
    async def test_it_reads_what_is_held(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")

        found = await daily_pass.listings_to_read(db_session, user_id=book["user"].id)

        assert [row.ticker for row in found] == ["MSFT"]

    async def test_it_reads_what_is_watched_even_with_nothing_held(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _watch(db_session, book, "BARC", "LSE")

        found = await daily_pass.listings_to_read(db_session, user_id=book["user"].id)

        assert [row.ticker for row in found] == ["BARC"]

    async def test_a_listing_held_and_watched_is_read_once(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")
        await _watch(db_session, book, "MSFT", "NASDAQ")

        found = await daily_pass.listings_to_read(db_session, user_id=book["user"].id)

        assert len(found) == 1

    async def test_a_withdrawn_watch_is_not_read(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        await _watch(db_session, book, "BARC", "LSE")
        entry = await db_session.scalar(
            WatchlistEntry.__table__.select().with_only_columns(WatchlistEntry.id)  # type: ignore[arg-type]
        )
        row = await db_session.get(WatchlistEntry, entry)
        assert row is not None
        row.withdrawn_at = datetime.now(UTC)
        row.withdrawn_reason = "Changed my mind."
        await db_session.flush()

        found = await daily_pass.listings_to_read(db_session, user_id=book["user"].id)

        assert found == []

    async def test_another_persons_book_is_not_read(
        self, db_session: AsyncSession, book: dict[str, Any]
    ) -> None:
        """ADR 0120 §1: the scope is in the query, not at the route."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")

        found = await daily_pass.listings_to_read(db_session, user_id=uuid.uuid4())

        assert found == []


class TestThePassRunsAndSaysSo:
    async def test_it_stores_the_closes_it_did_not_have(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")
        yesterday = AS_OF + timedelta(days=1)
        client = StubPrices({"MSFT.US": [(yesterday, "415")]})

        outcome = await daily_pass.run_daily_pass(
            db_session, client, user=book["user"], settings=api_settings, as_of=yesterday
        )

        assert client.asked == ["MSFT.US"]
        assert outcome.read == 1
        assert outcome.stored == 1
        assert outcome.problems == []

    async def test_a_bar_already_held_is_not_news(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """The fixture already stores a close at AS_OF. A pass re-reading it stores nothing
        and is not a failure — re-running an acquisition is not news."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")
        client = StubPrices({"MSFT.US": [(AS_OF, "410")]})

        outcome = await daily_pass.run_daily_pass(
            db_session, client, user=book["user"], settings=api_settings, as_of=AS_OF
        )

        assert outcome.read == 1
        assert outcome.stored == 0
        assert outcome.problems == []

    async def test_one_bad_symbol_does_not_stop_the_others(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")
        await trade(
            db_session, book, security=book["barc"], quantity="100", price="250", currency="GBX"
        )
        yesterday = AS_OF + timedelta(days=1)
        client = StubPrices({"MSFT.US": [(yesterday, "415")]})

        outcome = await daily_pass.run_daily_pass(
            db_session, client, user=book["user"], settings=api_settings, as_of=yesterday
        )

        assert outcome.read == 2
        assert outcome.stored == 1
        assert len(outcome.problems) == 1
        assert "BARC" in outcome.problems[0]

    async def test_it_records_a_finished_job_that_cost_nothing(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """A pass is a run like any other, and "when did this last work?" is answered from
        the record rather than from the worker's scrollback."""
        outcome = await daily_pass.run_daily_pass(
            db_session, StubPrices(), user=book["user"], settings=api_settings, as_of=AS_OF
        )

        assert outcome.job.status is JobStatus.SUCCEEDED
        assert outcome.job.finished_at is not None
        assert Decimal(str(outcome.job.total_cost_gbp)) == Decimal("0.00")
        order = await db_session.get(WorkOrder, outcome.job.work_order_id)
        assert order is not None
        assert order.tool == daily_pass.TOOL
        assert order.status is RequestStatus.COMPLETED

    async def test_with_no_subscription_it_still_runs_and_says_why_it_read_nothing(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        """A schedule that silently does nothing is the failure this feature exists to make
        visible, so the no-subscription case is a finished pass with a sentence, not a skip."""
        await funded(db_session, book)
        await trade(db_session, book, security=book["msft"], quantity="10", price="400")

        outcome = await daily_pass.run_daily_pass(
            db_session, None, user=book["user"], settings=api_settings, as_of=AS_OF
        )

        assert outcome.job.status is JobStatus.SUCCEEDED
        assert outcome.read == 0
        assert "No market-data subscription" in outcome.note

    async def test_the_newest_pass_is_the_one_found(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        earlier = datetime(2026, 6, 28, 22, 0, tzinfo=UTC)
        later = datetime(2026, 6, 29, 22, 0, tzinfo=UTC)
        for at in (earlier, later):
            await daily_pass.run_daily_pass(
                db_session, None, user=book["user"], settings=api_settings, as_of=at.date(), now=at
            )

        found = await daily_pass.last_pass(db_session, user_id=book["user"].id)

        assert found is not None
        assert found.finished_at == later

    async def test_another_persons_pass_is_not_found(
        self, db_session: AsyncSession, book: dict[str, Any], api_settings: Any
    ) -> None:
        await daily_pass.run_daily_pass(
            db_session, None, user=book["user"], settings=api_settings, as_of=AS_OF
        )

        assert await daily_pass.last_pass(db_session, user_id=uuid.uuid4()) is None


class TestAMissedPassIsNotSilent:
    """The half of F15's done-when that cannot be tested by waiting for it."""

    def test_a_pass_that_has_never_run_is_not_overdue(self) -> None:
        """There is nothing to be late. A platform whose first day reported a failure would
        be teaching its operator to ignore the indicator before it had ever been right."""
        state = daily_pass.pass_state(None, now=datetime(2026, 9, 20, 9, 0, tzinfo=UTC))

        assert state.is_missed is False
        assert state.sentence == daily_pass.NEVER_RUN

    def test_a_pass_last_night_is_not_overdue_this_morning(self) -> None:
        ran = datetime(2026, 9, 19, 22, 0, tzinfo=UTC)

        state = daily_pass.pass_state(ran, now=datetime(2026, 9, 20, 9, 0, tzinfo=UTC))

        assert state.is_missed is False
        assert "19 September 2026 at 22:00" in state.sentence

    def test_a_pass_inside_the_grace_is_not_overdue(self) -> None:
        """Twenty-eight hours: due, not missed. The grace exists because a schedule read at
        the wrong end of its own cadence has not failed, and calling that a miss is how an
        indicator teaches its reader to skip it."""
        ran = datetime(2026, 9, 19, 22, 0, tzinfo=UTC)

        state = daily_pass.pass_state(ran, now=ran + timedelta(hours=28))

        assert state.is_missed is False

    def test_a_pass_past_the_grace_is_overdue_and_says_how_stale(self) -> None:
        ran = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)

        state = daily_pass.pass_state(ran, now=datetime(2026, 9, 20, 9, 0, tzinfo=UTC))

        assert state.is_missed is True
        assert "2 days" in state.sentence
        assert "17 September 2026" in state.sentence
        # The consequence, not just the fact: a reader who is told the pass is late and not
        # told what that costs has to work it out, which is the silence F15 is about.
        assert "stale" in state.sentence

    def test_one_day_is_singular(self) -> None:
        ran = datetime(2026, 9, 18, 22, 0, tzinfo=UTC)

        state = daily_pass.pass_state(ran, now=datetime(2026, 9, 20, 9, 0, tzinfo=UTC))

        assert state.is_missed is True
        assert "1 day " in state.sentence
