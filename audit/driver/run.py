"""Drive one commission from request to rendered report, writing everything down.

    uv run python -m audit.driver.run msft1 --cap 10 --executor worker
    uv run python -m audit.driver.run msft2 --kill-at draft --kill-after 150

Stops for the operator on anything the policy will not decide: a failed blocking metric, a
post-approval pause it cannot reseal, a monthly-cap stop, an outstanding assumption nobody
stated, a third failure. Every stop is a line in ``audit/out/<subject>/driver.jsonl`` with
its reason.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import select

from aer.core.enums import AnalysisMode, JobStatus
from aer.db.models import Job, Report
from aer.services import approvals as approval_service
from aer.services import requests as request_service
from aer.services import runs as run_service
from aer.services.acceptance import acceptance_readout
from aer.services.mandate import mandate_of
from aer.services.resume import resume_run
from aer.services.run_export import export_run
from aer.services.run_replay import replay_run
from aer.services.spend import spend_by_role, spend_summary
from audit.driver.executor import InlineExecutor, WorkerExecutor
from audit.driver.gates import clear_pending_gate
from audit.driver.recorder import Recorder
from audit.driver.session import AuditRuntime
from audit.driver.web import WebServer
from audit.ledger import OUT_DIR, Ledger
from audit.subjects import Subject, subject_for

__all__ = ["drive", "main"]

_MAX_ITERATIONS: Final = 40


async def drive(
    subject: Subject,
    *,
    cap_gbp: Decimal = Decimal("10.00"),
    raise_to_gbp: Decimal = Decimal("12.00"),
    executor_kind: str = "worker",
    kill_at_step: str | None = None,
    kill_after_seconds: float = 150.0,
    max_resumes: int = 2,
    analysis_mode: AnalysisMode = AnalysisMode.STANDARD,
    out_root: Path = OUT_DIR,
    runtime: AuditRuntime | None = None,
    services: dict[str, Any] | None = None,
    label: str | None = None,
    screenshots: bool = False,
    existing_job_id: uuid.UUID | None = None,
    already_queued: bool = False,
) -> dict[str, Any]:
    label = label or subject.key
    recorder = Recorder(out_root / label)
    opened_here = runtime is None
    runtime = runtime or await AuditRuntime.open()
    ledger = Ledger(out_root / "ledger.json")
    executor: WorkerExecutor | InlineExecutor
    if executor_kind == "worker":
        executor = WorkerExecutor(
            runtime, recorder=recorder, log_path=out_root / label / "worker.log"
        )
    else:
        executor = InlineExecutor(runtime, recorder=recorder, services=services)

    summary: dict[str, Any] = {
        "subject": subject.key,
        "label": label,
        "started_at": datetime.now(UTC).isoformat(),
    }
    web = (
        WebServer(recorder=recorder, log_path=out_root / label / "web.log") if screenshots else None
    )
    if web is not None:
        web.start()
    try:
        async with runtime.session() as session:
            reading = await ledger.assert_room(
                session, stage=f"run {subject.key}", needs_gbp=raise_to_gbp
            )
            recorder.event("ledger.before", **reading.as_dict())
            actor = await runtime.operator(session)
            if existing_job_id is None:
                request, job = await commission(
                    session, runtime, subject, actor, cap_gbp, analysis_mode
                )
                job_id = job.id
                summary.update(
                    request_id=str(request.id),
                    job_id=str(job_id),
                    as_of_date=str(request.work_order.as_of_date),
                    cap_gbp=str(cap_gbp),
                )
                recorder.event(
                    "commissioned", **{k: v for k, v in summary.items() if k != "started_at"}
                )
            else:
                # A run picked up where an earlier driver left it: nothing is commissioned
                # and nothing already paid for is repeated.
                job = await _job(session, existing_job_id)
                request = await mandate_of(session, job)
                if request is None:
                    message = f"Run {existing_job_id} has no research request."
                    raise RuntimeError(message)
                job_id = job.id
                summary.update(
                    request_id=str(request.id),
                    job_id=str(job_id),
                    as_of_date=str(request.work_order.as_of_date),
                    cap_gbp=str(request.work_order.max_cost_gbp),
                    resumed_by_driver=True,
                )
                recorder.event(
                    "resumed_existing",
                    status=job.status.value,
                    **{k: v for k, v in summary.items() if k != "started_at"},
                )

        await executor.start()
        raised = False
        resumes = 0
        killed = False
        stop_reason: str | None = None
        enqueue = True
        if existing_job_id is not None:
            # Wherever the run is — at a gate, on a cap, failed — the first pass reads that
            # state rather than enqueueing over it; only a queued job needs the worker told.
            async with runtime.session() as session:
                job = await _job(session, job_id)
                if already_queued:
                    # The operator continued it through the product (`aer resume`, or the
                    # console's Continue on a stranded run) before this driver started, so
                    # the queue already holds it; a second task would race the first.
                    recorder.event("recovery.already_queued", status=job.status.value)
                    enqueue = False
                elif job.status is JobStatus.RUNNING:
                    # RUNNING with no worker alive but the one this driver just started:
                    # the previous worker died under it. Re-enqueueing is the only way on,
                    # and that it is the only way is itself a finding (use case 11).
                    recorder.event("recovery.reenqueue_running_job", status=job.status.value)
                    enqueue = True
                elif job.status is not JobStatus.QUEUED:
                    enqueue = False
        for _ in range(_MAX_ITERATIONS):
            result = await executor.advance(
                job_id,
                enqueue=enqueue,
                kill_at_step=None if killed else kill_at_step,
                kill_after_seconds=kill_after_seconds,
            )
            enqueue = True
            status = result.status
            recorder.event(
                "advanced",
                status=status.value,
                elapsed_seconds=round(result.elapsed_seconds, 1),
                killed_at=result.killed_at_step,
                worker_dead=result.worker_dead,
            )

            if result.killed_at_step:
                killed = True
                # The drill: what does the product do with a run its worker died under?
                # Restart the worker and give arq its chance to pick the job back up before
                # doing anything an operator would have to know a command for.
                await executor.start()
                waited = await _wait_for_movement(runtime, job_id, seconds=180)
                recorder.event("recovery.after_restart", moved=waited)
                if not waited:
                    async with runtime.session() as session:
                        job = await _job(session, job_id)
                        recorder.event("recovery.status_after_restart", status=job.status.value)
                        try:
                            await resume_run(
                                session,
                                job=job,
                                actor=await runtime.operator(session),
                                reason="audit drill: worker killed mid-draft",
                            )
                            await session.commit()
                            recorder.event("recovery.resumed_via_service")
                        except Exception as refused:
                            recorder.event("recovery.resume_refused", error=str(refused)[:300])
                            enqueue = True
                    resumes += 1
                else:
                    enqueue = False
                continue

            if status is JobStatus.AWAITING_APPROVAL:
                if web is not None:
                    async with runtime.session() as session:
                        pending = await approval_service.pending_gate(
                            session, await _job(session, job_id)
                        )
                    _capture(web, recorder, job_id, "console")
                    if pending is not None and pending.value in _GATE_PAGES:
                        _capture(web, recorder, job_id, _GATE_PAGES[pending.value])
                async with runtime.session() as session:
                    job = await _job(session, job_id)
                    outcome = await clear_pending_gate(
                        session,
                        job=job,
                        subject=subject,
                        actor=await runtime.operator(session),
                        cap_gbp=raise_to_gbp,
                        recorder=recorder,
                    )
                if outcome.not_waiting:
                    # A stale read: the worker already took the job. Poll on without
                    # enqueueing over it.
                    enqueue = False
                    continue
                if not outcome.approved:
                    stop_reason = outcome.stop_reason
                    break
                continue

            if status is JobStatus.BUDGET_EXCEEDED:
                async with runtime.session() as session:
                    state = await run_service.run_state(session, job_id=job_id)
                    scope = state.budget_scope
                    job = state.job
                    if scope == "per_run" and not raised:
                        request = await mandate_of(session, job)
                        if request is None:
                            message = f"Run {job_id} has no research request to raise a cap on."
                            raise RuntimeError(message)
                        actor = await runtime.operator(session)
                        await request_service.raise_cap(
                            session,
                            request=request,
                            actor=actor,
                            to=raise_to_gbp,
                            ceiling_gbp=runtime.resolved.per_run_budget_gbp,
                        )
                        await resume_run(
                            session,
                            job=job,
                            actor=actor,
                            reason=f"audit: cap raised from £{cap_gbp} to £{raise_to_gbp}",
                        )
                        await session.commit()
                        raised = True
                        recorder.event(
                            "budget.raised",
                            scope=scope,
                            to=str(raise_to_gbp),
                            spend=str(state.spend_gbp),
                        )
                        continue
                    recorder.event("budget.stop", scope=scope, spend=str(state.spend_gbp))
                    stop_reason = f"budget stop ({scope}) at £{state.spend_gbp}"
                    break

            if status is JobStatus.FAILED:
                async with runtime.session() as session:
                    job = await _job(session, job_id)
                    recorder.event("run.failed", error=job.error)
                    if resumes < max_resumes:
                        await resume_run(
                            session,
                            job=job,
                            actor=await runtime.operator(session),
                            reason=f"audit: resume {resumes + 1} after failure",
                        )
                        await session.commit()
                        resumes += 1
                        continue
                stop_reason = "failed and out of resumes"
                break

            if status is JobStatus.RUNNING and result.worker_dead:
                recorder.event("worker.dead_while_running")
                await executor.stop(hard=True)
                await executor.start()
                enqueue = False
                if not await _wait_for_movement(runtime, job_id, seconds=180):
                    stop_reason = "the worker died and the run did not move after a restart"
                    break
                continue

            if status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
                break
            if status is JobStatus.PAUSED:
                stop_reason = "the run paused in step mode, which the audit never arms"
                break
        else:
            stop_reason = "the driver's iteration bound was reached"

        summary.update(
            stop_reason=stop_reason,
            cap_raised=raised,
            resumes=resumes,
            killed_for_the_drill=killed,
            finished_at=datetime.now(UTC).isoformat(),
        )
        async with runtime.session() as session:
            summary.update(await _readouts(session, runtime, job_id, recorder))
        if web is not None:
            for page_key in ("console", "review", "sources", "claims", "valuation"):
                _capture(web, recorder, job_id, page_key)
            if summary.get("report_id"):
                _capture(web, recorder, job_id, "report", report_id=str(summary["report_id"]))
            reading = await ledger.reading(session)
            recorder.event("ledger.after", **reading.as_dict())
            summary["ledger"] = reading.as_dict()
        recorder.write_json("summary.json", summary)
        recorder.event(
            "run.finished",
            **{k: summary[k] for k in ("stop_reason", "status", "spend_gbp") if k in summary},
        )
        return summary
    finally:
        await executor.stop()
        if web is not None:
            web.stop()
        if opened_here:
            await runtime.close()


_GATE_PAGES: Final[dict[str, str]] = {
    "PLAN": "plan",
    "SECTOR_SPECIALIST": "sector",
    "PEER_SET": "peers",
    "THEME_SET": "themes",
    "UNMAPPED_CONCEPTS": "financials",
    "ASSUMPTIONS": "assumptions",
    "FINAL": "review",
}


def _capture(
    web: WebServer, recorder: Recorder, job_id: uuid.UUID, page_key: str, *, report_id: str = ""
) -> None:
    """One page, in a separate process, because Playwright's sync API owns a loop."""
    out = recorder.root / "screens"
    argv = [
        "uv",
        "run",
        "python",
        "-m",
        "audit.driver.screenshots",
        str(job_id),
        page_key,
        "--base-url",
        web.base_url,
        "--out",
        str(out),
    ]
    if report_id:
        argv += ["--report-id", report_id]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode == 0:
        try:
            record = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            record = {"stdout": completed.stdout[-300:]}
        recorder.event(
            "screen.captured",
            page=page_key,
            **{k: v for k, v in record.items() if k not in {"raw_tokens", "page"}},
            raw_token_count=len(record.get("raw_tokens", [])),
        )
    else:
        recorder.event("screen.failed", page=page_key, stderr=completed.stderr[-400:])


async def commission(
    session: Any,
    runtime: AuditRuntime,
    subject: Subject,
    actor: Any,
    cap_gbp: Decimal,
    analysis_mode: AnalysisMode,
) -> tuple[Any, Job]:
    from audit.driver.commission import commission as _commission  # noqa: PLC0415

    return await _commission(
        session,
        subject=subject,
        actor=actor,
        settings=runtime.resolved,
        cap_gbp=cap_gbp,
        analysis_mode=analysis_mode,
    )


async def _job(session: Any, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        message = f"No run {job_id}."
        raise RuntimeError(message)
    return cast("Job", job)


async def _wait_for_movement(runtime: AuditRuntime, job_id: uuid.UUID, *, seconds: float) -> bool:
    """Whether any step row changes within ``seconds`` — the sign a restarted worker took the job."""
    async with runtime.session() as session:
        before = await _fingerprint(session, job_id)
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(5)
        async with runtime.session() as session:
            if await _fingerprint(session, job_id) != before:
                return True
    return False


async def _fingerprint(session: Any, job_id: uuid.UUID) -> tuple[Any, ...]:
    state = await run_service.run_state(session, job_id=job_id)
    return (
        state.job.status.value,
        tuple((s.step_key, s.attempt, s.status.value) for s in state.steps),
    )


async def _readouts(
    session: Any, runtime: AuditRuntime, job_id: uuid.UUID, recorder: Recorder
) -> dict[str, Any]:
    state = await run_service.run_state(session, job_id=job_id)
    steps = [
        {
            "step": s.step_key,
            "attempt": s.attempt,
            "status": s.status.value,
            "cost_gbp": str(s.cost_gbp or 0),
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "error": s.error,
        }
        for s in state.steps
    ]
    recorder.write_json("steps.json", steps)
    out: dict[str, Any] = {"status": state.job.status.value, "spend_gbp": str(state.spend_gbp)}

    try:
        export = await export_run(session, job_id=job_id)
        recorder.write_json("run-export.json", export.document)
    except Exception as failure:
        recorder.event("readout.failed", readout="export", error=str(failure)[:300])

    try:
        readout = await acceptance_readout(session, job_id=job_id)
        checks = [
            {"name": c.name, "required": c.required, "measured": c.measured, "passed": c.passed}
            for c in readout.checks
        ]
        recorder.write_json(
            "acceptance.json",
            {"passed": readout.passed, "subject": readout.subject, "checks": checks},
        )
        out["acceptance_passed"] = readout.passed
    except Exception as failure:
        recorder.event("readout.failed", readout="acceptance", error=str(failure)[:300])

    try:
        if runtime.bundle is not None:
            replay = await replay_run(
                session, runtime.bundle.store, job_id=job_id, settings=runtime.resolved
            )
            recorder.write_json("replay.json", asdict(replay))
            out["replay"] = {
                k: v for k, v in asdict(replay).items() if isinstance(v, (int, bool, str))
            }
    except Exception as failure:
        recorder.event("readout.failed", readout="replay", error=str(failure)[:300])

    try:
        summary = await spend_summary(session, job_id=job_id)
        roles = await spend_by_role(session, job_id=job_id)
        recorder.write_json(
            "spend.json",
            {
                "total_gbp": str(summary.total_gbp),
                "calls": summary.calls,
                "output_tokens": summary.output_tokens,
                "cache": asdict(summary.cache),
                "by_kind": [[k, str(v)] for k, v in summary.by_kind],
                "by_role": [
                    {
                        "role": r.role,
                        "model": r.model,
                        "calls": r.calls,
                        "output_tokens": r.output_tokens,
                        "cache": asdict(r.cache),
                    }
                    for r in roles
                ],
            },
        )
    except Exception as failure:
        recorder.event("readout.failed", readout="spend", error=str(failure)[:300])

    report = await session.scalar(
        select(Report).where(Report.job_id == job_id).order_by(Report.created_at.desc()).limit(1)
    )
    if report is not None:
        markdown = str((report.content or {}).get("markdown", ""))
        recorder.write_text("report.md", markdown)
        out.update(
            report_id=str(report.id),
            report_hash=report.content_hash,
            report_immutable=report.immutable,
            report_words=len(markdown.split()),
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("subject")
    parser.add_argument("--cap", type=Decimal, default=Decimal("10.00"))
    parser.add_argument("--raise-to", type=Decimal, default=Decimal("12.00"))
    parser.add_argument("--executor", choices=("worker", "inline"), default="worker")
    parser.add_argument("--kill-at", default=None, help="step key to kill the worker under, once")
    parser.add_argument("--kill-after", type=float, default=150.0)
    parser.add_argument(
        "--mode", choices=[m.value for m in AnalysisMode], default=AnalysisMode.STANDARD.value
    )
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--resume-job", type=uuid.UUID, default=None)
    parser.add_argument(
        "--already-queued",
        action="store_true",
        help="the resumed job was continued through the product and is already on the queue",
    )
    args = parser.parse_args(argv)
    summary = asyncio.run(
        drive(
            subject_for(args.subject),
            cap_gbp=args.cap,
            raise_to_gbp=args.raise_to,
            executor_kind=args.executor,
            kill_at_step=args.kill_at,
            kill_after_seconds=args.kill_after,
            analysis_mode=AnalysisMode(args.mode),
            screenshots=args.screenshots,
            existing_job_id=args.resume_job,
            already_queued=args.already_queued,
        )
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary.get("status") == JobStatus.SUCCEEDED.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
