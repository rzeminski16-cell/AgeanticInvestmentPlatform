"""`GET /runs/active`: the run you are watching has an address (ADR 0089).

**A redirect and not a page.** It renders nothing and holds no state; opening it lands the
operator somewhere real, and that is the whole of its behaviour. The navigation item is a
literal href like any other, matched by the same prefix logic and held by the same drift test.

The property worth testing is not the redirect — it is that **"current" is defined once**. The
item and the redirect both call `runs.current_run`, so the link and the page it lands on cannot
disagree about which run is current, which they would the first time two definitions of
"latest" were written a fortnight apart.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aer.config import Settings
from aer.core.enums import AnalysisMode, JobStatus, RequestStatus
from aer.db.models.job import Job
from aer.db.models.user import User
from aer.services.runs import current_run
from tests.api_fixtures import build_app, client_for
from tests.request_fixtures import research_request


async def _run(
    session: AsyncSession,
    user: User,
    *,
    status: JobStatus,
    started: dt.datetime | None,
) -> Job:
    request = research_request(
        id=uuid.uuid4(),
        user_id=user.id,
        company_name="Contoso plc",
        ticker="CTSO",
        exchange="LSE",
        as_of_date=dt.date(2026, 8, 24),
        base_currency="GBP",
        investment_horizon_months=12,
        analysis_mode=AnalysisMode.STANDARD,
        point_in_time=True,
        status=RequestStatus.APPROVED,
    )
    request.work_order.max_cost_gbp = Decimal("8.00")
    session.add(request)
    await session.flush()
    job = Job(
        id=uuid.uuid4(),
        # The run root the mandate is a detail of, not a second one beside it.
        work_order_id=request.id,
        workflow_version="v1",
        code_version="test",
        status=status,
        started_at=started,
        total_cost_gbp=Decimal("0"),
    )
    session.add(job)
    await session.flush()
    return job


async def _user(session: AsyncSession) -> User:
    user = User(id=uuid.uuid4(), email="op@example.com", display_name="Operator")
    session.add(user)
    await session.flush()
    return user


class TestWhichRunIsCurrent:
    async def test_a_run_still_going_wins_over_a_newer_finished_one(
        self, db_session: AsyncSession
    ) -> None:
        """The one that matters. "Most recent" alone would send the operator to a run that
        has already ended while another is still spending their money."""
        user = await _user(db_session)
        running = await _run(
            db_session,
            user,
            status=JobStatus.RUNNING,
            started=dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.UTC),
        )
        await _run(
            db_session,
            user,
            status=JobStatus.SUCCEEDED,
            started=dt.datetime(2026, 8, 24, 11, 0, tzinfo=dt.UTC),
        )

        assert (await current_run(db_session, user_id=user.id)) is not None
        found = await current_run(db_session, user_id=user.id)
        assert found is not None
        assert found.id == running.id

    async def test_a_run_waiting_at_a_gate_is_still_the_current_one(
        self, db_session: AsyncSession
    ) -> None:
        """Waiting for a person is not finished. It is the state an operator is most likely
        to be looking for the run *from*."""
        user = await _user(db_session)
        waiting = await _run(
            db_session,
            user,
            status=JobStatus.AWAITING_APPROVAL,
            started=dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.UTC),
        )

        found = await current_run(db_session, user_id=user.id)
        assert found is not None
        assert found.id == waiting.id

    async def test_with_nothing_in_flight_the_most_recent_one_is_offered(
        self, db_session: AsyncSession
    ) -> None:
        """A finished run is still somewhere to go. A navigation item that vanished the
        moment a run ended would vanish exactly when the operator went looking for it."""
        user = await _user(db_session)
        await _run(
            db_session,
            user,
            status=JobStatus.SUCCEEDED,
            started=dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.UTC),
        )
        newest = await _run(
            db_session,
            user,
            status=JobStatus.FAILED,
            started=dt.datetime(2026, 8, 24, 11, 0, tzinfo=dt.UTC),
        )

        found = await current_run(db_session, user_id=user.id)
        assert found is not None
        assert found.id == newest.id

    async def test_a_queued_run_does_not_shadow_one_that_is_actually_running(
        self, db_session: AsyncSession
    ) -> None:
        """A job's life begins when it starts, so `jobs` has no `created_at` and an unstarted
        one has no `started_at` at all. Nulls last is what stops it sorting first."""
        user = await _user(db_session)
        running = await _run(
            db_session,
            user,
            status=JobStatus.RUNNING,
            started=dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.UTC),
        )
        await _run(db_session, user, status=JobStatus.QUEUED, started=None)

        found = await current_run(db_session, user_id=user.id)
        assert found is not None
        assert found.id == running.id

    async def test_somebody_elses_run_is_not_yours(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)
        other = User(id=uuid.uuid4(), email="other@example.com", display_name="Other")
        db_session.add(other)
        await db_session.flush()
        await _run(
            db_session,
            other,
            status=JobStatus.RUNNING,
            started=dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.UTC),
        )

        assert await current_run(db_session, user_id=user.id) is None

    async def test_no_runs_at_all_is_no_run(self, db_session: AsyncSession) -> None:
        user = await _user(db_session)

        assert await current_run(db_session, user_id=user.id) is None


class TestTheAddress:
    """The route itself. `tests/test_every_page_renders.py` already opens it against a driven
    run; these are the two answers that only show up when there is nothing to redirect to."""

    async def test_with_no_run_it_sends_you_to_your_requests(
        self, api_settings: Settings, db_engine: AsyncEngine, fake_redis: Redis
    ) -> None:
        """The honest next action for somebody with nothing in flight — not an empty console
        explaining that there is nothing to console.

        Seeded through the engine rather than the `db_session` fixture: that session lives in
        an outer transaction the test rolls back, on a different connection from the one the
        application opens, so a user written through it is a user the route cannot see.
        """
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as seeding:
            seeding.add(User(email="owner@example.invalid", display_name="Owner"))
            await seeding.commit()

        try:
            app = build_app(api_settings, engine=db_engine, redis=fake_redis)
            async for client in client_for(app):
                response = await client.get("/runs/active", follow_redirects=False)

                assert response.status_code == 303
                assert response.headers["location"] == "/requests"
        finally:
            async with factory() as cleanup:
                await cleanup.execute(delete(User))
                await cleanup.commit()

    async def test_it_is_not_parsed_as_a_job_id(
        self, api_settings: Settings, db_engine: AsyncEngine, fake_redis: Redis
    ) -> None:
        """Declared above `/runs/{job_id}`, because FastAPI matches in declaration order and
        below it `active` would be read as a uuid it never was — a 404 on a page that exists.

        No user is seeded here on purpose: the point is only that the *router* reaches this
        handler, and a 404 is the one answer that would mean it did not.
        """
        app = build_app(api_settings, engine=db_engine, redis=fake_redis)
        async for client in client_for(app):
            response = await client.get("/runs/active", follow_redirects=False)

            assert response.status_code != 404
