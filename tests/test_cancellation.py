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

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aer.core.enums import JobStatus
from aer.db.models import AuditEvent, Job, JobCancellation, JobStep
from aer.errors import ConflictError
from aer.services import cancellation as cancellation_service
from aer.workflow.engine import StepResult, WorkflowEngine, WorkflowStep
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.anyio


def counting_step(key: str, log: list[str]) -> WorkflowStep:
    """A step that records that it ran, so "did it stop?" is a fact rather than a status."""

    async def run(_context: object) -> StepResult:
        log.append(key)
        return StepResult(output={"ran": key})

    return WorkflowStep(key=key, run=run)  # type: ignore[arg-type]


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
