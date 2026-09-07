"""Raising what a run may spend, while the run is going.

Both spend guards refuse a step with "raise the cap on this request to continue", and the
console repeated it — while the only route to the cap was an edit that
:func:`aer.services.requests.immutable_reason` refuses for as long as a run is live. The
platform named a remedy it did not allow, and a run stopped at its ceiling had nowhere to
go but to be abandoned.

So the cap is the one field that moves under a worker, through one narrow operation. These
pin the three things that makes true: that it is allowed exactly where an edit is not, that
it only ever goes up, and that the guard which refused the step sees the new figure at the
next step rather than at the next execution.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.models import AuditEvent, Cost, WorkOrder
from aer.errors import BudgetExceededError, ValidationError
from aer.services import requests as request_service
from aer.workflow.engine import BudgetGuard
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio

CEILING = Decimal("12.00")


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """A run under way, on a request capped below the platform's own per-run budget."""
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user, max_cost_gbp=Decimal("1.00"))
    job = await seed_job(db_session, request=request)
    await db_session.flush()
    return {"session": db_session, "user": user, "request": request, "job": job}


async def _spend(session: AsyncSession, *, job_id: uuid.UUID, gbp: str) -> None:
    session.add(
        Cost(
            job_id=job_id,
            category="model",
            provider="anthropic",
            model="a-model",
            units=Decimal("1"),
            unit_type="call",
            amount_usd=Decimal(gbp) * Decimal("1.25"),
            amount_gbp=Decimal(gbp),
            fx_rate=Decimal("0.80"),
            occurred_at=datetime.now(UTC),
        )
    )
    await session.flush()


class TestTheCapIsTheOneFieldThatMovesUnderAWorker:
    async def test_the_edit_that_would_carry_it_is_refused(self, scene: dict[str, Any]) -> None:
        """The precondition. Without this the narrow operation would be redundant."""
        reason = await request_service.immutable_reason(scene["session"], request=scene["request"])

        assert reason is not None
        assert "worker" in reason

    async def test_the_raise_is_allowed_at_the_same_moment(self, scene: dict[str, Any]) -> None:
        await request_service.raise_cap(
            scene["session"],
            request=scene["request"],
            actor=scene["user"],
            to=Decimal("2.00"),
            ceiling_gbp=CEILING,
        )

        assert Decimal(str(scene["request"].work_order.max_cost_gbp)) == Decimal("2.00")

    async def test_the_work_order_moves_with_it(self, scene: dict[str, Any]) -> None:
        """ADR 0072: the guards read the work order, so a mandate-only raise raises nothing."""
        await request_service.raise_cap(
            scene["session"],
            request=scene["request"],
            actor=scene["user"],
            to=Decimal("3.50"),
            ceiling_gbp=CEILING,
        )

        order = await scene["session"].get(WorkOrder, scene["request"].id)
        assert order is not None
        assert Decimal(str(order.max_cost_gbp)) == Decimal("3.50")

    async def test_it_is_written_into_the_audit_chain(self, scene: dict[str, Any]) -> None:
        """Money the operator authorised is a decision, and decisions are recorded."""
        await request_service.raise_cap(
            scene["session"],
            request=scene["request"],
            actor=scene["user"],
            to=Decimal("2.00"),
            ceiling_gbp=CEILING,
        )

        event = await scene["session"].scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.cap_raised")
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        assert event.payload["from_gbp"] == "1.00"
        assert event.payload["to_gbp"] == "2.00"


class TestOnlyUpwards:
    @pytest.mark.parametrize("asked", ["1.00", "0.50"])
    async def test_a_figure_that_raises_nothing_is_refused(
        self, scene: dict[str, Any], asked: str
    ) -> None:
        """Lowering under a run that has already spent would stop it retroactively."""
        with pytest.raises(ValidationError) as caught:
            await request_service.raise_cap(
                scene["session"],
                request=scene["request"],
                actor=scene["user"],
                to=Decimal(asked),
                ceiling_gbp=CEILING,
            )

        assert "cancel it" in caught.value.message
        assert Decimal(str(scene["request"].work_order.max_cost_gbp)) == Decimal("1.00")

    async def test_the_platform_s_own_budget_is_still_the_ceiling(
        self, scene: dict[str, Any]
    ) -> None:
        """The same bound a new request is held to, from the same function."""
        with pytest.raises(ValidationError) as caught:
            await request_service.raise_cap(
                scene["session"],
                request=scene["request"],
                actor=scene["user"],
                to=CEILING + Decimal("0.01"),
                ceiling_gbp=CEILING,
            )

        assert "AER_PER_RUN_BUDGET_GBP" in caught.value.message
        assert Decimal(str(scene["request"].work_order.max_cost_gbp)) == Decimal("1.00")


class TestTheGuardSeesItAtTheNextStep:
    """The whole reason the operation exists: the run continues.

    The engine's guard used to hold the cap it was constructed with, once per execution —
    and an execution is most of a run. A raise would have been read only after the run
    stopped and was resumed, which is the ceremony this was meant to remove.
    """

    async def test_the_step_refused_before_the_raise_is_allowed_after_it(
        self, scene: dict[str, Any]
    ) -> None:
        session: AsyncSession = scene["session"]
        await _spend(session, job_id=scene["job"].id, gbp="0.90")
        guard = BudgetGuard(monthly_cap_gbp=Decimal("1000"))

        with pytest.raises(BudgetExceededError) as refused:
            await guard.check(session, job=scene["job"], projected_gbp=Decimal("0.30"))
        assert "Raise the cap on this request" in refused.value.message

        await request_service.raise_cap(
            session,
            request=scene["request"],
            actor=scene["user"],
            to=Decimal("2.00"),
            ceiling_gbp=CEILING,
        )

        await guard.check(session, job=scene["job"], projected_gbp=Decimal("0.30"))

    async def test_a_caller_with_its_own_ceiling_still_gets_it(self, scene: dict[str, Any]) -> None:
        """A skill dry run projects against a stated figure, not against a live control."""
        session: AsyncSession = scene["session"]
        await _spend(session, job_id=scene["job"].id, gbp="0.90")
        guard = BudgetGuard(monthly_cap_gbp=Decimal("1000"), per_run_cap_gbp=Decimal("0.95"))

        await request_service.raise_cap(
            session,
            request=scene["request"],
            actor=scene["user"],
            to=Decimal("9.00"),
            ceiling_gbp=CEILING,
        )

        with pytest.raises(BudgetExceededError):
            await guard.check(session, job=scene["job"], projected_gbp=Decimal("0.10"))
