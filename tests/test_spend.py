"""What the platform spent, and whether the cache is earning its keep.

Gap A15. The arithmetic here is simple enough that the risk is not a wrong sum — it is a
figure that looks right and means something else. Two of those are worth naming, and both
have a test below.

`input_tokens` from the API is the **uncached remainder**, not the whole prompt. A summary
that reported it as "prompt tokens" would show a heavily cached run as a cheap one, which is
the direction that makes the platform look better than it is.

A hit rate of zero and a hit rate of "no calls yet" are different facts. Collapsing them
puts a run that never asked for a cache beside one that asked and was refused — and the
second is a defect, so it has to be visible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import JobStatus, RequestStatus, UserRole
from aer.db.models import AgentRun, Cost, Job, JobStep, ResearchRequest, User
from aer.services.spend import CacheUse, recent_runs, spend_by_role, spend_summary
from tests.api_fixtures import build_app, client_for


async def _job(session: AsyncSession) -> Job:
    user = User(
        email=f"spend-{uuid.uuid4()}@example.invalid", display_name="S", role=UserRole.OWNER
    )
    session.add(user)
    await session.flush()
    request = ResearchRequest(
        user_id=user.id,
        company_name="Microsoft Corporation",
        ticker="MSFT",
        exchange="NASDAQ",
        as_of_date=datetime.now(UTC).date(),
        base_currency="USD",
        investment_horizon_months=12,
        max_cost_gbp="2.00",
        portfolio_context={},
        point_in_time=True,
        status=RequestStatus.DRAFT,
    )
    session.add(request)
    await session.flush()
    job = Job(
        request_id=request.id,
        workflow_version="test-1",
        code_version="abc",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    return job


async def _call(
    session: AsyncSession,
    job: Job,
    *,
    role: str = "report_writer",
    model: str = "claude-opus-5",
    fresh: int = 100,
    read: int = 0,
    written: int = 0,
    output: int = 50,
    sequence: int = 1,
) -> AgentRun:
    step = JobStep(
        job_id=job.id,
        step_key=f"step-{sequence}",
        sequence=sequence,
        status=JobStatus.SUCCEEDED,
        attempt=0,
        idempotency_key=f"{job.id}:step-{sequence}",
        input_hash="0" * 64,
        started_at=datetime.now(UTC),
    )
    session.add(step)
    await session.flush()
    run = AgentRun(
        job_step_id=step.id,
        agent_role=role,
        provider="anthropic",
        model=model,
        input_tokens=fresh,
        output_tokens=output,
        cache_read_tokens=read,
        cache_write_tokens=written,
    )
    session.add(run)
    await session.flush()
    return run


class TestTheCacheArithmetic:
    """Pure, so it can be wrong in only one way: by defining the ratio badly."""

    def test_prompt_tokens_counts_all_three_charges(self) -> None:
        """The trap. `input_tokens` alone is the uncached remainder, not the prompt."""
        use = CacheUse(fresh_tokens=100, read_tokens=900, written_tokens=200)

        assert use.prompt_tokens == 1200

    def test_the_hit_rate_is_reads_over_everything_sent(self) -> None:
        use = CacheUse(fresh_tokens=250, read_tokens=750, written_tokens=0)

        assert use.hit_rate == Decimal("0.7500")

    def test_no_tokens_at_all_has_no_hit_rate(self) -> None:
        """`None`, not zero: never asked and asked-and-refused are different findings."""
        assert CacheUse().hit_rate is None

    def test_asking_and_missing_is_zero_not_none(self) -> None:
        assert CacheUse(fresh_tokens=5_000).hit_rate == Decimal("0")


@pytest.mark.integration
class TestOverARealRun:
    async def test_a_run_with_no_calls_reports_nothing_rather_than_zero(
        self, db_session: AsyncSession
    ) -> None:
        job = await _job(db_session)

        summary = await spend_summary(db_session, job_id=job.id)

        assert summary.calls == 0
        assert summary.hit_rate is None
        assert summary.total_gbp == Decimal(0)

    async def test_the_summary_adds_up_what_the_run_spent(self, db_session: AsyncSession) -> None:
        job = await _job(db_session)
        await _call(db_session, job, fresh=1_000, read=3_000, written=500, sequence=1)
        await _call(db_session, job, fresh=200, read=4_000, sequence=2)

        summary = await spend_summary(db_session, job_id=job.id)

        assert summary.calls == 2
        assert summary.cache.prompt_tokens == 1_000 + 3_000 + 500 + 200 + 4_000
        assert summary.cache.read_tokens == 7_000

    async def test_the_money_comes_from_the_cost_rows_as_metered(
        self, db_session: AsyncSession
    ) -> None:
        """Not recomputed from tokens: a later price change must not rewrite history."""
        job = await _job(db_session)
        run = await _call(db_session, job)
        db_session.add(
            Cost(
                job_id=job.id,
                agent_run_id=run.id,
                category="llm_input",
                provider="anthropic",
                model="claude-opus-5",
                units=Decimal("100"),
                unit_type="tokens",
                amount_usd=Decimal("0.500000"),
                amount_gbp=Decimal("0.400000"),
                fx_rate=Decimal("0.800000"),
            )
        )
        await db_session.flush()

        summary = await spend_summary(db_session, job_id=job.id)

        assert summary.total_gbp == Decimal("0.400000")
        assert summary.by_kind == (("llm_input", Decimal("0.400000")),)

    async def test_roles_are_split_by_model_as_well_as_role(self, db_session: AsyncSession) -> None:
        """A routing change leaves one role against two models; averaging hides it."""
        job = await _job(db_session)
        await _call(db_session, job, role="analysis", model="claude-opus-5", sequence=1)
        await _call(db_session, job, role="analysis", model="claude-sonnet-5", sequence=2)

        rows = await spend_by_role(db_session, job_id=job.id)

        assert len(rows) == 2
        assert {r.model for r in rows} == {"claude-opus-5", "claude-sonnet-5"}

    async def test_roles_are_ordered_by_what_they_consumed(self, db_session: AsyncSession) -> None:
        job = await _job(db_session)
        await _call(db_session, job, role="small", fresh=10, sequence=1)
        await _call(db_session, job, role="large", fresh=90_000, sequence=2)

        rows = await spend_by_role(db_session, job_id=job.id)

        assert [r.role for r in rows] == ["large", "small"]

    async def test_one_run_does_not_count_another(self, db_session: AsyncSession) -> None:
        """The scoping join. Without it every run reports the whole platform's spend."""
        mine = await _job(db_session)
        theirs = await _job(db_session)
        await _call(db_session, mine, fresh=1_000, sequence=1)
        await _call(db_session, theirs, fresh=5_000, sequence=1)

        summary = await spend_summary(db_session, job_id=mine.id)

        assert summary.calls == 1
        assert summary.cache.fresh_tokens == 1_000

    async def test_omitting_the_job_totals_the_platform(self, db_session: AsyncSession) -> None:
        await db_session.execute(text("DELETE FROM agent_runs"))
        first = await _job(db_session)
        second = await _job(db_session)
        await _call(db_session, first, fresh=1_000, sequence=1)
        await _call(db_session, second, fresh=5_000, sequence=1)

        summary = await spend_summary(db_session)

        assert summary.calls == 2
        assert summary.cache.fresh_tokens == 6_000

    async def test_recent_runs_reports_zero_for_a_run_that_spent_nothing(
        self, db_session: AsyncSession
    ) -> None:
        """A run with no cost rows must appear, not vanish from the list."""
        job = await _job(db_session)

        listed = await recent_runs(db_session, limit=50)

        assert any(row_job.id == job.id and amount == Decimal(0) for row_job, amount in listed)


@pytest.fixture
async def committed_user(db_engine: Any) -> Any:
    """A user the app's own session can see. `db_session` rolls back, so this commits."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="costs@example.invalid", display_name="Costs", role=UserRole.OWNER)
        session.add(user)
        await session.commit()
        yield user
        await session.delete(user)
        await session.commit()


@pytest.fixture
async def api(api_settings: Any, db_engine: Any, fake_redis: Any, committed_user: Any) -> Any:
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        yield client


@pytest.mark.integration
class TestThePage:
    async def test_it_renders(self, api: Any) -> None:
        response = await api.get("/costs")

        assert response.status_code == 200
        assert "Served from cache" in response.text

    async def test_an_empty_platform_says_so_rather_than_showing_a_false_zero(
        self, api: Any
    ) -> None:
        """With no calls recorded the hit rate is a dash, not 0%."""
        response = await api.get("/costs")

        assert "No model calls recorded yet." in response.text
