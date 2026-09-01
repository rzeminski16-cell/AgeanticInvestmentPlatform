"""The mandate behind a run, when the run has one — roadmap §3.3, ADR 0072.

The last thing standing between the schema and a run that is not about a company. While
every caller reached for `job.request_id` and assumed a row came back, a monitor could not
exist: the read asserted an equity mandate, for every kind of run there would ever be.

Two functions rather than one, because the callers genuinely split. A run console serves
whatever ran; a report page is research machinery and a research job with no detail row is
referential breakage. The tests below are mostly about that split holding — an absence
returned where it should be returned, and refused where it should be refused.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import WorkOrder
from aer.errors import IntegrityError as BrokenRecordError
from aer.services.mandate import (
    mandate_for,
    mandate_of,
    required_mandate,
    required_mandate_for,
)
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def _research_run(session: AsyncSession) -> Any:
    user = await seed_user(session)
    request = await seed_request(session, user=user)
    return await seed_job(session, request=request)


async def _monitor_run(session: AsyncSession) -> WorkOrder:
    """A work order with no research request — what §3.6 will be built out of.

    Constructed directly rather than through `create_request`, which is the point: the
    whole item is that a run root can exist without a mandate hanging off it.
    """
    user = await seed_user(session)
    order = WorkOrder(
        user_id=user.id,
        tool="monitor",
        subject_kind="thesis",
        subject_id=uuid.uuid4(),
        as_of_date=date(2026, 6, 30),
        point_in_time=True,
    )
    session.add(order)
    await session.flush()
    return order


class TestAResearchRunHasOne:
    async def test_it_is_read_by_the_work_orders_id(self, db_session: AsyncSession) -> None:
        """The detail row shares the run root's key, so there is no join to find and no
        second column to keep in step — which is what lets `jobs.request_id` be dropped
        without any of these callers changing again."""
        job = await _research_run(db_session)

        mandate = await mandate_of(db_session, job)

        assert mandate is not None
        assert mandate.id == job.work_order_id
        assert mandate.ticker == "MSFT"

    async def test_the_refusing_read_returns_it_too(self, db_session: AsyncSession) -> None:
        job = await _research_run(db_session)

        assert (await required_mandate(db_session, job)).ticker == "MSFT"

    async def test_it_can_be_read_from_the_work_order_alone(self, db_session: AsyncSession) -> None:
        """A caller holding a work order and no job — a gate decided before any job exists
        — reaches the same row by the same key."""
        job = await _research_run(db_session)

        assert (await mandate_for(db_session, job.work_order_id)) is not None


class TestARunWithoutOne:
    async def test_the_optional_read_says_there_is_none(self, db_session: AsyncSession) -> None:
        """Not an error and not an empty mandate: a fact about what kind of run this is."""
        order = await _monitor_run(db_session)

        assert await mandate_for(db_session, order.id) is None

    async def test_the_refusing_read_refuses(self, db_session: AsyncSession) -> None:
        order = await _monitor_run(db_session)

        with pytest.raises(BrokenRecordError, match="no research request"):
            await required_mandate_for(db_session, order.id)

    async def test_the_refusal_names_both_ways_it_can_be_true(
        self, db_session: AsyncSession
    ) -> None:
        """A guard that shrugged here is one an orphaned step walks straight past — but a
        guard that only says "missing" sends a reader looking for corruption when the
        answer is that the surface is reading the wrong row."""
        order = await _monitor_run(db_session)

        with pytest.raises(BrokenRecordError) as refused:
            await required_mandate_for(db_session, order.id)

        assert "not a research run" in str(refused.value)
        assert "referential breakage" in str(refused.value)
