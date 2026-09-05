"""What a run at this depth has actually cost, and when the platform declines to say.

The valuable half is the refusal. A fresh install has no history, and the tempting answer —
average the zero runs it has, render "£0.00" — is a figure carrying the confidence of a
measurement with nothing at all behind it. An operator setting a spending ceiling against that
number is being misled by a page that looks helpful.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import AnalysisMode, JobStatus, RequestStatus
from aer.db.models.job import Job
from aer.db.models.user import User
from aer.services.overview import MINIMUM_SAMPLE, typical_cost
from tests.request_fixtures import research_request


async def _user(session: AsyncSession) -> User:
    user = User(id=uuid.uuid4(), email=f"op-{uuid.uuid4().hex[:8]}@example.com", display_name="Op")
    session.add(user)
    await session.flush()
    return user


async def _finished(
    session: AsyncSession,
    user: User,
    cost: str,
    *,
    mode: AnalysisMode = AnalysisMode.STANDARD,
    status: JobStatus = JobStatus.SUCCEEDED,
) -> None:
    request = research_request(
        id=uuid.uuid4(),
        user_id=user.id,
        company_name="Contoso plc",
        ticker="CTSO",
        exchange="LSE",
        as_of_date=dt.date(2026, 8, 24),
        base_currency="GBP",
        investment_horizon_months=12,
        analysis_mode=mode,
        point_in_time=True,
        status=RequestStatus.APPROVED,
    )
    request.work_order.max_cost_gbp = Decimal("8.00")
    session.add(request)
    await session.flush()
    session.add(
        Job(
            id=uuid.uuid4(),
            # The run root the mandate is a detail of, not a second one beside it.
            work_order_id=request.id,
            workflow_version="v1",
            code_version="test",
            status=status,
            started_at=dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.UTC),
            total_cost_gbp=Decimal(cost),
        )
    )
    await session.flush()


class TestItRefusesToGuess:
    async def test_no_history_at_all_is_unknown(self, db_session: AsyncSession) -> None:
        """The state every fresh install is in, and the one a mean would render as £0.00."""
        user = await _user(db_session)

        found = await typical_cost(db_session, user_id=user.id, mode=AnalysisMode.STANDARD)

        assert not found.is_known
        assert found.low is None
        assert found.high is None
        assert found.sample == 0

    async def test_one_run_is_an_anecdote_and_not_a_range(self, db_session: AsyncSession) -> None:
        """A "typical" cost quoted from a single run is that run's cost with a plural noun."""
        user = await _user(db_session)
        await _finished(db_session, user, "6.40")

        found = await typical_cost(db_session, user_id=user.id, mode=AnalysisMode.STANDARD)

        assert not found.is_known
        assert found.sample == 1

    async def test_it_says_how_little_it_has(self, db_session: AsyncSession) -> None:
        """The count survives the refusal, so a surface can say *why* there is no range."""
        user = await _user(db_session)
        for cost in ("1.00", "2.00"):
            await _finished(db_session, user, cost)

        assert (
            await typical_cost(db_session, user_id=user.id, mode=AnalysisMode.STANDARD)
        ).sample == MINIMUM_SAMPLE - 1


class TestWhatItReportsWhenItCan:
    async def test_the_extremes_rather_than_the_middle(self, db_session: AsyncSession) -> None:
        """An operator setting a ceiling wants to know what it *might* cost. A mean hides the
        run that went to eight pounds behind four that went to two."""
        user = await _user(db_session)
        for cost in ("2.10", "8.40", "3.00"):
            await _finished(db_session, user, cost)

        found = await typical_cost(db_session, user_id=user.id, mode=AnalysisMode.STANDARD)

        assert found.is_known
        assert found.low == Decimal("2.10")
        assert found.high == Decimal("8.40")
        assert found.sample == 3

    async def test_another_depth_is_another_question(self, db_session: AsyncSession) -> None:
        """Depth is most of what a run costs, so a deep run's price is no guidance at all
        about a standard one."""
        user = await _user(db_session)
        for cost in ("2.00", "3.00", "4.00"):
            await _finished(db_session, user, cost, mode=AnalysisMode.FULL)

        assert not (
            await typical_cost(db_session, user_id=user.id, mode=AnalysisMode.STANDARD)
        ).is_known

    async def test_a_run_that_did_not_finish_is_not_a_cost(self, db_session: AsyncSession) -> None:
        """A run that failed halfway spent something, and what it spent is not what the work
        costs — quoting it would make the guidance cheaper the more often runs broke."""
        user = await _user(db_session)
        for cost in ("2.00", "3.00", "4.00"):
            await _finished(db_session, user, cost, status=JobStatus.FAILED)

        assert not (
            await typical_cost(db_session, user_id=user.id, mode=AnalysisMode.STANDARD)
        ).is_known

    async def test_somebody_elses_runs_are_not_guidance(self, db_session: AsyncSession) -> None:
        """Cost depends on the provider, the model and the company. A number from another
        operator's setup is guidance about their setup."""
        user = await _user(db_session)
        other = await _user(db_session)
        for cost in ("2.00", "3.00", "4.00"):
            await _finished(db_session, other, cost)

        assert not (
            await typical_cost(db_session, user_id=user.id, mode=AnalysisMode.STANDARD)
        ).is_known
