"""The worker the browser suite does not have.

There is no arq worker in these tests and no queue, so a run has to be advanced from the
test process — directly against the same database the live server is reading, which is
exactly what the worker does minus the queue. What the browser exercises is the pages; the
workflow has its own tests.

**Everything here is a person's job or a worker's, never both.** A browser test clicks the
things an operator clicks and calls into this module for the things a worker would have
done in the background. Keeping the two apart is what stops a journey test quietly proving
that the *test* can drive a workflow rather than that the *product* can.

Shared rather than duplicated: `test_run_console.py` and `test_research_journey.py` both
need a run moved along, and two advancers would drift into two ideas of what "advance"
means — the one that stops clearing interim gates being the one nobody notices.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.config import load_settings
from aer.core.enums import Decision, GateKind, JobStatus
from aer.db.models import Job, JobStep, User
from aer.services import approvals as approval_service
from aer.services import runs as run_service
from aer.storage.local import LocalArtefactStore
from tests.db_fixtures import run_async
from tests.workflow_fixtures import (
    StubSecClient,
    gate_for,
    make_provider,
    paused_at,
    with_price_feed,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["Worker"]

# Bounded: the conditional gates are few, and a run that keeps pausing is a failure to
# surface rather than to orbit.
_MAX_INTERIM_GATES: Final = 4

# The always-gates, in the shape `CONDITIONAL_GATES` uses: the gate and the step whose
# output carries the hash its approval must echo. `gate_plan` is not in that mapping
# because the journey tests approve it through the browser; `advance_until` needs to clear
# it programmatically on the way to a later gate.
_ALWAYS_GATES: Final[dict[str, tuple[GateKind, str]]] = {
    "gate_plan": (GateKind.PLAN, "critique_plan"),
}


class Worker:
    """Advances one run, with a fake provider and no network.

    Constructed per run rather than per suite because the artefact store and the stub
    filing client hold state a second run would inherit.
    """

    def __init__(self, database_url: str, *, subscribed: bool = False) -> None:
        self._database_url = database_url
        # Subscribed: the peer step asks the model for a slate only when a price feed is
        # configured (ADR 0059, second amendment); a scenario that expects the model's
        # peer, and the gate it opens, runs as a subscribed machine would.
        self._settings = with_price_feed(load_settings()) if subscribed else load_settings()
        self._store = LocalArtefactStore(
            self._settings.artefact_root, max_bytes=self._settings.max_artefact_bytes
        )
        self._provider = make_provider()
        self._sec_client = StubSecClient(self._store)

    def advance(self, job_id: uuid.UUID) -> JobStatus:
        """Run from the first incomplete step until it stops."""
        return run_async(self._advance(job_id))  # type: ignore[no-any-return]

    def advance_until(self, job_id: uuid.UUID, gate: GateKind) -> JobStatus:
        """Run until the named gate is the pending one, approving every gate before it.

        What the gate tests need: a run genuinely stopped at the assumptions or the peer
        gate, with everything earlier cleared the way an operator would have cleared it.
        """
        return run_async(self._advance_until(job_id, gate))

    def advance_to_the_final_gate(self, job_id: uuid.UUID) -> JobStatus:
        """Run until the final gate pauses, clearing the interim gates on the way.

        The workflow has grown gates between the plan and the final review — the
        assumptions (ADR 0046) and the peer set (ADR 0059) — and a journey through the
        product is about neither. Which gate a pause belongs to is read from the run's own
        record and looked up in `CONDITIONAL_GATES`, so a gate that starts firing for this
        scene is cleared rather than asserted against. Whether a pause is the *final* gate
        is read the same way: `red_team` — the final gate's sealing step — has always run
        by the time the final gate pauses, and can never have run before it.
        """
        return run_async(self._advance_to_the_final_gate(job_id))  # type: ignore[no-any-return]

    async def _engine(self) -> Any:
        """A throwaway engine for one operation, pooling nothing.

        Each call runs on its own event loop (see `run_async`), and an asyncpg connection
        belongs to the loop that opened it. `NullPool` closes every connection the moment
        its session ends, so nothing survives the loop to be garbage-collected later.
        """
        return create_async_engine(self._database_url, poolclass=NullPool)

    async def _session(self) -> AsyncIterator[Any]:
        engine = await self._engine()
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()

    async def _advance(self, job_id: uuid.UUID) -> JobStatus:
        async for session in self._session():
            job = await session.get(Job, job_id)
            assert job is not None
            outcome = await run_service.execute(
                session,
                job=job,
                settings=self._settings,
                provider=self._provider,
                store=self._store,
                sec_client=self._sec_client,
            )
            await session.commit()
            return outcome.status  # type: ignore[no-any-return]
        raise AssertionError("unreachable")

    async def _advance_until(self, job_id: uuid.UUID, target: GateKind) -> JobStatus:
        async for session in self._session():
            for _ in range(_MAX_INTERIM_GATES + 2):
                job = await session.get(Job, job_id)
                assert job is not None
                outcome = await run_service.execute(
                    session,
                    job=job,
                    settings=self._settings,
                    provider=self._provider,
                    store=self._store,
                    sec_client=self._sec_client,
                )
                await session.commit()
                if outcome.status is not JobStatus.AWAITING_APPROVAL:
                    return outcome.status  # type: ignore[no-any-return]
                pending = await approval_service.pending_gate(session, job)
                if pending is target:
                    return outcome.status  # type: ignore[no-any-return]
                await self._approve_the_interim_gate(session, job)
            message = f"the run never stopped at {target}"
            raise AssertionError(message)
        raise AssertionError("unreachable")

    async def _advance_to_the_final_gate(self, job_id: uuid.UUID) -> JobStatus:
        async for session in self._session():
            for _ in range(_MAX_INTERIM_GATES):
                job = await session.get(Job, job_id)
                assert job is not None
                outcome = await run_service.execute(
                    session,
                    job=job,
                    settings=self._settings,
                    provider=self._provider,
                    store=self._store,
                    sec_client=self._sec_client,
                )
                await session.commit()
                if outcome.status is not JobStatus.AWAITING_APPROVAL:
                    return outcome.status  # type: ignore[no-any-return]
                sealed = await session.scalar(
                    select(JobStep).where(JobStep.job_id == job_id, JobStep.step_key == "revise")
                )
                if sealed is not None:
                    return outcome.status  # type: ignore[no-any-return]
                await self._approve_the_interim_gate(session, job)
            raise AssertionError("the run kept pausing at interim gates")
        raise AssertionError("unreachable")

    async def _approve_the_interim_gate(self, session: Any, job: Job) -> None:
        """Approve whichever conditional gate this run stopped at, as an operator would."""
        paused = await paused_at(session, job.id)
        assert paused is not None, "the run pauses awaiting approval with no recorded gate step"
        clearing = gate_for(paused) or _ALWAYS_GATES.get(paused)
        assert clearing is not None, (
            f"the run paused at {paused!r}; a gate this helper does not know how to clear "
            "now fires for this scene. Add it to CONDITIONAL_GATES if an operator would "
            "clear it on the way to the final review."
        )
        gate, step = clearing
        produced = await session.scalar(
            select(JobStep).where(JobStep.job_id == job.id, JobStep.step_key == step)
        )
        assert produced is not None, f"the {step} step has not run"
        user = await session.scalar(select(User))
        assert user is not None
        await approval_service.record_decision(
            session,
            job=job,
            gate=gate,
            decision=Decision.APPROVED,
            actor=user,
            payload_hash=str((produced.output_ref or {})["payload_hash"]),
        )
        await session.commit()
