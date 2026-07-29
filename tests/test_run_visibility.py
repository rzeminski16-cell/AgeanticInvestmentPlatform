"""What another process can see while a run is going, and after one dies.

**Every assertion here reads through a second connection.** That is not ceremony — it is the
only way to test the property at issue. A run executes in the worker; the console renders in
the web process; and uncommitted work is invisible between them however carefully it was
flushed. A test that asserted on the *same* session the engine wrote to would pass with the
transaction boundary in either place, which is exactly how the defects below survived a suite
of 1,369 tests.

Three defects, one cause: the worker held a single transaction for a whole run.

* **The console showed ``QUEUED`` from start to gate**, while money was being spent. Reported
  from a live run, not hypothesised — spend visible at the provider, ``QUEUED`` on the page.
* **A failed run reverted to ``QUEUED``.** ``_fail`` flushed ``FAILED`` and re-raised, and the
  exception left the worker's session without committing. The log line was the only evidence.
* **A crash between gates rolled back every completed step**, so the next attempt re-ran work
  already paid for — the opposite of what the worker's own docstring claimed.

Each test below is written so that it *fails* with the commit in the old place. A test that
only observes after ``execute`` returns cannot tell the two apart, because the worker's final
commit publishes everything at that point; so the ones that need a mid-run view take it from
inside the run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus
from aer.db.models import Job, JobStep, User
from aer.errors import ExternalServiceError
from aer.providers.fake import FakeProvider
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.storage.local import LocalArtefactStore
from aer.workflow.engine import StepContext, StepResult
from aer.workflow.workflows import vertical_slice_v1
from tests.workflow_fixtures import (
    StubSecClient,
    make_provider,
    seed_job,
    seed_request,
    seed_user,
)

pytestmark = pytest.mark.integration

_TABLES = "research_requests, audit_events, users, artefacts, prompts, companies"

# The first step, and the one the operator waits on: a model call lasting about a minute.
_PLAN = "plan"


@pytest.fixture
async def clean_slate(db_engine: Any) -> AsyncIterator[None]:
    """Empty the tables a run writes to, before and after — this file commits for real."""
    await _truncate(db_engine)
    yield
    await _truncate(db_engine)


async def _truncate(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
        await connection.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


class Observer:
    """Reads the database the way the web process does: its own connection, committed only.

    Every method opens a fresh session. Holding one open would pin a snapshot taken before
    the commit under test, and the test would then fail for a reason that has nothing to do
    with the code.
    """

    def __init__(self, engine: Any) -> None:
        self._factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def job_status(self, job_id: uuid.UUID) -> JobStatus | None:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            return job.status if job is not None else None

    async def succeeded_steps(self, job_id: uuid.UUID) -> set[str]:
        return {
            key
            for key, status in (await self.step_statuses(job_id)).items()
            if status is JobStatus.SUCCEEDED
        }

    async def step_statuses(self, job_id: uuid.UUID) -> dict[str, JobStatus]:
        async with self._factory() as session:
            rows = (await session.scalars(select(JobStep).where(JobStep.job_id == job_id))).all()
            return {row.step_key: row.status for row in rows}

    async def step_error(self, job_id: uuid.UUID, step: str) -> dict[str, Any] | None:
        async with self._factory() as session:
            row = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == step)
            )
            return None if row is None else row.error


class Worker:
    """Drives a run the way ``aer.worker.run_research`` does: one session, and it owns it."""

    def __init__(self, engine: Any, settings: Settings, *, provider: Any = None) -> None:
        self._factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        self._settings = settings
        self._store = LocalArtefactStore(
            settings.artefact_root, max_bytes=settings.max_artefact_bytes
        )
        self.provider = provider if provider is not None else make_provider()
        self.sec_client = StubSecClient(self._store)

    async def run(self, job_id: uuid.UUID) -> JobStatus:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            outcome = await run_service.execute(
                session,
                job=job,
                settings=self._settings,
                provider=self.provider,
                store=self._store,
                sec_client=self.sec_client,
            )
            await session.commit()
            return outcome.status

    async def run_expecting_failure(self, job_id: uuid.UUID) -> None:
        """Run, and let the exception unwind the session exactly as the worker's does.

        The ``async with`` closing on an exception is the mechanism under test: it rolls back
        anything uncommitted, which is what used to erase the failure it had just recorded.
        """
        with pytest.raises(Exception):  # noqa: B017, PT011 -- see the docstring
            await self.run(job_id)

    async def approve(self, job_id: uuid.UUID, *, gate: GateKind, step: str) -> None:
        """Approve a gate, so the next call runs the following leg."""
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            row = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == step)
            )
            assert job is not None
            assert user is not None
            assert row is not None, f"the {step} step has not run"
            await approval_service.record_decision(
                session,
                job=job,
                gate=gate,
                decision=Decision.APPROVED,
                actor=user,
                payload_hash=str((row.output_ref or {})["payload_hash"]),
            )
            await session.commit()


class StatusAtCallTime:
    """A provider that records what another connection could see *during* the first step.

    A wrapper rather than a fixture flag, because the interesting instant is inside the model
    call. Nothing outside the run can observe that moment, so the observation has to sit on
    the code path itself.
    """

    def __init__(self, inner: FakeProvider, observer: Observer, *, job_id: uuid.UUID) -> None:
        self._inner = inner
        self._observer = observer
        self._job_id = job_id
        self.seen: JobStatus | None = None

    @property
    def name(self) -> str:
        return self._inner.name

    async def complete_structured(self, *args: Any, **kwargs: Any) -> Any:
        self.seen = await self._observer.job_status(self._job_id)
        return await self._inner.complete_structured(*args, **kwargs)

    async def count_tokens(self, *args: Any, **kwargs: Any) -> int:
        return await self._inner.count_tokens(*args, **kwargs)


@pytest.fixture
async def seeded(clean_slate: None, db_engine: Any) -> dict[str, Any]:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        user = await seed_user(session)
        request = await seed_request(session, user=user)
        job = await seed_job(session, request=request)
        await session.commit()
        return {"user": user, "request": request, "job": job}


class TestTheConsoleSeesTheRunStart:
    async def test_the_job_leaves_queued_before_the_first_step_finishes(
        self, seeded: dict[str, Any], db_engine: Any, workflow_settings: Settings
    ) -> None:
        """``execute`` commits ``RUNNING`` before the first step rather than flushing it.

        Observed from inside the model call, which is both the earliest point a test can reach
        and the point that matters: it is where an operator sits for a minute with no way to
        tell a working run from a dead worker. A flush alone leaves the page reading ``QUEUED``
        for the whole call.
        """
        job_id = seeded["job"].id
        observer = Observer(db_engine)
        watcher = StatusAtCallTime(make_provider(), observer, job_id=job_id)

        await Worker(db_engine, workflow_settings, provider=watcher).run(job_id)

        assert watcher.seen is JobStatus.RUNNING


class TestAFailureSurvivesTheExceptionThatCausedIt:
    async def test_a_failed_step_is_recorded_rather_than_rolled_back(
        self, seeded: dict[str, Any], db_engine: Any, workflow_settings: Settings
    ) -> None:
        """The defect a live run found.

        ``_fail`` recorded ``FAILED`` and re-raised; the exception left the worker's session
        without a commit, so the row reverted and the database went on saying ``QUEUED`` for a
        run that had died. An operator refreshing the console saw something that would never
        move and never explain itself.
        """
        job_id = seeded["job"].id
        observer = Observer(db_engine)

        await Worker(
            db_engine, workflow_settings, provider=_provider_that_explodes()
        ).run_expecting_failure(job_id)

        assert await observer.job_status(job_id) is JobStatus.FAILED
        assert (await observer.step_statuses(job_id))[_PLAN] is JobStatus.FAILED

    async def test_the_recorded_failure_says_what_went_wrong(
        self, seeded: dict[str, Any], db_engine: Any, workflow_settings: Settings
    ) -> None:
        """A ``FAILED`` row with no detail sends the operator to the worker's terminal, which
        is a scrollback buffer that may already be gone."""
        job_id = seeded["job"].id
        observer = Observer(db_engine)

        await Worker(
            db_engine, workflow_settings, provider=_provider_that_explodes()
        ).run_expecting_failure(job_id)

        error = await observer.step_error(job_id, _PLAN)
        assert error is not None
        assert error["code"] == "external_service_error"
        assert "on fire" in error["message"]


class TestACrashDoesNotUnspendCompletedWork:
    async def test_steps_finished_before_a_failure_are_kept(
        self,
        seeded: dict[str, Any],
        db_engine: Any,
        workflow_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The claim the worker's docstring made and did not keep.

        One transaction per run was justified by saying a crashed run "resumes from the last
        step that succeeded". Between gates it did not: a rollback took every completed step
        with it, so the next attempt started over and paid for the same work twice.

        Proved on the second leg, because that is the only leg with several steps in it. The
        run is taken through gate 1, then ``calculate`` is made to raise — leaving ``acquire``
        and ``extract`` finished and a failure after them.
        """
        job_id = seeded["job"].id
        observer = Observer(db_engine)
        worker = Worker(db_engine, workflow_settings)

        await worker.run(job_id)
        await worker.approve(job_id, gate=GateKind.PLAN, step=_PLAN)

        monkeypatch.setattr(vertical_slice_v1, "_calculate", _step_that_explodes)
        await worker.run_expecting_failure(job_id)

        survived = await observer.succeeded_steps(job_id)
        assert {"acquire", "extract"} <= survived, (
            "steps that finished before the failure were rolled back with it, so a resume "
            f"would pay for them again; committed steps were {sorted(survived)}"
        )
        assert (await observer.step_statuses(job_id))["calculate"] is JobStatus.FAILED

    async def test_a_resume_does_not_re_execute_what_survived(
        self,
        seeded: dict[str, Any],
        db_engine: Any,
        workflow_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The consequence that costs money, stated as a consequence.

        ``_completed`` skips a step whose row says ``SUCCEEDED``, so keeping those rows is
        what makes a resume cheap. With the rows gone the engine re-ran them — including the
        SEC fetches — which is the same work billed twice.
        """
        job_id = seeded["job"].id
        worker = Worker(db_engine, workflow_settings)

        await worker.run(job_id)
        await worker.approve(job_id, gate=GateKind.PLAN, step=_PLAN)

        monkeypatch.setattr(vertical_slice_v1, "_calculate", _step_that_explodes)
        await worker.run_expecting_failure(job_id)

        fetches_before = len(worker.sec_client.facts_calls)
        monkeypatch.undo()
        await worker.run(job_id)

        assert len(worker.sec_client.facts_calls) == fetches_before, (
            "the resumed run fetched from EDGAR again, so `acquire` had not survived"
        )


async def _step_that_explodes(context: StepContext) -> StepResult:
    """A step that fails, for testing what happens to the steps before it."""
    message = "the calculator caught fire"
    raise ExternalServiceError(message, provider="test", retryable=False)


def _provider_that_explodes() -> FakeProvider:
    """A provider whose call raises, so the run fails inside a step rather than before one."""
    boom = ExternalServiceError("the model is on fire", provider="fake", retryable=False)
    return make_provider(fail_with=boom)
