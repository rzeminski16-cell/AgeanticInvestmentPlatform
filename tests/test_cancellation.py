"""Stopping a run that is already going.

The design being protected here is the one that is easy to get wrong and expensive to
discover: cancellation is a *request*, recorded in its own table, acted on by the engine
between steps. Three properties matter, and each has its own class.

1. **Recording it never blocks.** The worker holds the ``jobs`` row's lock for the whole
   run, so a cancel that wrote to ``jobs`` would wait for exactly as long as cancelling was
   still worth doing. Writing elsewhere is the entire reason the table exists.
2. **The engine actually stops.** A cancellation nobody reads is a button that lies.
3. **It stops at a boundary, not mid-step.** A step already running finishes. Anything else
   would abandon work already paid for and record a stop time that never happened.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus
from aer.db.models import AuditEvent, Cost, Job, JobCancellation, JobStep
from aer.errors import ConflictError
from aer.services import cancellation as cancellation_service
from aer.services import requests as request_service
from aer.services import runs as run_service
from aer.workflow.engine import StepResult, WorkflowEngine, WorkflowStep
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio


def counting_step(key: str, log: list[str]) -> WorkflowStep:
    """A step that records that it ran, so "did it stop?" is a fact rather than a status."""

    async def run(_context: object) -> StepResult:
        log.append(key)
        return StepResult(output={"ran": key})

    return WorkflowStep(key=key, run=run)  # type: ignore[arg-type]


def _a_cost(job_id: uuid.UUID) -> Cost:
    """One model call's worth of spend, as the planner would record it."""
    return Cost(
        job_id=job_id,
        category="model",
        provider="anthropic",
        model="claude-opus-5",
        units=Decimal(150),
        unit_type="tokens",
        amount_usd=Decimal("0.0500"),
        amount_gbp=Decimal("0.0400"),
        fx_rate=Decimal("0.8"),
    )


@pytest.fixture
async def queued(db_session: AsyncSession) -> dict[str, object]:
    user = await seed_user(db_session)
    request = await seed_request(db_session, user=user)
    job = await seed_job(db_session, request=request)
    return {"session": db_session, "user": user, "request": request, "job": job}


class TestRequestingIt:
    async def test_a_cancellation_is_recorded_against_the_job(self, queued: dict) -> None:
        session, job, user = queued["session"], queued["job"], queued["user"]

        await cancellation_service.request_cancellation(
            session, job=job, actor=user, reason="Wrong as-of date"
        )

        found = await cancellation_service.cancellation_for(session, job_id=job.id)
        assert found is not None
        assert found.reason == "Wrong as-of date"
        assert found.requested_by == user.id

    async def test_it_is_not_written_to_the_jobs_row(self, queued: dict) -> None:
        # The whole design in one assertion. The worker holds this row's lock for the length
        # of the run, so a cancellation that touched it would block until the run it was
        # trying to stop had finished on its own.
        session, job, user = queued["session"], queued["job"], queued["user"]
        before = job.status

        await cancellation_service.request_cancellation(session, job=job, actor=user)

        assert job.status is before

    async def test_asking_twice_returns_the_standing_request(self, queued: dict) -> None:
        session, job, user = queued["session"], queued["job"], queued["user"]

        first = await cancellation_service.request_cancellation(session, job=job, actor=user)
        second = await cancellation_service.request_cancellation(
            session, job=job, actor=user, reason="changed my mind about the reason"
        )

        assert second.id == first.id
        # The first reason stands. A second row would be a second thing to interpret, and
        # there is only one decision here.
        assert second.reason is None
        rows = (
            await session.scalars(select(JobCancellation).where(JobCancellation.job_id == job.id))
        ).all()
        assert len(rows) == 1

    @pytest.mark.parametrize("status", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED])
    async def test_a_finished_run_cannot_be_cancelled(
        self, queued: dict, status: JobStatus
    ) -> None:
        # Refused because it would be a false record, not because the write would fail. The
        # run reached its end; the audit trail must not say something else stopped it.
        session, job, user = queued["session"], queued["job"], queued["user"]
        job.status = status
        await session.flush()

        with pytest.raises(ConflictError) as raised:
            await cancellation_service.request_cancellation(session, job=job, actor=user)

        assert raised.value.http_status == 409
        assert await cancellation_service.cancellation_for(session, job_id=job.id) is None

    async def test_it_is_recorded_in_the_audit_log_with_the_status_at_the_time(
        self, queued: dict
    ) -> None:
        session, job, user = queued["session"], queued["job"], queued["user"]
        job.status = JobStatus.RUNNING
        await session.flush()

        await cancellation_service.request_cancellation(session, job=job, actor=user, reason="oops")

        event = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "run.cancellation_requested")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["job_id"] == str(job.id)
        assert event.payload["reason"] == "oops"
        # What the run was doing when the operator asked. Reconstructing that later from
        # timestamps alone is guesswork.
        assert event.payload["status_when_requested"] == JobStatus.RUNNING.value
        assert event.this_hash


class TestTheEngineActsOnIt:
    async def test_a_cancelled_run_executes_no_further_steps(self, queued: dict) -> None:
        session, job, user = queued["session"], queued["job"], queued["user"]
        log: list[str] = []
        engine = WorkflowEngine([counting_step("first", log), counting_step("second", log)])

        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await engine.run(session, job=job, services={})

        assert log == []

    async def test_the_job_ends_in_cancelled(self, queued: dict) -> None:
        session, job, user = queued["session"], queued["job"], queued["user"]
        engine = WorkflowEngine([counting_step("first", [])])

        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await engine.run(session, job=job, services={})

        assert job.status is JobStatus.CANCELLED
        assert job.finished_at is not None

    async def test_the_step_in_flight_finishes_before_it_stops(self, queued: dict) -> None:
        """The honest granularity, asserted rather than described.

        The first step cancels the run from inside itself, standing in for an operator who
        pressed the button while it was running. It still completes and still records its
        output; only the *next* step is skipped.
        """
        session, job, user = queued["session"], queued["job"], queued["user"]
        log: list[str] = []

        async def cancel_midway(_context: object) -> StepResult:
            log.append("first")
            await cancellation_service.request_cancellation(session, job=job, actor=user)
            return StepResult(output={"ran": "first"})

        engine = WorkflowEngine(
            [
                WorkflowStep(key="first", run=cancel_midway),  # type: ignore[arg-type]
                counting_step("second", log),
            ]
        )
        await engine.run(session, job=job, services={})

        assert log == ["first"]
        first = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == "first")
        )
        assert first is not None
        assert first.status is JobStatus.SUCCEEDED
        assert first.output_ref == {"ran": "first"}

    async def test_no_step_row_is_written_for_a_step_that_never_ran(self, queued: dict) -> None:
        # A row for a step that did not execute would make the console show work that never
        # happened, and would make a resumed run's idempotency check ambiguous.
        session, job, user = queued["session"], queued["job"], queued["user"]
        engine = WorkflowEngine([counting_step("first", [])])

        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await engine.run(session, job=job, services={})

        rows = (await session.scalars(select(JobStep).where(JobStep.job_id == job.id))).all()
        assert rows == []

    async def test_an_uncancelled_run_is_unaffected(self, queued: dict) -> None:
        session, job = queued["session"], queued["job"]
        log: list[str] = []
        engine = WorkflowEngine([counting_step("first", log), counting_step("second", log)])

        await engine.run(session, job=job, services={})

        assert log == ["first", "second"]
        assert job.status is not JobStatus.CANCELLED


class TestTheCancellationSurvivesTheRun:
    async def test_the_request_row_is_kept_after_the_run_stops(self, queued: dict) -> None:
        # Kept, not consumed. `requested_at` and `finished_at` are different moments and the
        # gap between them is the honest answer to "how long did it take to stop?".
        session, job, user = queued["session"], queued["job"], queued["user"]
        engine = WorkflowEngine([counting_step("first", [])])

        recorded = await cancellation_service.request_cancellation(session, job=job, actor=user)
        await engine.run(session, job=job, services={})

        assert await cancellation_service.cancellation_for(session, job_id=job.id) is not None
        assert job.finished_at is not None
        assert recorded.requested_at <= job.finished_at

    async def test_deleting_the_job_takes_the_cancellation_with_it(self, queued: dict) -> None:
        session, job, user = queued["session"], queued["job"], queued["user"]
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        job_id = job.id

        await session.delete(await session.get(Job, job_id))
        await session.flush()

        assert await cancellation_service.cancellation_for(session, job_id=job_id) is None


class TestTheServiceLayerBoundary:
    async def test_a_cancellation_costs_nothing(self, queued: dict) -> None:
        # There is no model call and no fetch on this path. Asserted because the obvious
        # future "helpfully summarise why the run was stopped" would break it silently.
        session, job, user = queued["session"], queued["job"], queued["user"]
        from aer.workflow.engine import spend_so_far  # noqa: PLC0415 -- local to this check

        await cancellation_service.request_cancellation(session, job=job, actor=user)

        assert await spend_so_far(session, job_id=job.id) == Decimal(0)


class TestACancelledRunIsNotADeadEnd:
    """The bug this class exists for, found by using the feature.

    Cancelling a run left the request in a state with no way out: the page offered only
    "open the run", ``start_run`` returned the dead job, and the request was frozen against
    editing and deletion because a ``jobs`` row existed. Cancel by mistake and the request
    was rubbish that could not even be thrown away.
    """

    async def test_a_cancelled_run_can_be_replaced_by_a_new_one(self, queued: dict) -> None:
        session, job, user, request = (
            queued["session"],
            queued["job"],
            queued["user"],
            queued["request"],
        )
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await WorkflowEngine([counting_step("first", [])]).run(session, job=job, services={})

        replacement = await run_service.start_run(session, request=request)

        assert replacement.id != job.id
        assert replacement.status is JobStatus.QUEUED

    async def test_the_replacement_is_not_immediately_cancelled_again(self, queued: dict) -> None:
        # A resurrected job would still carry its cancellation and stop on its first step,
        # which is the trap that makes reusing the row wrong rather than merely untidy.
        session, job, user, request = (
            queued["session"],
            queued["job"],
            queued["user"],
            queued["request"],
        )
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await WorkflowEngine([counting_step("first", [])]).run(session, job=job, services={})

        replacement = await run_service.start_run(session, request=request)
        log: list[str] = []
        await WorkflowEngine([counting_step("first", log)]).run(
            session, job=replacement, services={}
        )

        assert log == ["first"]
        assert replacement.status is not JobStatus.CANCELLED

    async def test_the_cancelled_run_is_still_there(self, queued: dict) -> None:
        # Superseded, not erased. What happened, happened, and the audit trail says so.
        session, job, user, request = (
            queued["session"],
            queued["job"],
            queued["user"],
            queued["request"],
        )
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await WorkflowEngine([counting_step("first", [])]).run(session, job=job, services={})
        await run_service.start_run(session, request=request)

        original = await session.get(Job, job.id)
        assert original is not None
        assert original.status is JobStatus.CANCELLED

    async def test_a_live_run_is_returned_rather_than_replaced(self, queued: dict) -> None:
        # "Start again" on a run that is still going means watching that one. Creating a
        # second would have two workers on one request.
        session, job, request = queued["session"], queued["job"], queued["request"]

        assert (await run_service.start_run(session, request=request)).id == job.id

    async def test_a_request_whose_run_left_nothing_behind_is_editable_again(
        self, queued: dict
    ) -> None:
        # The heart of it. A run that gathered nothing, cited nothing and spent nothing
        # leaves nothing an edit could falsify, so freezing the request would be a rule
        # with no damage behind it.
        session, job, user, request = (
            queued["session"],
            queued["job"],
            queued["user"],
            queued["request"],
        )
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await WorkflowEngine([counting_step("first", [])]).run(session, job=job, services={})

        assert await request_service.immutable_reason(session, request=request) is None

    async def test_a_live_run_still_freezes_it(self, queued: dict) -> None:
        session, request = queued["session"], queued["request"]

        reason = await request_service.immutable_reason(session, request=request)

        assert reason is not None
        assert "cancel it" in reason

    async def test_spending_does_not_freeze_it(self, queued: dict) -> None:
        """The common real case, and the one that made "delete" unreachable in practice.

        The planner runs before anyone presses stop, so nearly every cancelled request has
        a cost row. Blocking on that made the delete button theoretical — and it was only
        ever blocking because ``costs`` cascaded away with the request, which is a defect in
        the cascade rather than a reason to keep the request forever.
        """
        session, job, user, request = (
            queued["session"],
            queued["job"],
            queued["user"],
            queued["request"],
        )
        session.add(_a_cost(job.id))
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await WorkflowEngine([counting_step("first", [])]).run(session, job=job, services={})

        assert await request_service.immutable_reason(session, request=request) is None

    async def test_the_spend_survives_the_deletion(self, queued: dict) -> None:
        # The property that makes the above safe. A monthly cap you can get under by
        # deleting what you spent it on is not a cap.
        session, job, user, request = (
            queued["session"],
            queued["job"],
            queued["user"],
            queued["request"],
        )
        session.add(_a_cost(job.id))
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await WorkflowEngine([counting_step("first", [])]).run(session, job=job, services={})

        await request_service.delete_request(session, request=request, actor=user)

        remaining = (await session.scalars(select(Cost))).all()
        assert len(remaining) == 1
        assert remaining[0].amount_gbp == Decimal("0.0400")
        # Orphaned, not deleted: the job it pointed at is gone.
        assert remaining[0].job_id is None

    async def test_the_deletion_records_what_the_spend_was_for(self, queued: dict) -> None:
        # The cost row loses its job reference, so without this the money would still be
        # counted and no longer explicable.
        session, job, user, request = (
            queued["session"],
            queued["job"],
            queued["user"],
            queued["request"],
        )
        session.add(_a_cost(job.id))
        await cancellation_service.request_cancellation(session, job=job, actor=user)
        await WorkflowEngine([counting_step("first", [])]).run(session, job=job, services={})

        await request_service.delete_request(session, request=request, actor=user)

        event = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "request.deleted")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        # Compared as a Decimal, not a string: the sum comes back at the column's own
        # scale, and asserting on "0.040000" would be asserting on NUMERIC(12, 6).
        assert Decimal(event.payload["spend_gbp"]) == Decimal("0.04")
        assert event.payload["ticker"] == "MSFT"
