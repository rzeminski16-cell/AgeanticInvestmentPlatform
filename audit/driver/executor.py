"""Two ways to move a run: the product's own worker, or the same call made inline.

The worker executor is the audit's default because it is where the operator's runs execute:
a queue, a process, a health record, and everything that can go wrong between them. The
inline executor is the fallback and the offline smoke path; both end in
:func:`aer.services.runs.execute`, which is the whole of what the worker adds a process to.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from aer.core.enums import JobStatus
from aer.db.models import Job
from aer.queue import enqueue_run, worker_health
from aer.services import runs as run_service
from audit.driver.recorder import Recorder
from audit.driver.session import AuditRuntime

__all__ = ["AdvanceResult", "InlineExecutor", "WorkerExecutor"]

_POLL_SECONDS: Final = 5.0
_RUN_TIMEOUT_SECONDS: Final = 3 * 60 * 60
_WORKER_START_SECONDS: Final = 120.0
# The worker records its health every thirty seconds (`aer.queue.HEALTH_CHECK_INTERVAL_SECONDS`);
# three missed beats is a dead worker rather than a slow one.
_WORKER_SILENCE_SECONDS: Final = 100.0


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    """Where one advance left the run."""

    status: JobStatus
    elapsed_seconds: float
    killed_at_step: str | None = None
    worker_dead: bool = False
    step_timeline: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class WorkerExecutor:
    """Runs the arq worker as a subprocess and drives a job through it."""

    def __init__(self, runtime: AuditRuntime, *, recorder: Recorder, log_path: Path) -> None:
        self._runtime = runtime
        self._recorder = recorder
        self._log_path = log_path
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    async def start(self) -> None:
        if self.alive:
            return
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self._log_path.open("ab")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        self._process = subprocess.Popen(
            ["uv", "run", "arq", "aer.worker.WorkerSettings"],
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=Path.cwd(),
            start_new_session=True,
        )
        process = self._process
        started = time.monotonic()
        while time.monotonic() - started < _WORKER_START_SECONDS:
            if process.poll() is not None:
                message = f"The worker exited at startup; see {self._log_path}."
                raise RuntimeError(message)
            if await worker_health(self._runtime.redis) is not None:
                self._recorder.event(
                    "worker.started",
                    pid=process.pid,
                    after_seconds=round(time.monotonic() - started, 1),
                )
                return
            await asyncio.sleep(1.0)
        message = f"The worker never reported health within {_WORKER_START_SECONDS:.0f}s; see {self._log_path}."
        raise RuntimeError(message)

    async def stop(self, *, hard: bool = False) -> None:
        if self._process is None:
            return
        if self.alive:
            sig = signal.SIGKILL if hard else signal.SIGTERM
            os.killpg(os.getpgid(self._process.pid), sig)
            try:
                self._process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                self._process.wait(timeout=30)
        self._recorder.event(
            "worker.stopped", pid=self._process.pid, hard=hard, returncode=self._process.returncode
        )
        self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    async def enqueue(self, job_id: uuid.UUID) -> str | None:
        task = await enqueue_run(self._runtime.redis, job_id)
        self._recorder.event("run.enqueued", job_id=str(job_id), task=task)
        return task

    async def advance(
        self,
        job_id: uuid.UUID,
        *,
        enqueue: bool = True,
        kill_at_step: str | None = None,
        kill_after_seconds: float = 120.0,
    ) -> AdvanceResult:
        """Enqueue (unless told not to) and poll until the run stops moving.

        ``kill_at_step`` kills the worker outright once that step has been running for
        ``kill_after_seconds`` — the failure-and-recovery drill — and returns with the job
        still ``RUNNING`` in the database, which is exactly the state an operator meets.
        """
        if enqueue:
            await self.enqueue(job_id)
        started = time.monotonic()
        last_seen: dict[str, str] = {}
        timeline: list[dict[str, Any]] = []
        running_since: dict[str, float] = {}
        silent_since: float | None = None
        while time.monotonic() - started < _RUN_TIMEOUT_SECONDS:
            async with self._runtime.session() as session:
                state = await run_service.run_state(session, job_id=job_id)
                status = state.job.status
                for step in state.steps:
                    key = f"{step.step_key}#{step.attempt}"
                    value = step.status.value
                    if last_seen.get(key) != value:
                        last_seen[key] = value
                        timeline.append(
                            {
                                "at": datetime.now(UTC).isoformat(),
                                "step": step.step_key,
                                "attempt": step.attempt,
                                "status": value,
                            }
                        )
                        self._recorder.event(
                            "step",
                            step=step.step_key,
                            attempt=step.attempt,
                            status=value,
                            cost_gbp=str(step.cost_gbp or 0),
                        )
                        if step.status is JobStatus.RUNNING:
                            running_since.setdefault(key, time.monotonic())
                current = state.current_step
            if kill_at_step and current is not None and current.step_key == kill_at_step:
                key = f"{current.step_key}#{current.attempt}"
                if (
                    time.monotonic() - running_since.get(key, time.monotonic())
                    >= kill_after_seconds
                ):
                    self._recorder.event(
                        "worker.kill_requested", step=kill_at_step, after_seconds=kill_after_seconds
                    )
                    await self.stop(hard=True)
                    return AdvanceResult(
                        status=JobStatus.RUNNING,
                        elapsed_seconds=time.monotonic() - started,
                        killed_at_step=kill_at_step,
                        step_timeline=tuple(timeline),
                    )
            if status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                return AdvanceResult(
                    status=status,
                    elapsed_seconds=time.monotonic() - started,
                    step_timeline=tuple(timeline),
                )
            health = await worker_health(self._runtime.redis)
            if health is None:
                silent_since = silent_since or time.monotonic()
                if time.monotonic() - silent_since > _WORKER_SILENCE_SECONDS:
                    self._recorder.event("worker.silent", job_id=str(job_id), status=status.value)
                    return AdvanceResult(
                        status=status,
                        elapsed_seconds=time.monotonic() - started,
                        worker_dead=True,
                        step_timeline=tuple(timeline),
                    )
            else:
                silent_since = None
            await asyncio.sleep(_POLL_SECONDS)
        message = f"Run {job_id} did not stop within {_RUN_TIMEOUT_SECONDS} seconds."
        raise TimeoutError(message)


class InlineExecutor:
    """The same `execute` call, in this process. The fallback, and the offline smoke path."""

    def __init__(
        self, runtime: AuditRuntime, *, recorder: Recorder, services: dict[str, Any] | None = None
    ) -> None:
        self._runtime = runtime
        self._recorder = recorder
        self._services = services

    async def start(self) -> None:
        return None

    async def stop(self, *, hard: bool = False) -> None:
        del hard

    async def advance(
        self,
        job_id: uuid.UUID,
        *,
        enqueue: bool = True,
        kill_at_step: str | None = None,
        kill_after_seconds: float = 0.0,
    ) -> AdvanceResult:
        del enqueue, kill_at_step, kill_after_seconds
        started = time.monotonic()
        services = self._services
        if services is None:
            if self._runtime.bundle is None:
                message = "The inline executor needs a service bundle or explicit services."
                raise RuntimeError(message)
            services = self._runtime.bundle.for_execution()
        error: str | None = None
        async with self._runtime.session() as session:
            job = await session.get(Job, job_id)
            if job is None:
                message = f"No run {job_id}."
                raise RuntimeError(message)
            try:
                outcome = await run_service.execute(
                    session,
                    job=job,
                    settings=self._runtime.resolved,
                    session_factory=self._runtime.factory,
                    **services,
                )
                status = outcome.status
            except Exception as failure:
                # The engine records the failed step and the job before re-raising, the
                # same way the worker sees it; the record is the answer, not the exception.
                error = f"{type(failure).__name__}: {failure}"
                await session.rollback()
                fresh = await session.get(Job, job_id)
                status = fresh.status if fresh is not None else JobStatus.FAILED
            else:
                await session.commit()
        self._recorder.event(
            "inline.advanced", job_id=str(job_id), status=status.value, error=error
        )
        return AdvanceResult(status=status, elapsed_seconds=time.monotonic() - started)
