"""The two spending ceilings, and the one that was not being checked.

Invariant 6: cost is metered and capped **in code**. ``BudgetGuard`` has carried a
``monthly_cap_gbp`` field since the engine was written, its docstring said both ceilings
mattered, and ``aer.services.runs`` populated it from settings on every run — but nothing
ever read it. ``check`` compared the per-run cap and returned. The monthly ceiling did not
stop a run, and did not even warn: a £2.50 request under an £80 month could be started
thirty-two times and the platform would have said nothing at all.

The interesting part of a monthly cap is not the comparison, it is the window. Four
boundaries decide whether it holds, and each has a test here:

* **other runs count** — otherwise it is a per-run cap with a bigger number;
* **last month does not** — otherwise it is a lifetime cap;
* **a deleted request's spend still counts** — the ``costs`` rows deliberately outlive the
  request (migration 0009) so that a month cannot be cleared by deleting what it was spent
  on, and this query must not undo that by joining;
* **this run's own spend is counted once** — the run's rows are already inside the window,
  so adding ``spend_so_far`` to the month's total would refuse runs that fit.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import JobStatus, RequestStatus, UserRole
from aer.db.models import Cost, Job, JobStep, ResearchRequest, User
from aer.errors import BudgetExceededError
from aer.services.runs import RunState
from aer.workflow.engine import BudgetGuard, spend_this_month
from tests.api_fixtures import build_app, client_for
from tests.db_cleanup import delete_all
from tests.log_helpers import structlog_events

pytestmark = pytest.mark.integration

# Mid-month and mid-day, so the first-of-the-month boundary is a long way from `now` and a
# test that accidentally depends on the real clock fails rather than passing by luck.
NOW = datetime(2026, 8, 17, 13, 30, tzinfo=UTC)
MONTH_START = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


async def _job(session: AsyncSession) -> Job:
    user = User(
        email=f"budget-{uuid.uuid4()}@example.invalid", display_name="B", role=UserRole.OWNER
    )
    session.add(user)
    await session.flush()
    request = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=NOW.date(),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp=Decimal("2.50"),
        portfolio_context={},
        point_in_time=True,
        status=RequestStatus.DRAFT,
    )
    session.add(request)
    await session.flush()
    job = Job(
        work_order_id=request.id,
        request_id=request.id,
        workflow_version="test-1",
        code_version="abc",
        status=JobStatus.RUNNING,
        started_at=NOW,
    )
    session.add(job)
    await session.flush()
    return job


async def _spend(
    session: AsyncSession,
    *,
    job: Job | None,
    gbp: str,
    occurred_at: datetime = NOW,
) -> Cost:
    cost = Cost(
        job_id=job.id if job is not None else None,
        category="llm_input",
        provider="anthropic",
        model="claude-opus-5",
        units=Decimal("1000"),
        unit_type="tokens",
        amount_usd=Decimal(gbp) * Decimal("1.25"),
        amount_gbp=Decimal(gbp),
        fx_rate=Decimal("0.800000"),
        occurred_at=occurred_at,
    )
    session.add(cost)
    await session.flush()
    return cost


def _guard(*, per_run: str = "100.00", monthly: str = "10.00") -> BudgetGuard:
    return BudgetGuard(per_run_cap_gbp=Decimal(per_run), monthly_cap_gbp=Decimal(monthly))


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


class TestTheMonthlyWindow:
    """What ``spend_this_month`` counts, which is what the cap is."""

    async def test_it_is_zero_when_nothing_has_been_spent(self, db_session: AsyncSession) -> None:
        assert await spend_this_month(db_session, now=NOW) == Decimal(0)

    async def test_every_run_counts_not_only_one(self, db_session: AsyncSession) -> None:
        await _spend(db_session, job=await _job(db_session), gbp="1.00")
        await _spend(db_session, job=await _job(db_session), gbp="2.00")

        assert await spend_this_month(db_session, now=NOW) == Decimal("3.00")

    async def test_last_month_does_not(self, db_session: AsyncSession) -> None:
        """A monthly cap that counted everything ever would be a lifetime cap."""
        job = await _job(db_session)
        await _spend(
            db_session, job=job, gbp="9.00", occurred_at=MONTH_START - timedelta(seconds=1)
        )

        assert await spend_this_month(db_session, now=NOW) == Decimal(0)

    async def test_the_first_instant_of_the_month_is_inside_it(
        self, db_session: AsyncSession
    ) -> None:
        job = await _job(db_session)
        await _spend(db_session, job=job, gbp="4.00", occurred_at=MONTH_START)

        assert await spend_this_month(db_session, now=NOW) == Decimal("4.00")

    async def test_spend_whose_request_was_deleted_still_counts(
        self, db_session: AsyncSession
    ) -> None:
        """The reason ``costs.job_id`` is SET NULL rather than CASCADE.

        A cap you can get under by deleting the request you spent it on is not a cap, so the
        month's total must not be reached through a join to something deletable.
        """
        await _spend(db_session, job=None, gbp="6.00")

        assert await spend_this_month(db_session, now=NOW) == Decimal("6.00")

    async def test_the_month_is_utc_s_not_the_reader_s(self, db_session: AsyncSession) -> None:
        """``occurred_at`` is stored in UTC; the boundary has to agree with it.

        Asked from a timezone where it is already the 1st, the window must still open at the
        UTC 1st — otherwise the cap resets at a different moment depending on where the
        operator is standing.
        """
        job = await _job(db_session)
        await _spend(db_session, job=job, gbp="5.00", occurred_at=MONTH_START + timedelta(hours=2))
        # A fixed offset rather than a named zone: `ZoneInfo("Pacific/Auckland")` needs a
        # tz database, which Windows does not ship and this project does not depend on —
        # so the named form failed on the first Windows machine, for a reason that had
        # nothing to do with the property under test. Thirteen hours ahead is the same
        # reader, minus the dependency.
        ahead = datetime(2026, 8, 17, 2, 30, tzinfo=UTC).astimezone(timezone(timedelta(hours=13)))

        assert await spend_this_month(db_session, now=ahead) == Decimal("5.00")


class TestTheMonthlyCap:
    async def test_a_step_inside_the_run_cap_but_over_the_month_is_refused(
        self, db_session: AsyncSession
    ) -> None:
        """The case that was passing silently: a modest run, a month already spent."""
        job = await _job(db_session)
        await _spend(db_session, job=await _job(db_session), gbp="9.50")

        with pytest.raises(BudgetExceededError):
            await _guard().check(db_session, job=job, projected_gbp=Decimal("1.00"), now=NOW)

    async def test_the_refusal_names_the_monthly_scope(self, db_session: AsyncSession) -> None:
        """The console has to tell the two stops apart; the remedies are different."""
        job = await _job(db_session)
        await _spend(db_session, job=await _job(db_session), gbp="9.50")

        with pytest.raises(BudgetExceededError) as refused:
            await _guard().check(db_session, job=job, projected_gbp=Decimal("1.00"), now=NOW)

        assert refused.value.context["scope"] == "monthly"
        assert refused.value.context["cap_gbp"] == "10.00"

    async def test_the_refusal_says_the_request_s_own_cap_will_not_release_it(
        self, db_session: AsyncSession
    ) -> None:
        """An operator who raises the wrong number learns nothing and spends a minute."""
        job = await _job(db_session)
        await _spend(db_session, job=await _job(db_session), gbp="9.50")

        with pytest.raises(BudgetExceededError, match="will not release it"):
            await _guard().check(db_session, job=job, projected_gbp=Decimal("1.00"), now=NOW)

    async def test_a_step_that_fits_under_both_is_allowed(self, db_session: AsyncSession) -> None:
        job = await _job(db_session)
        await _spend(db_session, job=await _job(db_session), gbp="4.00")

        await _guard().check(db_session, job=job, projected_gbp=Decimal("1.00"), now=NOW)

    async def test_the_run_s_own_spend_is_counted_once_not_twice(
        self, db_session: AsyncSession
    ) -> None:
        """This run's rows are already inside the month's window.

        Adding ``spend_so_far`` to the month's total would double them and refuse a step
        that fits — the failure mode nobody would report, because it looks like the cap
        working.
        """
        job = await _job(db_session)
        await _spend(db_session, job=job, gbp="5.00")

        await _guard(monthly="10.00").check(
            db_session, job=job, projected_gbp=Decimal("4.00"), now=NOW
        )

    async def test_exactly_at_the_cap_is_allowed(self, db_session: AsyncSession) -> None:
        """Over, not at. A cap of £10 must permit the step that brings the month to £10."""
        job = await _job(db_session)
        await _spend(db_session, job=await _job(db_session), gbp="9.00")

        await _guard().check(db_session, job=job, projected_gbp=Decimal("1.00"), now=NOW)

    async def test_the_per_run_cap_is_still_checked_and_named(
        self, db_session: AsyncSession
    ) -> None:
        job = await _job(db_session)
        await _spend(db_session, job=job, gbp="0.90")

        with pytest.raises(BudgetExceededError) as refused:
            await _guard(per_run="1.00", monthly="1000.00").check(
                db_session, job=job, projected_gbp=Decimal("0.50"), now=NOW
            )

        assert refused.value.context["scope"] == "per_run"

    async def test_the_per_run_cap_is_reported_when_both_are_breached(
        self, db_session: AsyncSession
    ) -> None:
        """The narrower ceiling first: it is the one the operator set on this request."""
        job = await _job(db_session)
        await _spend(db_session, job=job, gbp="0.90")

        with pytest.raises(BudgetExceededError) as refused:
            await _guard(per_run="1.00", monthly="1.00").check(
                db_session, job=job, projected_gbp=Decimal("0.50"), now=NOW
            )

        assert refused.value.context["scope"] == "per_run"

    async def test_the_month_warns_before_it_stops(
        self,
        db_session: AsyncSession,
        caplog: pytest.LogCaptureFixture,
        bridged_logging: None,
    ) -> None:
        """A ceiling arrived at without notice is one the operator finds out about late."""
        job = await _job(db_session)
        await _spend(db_session, job=await _job(db_session), gbp="8.00")

        with caplog.at_level(logging.WARNING):
            await _guard().check(db_session, job=job, projected_gbp=Decimal("0.10"), now=NOW)

        warned = [
            e for e in structlog_events(caplog.records) if e["event"] == "budget.approaching_cap"
        ]
        assert [e["scope"] for e in warned] == ["monthly"]


class TestWhichCapTheConsoleReports:
    """``RunState`` reads the scope back off the step that recorded the refusal."""

    def _state(self, error: dict[str, Any] | None) -> RunState:
        job = Job(
            work_order_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            workflow_version="test-1",
            code_version="abc",
            status=JobStatus.BUDGET_EXCEEDED,
            started_at=NOW,
        )
        step = JobStep(
            job_id=uuid.uuid4(),
            step_key="plan",
            sequence=1,
            status=JobStatus.FAILED,
            idempotency_key="k",
            error=error,
        )
        return RunState(job=job, steps=[step], spend_gbp=Decimal(0))

    def test_a_monthly_stop_is_reported_as_monthly(self) -> None:
        state = self._state(
            {"code": "budget_exceeded", "message": "m", "context": {"scope": "monthly"}}
        )

        assert state.budget_scope == "monthly"

    def test_a_per_run_stop_is_reported_as_per_run(self) -> None:
        state = self._state(
            {"code": "budget_exceeded", "message": "m", "context": {"scope": "per_run"}}
        )

        assert state.budget_scope == "per_run"

    def test_a_step_that_failed_for_another_reason_reports_nothing(self) -> None:
        state = self._state({"code": "unexpected_error", "message": "boom"})

        assert state.budget_scope is None

    def test_a_run_with_no_recorded_error_reports_nothing(self) -> None:
        assert self._state(None).budget_scope is None


class TestWhatTheConsoleSays:
    """The banner is the whole point of carrying the scope through.

    Fetched rather than reasoned about: the scope ends up in an ``{% if %}`` in a template,
    and a Jinja mistake there is invisible to every test that stops at the service layer.
    """

    @pytest.fixture
    async def stopped_run(self, db_engine: Any) -> Any:
        """Commits a run halted on budget, cleaning the slate on **both** sides of the test.

        Committed rather than flushed, because the console reads through its own session and
        a savepoint-scoped write is invisible to it.

        The clean *before* is as load-bearing as the one after, and it was missing. The
        console resolves "the current user" as the **oldest** row in ``users``, and shows a
        run only to its owner — so a user left behind by an earlier file (they truncate at
        setup, serving the next test rather than this one) became the current user, this
        fixture's newer user did not, and an entirely correct ownership check turned the
        page into a 404. Only ever visible when such a file ran *first*, an ordering none
        of the three suite runs had produced.
        """
        await delete_all(db_engine)
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

        async def make(*, scope: str | None) -> uuid.UUID:
            async with factory() as session:
                job = await _job(session)
                job.status = JobStatus.BUDGET_EXCEEDED
                session.add(
                    JobStep(
                        job_id=job.id,
                        step_key="plan",
                        sequence=1,
                        status=JobStatus.FAILED,
                        idempotency_key=f"k-{uuid.uuid4()}",
                        input_hash="0" * 64,
                        error={
                            "code": "budget_exceeded",
                            "message": "stopped",
                            "context": {"scope": scope} if scope is not None else {},
                        },
                    )
                )
                await session.commit()
                return job.id

        yield make

        await delete_all(db_engine)

    async def test_a_monthly_stop_does_not_tell_the_operator_to_raise_the_request_cap(
        self, api: Any, stopped_run: Any
    ) -> None:
        """The wrong remedy, confidently given, costs more than no remedy at all."""
        job_id = await stopped_run(scope="monthly")

        page = await api.get(f"/runs/{job_id}")

        assert page.status_code == 200
        assert "Stopped on the monthly budget" in page.text
        assert "Raise this run" not in page.text
        assert "will not release it" in page.text

    async def test_a_per_run_stop_offers_the_ceiling_it_names(
        self, api: Any, stopped_run: Any
    ) -> None:
        """The remedy and the control are on one page. They were on two, and one refused."""
        job_id = await stopped_run(scope="per_run")

        page = await api.get(f"/runs/{job_id}")

        assert "Raise this run" in page.text
        assert 'id="raise-cap-form"' in page.text
        assert "Stopped on the monthly budget" not in page.text

    async def test_a_stop_with_no_recorded_scope_falls_back_to_the_request(
        self, api: Any, stopped_run: Any
    ) -> None:
        """A run started before the scope existed still gets a sentence that makes sense."""
        job_id = await stopped_run(scope=None)

        page = await api.get(f"/runs/{job_id}")

        assert "Raise this run" in page.text
        assert 'id="raise-cap-form"' in page.text
