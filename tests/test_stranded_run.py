"""A run whose worker died under it can be continued as itself.

The readiness audit of 2026-09 watched it happen: the worker was stopped while `extract`
ran, the job and its step row stayed RUNNING, a restarted worker never picked it up (arq
does not retry, by design), and *Continue* — the console's and the CLI's — refused with
"It is running now". The only way on was to re-enqueue the job id by hand, which no
surface offers. These tests hold the fix to its evidence: a RUNNING run is offered a
resume only once the worker's own health record says nothing is executing it.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aer.core.enums import JobStatus
from aer.db.models import AuditEvent, Job, JobStep, User
from aer.errors import ConflictError
from aer.queue import HEALTH_CHECK_KEY, WorkerHealth
from aer.services.resume import STRANDED_AFTER_SECONDS, resume_run, stranding_of
from tests.api_fixtures import build_app, client_for
from tests.workflow_fixtures import seed_job, seed_request, seed_user

pytestmark = pytest.mark.integration


async def _running_job(session: AsyncSession, *, began_seconds_ago: int) -> tuple[Job, User]:
    """A run recorded as RUNNING, with the step it stopped in recorded the same way."""
    user = await seed_user(session)
    request = await seed_request(session, user=user)
    job = await seed_job(session, request=request)
    job.status = JobStatus.RUNNING
    session.add(
        JobStep(
            job_id=job.id,
            step_key="extract",
            sequence=3,
            status=JobStatus.RUNNING,
            attempt=0,
            idempotency_key=f"{job.id}:extract",
            input_hash="0" * 64,
            started_at=datetime.now(UTC) - timedelta(seconds=began_seconds_ago),
        )
    )
    await session.flush()
    return job, user


def _health(*, ongoing: int, seconds_ago: int = 5) -> WorkerHealth:
    return WorkerHealth(
        reported_seconds_ago=seconds_ago, ongoing=ongoing, queued=0, completed=0, failed=0
    )


class TestStrandingIsDecidedFromTheWorkersOwnRecord:
    async def test_no_worker_at_all_is_stranded(self, db_session: AsyncSession) -> None:
        job, _ = await _running_job(db_session, began_seconds_ago=600)

        verdict = await stranding_of(db_session, job=job, health=None)

        assert verdict.stranded is True
        assert "No worker has reported" in verdict.reason

    async def test_a_worker_with_nothing_in_flight_is_stranded_once_the_step_is_old(
        self, db_session: AsyncSession
    ) -> None:
        job, _ = await _running_job(db_session, began_seconds_ago=STRANDED_AFTER_SECONDS + 30)

        verdict = await stranding_of(db_session, job=job, health=_health(ongoing=0))

        assert verdict.stranded is True
        assert "nothing in flight" in verdict.reason

    async def test_a_step_that_only_just_began_is_not_stranded(
        self, db_session: AsyncSession
    ) -> None:
        """The health record lags by up to its interval: a job picked up a moment ago can
        still read as "nothing ongoing", and continuing it then would race the worker."""
        job, _ = await _running_job(db_session, began_seconds_ago=3)

        verdict = await stranding_of(db_session, job=job, health=_health(ongoing=0))

        assert verdict.stranded is False
        assert "may not have reported it yet" in verdict.reason

    async def test_a_worker_with_a_job_in_flight_is_not_evidence(
        self, db_session: AsyncSession
    ) -> None:
        job, _ = await _running_job(db_session, began_seconds_ago=600)

        verdict = await stranding_of(db_session, job=job, health=_health(ongoing=1))

        assert verdict.stranded is False
        assert "in flight" in verdict.reason

    async def test_only_a_running_job_can_be_stranded(self, db_session: AsyncSession) -> None:
        job, _ = await _running_job(db_session, began_seconds_ago=600)
        job.status = JobStatus.FAILED

        verdict = await stranding_of(db_session, job=job, health=None)

        assert verdict.stranded is False


class TestAStrandedRunResumesAsItself:
    async def test_the_attestation_lets_a_running_job_continue_and_the_chain_says_so(
        self, db_session: AsyncSession
    ) -> None:
        job, user = await _running_job(db_session, began_seconds_ago=600)

        await resume_run(db_session, job=job, actor=user, reason="worker died", stranded=True)

        assert job.status is JobStatus.QUEUED
        event = await db_session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
        assert event is not None
        assert event.event_type == "run.resumed"
        assert event.payload["resumed_from"] == "RUNNING"
        assert event.payload["stranded"] is True

    async def test_without_it_running_is_refused_as_before(self, db_session: AsyncSession) -> None:
        job, user = await _running_job(db_session, began_seconds_ago=600)

        with pytest.raises(ConflictError, match="running now"):
            await resume_run(db_session, job=job, actor=user)

        assert job.status is JobStatus.RUNNING

    async def test_the_attestation_does_not_reach_the_other_refusals(
        self, db_session: AsyncSession
    ) -> None:
        """Stranded is a fact about RUNNING. A SUCCEEDED or CANCELLED run is not one."""
        job, user = await _running_job(db_session, began_seconds_ago=600)
        job.status = JobStatus.CANCELLED

        with pytest.raises(ConflictError, match="CANCELLED"):
            await resume_run(db_session, job=job, actor=user, stranded=True)

    async def test_a_resume_from_elsewhere_records_that_it_was_not_stranded(
        self, db_session: AsyncSession
    ) -> None:
        job, user = await _running_job(db_session, began_seconds_ago=600)
        job.status = JobStatus.FAILED

        await resume_run(db_session, job=job, actor=user, stranded=True)

        event = await db_session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
        assert event is not None
        assert event.payload["stranded"] is False


_TABLES = "research_requests, audit_events, users"


@pytest.fixture
async def stranded(db_engine: Any) -> dict[str, Any]:
    """A stranded run, committed, so the pages see it."""
    async with db_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        job, user = await _running_job(session, began_seconds_ago=600)
        await session.commit()
        return {"job_id": job.id, "user_id": user.id}


@pytest.fixture
async def api(
    api_settings: Any,
    db_engine: Any,
    fake_redis: Any,
    stranded: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    enqueued: list[str] = []

    async def record(_redis: Any, job_id: uuid.UUID) -> None:
        enqueued.append(str(job_id))

    monkeypatch.setattr("aer.web.pages.enqueue_run", record)
    async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
        client.enqueued = enqueued  # type: ignore[attr-defined]
        yield client


class TestTheConsoleOffersTheWayOn:
    async def test_a_stranded_run_is_offered_continue_with_the_reason(
        self, api: Any, stranded: dict[str, Any]
    ) -> None:
        # No health record in Redis: no worker has reported.
        page = await api.get(f"/runs/{stranded['job_id']}")

        assert page.status_code == 200
        assert 'id="resume-run"' in page.text
        assert 'id="stranded-run"' in page.text
        assert "No worker has reported" in page.text

    async def test_a_run_a_worker_is_executing_is_not(
        self, api: Any, fake_redis: Any, stranded: dict[str, Any]
    ) -> None:
        await fake_redis.set(
            HEALTH_CHECK_KEY,
            "Sep-11 16:00:00 j_complete=0 j_failed=0 j_retried=0 j_ongoing=1 queued=0",
            px=31_000,
        )

        page = await api.get(f"/runs/{stranded['job_id']}")

        assert page.status_code == 200
        assert 'id="resume-run"' not in page.text

    async def test_continuing_a_stranded_run_queues_it_again(
        self, api: Any, db_engine: Any, stranded: dict[str, Any]
    ) -> None:
        token = await _token(api, stranded["job_id"])

        response = await api.post(f"/runs/{stranded['job_id']}/resume", data={"csrf_token": token})

        assert response.status_code == 303, response.text
        assert api.enqueued == [str(stranded["job_id"])]
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            job = await session.get(Job, stranded["job_id"])
            assert job is not None
            assert job.status is JobStatus.QUEUED

    async def test_continuing_a_run_a_worker_holds_is_refused_with_the_evidence(
        self, api: Any, fake_redis: Any, stranded: dict[str, Any]
    ) -> None:
        token = await _token(api, stranded["job_id"])
        await fake_redis.set(
            HEALTH_CHECK_KEY,
            "Sep-11 16:00:00 j_complete=0 j_failed=0 j_retried=0 j_ongoing=1 queued=0",
            px=31_000,
        )

        response = await api.post(f"/runs/{stranded['job_id']}/resume", data={"csrf_token": token})

        assert response.status_code == 409
        assert "in flight" in response.text
        assert api.enqueued == []


async def _token(api: Any, job_id: uuid.UUID) -> str:
    """The form's token, read from the console; the cookie half rides in the client's jar."""
    page = await api.get(f"/runs/{job_id}")
    found = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert found, "no CSRF token on the console"
    return found.group(1)
