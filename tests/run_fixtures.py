"""Driving a real run from a test: the FakeProvider workflow, gate by gate.

A plain builder module rather than fixtures, for the reason ``scene_fixtures`` gives:
pytest reports a fixture imported into two modules as a redefinition at every call site.

**Runs are driven directly, not through the queue.** Executing a run inside a request
would be a different system from the one that ships, and an arq worker in the test suite
would be a second thing to keep alive. So the driver calls the same
:func:`aer.services.runs.execute` the worker calls, and commits as the worker would.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus
from aer.db.models import Job, JobStep, User
from aer.providers.fake import FakeProvider
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.storage.local import LocalArtefactStore
from tests.workflow_fixtures import StubSecClient, make_provider

__all__ = ["Driver", "start_run", "to_final_gate"]


class Driver:
    """Advances a run to its next stopping point, committing as the worker would."""

    def __init__(self, engine: Any, settings: Settings) -> None:
        self._factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        self._settings = settings
        self._store = LocalArtefactStore(
            settings.artefact_root, max_bytes=settings.max_artefact_bytes
        )
        self.provider: FakeProvider = make_provider()
        self.sec_client = StubSecClient(self._store)

    async def advance(self, job_id: uuid.UUID) -> JobStatus:
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

    async def payload_hash_of(self, job_id: uuid.UUID, step: str) -> str:
        async with self._factory() as session:
            row = await session.scalar(
                select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == step)
            )
            assert row is not None, f"the {step} step has not run"
            return str((row.output_ref or {})["payload_hash"])

    async def approve(self, job_id: uuid.UUID, *, gate: GateKind, step: str) -> None:
        async with self._factory() as session:
            job = await session.get(Job, job_id)
            user = await session.scalar(select(User))
            assert job is not None
            assert user is not None
            await approval_service.record_decision(
                session,
                job=job,
                gate=gate,
                decision=Decision.APPROVED,
                actor=user,
                payload_hash=await self.payload_hash_of(job_id, step),
            )
            await session.commit()


async def start_run(api: Any, request_id: uuid.UUID) -> dict[str, Any]:
    response = await api.post("/api/runs", json={"request_id": str(request_id)})
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


async def to_final_gate(api: Any, request_id: uuid.UUID, driver: Driver) -> uuid.UUID:
    """Start a run and drive it to the final gate, approving the plan on the way."""
    body = await start_run(api, request_id)
    job_id = uuid.UUID(body["job_id"])

    await driver.advance(job_id)
    await driver.approve(job_id, gate=GateKind.PLAN, step="plan")
    await driver.advance(job_id)
    return job_id
