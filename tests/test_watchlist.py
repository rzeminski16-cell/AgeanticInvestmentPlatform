"""A watchlist is followed continuously and researched as at a date, and the queue spends a
standing budget that is not one run's cap.

Four layers. Following: a listing is checked against the universe a request is, followed
once, withdrawn with a reason, and kept. The standing budget: a live run reserves its cap,
a finished one counts what it spent, last month's count for nothing. Commissioning: an
entry becomes an ordinary research request as at a date with the per-run cap and a run at
gate one, the budget refuses what it cannot afford by name, a dead run puts the entry back
in the queue and a report takes it out, and the walk stops at the first refusal. And the
page, the work list and the terminal prove a person and a scheduler can drive it.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from aer.cli import app as cli
from aer.config import Settings
from aer.core.enums import AnalysisMode, JobStatus, UserRole
from aer.db.models import (
    AuditEvent,
    Cost,
    Job,
    Report,
    ResearchRequest,
    User,
    WatchlistCommission,
    WatchlistEntry,
)
from aer.errors import BudgetExceededError, ConflictError, ValidationError
from aer.services import watchlist as watchlist_service
from aer.web.overview import watchlist as watchlist_feed
from aer.web.overview.attention import Severity
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import empty_the_database

pytestmark = pytest.mark.integration

TODAY = datetime.now(UTC).date()


async def _user(session: AsyncSession, email: str = "follower@example.invalid") -> User:
    user = User(email=email, display_name="Follower", role=UserRole.OWNER)
    session.add(user)
    await session.flush()
    return user


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    fields: dict[str, Any] = {
        "http_user_agent": "Test test@example.invalid",
        "artefact_root": tmp_path / "artefacts",
        "per_run_budget_gbp": Decimal("12.00"),
        "watchlist_budget_gbp": Decimal("30.00"),
    }
    fields.update(overrides)
    return Settings(**fields)


async def _follow(
    session: AsyncSession,
    user: User,
    ticker: str = "CTSO",
    exchange: str = "LSE",
    name: str = "Contoso plc",
) -> WatchlistEntry:
    return await watchlist_service.follow(
        session,
        user=user,
        company_name=name,
        ticker=ticker,
        exchange=exchange,
        why="The FY25 margin bridge looks too good.",
    )


async def _commission(
    session: AsyncSession, settings: Settings, user: User, entry: WatchlistEntry, **kwargs: Any
) -> tuple[WatchlistCommission, Job]:
    return await watchlist_service.commission(
        session, settings=settings, user=user, entry=entry, **kwargs
    )


async def _budget(
    session: AsyncSession, settings: Settings, user: User
) -> watchlist_service.StandingBudget:
    return await watchlist_service.standing_budget(session, settings=settings, user_id=user.id)


async def _spend(session: AsyncSession, job: Job, gbp: str) -> None:
    session.add(
        Cost(
            job_id=job.id,
            category="model",
            provider="fake",
            model="fake",
            units=Decimal(1000),
            unit_type="tokens",
            amount_usd=Decimal(gbp),
            amount_gbp=Decimal(gbp),
            fx_rate=Decimal(1),
        )
    )
    await session.flush()


# -- Following -------------------------------------------------------------------------------


class TestFollowing:
    async def test_a_listing_is_followed_with_its_reason(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)

        entry = await _follow(db_session, user, ticker="ctso", exchange="lse")

        assert entry.listing == "CTSO.LSE"
        assert entry.why.startswith("The FY25")
        assert entry.followed_at is not None
        assert not entry.is_withdrawn
        event = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "watchlist.followed")
        )
        assert event is not None
        assert event.subject_kind == "watchlist_entry"
        assert event.subject_id == entry.id

    async def test_outside_the_universe_is_refused_with_every_reason(
        self, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)

        with pytest.raises(ValidationError) as refused:
            await _follow(db_session, user, ticker="SAP", exchange="XETRA", name="SAP SE")

        assert "unsupported_exchange" in refused.value.context["rules"]

    async def test_followed_twice_is_refused_until_withdrawn(
        self, db_session: AsyncSession
    ) -> None:
        user = await _user(db_session)
        first = await _follow(db_session, user)

        with pytest.raises(ConflictError, match="already followed"):
            await _follow(db_session, user)

        await watchlist_service.stop_following(
            db_session, user=user, entry=first, reason="Researched elsewhere."
        )
        second = await _follow(db_session, user)

        assert second.id != first.id
        assert first.is_withdrawn
        assert first.withdrawn_reason == "Researched elsewhere."
        listed = await watchlist_service.entries_for(db_session, user_id=user.id)
        assert [row.id for row in listed] == [second.id]
        kept = await watchlist_service.entries_for(
            db_session, user_id=user.id, include_withdrawn=True
        )
        assert len(kept) == 2

    async def test_stopping_needs_a_reason(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        entry = await _follow(db_session, user)

        with pytest.raises(ValidationError, match="reason"):
            await watchlist_service.stop_following(db_session, user=user, entry=entry, reason=" ")

    async def test_a_blank_listing_is_refused(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)

        with pytest.raises(ValidationError, match="needs its name"):
            await watchlist_service.follow(
                db_session, user=user, company_name=" ", ticker="CTSO", exchange="LSE"
            )


# -- The standing budget ---------------------------------------------------------------------


class TestTheStandingBudget:
    async def test_nothing_commissioned_leaves_the_whole_budget(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user = await _user(db_session)

        budget = await _budget(db_session, _settings(tmp_path), user)

        assert budget.budget_gbp == Decimal("30.00")
        assert budget.spent_gbp == 0
        assert budget.reserved_gbp == 0
        assert budget.room_gbp == Decimal("30.00")
        assert budget.fits == 2
        assert budget.affords(Decimal("12.00"))
        assert budget.month_start == TODAY.replace(day=1)

    async def test_a_live_run_reserves_its_cap(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """ADR 0107 §2: a run at gate one has spent pence and may spend pounds."""
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        _, job = await _commission(db_session, settings, user, entry)
        await _spend(db_session, job, "0.50")

        budget = await _budget(db_session, settings, user)

        assert job.status is JobStatus.QUEUED
        assert budget.spent_gbp == Decimal("0.50")
        assert budget.reserved_gbp == Decimal("11.50")
        assert budget.room_gbp == Decimal("18.00")
        assert budget.fits == 1

    async def test_a_finished_run_counts_what_it_spent_and_reserves_nothing(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        _, job = await _commission(db_session, settings, user, entry)
        await _spend(db_session, job, "3.00")
        job.status = JobStatus.SUCCEEDED
        await db_session.flush()

        budget = await _budget(db_session, settings, user)

        assert budget.spent_gbp == Decimal("3.00")
        assert budget.reserved_gbp == 0
        assert budget.room_gbp == Decimal("27.00")

    async def test_last_months_commissions_count_for_nothing(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        row, _ = await _commission(db_session, settings, user, entry)
        row.commissioned_at = datetime.now(UTC) - timedelta(days=40)
        await db_session.flush()

        budget = await _budget(db_session, settings, user)

        assert budget.reserved_gbp == 0
        assert budget.room_gbp == Decimal("30.00")


# -- Commissioning ---------------------------------------------------------------------------


class TestCommissioning:
    async def test_a_commission_is_an_ordinary_request_as_at_today_with_the_cap(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)

        row, job = await _commission(db_session, settings, user, entry)

        request = await db_session.get(ResearchRequest, row.request_id)
        assert request is not None
        assert (request.company_name, request.ticker, request.exchange) == (
            "Contoso plc",
            "CTSO",
            "LSE",
        )
        assert request.analysis_mode is AnalysisMode.STANDARD
        assert request.investment_horizon_months == 12
        assert request.work_order.as_of_date == TODAY
        assert request.work_order.point_in_time is True
        assert request.work_order.max_cost_gbp == Decimal("12.00")
        assert request.work_order.user_id == user.id
        assert job.work_order_id == request.id
        assert job.status is JobStatus.QUEUED
        assert row.as_of_date == TODAY
        assert row.cap_gbp == Decimal("12.00")
        assert row.commissioned_by == user.email
        state = await watchlist_service.state_of(db_session, entry)
        assert state.state == "commissioned"
        assert not state.is_queued
        event = await db_session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "watchlist.commissioned")
        )
        assert event is not None
        assert event.payload["job_id"] == str(job.id)

    async def test_as_at_a_stated_date(self, db_session: AsyncSession, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)

        row, _ = await _commission(db_session, settings, user, entry, as_of=date(2026, 6, 30))

        request = await db_session.get(ResearchRequest, row.request_id)
        assert request is not None
        assert request.work_order.as_of_date == date(2026, 6, 30)
        assert row.as_of_date == date(2026, 6, 30)

    async def test_a_live_run_is_not_commissioned_twice(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        await _commission(db_session, settings, user, entry)

        with pytest.raises(ConflictError, match="already has a run alive"):
            await _commission(db_session, settings, user, entry)

    async def test_the_budget_refuses_by_name_what_it_cannot_afford(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path, watchlist_budget_gbp=Decimal("20.00"))
        user = await _user(db_session)
        first = await _follow(db_session, user)
        second = await _follow(db_session, user, ticker="MSFT", exchange="NASDAQ", name="Microsoft")
        await _commission(db_session, settings, user, first)

        with pytest.raises(
            BudgetExceededError, match=r"standing budget has £8\.00 of room"
        ) as refused:
            await _commission(db_session, settings, user, second)

        assert refused.value.context["scope"] == "watchlist"
        assert (
            await db_session.scalar(
                select(WatchlistCommission).where(WatchlistCommission.entry_id == second.id)
            )
            is None
        )

    async def test_a_withdrawn_entry_is_not_commissioned(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        await watchlist_service.stop_following(db_session, user=user, entry=entry, reason="Done.")

        with pytest.raises(ConflictError, match="no longer followed"):
            await _commission(db_session, settings, user, entry)

    async def test_a_dead_run_puts_the_entry_back_in_the_queue(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        first, job = await _commission(db_session, settings, user, entry)
        job.status = JobStatus.FAILED
        await db_session.flush()

        state = await watchlist_service.state_of(db_session, entry)
        assert state.state == "stopped"
        assert state.is_queued

        second, _ = await _commission(db_session, settings, user, entry)
        assert second.request_id != first.request_id
        assert len(entry.commissions) == 2

    async def test_a_report_makes_it_researched(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        row, job = await _commission(db_session, settings, user, entry)
        job.status = JobStatus.SUCCEEDED
        assert row.request_id is not None
        db_session.add(
            Report(
                job_id=job.id,
                request_id=row.request_id,
                as_of_date=TODAY,
                content={},
                content_hash="a" * 64,
            )
        )
        await db_session.flush()

        state = await watchlist_service.state_of(db_session, entry)

        assert state.state == "researched"
        assert not state.is_queued
        assert state.report is not None
        assert await watchlist_service.queue_for(db_session, user_id=user.id) == []

    async def test_another_persons_entry_is_refused(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        other = await _user(db_session, email="other@example.invalid")
        entry = await _follow(db_session, user)

        with pytest.raises(ConflictError, match="person following it"):
            await _commission(db_session, settings, other, entry)


class TestTheWalk:
    async def test_it_runs_in_the_order_followed_and_stops_at_the_budget(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        first = await _follow(db_session, user)
        second = await _follow(db_session, user, ticker="MSFT", exchange="NASDAQ", name="Microsoft")
        third = await _follow(db_session, user, ticker="BARC", exchange="LSE", name="Barclays plc")

        drain = await watchlist_service.commission_next(db_session, settings=settings, user=user)

        assert [row.entry_id for row, _ in drain.commissioned] == [first.id, second.id]
        assert drain.left == 1
        assert third.listing in drain.stopped
        assert "standing budget" in drain.stopped
        assert len(await watchlist_service.queue_for(db_session, user_id=user.id)) == 1

    async def test_it_honours_a_limit(self, db_session: AsyncSession, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        await _follow(db_session, user)
        await _follow(db_session, user, ticker="MSFT", exchange="NASDAQ", name="Microsoft")

        drain = await watchlist_service.commission_next(
            db_session, settings=settings, user=user, limit=1
        )

        assert len(drain.commissioned) == 1
        assert drain.left == 1
        assert drain.stopped == ""

    async def test_an_empty_queue_walks_nowhere(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user = await _user(db_session)

        drain = await watchlist_service.commission_next(
            db_session, settings=_settings(tmp_path), user=user
        )

        assert drain.commissioned == ()
        assert drain.left == 0
        assert drain.stopped == ""


# -- The work list ---------------------------------------------------------------------------


class TestTheWorkList:
    async def test_nothing_followed_asks_nothing(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)

        assert await watchlist_feed.items(db_session, user_id=user.id) == []

    async def test_the_queue_is_not_started(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        await _follow(db_session, user)
        await _follow(db_session, user, ticker="MSFT", exchange="NASDAQ", name="Microsoft")

        [item] = await watchlist_feed.items(db_session, user_id=user.id)

        assert item.severity is Severity.IDLE
        assert item.title.startswith("2 followed companies are waiting")
        assert "Contoso plc" in item.detail
        assert item.href == "/watchlist"

    async def test_a_commissioned_queue_asks_nothing(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        user = await _user(db_session)
        entry = await _follow(db_session, user)
        await _commission(db_session, settings, user, entry)

        assert await watchlist_feed.items(db_session, user_id=user.id) == []


# -- The pages -------------------------------------------------------------------------------


_TABLES = "audit_events, users, companies, work_orders"


class _EnqueueRecorder:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def __call__(self, redis: Any, job_id: uuid.UUID) -> str:
        self.job_ids.append(str(job_id))
        return f"task-{job_id}"


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> _EnqueueRecorder:
    recorder = _EnqueueRecorder()
    monkeypatch.setattr("aer.web.watchlist.pages.enqueue_run", recorder)
    return recorder


@pytest.fixture
async def committed(db_engine: Any) -> Any:
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = await _user(session, email="owner@example.invalid")
        await session.commit()
        yield {"user": user}
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def api(
    api_settings: Any, db_engine: Any, fake_redis: Any, committed: Any, enqueued: _EnqueueRecorder
) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


def _csrf(html: str) -> str:
    found = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert found is not None, "the page rendered no CSRF token"
    return str(found.group(1))


async def _followed_from_the_page(api: Any, ticker: str = "CTSO", exchange: str = "LSE") -> str:
    page = await api.get("/watchlist")
    response = await api.post(
        "/watchlist",
        data={
            "csrf_token": _csrf(page.text),
            "company_name": "Contoso plc",
            "ticker": ticker,
            "exchange": exchange,
            "why": "The FY25 margin bridge looks too good.",
        },
    )
    assert response.status_code == 303, response.text
    body = (await api.get("/watchlist")).text
    found = re.search(r'data-entry="([0-9a-f-]{36})"', body)
    assert found is not None
    return found.group(1)


class TestThePages:
    async def test_an_empty_watchlist_explains_itself(self, api: Any) -> None:
        body = (await api.get("/watchlist")).text

        assert "Nothing followed" in body
        assert 'data-figure="room"' in body
        assert 'id="follow-company"' in body

    async def test_follow_commission_and_stop(self, api: Any, enqueued: _EnqueueRecorder) -> None:
        entry_id = await _followed_from_the_page(api)
        body = (await api.get("/watchlist")).text
        assert f'data-entry="{entry_id}" data-state="queued"' in body
        assert "Followed and not yet researched" not in body  # the detail is not on the row
        assert "company is followed and not yet researched" in body
        assert 'id="commission-next"' in body

        commissioned = await api.post(
            f"/watchlist/{entry_id}/commission",
            data={"csrf_token": _csrf(body), "as_of": TODAY.isoformat()},
        )
        assert commissioned.status_code == 303, commissioned.text
        assert commissioned.headers["location"].startswith("/runs/")
        assert len(enqueued.job_ids) == 1

        body = (await api.get("/watchlist")).text
        assert f'data-entry="{entry_id}" data-state="commissioned"' in body
        assert "researched as at" in body
        assert "/requests/" in body
        assert 'data-figure="reserved"' in body

        stopped = await api.post(
            f"/watchlist/{entry_id}/stop",
            data={"csrf_token": _csrf(body), "reason": "Researched by hand."},
        )
        assert stopped.status_code == 303
        assert f'data-entry="{entry_id}"' not in (await api.get("/watchlist")).text

    async def test_commission_the_next_from_the_page(
        self, api: Any, enqueued: _EnqueueRecorder
    ) -> None:
        await _followed_from_the_page(api)
        await _followed_from_the_page(api, ticker="MSFT", exchange="NASDAQ")
        page = await api.get("/watchlist")

        response = await api.post(
            "/watchlist/commission-next", data={"csrf_token": _csrf(page.text), "limit": "1"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/watchlist?queued=1"
        assert len(enqueued.job_ids) == 1
        body = (await api.get(response.headers["location"])).text
        assert 'id="queued-notice"' in body
        assert "1 run started from the queue" in body

    async def test_outside_the_universe_is_refused_with_the_reason(self, api: Any) -> None:
        page = await api.get("/watchlist")
        response = await api.post(
            "/watchlist",
            data={
                "csrf_token": _csrf(page.text),
                "company_name": "SAP SE",
                "ticker": "SAP",
                "exchange": "XETRA",
                "why": "",
            },
        )

        assert response.status_code in {400, 422}
        assert "XETRA" in response.text or "exchange" in response.text.lower()

    async def test_a_form_without_a_token_is_refused(self, api: Any) -> None:
        response = await api.post("/watchlist", data={"company_name": "x"})

        assert response.status_code == 403
        assert "Nothing was followed" in response.text


# -- The terminal ----------------------------------------------------------------------------


class TestTheCommand:
    """Synchronous on purpose: ``aer queue`` owns its event loop, so it seeds and reads with
    an engine of its own, as `test_cli.py` does, rather than the suite's."""

    def test_aer_queue_commissions_the_next_the_budget_affords(
        self,
        settings_env: Any,
        tmp_path: Path,
        database_url: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings_env.setenv("AER_DATABASE_URL", database_url)
        settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
        settings_env.setenv("AER_SECRET_KEY", "test-signing-key-not-a-real-one")
        settings_env.setenv("AER_WATCHLIST_BUDGET_GBP", "20.00")
        settings_env.setenv("AER_PER_RUN_BUDGET_GBP", "12.00")
        recorder = _EnqueueRecorder()
        monkeypatch.setattr("aer.queue.enqueue_run", recorder)

        async def seed() -> None:
            engine = create_async_engine(database_url)
            try:
                async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                    user = await _user(session, email="owner@example.invalid")
                    await _follow(session, user)
                    await _follow(session, user, ticker="MSFT", exchange="NASDAQ", name="Microsoft")
                    await session.commit()
            finally:
                await engine.dispose()

        async def count() -> int:
            engine = create_async_engine(database_url)
            try:
                async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                    rows = await session.scalars(select(WatchlistCommission))
                    return len(list(rows))
            finally:
                await engine.dispose()

        asyncio.run(empty_the_database(database_url))
        asyncio.run(seed())
        try:
            result = CliRunner().invoke(cli, ["queue"])

            # Two queued, room for one at £12 in £20: one commissioned, then stopped short.
            assert result.exit_code == 1, result.output
            assert "CTSO.LSE: commissioned" in result.output
            assert "Stopped with 1 left" in result.output
            assert asyncio.run(count()) == 1
            assert len(recorder.job_ids) == 1
        finally:
            asyncio.run(empty_the_database(database_url))
