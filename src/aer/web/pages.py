"""Server-rendered pages for a run: plan review, console, draft review, report.

These are the surfaces a human actually makes decisions on, so two properties matter more
here than anywhere else in the web layer.

**What is displayed and what is approved are the same object.** Each gate page builds its
payload from the workflow's own function, renders that, and puts its hash in a hidden
field. The approval carries the hash back. If the run's content changed between the render
and the click, the hashes differ and the gate refuses — which is the entire reason the hash
exists rather than a timestamp and a user id.

**The console works without JavaScript.** Server-sent events are an enhancement layered on
a page that already shows the run's state on load and refreshes itself with a meta refresh
when ``EventSource`` is unavailable. A progress page that renders blank without a script is
a progress page that lies about a run still spending money.

Nothing here decides anything. Approving calls :mod:`aer.services.approvals`, exactly as
the JSON API does; the pages are a second interface to one implementation, not a second
implementation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_CONTENT,
)

from aer.api.deps import CurrentUser, DbSession, RedisClient, SettingsDep
from aer.api.routes.assumptions import assumptions_payload
from aer.calc.comps import MULTIPLE_DEFINITIONS, CompsTable
from aer.charts import (
    ValuationHistoryInput,
    ValuationRangePoint,
    svg_data_uri,
    valuation_history,
)
from aer.config import HouseStyle, Settings
from aer.core.assumption_scales import UNIT_CHOICES
from aer.core.disagreement import DisagreementKind, ResolutionOutcome
from aer.core.enums import CatalystOutcomeKind, Decision, GateKind, JobStatus
from aer.core.escalation import COST_ALERT_RATIO
from aer.db.models import (
    Calculation,
    Claim,
    Company,
    Disagreement,
    Job,
    JobStep,
    ObsidianExport,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
    SourceDocument,
    WorkOrder,
)
from aer.errors import ConflictError, ValidationError
from aer.obsidian import ObsidianExportError, VaultWriteError, export_report
from aer.queue import enqueue_run
from aer.render import display
from aer.render.document import UnresolvedFootnote, assemble_document
from aer.render.html import render_html
from aer.render.markdown import render_markdown
from aer.render.summary import summary_document
from aer.sections.registry import section_outcomes
from aer.services import approvals as approval_service
from aer.services import calculations as calculation_service
from aer.services import cancellation as cancellation_service
from aer.services import catalyst_resolutions as catalyst_service
from aer.services import configuration, provenance
from aer.services import history as history_service
from aer.services import requests as requests_service
from aer.services import resume as resume_service
from aer.services import runs as run_service
from aer.services.approvals import payload_hash_for
from aer.services.assumptions import assumptions_for_request
from aer.services.challenge_briefs import briefs_from_output
from aer.services.comps import (
    PEER_SET_STEP,
    peer_set_payload,
    peer_set_required,
)
from aer.services.comps_run import grouped_exclusions
from aer.services.disagreements import disagreements_for_job, settle_by_hand
from aer.services.escalation import cost_scene_for_job
from aer.services.evaluations import evaluations_for_job, section_coverage_for_job
from aer.services.exhibits import exportable_charts_for, internal_charts_for, sensitivity_chart
from aer.services.graph_view import graph_picture
from aer.services.knowledge import knowledge_stats
from aer.services.mandate import mandate_of
from aer.services.run_replay import replay_run
from aer.services.sectors import (
    CLASSIFY_STEP,
    classification_payload,
    sector_gate_required,
)
from aer.services.spend import recent_runs, spend_by_role, spend_summary
from aer.services.themes import THEME_STEP, theme_set_payload, theme_set_required
from aer.services.valuation_view import lineage_rows, valuation_view
from aer.storage.local import LocalArtefactStore
from aer.web import figures, vocabulary
from aer.web import verdict as verdicts
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.gates import frame_for, journey
from aer.web.templating import render
from aer.workflow.registry import WorkflowRegistryError, resolve_workflow
from aer.workflow.workflows.vertical_slice_v1 import (
    ASSUMPTIONS_STEP,
    assumptions_gate_required,
    comps_note_for,
    sector_note_for,
    unmapped_gate_required,
)

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

# What the no-JavaScript console falls back to. Long enough that a run's whole lifetime is
# not a wall of requests, short enough that a paused gate is noticed while the operator is
# still looking at the page.
POLL_SECONDS = 5


@router.post("/runs", summary="Start a run from a request")
async def start_run_page(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    redis: RedisClient,
    user: CurrentUser,
) -> Response:
    """Create the run and send the operator to its console.

    A ``POST`` followed by a redirect, so a refresh on the console cannot start a second
    run — and because a run is a side effect with a cost, which is not something a ``GET``
    is allowed to have.
    """
    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}

    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was started.",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        request_id = uuid.UUID(submitted.get("request_id", ""))
    except ValueError:
        return _problem(request, "That is not a research request.", status=HTTP_404_NOT_FOUND)

    found = await session.get(ResearchRequest, request_id)
    if found is None or found.work_order.user_id != user.id:
        return _problem(request, f"No research request {request_id}.", status=HTTP_404_NOT_FOUND)

    job = await run_service.start_run(session, request=found)
    await session.commit()
    await enqueue_run(redis, job.id)

    return RedirectResponse(f"/runs/{job.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/runs/active", summary="The run you are watching")
async def active_run(session: DbSession, user: CurrentUser) -> Response:
    """Wherever the operator's current run is, right now (ADR 0089).

    **A redirect and not a page.** It renders nothing and holds no state; opening it lands the
    operator somewhere real, and that is the whole of its behaviour.

    303 to `/requests` when there is no run at all, because the honest next action for somebody
    with nothing in flight is to look at their requests — not an empty console explaining that
    there is nothing to console.

    Declared **above** `/runs/{job_id}` deliberately: FastAPI matches in declaration order, and
    below it `active` would be parsed as a job id and 404 on a uuid it never was.
    """
    job = await run_service.current_run(session, user_id=user.id)
    if job is None:
        return RedirectResponse("/requests", status_code=HTTP_303_SEE_OTHER)
    return RedirectResponse(f"/runs/{job.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/runs/{job_id}", response_class=HTMLResponse, summary="Run console")
async def run_console(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Watch a run.

    Rendered fully on the server first. The event stream then keeps it current; if the
    browser has no ``EventSource``, a meta refresh does the same job more slowly. Either
    way the page never shows less than the state at the moment it was requested.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    state = await run_service.run_state(session, job_id=job_id)
    research_request = await mandate_of(session, job)
    report = await session.scalar(select(Report).where(Report.job_id == job_id))
    pending = await approval_service.pending_gate(session, job)
    approvals = await approval_service.approvals_for_job(session, job_id)
    cancellation = await cancellation_service.cancellation_for(session, job_id=job_id)
    state_dict = state.as_dict()

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "runs/console.html",
        {
            "job": job,
            "research_request": research_request,
            "cancellation": cancellation,
            "can_cancel": job.status not in cancellation_service.TERMINAL_STATUSES,
            "state": state_dict,
            "spend_gbp": state.spend_gbp,
            "is_terminal": state.is_terminal,
            "awaiting": job.status is JobStatus.AWAITING_APPROVAL,
            "budget_exceeded": job.status is JobStatus.BUDGET_EXCEEDED,
            "budget_scope": state.budget_scope,
            "pending_gate": pending.value if pending else None,
            "pending_words": vocabulary.GATES[pending] if pending else None,
            "report_id": str(report.id) if report else None,
            "poll_seconds": POLL_SECONDS,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
            "cap": _cap_offer(
                request_row=research_request,
                spend_gbp=state.spend_gbp,
                is_terminal=state.is_terminal,
                stopped_on_run_budget=(
                    job.status is JobStatus.BUDGET_EXCEEDED and state.budget_scope != "monthly"
                ),
                settings=settings,
            ),
            **await _console_view(
                session,
                job=job,
                request_row=research_request,
                state_dict=state_dict,
                spend_gbp=state.spend_gbp,
                pending=pending,
                approvals=approvals,
            ),
        },
    )
    set_csrf_cookie(response, token)
    return response


@dataclass(frozen=True, slots=True)
class CapOffer:
    """Whether to offer a raise on the console, and what the form needs to render one."""

    offered: bool
    cap_display: str
    cap_value: str
    ceiling_display: str
    ceiling_value: str
    at_ceiling: bool


def _cap_offer(
    *,
    request_row: ResearchRequest | None,
    spend_gbp: Decimal,
    is_terminal: bool,
    stopped_on_run_budget: bool,
    settings: Settings,
) -> CapOffer:
    """Offer the raise where the operator meets the problem, at the ratio that warns.

    ``budget_warn_ratio`` rather than a constant of this module's own: the engine already
    logs that a run is approaching its ceiling at exactly that fraction, and an offer
    appearing at some other one would be a second opinion about when a run is in trouble.

    **A run stopped on its ceiling is offered it whatever it has spent.** The guard refuses
    a step whose *projection* would cross the line, so a run with an expensive step ahead
    of it can stop having spent a tenth of its cap — the case that needs the offer most,
    and the one a threshold on spend alone would not show it to.

    A finished run is not offered a raise, having nothing left to spend it on; a run
    stopped on the *monthly* ceiling is not either, because its own cap is not what
    stopped it and the block above it says so. A request already at the platform's own
    per-run budget is shown where that ceiling lifts instead of a form that would be
    refused.
    """
    if request_row is None:
        return CapOffer(False, "", "", "", "", at_ceiling=False)

    cap = Decimal(str(request_row.work_order.max_cost_gbp))
    ceiling = Decimal(str(settings.per_run_budget_gbp))
    near = cap > 0 and spend_gbp >= cap * Decimal(str(settings.budget_warn_ratio))
    return CapOffer(
        offered=(near or stopped_on_run_budget) and not is_terminal,
        cap_display=figures.pounds(cap),
        cap_value=f"{cap:.2f}",
        ceiling_display=figures.pounds(ceiling),
        ceiling_value=f"{ceiling:.2f}",
        at_ceiling=cap >= ceiling,
    )


async def _console_view(
    session: AsyncSession,
    *,
    job: Job,
    request_row: ResearchRequest | None,
    state_dict: dict[str, Any],
    spend_gbp: Decimal,
    pending: GateKind | None,
    approvals: list[Any],
) -> dict[str, Any]:
    """What the console says, decided here rather than in Jinja.

    Every value is a finished string or a typed shape from the vocabulary. The step keys
    stay on the page as secondary text — the worker terminal speaks in them — but the
    primary answer to "is it alive, does it want me, what has it cost" is composed here,
    in the operator's language.
    """
    run_words = vocabulary.job_state(job.status)
    steps = [
        {
            **entry,
            "label": vocabulary.step_label(str(entry.get("key", ""))),
            "status_words": vocabulary.job_state(JobStatus(str(entry.get("status")))),
        }
        for entry in state_dict["steps"]
    ]

    current = state_dict.get("current_step")
    current_label = vocabulary.step_label(str(current)) if current else None
    if current_label:
        plain_status = f"Working: {current_label.lower()[:1]}{current_label[1:]}."
    elif job.status is JobStatus.AWAITING_APPROVAL and pending is not None:
        asks = vocabulary.GATES[pending].asks
        plain_status = f"The run stopped so you could {asks}. {run_words.detail}"
    elif job.status is JobStatus.SUCCEEDED:
        plain_status = "The report is approved and frozen. Read it, or inspect its evidence."
    elif job.status is JobStatus.QUEUED:
        plain_status = "Queued. A worker normally begins within a few seconds."
    elif run_words.detail:
        plain_status = f"{run_words.label}. {run_words.detail}"
    else:
        plain_status = f"{run_words.label}."

    # Only while the run is *still* failed. A resume re-queues the job and leaves the step
    # row failed until it is re-executed, so an unconditional read put a red "this failed"
    # alert on a run that was queued and waiting for a worker — which is how an operator
    # comes to believe a fixed failure recurred when nothing has run since.
    failed = (
        next((row for row in steps if row.get("status") == JobStatus.FAILED.value), None)
        if job.status is JobStatus.FAILED
        else None
    )

    # Honest counts for the evidence links: what the run has actually gathered, so an
    # operator is never sent to an empty page without being told it is empty.
    source_count = (
        await session.scalar(
            select(func.count()).select_from(SourceDocument).where(SourceDocument.job_id == job.id)
        )
        or 0
    )
    claim_count = (
        await session.scalar(
            select(func.count())
            .select_from(Claim)
            .join(ReportSection, Claim.report_section_id == ReportSection.id)
            .where(ReportSection.job_id == job.id)
        )
        or 0
    )
    valuation_ready = any(
        row.get("key") == "value" and row.get("status") == JobStatus.SUCCEEDED.value
        for row in steps
    )

    return {
        "run_words": run_words,
        "plain_status": plain_status,
        "steps_display": steps,
        "current_label": current_label,
        "failed_step": failed,
        # FAILED, PAUSED and BUDGET_EXCEEDED continue as themselves (ADR 0090); the
        # service refuses the states that do not admit it, so this only decides whether
        # the form renders.
        "can_resume": job.status not in resume_service.UNRESUMABLE_STATUSES
        and job.status is not JobStatus.QUEUED
        and job.status is not JobStatus.AWAITING_APPROVAL,
        "journey": journey(
            state_dict["steps"],
            decisions={row.gate: row.decision for row in approvals},
            pending=pending,
        ),
        "cost": figures.cost_context(
            spent=spend_gbp,
            ceiling=(request_row.work_order.max_cost_gbp if request_row is not None else None),
        ),
        "evidence_counts": {
            "sources": source_count,
            "claims": claim_count,
            "valuation_ready": valuation_ready,
        },
        # The vocabulary's labels, for the one script that keeps rows current between
        # server renders. Authored here so the words stay the server's (ADR 0077: chrome
        # may be the client's, a label is not invented there).
        "status_labels_json": json.dumps(
            {status.value: words.label for status, words in vocabulary.JOB_STATES.items()}
        ),
    }


@router.post("/runs/{job_id}/resume", summary="Continue a stopped run as itself")
async def resume_run_page(
    request: Request,
    job_id: uuid.UUID,
    *,
    session: DbSession,
    settings: SettingsDep,
    redis: RedisClient,
    user: CurrentUser,
) -> Response:
    """Record the decision to continue, re-enqueue the same job, return to the console.

    ADR 0090: resuming appends to the audit chain and re-enqueues the *same* job — the
    engine skips the completed steps, so a failure one step from the end costs the failed
    step onward rather than the whole run again. The service refuses the states that do
    not admit continuing, each with its reason, and the page shows that reason.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}

    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was resumed.",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        await resume_service.resume_run(
            session, job=job, actor=user, reason=(submitted.get("reason") or None)
        )
    except ConflictError as exc:
        return _problem(request, exc.message, status=HTTP_409_CONFLICT)

    await session.commit()
    await enqueue_run(redis, job.id)
    return RedirectResponse(f"/runs/{job_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/runs/{job_id}/cap", summary="Raise what this run may spend")
async def raise_run_cap(
    request: Request,
    job_id: uuid.UUID,
    *,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Raise this run's ceiling from the console, and return to it.

    On the console rather than on the request page because this is a decision about a run
    in front of the operator watching it — and because the request page refuses an edit
    while a run is live, correctly. :func:`aer.services.requests.raise_cap` is the narrow
    exception and says why the cap is the one field that can move under a worker.

    The run is not restarted here. A run still going picks the new ceiling up at its next
    step; one already stopped on the old one continues through the button beside this
    form, which is the same recorded decision continuing has always been.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}

    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was changed.",
            status=HTTP_403_FORBIDDEN,
        )

    research_request = await mandate_of(session, job)
    if research_request is None:  # pragma: no cover -- a job cannot outlive its request
        return _problem(request, f"No request for run {job_id}.", status=HTTP_404_NOT_FOUND)

    raw = (submitted.get("max_cost_gbp") or "").strip()
    try:
        asked = Decimal(raw)
    except InvalidOperation:
        return _problem(
            request,
            f"{raw!r} is not an amount. Give the new ceiling in pounds, as a number.",
            status=HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        await requests_service.raise_cap(
            session,
            request=research_request,
            actor=user,
            to=asked,
            ceiling_gbp=settings.per_run_budget_gbp,
        )
    except ValidationError as exc:
        return _problem(request, exc.message, status=HTTP_422_UNPROCESSABLE_CONTENT)

    await session.commit()
    return RedirectResponse(f"/runs/{job_id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/runs/{job_id}/cancel", summary="Ask a run to stop")
async def cancel_run_page(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Record a request to stop, then return to the console.

    The run does not stop here. It stops at its next step boundary, which the console will
    show — a page that reported the run as stopped the moment the button was pressed would
    be wrong for as long as the current step took.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}

    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was cancelled.",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        await cancellation_service.request_cancellation(
            session, job=job, actor=user, reason=(submitted.get("reason") or None)
        )
    except ConflictError as exc:
        # The run finished between the page rendering and the button being pressed. Nothing
        # went wrong; there is simply nothing left to stop, and the page says so.
        return _problem(request, exc.message, status=HTTP_409_CONFLICT)

    await session.commit()
    return RedirectResponse(f"/runs/{job_id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/runs/{job_id}/plan", response_class=HTMLResponse, summary="Review the plan")
async def plan_review(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Gate 1: what the run intends to do, what it will cost, and what it may get wrong."""
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    plan = await session.scalar(
        select(ResearchPlan)
        .where(ResearchPlan.request_id == job.work_order_id)
        .order_by(ResearchPlan.created_at.desc())
    )
    if plan is None:
        return _problem(
            request,
            "This run has not produced a plan yet. There is nothing to approve.",
            status=HTTP_404_NOT_FOUND,
        )

    # The pins reach the page inside the payload now — the builder reads them itself, so
    # fetching them here as well would be a second answer to the question the gate hashes.
    payload = await _payload_for(session, job=job, gate=GateKind.PLAN)
    frame = await frame_for(session, job=job, gate=GateKind.PLAN)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "plans/review.html",
        {
            "job": job,
            "plan": plan,
            "payload": payload,
            # The hash of exactly the structure rendered below. Carried back by the form,
            # so approving a plan that has since changed is refused rather than recorded.
            "payload_hash": payload_hash_for(payload),
            **frame,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get(
    "/runs/{job_id}/financials",
    response_class=HTMLResponse,
    summary="Confirm an extraction that left tags unmapped",
)
async def financials_review(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The conditional gate: tags this platform's concept map could not place."""
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    produced = await _step_output(session, job_id=job_id, step_key="extract")
    if produced is None:
        return _problem(
            request,
            "This run has not extracted anything yet. There is nothing to confirm.",
            status=HTTP_404_NOT_FOUND,
        )

    if not unmapped_gate_required(produced):
        return _problem(
            request,
            "Every tag in this filing mapped onto a canonical concept, so this gate does not "
            "apply to this run. There is nothing to confirm.",
            status=HTTP_404_NOT_FOUND,
        )

    payload = await _payload_for(session, job=job, gate=GateKind.UNMAPPED_CONCEPTS)
    frame = await frame_for(session, job=job, gate=GateKind.UNMAPPED_CONCEPTS)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/financials.html",
        {
            "job": job,
            "payload": payload,
            "counts": _extraction_counts(produced),
            "payload_hash": payload_hash_for(payload),
            **frame,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/runs/{job_id}/sector", response_class=HTMLResponse, summary="Confirm the sector")
async def sector_review(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The conditional gate that decides which valuation models this run may use.

    Unlike the other gates, approving here does not only let the run continue: it grants the
    mandate the calculation layer requires. So the page leads with what confirming *blocks*
    rather than with the classification itself.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    produced = await _step_output(session, job_id=job_id, step_key=CLASSIFY_STEP)
    if produced is None:
        return _problem(
            request,
            "This run has not classified the company yet. There is nothing to confirm.",
            status=HTTP_404_NOT_FOUND,
        )

    if not sector_gate_required(produced):
        return _problem(
            request,
            "This company was not classified into a specialist sector, so this gate does not "
            "apply to this run. The standard model applies and there is nothing to confirm.",
            status=HTTP_404_NOT_FOUND,
        )

    payload = classification_payload(produced)
    frame = await frame_for(session, job=job, gate=GateKind.SECTOR_SPECIALIST)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/sector.html",
        {
            "job": job,
            "payload": payload,
            "payload_hash": payload_hash_for(payload),
            **frame,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/runs/{job_id}/peers", response_class=HTMLResponse, summary="Confirm the peer set")
async def peer_review(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The conditional gate that decides which companies this one is compared with.

    Every peer's rationale is rendered at full length. A page that truncated them would be a
    page that invites approving a set nobody read, which is the failure this gate exists to
    prevent.

    **Refusals are shown beside the set, and are deliberately not part of what is hashed**
    (ADR 0059). A model proposing peers will name companies the registry cannot resolve, and
    a reviewer judging the ones that did resolve is better off knowing what did not — but
    what they are approving is the peer set, so the hash covers that and nothing else.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    produced = await _step_output(session, job_id=job_id, step_key=PEER_SET_STEP)
    if produced is None:
        return _problem(
            request,
            "This run has not proposed a peer set yet. There is nothing to confirm.",
            status=HTTP_404_NOT_FOUND,
        )

    refused = [item for item in produced.get("refused", []) if isinstance(item, dict)]
    if not peer_set_required(produced):
        # Names were put forward and none of them survived resolution: a different situation
        # from a run that put nothing forward, and one an operator would otherwise have to
        # read the step's output to tell apart.
        tried = (
            f" {len(refused)} compan{'y was' if len(refused) == 1 else 'ies were'} proposed "
            "and none could be used: " + "; ".join(str(item.get("reason", "")) for item in refused)
            if refused
            else ""
        )
        # A run that asked no model is a third situation again (ADR 0059, second
        # amendment), and the reason is the operator's to read: a subscription, not a fault.
        not_asked = str(produced.get("model_skipped_because", "")).strip()
        return _problem(
            request,
            "This run proposed no comparable companies, so this gate does not apply to it. "
            "No comparables table will be produced and the report says so."
            + tried
            + (f" {not_asked}" if not_asked else ""),
            status=HTTP_404_NOT_FOUND,
        )

    payload = peer_set_payload(produced)
    frame = await frame_for(session, job=job, gate=GateKind.PEER_SET)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/peers.html",
        {
            "job": job,
            "payload": payload,
            "payload_hash": payload_hash_for(payload),
            # Why the model was not asked, when it was not: context, never part of the hash.
            "not_asked": str(produced.get("model_skipped_because", "")).strip(),
            "refused": [item for item in produced.get("refused", []) if isinstance(item, dict)],
            **frame,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/runs/{job_id}/themes", response_class=HTMLResponse, summary="Confirm the themes")
async def theme_review(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The conditional gate that decides which stories this company is filed under.

    Every rationale is rendered at full length, exactly as the peer gate renders its
    reasons: a page that truncated them would invite approving a slate nobody read, and a
    theme shapes how every later reader of the library weighs the company (K1, ADR 0065).
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    produced = await _step_output(session, job_id=job_id, step_key=THEME_STEP)
    if produced is None:
        return _problem(
            request,
            "This run has not proposed themes yet. There is nothing to confirm.",
            status=HTTP_404_NOT_FOUND,
        )
    if not theme_set_required(produced):
        return _problem(
            request,
            "This run proposed no themes, so this gate does not apply to it. The company "
            "is filed under nothing new, and that is a fact rather than a failure.",
            status=HTTP_404_NOT_FOUND,
        )

    payload = theme_set_payload(produced)
    # The subject's display name travels beside the payload rather than inside it: the
    # hash covers what is being approved, and the name is presentation.
    payload_for_page = dict(payload)
    payload_for_page["subject_name"] = str(produced.get("subject_name", ""))
    frame = await frame_for(session, job=job, gate=GateKind.THEME_SET)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/themes.html",
        {
            "job": job,
            "payload": payload_for_page,
            "payload_hash": payload_hash_for(payload),
            **frame,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get(
    "/runs/{job_id}/assumptions",
    response_class=HTMLResponse,
    summary="Confirm the assumptions a valuation will rest on",
)
async def assumptions_review(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The one gate that approves work which has **not happened yet** (ADR 0046).

    Every other gate confirms something the run produced and can be read back. This one
    confirms the numbers a discounted cash flow is about to be built on, some of them a
    model's proposal — so each row is shown with its value, its justification and who
    proposed it, and the names nobody could put a number against are shown as outstanding
    rather than quietly defaulted.

    **Rendered from the rows, not from the step's frozen output** (gap A52). The live
    run's operator typed the missing cost-of-capital values and watched this page keep
    calling them outstanding, because it showed the record from the moment the step
    assembled — a saved value that stays invisible where the decision is made reads as a
    save that failed. The rows are what the valuation will read, so they are what this
    page shows, what its hash covers, and what the resuming workflow verifies. The forms
    to supply, amend and confirm are here too, because the operator standing at this gate
    is exactly the person the per-request surface was built for.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    produced = await _step_output(session, job_id=job_id, step_key=ASSUMPTIONS_STEP)
    if produced is None:
        return _problem(
            request,
            "This run has not proposed any assumptions yet. There is nothing to confirm.",
            status=HTTP_404_NOT_FOUND,
        )

    if not assumptions_gate_required(produced):
        return _problem(
            request,
            "This run has nothing to confirm here: either its sector mandate does not "
            "permit a discounted cash flow, or the run proposed no assumptions and left "
            "no gaps. Either way it does not stop at this gate.",
            status=HTTP_404_NOT_FOUND,
        )

    rows = await assumptions_for_request(session, job.work_order_id)
    payload = await _payload_for(session, job=job, gate=GateKind.ASSUMPTIONS)
    frame = await frame_for(session, job=job, gate=GateKind.ASSUMPTIONS)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/assumptions.html",
        {
            "job": job,
            "payload": payload,
            "payload_hash": payload_hash_for(payload),
            # The rows themselves, for the entry forms: amending and confirming need ids,
            # which the hashed payload deliberately does not carry.
            "rows": {row.name: row for row in rows},
            "unconfirmed": sum(1 for row in rows if not row.approved),
            # The per-request surface's own hash, carried by each confirm form — that
            # route compares against its own payload shape, not the gate's.
            "list_hash": payload_hash_for(assumptions_payload(list(rows))),
            "unit_choices": UNIT_CHOICES,
            # Every save posts to the per-request routes and returns here, so the operator
            # never leaves the decision they are making.
            "return_to": f"/runs/{job.id}/assumptions",
            **frame,
            # Where the full history lives; editing no longer requires leaving this page.
            "request_id": str(job.work_order_id),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/runs/{job_id}/review", response_class=HTMLResponse, summary="Review the draft")
async def draft_review(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Gate 2: the drafted sections, exactly as the report will carry them."""
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    payload = await _payload_for(session, job=job, gate=GateKind.FINAL)
    # Rows exist from the moment a plan is approved; content arrives only when the draft
    # step runs. Testing for rows rather than for content would show an empty document and
    # invite an approval of nothing.
    if not any(section["content"] for section in payload["sections"]):
        return _problem(
            request,
            "This run has drafted nothing yet. There is nothing to approve.",
            status=HTTP_404_NOT_FOUND,
        )

    research_request = await mandate_of(session, job)
    if research_request is None:  # pragma: no cover -- a job cannot exist without its request
        return _problem(request, "This run has no research request.", status=HTTP_404_NOT_FOUND)

    # Matched on the listing rather than a foreign key: a request names a ticker somebody
    # typed, and only the acquire step turns that into a company row. Before then there is
    # nothing to match, and the preview falls back to the name on the request.
    company = await session.scalar(
        select(Company).where(
            Company.ticker == research_request.ticker,
            Company.exchange == research_request.exchange,
        )
    )
    # The document as it will be, rendered from the same rows the payload was built from.
    # Approving a bullet list and receiving a report is not a review; the operator decides
    # on the thing itself.
    preview = await render_markdown(session, job=job, request=research_request, company=company)

    # The rest of the §2.4 dashboard: every row the operator needs to judge the draft,
    # read from the same recorded state the triggers in the payload were computed over.
    # Server-rendered like everything else on this page — a gate that needs a script to
    # show its warnings is a gate that can be approved without seeing them.
    evaluations = await evaluations_for_job(session, job.id)
    coverage = await section_coverage_for_job(session, job=job, request=research_request)
    recorded = await disagreements_for_job(session, job.id)
    # Period and house-style value per row (gap R11): the raw table showed
    # `928567000.000000000000 USD` and six depreciation rates with nothing saying which
    # year each belonged to — the red team had to reconstruct the vintages itself, and
    # the operator approving the run could not see what the red team saw.
    style = HouseStyle()
    calculations = [
        {
            "name": row.name,
            "formula": row.formula,
            "period": row.period_label or "—",
            "shown": display.scalar(
                row.output_value, style=style, unit=row.output_unit, label=row.name, in_table=True
            ),
            "input_count": len(row.inputs or []),
        }
        for row in await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job.id)
            .order_by(Calculation.created_at, Calculation.id)
        )
    ]
    cost = await cost_scene_for_job(session, job=job, request=research_request)
    outcomes = await section_outcomes(session, job_id=job.id)

    frame = await frame_for(session, job=job, gate=GateKind.FINAL)
    review = _review_verdict(
        payload=payload,
        evaluations=evaluations,
        recorded=recorded,
        cost_scene=cost,
        cost_alert_gbp=cost.cap_gbp * COST_ALERT_RATIO,
        cost_summary=frame["gate_cost"].summary,
        authored_output=await _step_output(session, job_id=job_id, step_key="verdict"),
    )
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/review.html",
        {
            "job": job,
            "sections": payload["sections"],
            # Split by what the two things *are*, rather than shown as one list of
            # "disagreements" (gap R15). A source conflict is a fault: two documents say
            # different numbers and somebody has to decide. A red-team challenge is the
            # adversary doing its job, and seven of them listed under "unresolved" read as
            # seven problems with the run rather than as the review the run paid for.
            "escalations": [
                row
                for row in payload["escalations"]
                if row["kind"] != DisagreementKind.THESIS_CONFLICT.value
            ],
            "challenges": [row for row in recorded if row.kind is DisagreementKind.THESIS_CONFLICT],
            # Keyed by disagreement id, so a challenge with no brief renders exactly as it
            # did before ADR 0095 — which is the fallback for a run that predates the step,
            # one whose briefing failed, and one whose adversary raised more than a sitting.
            "briefs": briefs_from_output(
                await _step_output(session, job_id=job_id, step_key="brief_challenges")
            ),
            "triggers": payload["triggers"],
            "evaluations": evaluations,
            "coverage": coverage,
            "disagreements": [
                row for row in recorded if row.kind is not DisagreementKind.THESIS_CONFLICT
            ],
            "open_outcome": ResolutionOutcome.ESCALATED,
            "calculations": calculations,
            # One row per section: what it was handed, how many tries it took, and why it
            # refused (gap A63). All of it was already recorded by `sections.writing._failed`
            # and none of it was displayed, so five sections that died on a starved evidence
            # pack showed as five blanks and a coverage table full of zeros.
            "outcomes": [outcomes.get(section["key"], {}) for section in payload["sections"]],
            "cost": cost,
            "cost_alert_gbp": cost.cap_gbp * COST_ALERT_RATIO,
            "markdown": preview.markdown,
            "footnote_count": preview.footnote_count,
            "payload_hash": payload_hash_for(payload),
            "review_verdict": review["verdict"],
            "attention_index": review["attention"],
            **frame,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


def _review_verdict(
    *,
    payload: dict[str, Any],
    evaluations: Sequence[Any],
    recorded: Sequence[Any],
    cost_scene: Any,
    cost_alert_gbp: Decimal,
    cost_summary: str,
    authored_output: dict[str, Any] | None,
) -> dict[str, Any]:
    """The review's two-half verdict and its attention index (ADR 0087).

    The composed half counts what actually needs the operator — open conflicts, failed
    checks, missing sections, spend — and the attention index links each count to the
    real heading beneath it, so the page is triageable in thirty seconds without a block
    being removed. The red team's challenges ride in both as value received, never as
    faults, and never move the tone on their own: a run where the adversary found nothing
    would be the one worth worrying about.
    """
    challenges = [row for row in recorded if row.kind is DisagreementKind.THESIS_CONFLICT]
    conflicts = [row for row in recorded if row.kind is not DisagreementKind.THESIS_CONFLICT]
    open_conflicts = [row for row in conflicts if row.resolution is ResolutionOutcome.ESCALATED]
    failed_checks = [row for row in evaluations if row.passed is False]
    not_generated = [row for row in payload["sections"] if not row["content"]]
    triggers = payload["triggers"]
    over_alert = cost_scene.actual_gbp >= cost_alert_gbp or (
        cost_scene.estimated_gbp is not None and cost_scene.estimated_gbp >= cost_alert_gbp
    )

    attention: list[dict[str, str]] = []
    if triggers:
        attention.append(
            {
                "label": f"{len(triggers)} escalation trigger"
                + ("" if len(triggers) == 1 else "s")
                + " fired",
                "target": "#triggers",
                "tone": "failure",
            }
        )
    if failed_checks:
        attention.append(
            {
                "label": f"{len(failed_checks)} check"
                + (" failed" if len(failed_checks) == 1 else "s failed"),
                "target": "#validations",
                "tone": "failure",
            }
        )
    if open_conflicts:
        attention.append(
            {
                "label": f"{len(open_conflicts)} source conflict"
                + ("" if len(open_conflicts) == 1 else "s")
                + " unsettled",
                "target": "#escalations",
                "tone": "warning",
            }
        )
    if not_generated:
        attention.append(
            {
                "label": f"{len(not_generated)} section"
                + ("" if len(not_generated) == 1 else "s")
                + " not generated",
                "target": "#draft-sections",
                "tone": "warning",
            }
        )
    if over_alert:
        attention.append(
            {"label": "Spend is near the ceiling", "target": "#cost", "tone": "warning"}
        )
    if challenges:
        attention.append(
            {
                "label": f"{len(challenges)} challenge"
                + ("" if len(challenges) == 1 else "s")
                + " to read — the review the run paid for",
                "target": "#red-team",
                "tone": "info",
            }
        )

    authored = None
    if authored_output and authored_output.get("written"):
        authored = verdicts.Authored(
            str(authored_output.get("sentence", "")),
            vocabulary.Tone(str(authored_output.get("tone", "info"))),
        )

    demands_attention = bool(triggers or failed_checks or open_conflicts or not_generated)
    verdict = verdicts.sentence(
        [
            verdicts.Count(
                len(open_conflicts),
                "source conflict needs settling",
                "source conflicts need settling",
            ),
            verdicts.Count(len(failed_checks), "check failed", "checks failed"),
            verdicts.Count(
                len(not_generated),
                "section was not generated",
                "sections were not generated",
            ),
            verdicts.Count(
                len(challenges),
                "red-team challenge is there to read",
                "red-team challenges are there to read",
            ),
            f"{cost_summary} spent",
        ],
        when_none=f"Nothing needs your attention before the decision; {cost_summary} spent",
        tone=vocabulary.Tone.WARNING if demands_attention else vocabulary.Tone.SUCCESS,
        authored=authored,
    )
    return {"verdict": verdict, "attention": attention}


@router.get(
    "/runs/{job_id}/preview",
    response_class=HTMLResponse,
    summary="The document as it stands",
)
async def run_preview(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The run's draft as the finished document: the HTML the report itself will be.

    This page is the report's own HTML notation, not a site page — no navigation, no
    scripts, the print stylesheet included. It is assembled by the same call, with the
    same inputs, as the render step will use, which is what makes looking at it *before*
    approving Gate 2 meaningful: what is approved is what exists.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    research_request = await mandate_of(session, job)
    if research_request is None:  # pragma: no cover -- a job cannot exist without its request
        return _problem(request, "This run has no research request.", status=HTTP_404_NOT_FOUND)

    exists = await session.scalar(
        select(ReportSection.id).where(ReportSection.job_id == job_id).limit(1)
    )
    if exists is None:
        return _problem(
            request,
            "This run has no sections yet, so there is no document to preview. Sections "
            "appear once the plan is approved.",
            status=HTTP_404_NOT_FOUND,
        )

    document = await _run_document(session, job=job, research_request=research_request)
    return HTMLResponse(render_html(document))


@router.get(
    "/runs/{job_id}/summary",
    response_class=HTMLResponse,
    summary="The one-page summary",
)
async def run_summary(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The run's document narrowed to one page (gap O8).

    A second renderer over the same assembly, never new analysis: the front matter, the
    at-a-glance numbers, and the sections whose definition rows claim a place. Footnote
    numbers match the full note, so a marker here is an entry point into it.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    research_request = await mandate_of(session, job)
    if research_request is None:  # pragma: no cover -- a job cannot exist without its request
        return _problem(request, "This run has no research request.", status=HTTP_404_NOT_FOUND)

    exists = await session.scalar(
        select(ReportSection.id).where(ReportSection.job_id == job_id).limit(1)
    )
    if exists is None:
        return _problem(
            request,
            "This run has no sections yet, so there is no document to summarise.",
            status=HTTP_404_NOT_FOUND,
        )

    document = await _run_document(session, job=job, research_request=research_request)
    return HTMLResponse(render_html(summary_document(document), contents=False))


async def _run_document(
    session: AsyncSession, *, job: Job, research_request: ResearchRequest
) -> Any:
    """The run's document, assembled exactly as the preview shows it.

    One function for the preview and the footnote drill-down, because the drill-down
    resolves a *marker number* — and marker numbers only mean anything if both pages
    assemble the same document with the same inputs.
    """
    # Matched on the listing for the same reason as the review page: the company row only
    # exists once the acquire step has run.
    company = await session.scalar(
        select(Company).where(
            Company.ticker == research_request.ticker,
            Company.exchange == research_request.exchange,
        )
    )
    comps = await comps_note_for(session, job=job, request=research_request)
    return await assemble_document(
        session,
        job=job,
        request=research_request,
        company=company,
        sector=await sector_note_for(session, job=job),
        comps=comps,
        style=await configuration.effective_house_style(session),
        charts=await exportable_charts_for(
            session,
            job=job,
            request=research_request,
            licence_note=comps.licence_note if comps else "",
        ),
    )


@router.post("/runs/{job_id}/replay", summary="Reproduce this run from its own record")
async def replay_run_page(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Re-derive everything the run produced and show what still holds.

    A POST rather than a link, and not only for the CSRF token: re-verifying a citation
    writes its verdict back onto the row, so this changes stored state even though it reads
    like a report. It fetches nothing and calls no model, so it costs nothing to press.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was replayed.",
            status=HTTP_403_FORBIDDEN,
        )

    store = LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)
    report = await replay_run(session, store, job_id=job_id, settings=settings)
    await session.commit()

    if not report.reproduces:
        lead = verdicts.sentence(
            [
                verdicts.Count(
                    len(report.calculations_diverged),
                    "calculation re-derives outside tolerance",
                    "calculations re-derive outside tolerance",
                ),
                verdicts.Count(
                    len(report.citations_failed),
                    "citation could not be re-verified",
                    "citations could not be re-verified",
                ),
                verdicts.Count(
                    len(report.artefacts_unreadable),
                    "archived artefact cannot be read back",
                    "archived artefacts cannot be read back",
                ),
                verdicts.Count(
                    len(report.model_calls_unarchived),
                    "model call has no archived exchange",
                    "model calls have no archived exchange",
                ),
            ],
            when_none="This run no longer reproduces",
            tone=vocabulary.Tone.FAILURE,
        )
    elif report.checked:
        lead = verdicts.sentence(
            [
                verdicts.Count(
                    report.checked,
                    "recorded derivation or citation still holds",
                    "recorded derivations and citations still hold",
                )
            ],
            when_none="Reproduces",
            tone=vocabulary.Tone.SUCCESS,
        )
    else:
        # Zero checks is not a pass: nothing failed because nothing was checkable, and the
        # tone must not read as the all-clear (the verdict module refuses SUCCESS here).
        lead = verdicts.sentence(
            ["nothing in this run's record was checkable, which is not a pass"],
            when_none="Nothing in this run's record was checkable, which is not a pass",
            tone=vocabulary.Tone.MUTED,
        )

    page: Response = render(
        request,
        "runs/replay.html",
        {
            "job": job,
            "report": report,
            "verdict": lead,
            "replayed_at_display": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "findings": [
                group
                for group in (
                    {
                        "title": "Re-derivation outside tolerance",
                        "items": report.calculations_diverged,
                    },
                    {"title": "Citation verification", "items": report.citations_failed},
                    {"title": "Archived bytes", "items": report.artefacts_unreadable},
                    {"title": "Model call archive", "items": report.model_calls_unarchived},
                )
                if group["items"]
            ],
        },
    )
    return page


@router.post(
    "/runs/{job_id}/disagreements/{disagreement_id}/settle",
    summary="Settle one escalated disagreement",
)
async def settle_disagreement_page(
    request: Request,
    job_id: uuid.UUID,
    disagreement_id: uuid.UUID,
    *,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Choose a side on a conflict the ladder declined to decide, and say why.

    ``services.disagreements.settle_by_hand`` has existed since the ladder did and nothing
    reached it, so gate 3 showed two positions and offered no way to prefer either — which
    reads as a question the operator is failing to answer rather than as a record they may
    add to. This is the door.

    **The rule that escalated it is not overwritten**, and the rationale is appended rather
    than replaced. Both are the service's rules; this handler only carries the form to them.
    A disagreement nobody settles keeps publishing both sides, which is still the default
    and still the honest outcome for most of them.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was settled.",
            status=HTTP_403_FORBIDDEN,
        )

    found = await session.get(Disagreement, disagreement_id)
    # Checked against the job in the URL as well as by id: a disagreement belongs to one
    # run, and a settle posted at the wrong run is a mistake worth refusing rather than
    # silently honouring.
    if found is None or found.job_id != job_id:
        return _problem(
            request, f"No disagreement {disagreement_id} on this run.", status=HTTP_404_NOT_FOUND
        )

    try:
        outcome = ResolutionOutcome(submitted.get("outcome", ""))
    except ValueError:
        return _problem(
            request, "That is not a side of this disagreement.", status=HTTP_404_NOT_FOUND
        )

    try:
        await settle_by_hand(
            session,
            disagreement=found,
            outcome=outcome,
            actor=user,
            rationale=submitted.get("rationale", ""),
        )
    except ValidationError as problem:
        # The service's messages name the rule they enforce — "a human resolution needs a
        # reason", "this was settled by rule and is not open" — and each is the useful
        # answer to what the operator just tried.
        return _problem(request, str(problem), status=HTTP_400_BAD_REQUEST)

    await session.commit()
    return RedirectResponse(f"/runs/{job_id}/review#disagreements", status_code=HTTP_303_SEE_OTHER)


@router.post("/runs/{job_id}/gates/{gate}", summary="Record a gate decision")
async def decide_gate_page(
    request: Request,
    job_id: uuid.UUID,
    gate: GateKind,
    *,
    session: DbSession,
    settings: SettingsDep,
    redis: RedisClient,
    user: CurrentUser,
) -> Response:
    """Approve or reject, then return to the console.

    The decision is recorded and the run is *queued*, never executed inline. A gate
    approval that ran the remaining steps inside the request would hold the browser open
    for the length of a research run and would abandon it if the tab closed.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}

    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was decided.",
            status=HTTP_403_FORBIDDEN,
        )

    decision = (
        Decision.APPROVED
        if submitted.get("decision") == Decision.APPROVED.value
        else Decision.REJECTED
    )

    # A stale page is refused before anything is recorded. The workflow's own
    # `_require_approval` is the deep guarantee — an approval carrying the wrong hash
    # never unlocks the gate however it got recorded — but an operator who pressed the
    # button on a page that has moved deserves the reason now, not a run that quietly
    # stays paused. Verified against the same builder the page rendered from; a workflow
    # this build cannot read returns an empty payload, and the deep check still holds.
    current = await _payload_for(session, job=job, gate=gate)
    if current and submitted.get("payload_hash", "") != payload_hash_for(current):
        return _problem(
            request,
            "The proposal changed after this page was opened. Nothing was approved. "
            "Review the current version and decide again.",
            status=HTTP_409_CONFLICT,
        )

    try:
        await approval_service.record_decision(
            session,
            job=job,
            gate=gate,
            decision=decision,
            actor=user,
            payload_hash=submitted.get("payload_hash", ""),
            notes=(submitted.get("notes") or None),
        )
    except ValidationError as exc:
        # Shown rather than swallowed. Every refusal from the approval service names a
        # rule the operator can act on -- already decided, or out of order -- and hiding
        # that behind a generic error would make the gates feel arbitrary.
        return _problem(request, exc.message, status=HTTP_422_UNPROCESSABLE_CONTENT)

    await session.commit()

    if decision is Decision.APPROVED:
        await enqueue_run(redis, job.id)

    return RedirectResponse(f"/runs/{job_id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/runs/{job_id}/sources", response_class=HTMLResponse, summary="What a run acquired")
async def run_sources(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The evidence table: every document, its tier, its dates, its hash, its flags.

    Read-only, and deliberately shows everything — a quarantined source appears here with
    its reason rather than being filtered out. "What did this run refuse to use, and why?"
    is a question a reader of the report is entitled to ask, and a table that showed only
    the sources that were used could not answer it.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    sources = await provenance.sources_for_run(session, job_id)
    research_request = await mandate_of(session, job)

    # The breakdown is by what happened to each document, so it always sums: a quarantined
    # source a person overrode counts as admissible, exactly as the verifier now treats it.
    admissible = sum(1 for source in sources if source.is_admissible)
    quarantined_out = sum(
        1 for source in sources if source.quarantined and not source.is_admissible
    )
    other_out = len(sources) - admissible - quarantined_out
    page: Response = render(
        request,
        "runs/sources.html",
        {
            "job": job,
            "research_request": research_request,
            "sources": sources,
            "quarantined": sum(1 for source in sources if source.quarantined),
            "inadmissible": sum(1 for source in sources if not source.is_admissible),
            "flagged": sum(1 for source in sources if source.injection_flagged),
            "verdict": verdicts.tally(
                verdicts.Count(len(sources), "source acquired", "sources acquired"),
                [
                    verdicts.Part(admissible, "admissible"),
                    verdicts.Part(quarantined_out, "quarantined"),
                    verdicts.Part(other_out, "inadmissible for other reasons"),
                ],
                when_none="Acquisition has not completed, so no source record exists yet.",
                tone=(
                    vocabulary.Tone.MUTED
                    if not sources
                    else (
                        vocabulary.Tone.SUCCESS
                        if admissible == len(sources)
                        else vocabulary.Tone.INFO
                    )
                ),
            ),
        },
    )
    return page


@router.get("/runs/{job_id}/claims", response_class=HTMLResponse, summary="What a run asserts")
async def run_claims(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The claim index a reader arrives at from a report.

    One click from here to the excerpt behind any of them, which is what makes the whole
    chain checkable in two rather than in "read the source and search for the sentence".
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    claims = await provenance.claims_for_run(session, job_id)
    research_request = await mandate_of(session, job)

    unsupported = sum(1 for claim in claims if not claim.is_supported)
    page: Response = render(
        request,
        "runs/claims.html",
        {
            "job": job,
            "research_request": research_request,
            "claims": claims,
            "unsupported": unsupported,
            "verdict": verdicts.tally(
                verdicts.Count(len(claims), "claim recorded", "claims recorded"),
                [
                    verdicts.Part(len(claims) - unsupported, "supported"),
                    verdicts.Part(unsupported, "unsupported"),
                ],
                when_none="No claims have been recorded yet; drafting has not produced assertions.",
                tone=(
                    vocabulary.Tone.MUTED
                    if not claims
                    else (vocabulary.Tone.WARNING if unsupported else vocabulary.Tone.SUCCESS)
                ),
            ),
        },
    )
    return page


@router.get("/claims/{claim_id}", response_class=HTMLResponse, summary="The evidence for a claim")
async def claim_detail(
    request: Request,
    claim_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The drill-down: the sentence, the figure, and the exact words behind it.

    The excerpt is shown **verbatim, as stored**, with the verifier's verdict beside it. A
    page that showed the excerpt without saying whether code had confirmed it would imply a
    check that may never have happened, which is worse than showing nothing.
    """
    if not await _claim_is_visible(session, claim_id=claim_id, user_id=user.id):
        return _problem(request, f"No claim {claim_id}.", status=HTTP_404_NOT_FOUND)

    view = await provenance.claim_view(session, claim_id)
    if view is None:  # pragma: no cover -- visibility already proved it exists
        return _problem(request, f"No claim {claim_id}.", status=HTTP_404_NOT_FOUND)

    states = [citation.state for citation in view.citations]
    verified = states.count("verified")
    overridden = states.count("overridden")
    unverified = len(states) - verified - overridden
    page: Response = render(
        request,
        "claims/detail.html",
        {
            "claim": view,
            "verdict": verdicts.tally(
                verdicts.Count(
                    len(states), "citation stands behind it", "citations stand behind it"
                ),
                [
                    verdicts.Part(verified, "verified against the archived bytes"),
                    verdicts.Part(overridden, "accepted by a person after verification failed"),
                    verdicts.Part(unverified, "not confirmed"),
                ],
                when_none=(
                    "This claim cites nothing. Its kind carries a stated basis instead."
                    if view.is_supported
                    else "This claim cites nothing, and its kind requires that it does."
                ),
                tone=(
                    vocabulary.Tone.SUCCESS
                    if states and unverified == 0 and overridden == 0
                    else (
                        vocabulary.Tone.WARNING
                        if overridden or unverified or not view.is_supported
                        else vocabulary.Tone.MUTED
                    )
                ),
            ),
        },
    )
    return page


@router.get(
    "/runs/{job_id}/footnotes/{number}",
    response_class=HTMLResponse,
    summary="What one footnote marker rests on",
)
async def footnote_drilldown(
    request: Request,
    job_id: uuid.UUID,
    number: int,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The walk back from a marker: the excerpt, the verdict, the digest — or the walk on.

    Marker numbers are meaningful because this assembles the same document, with the same
    inputs, as the preview that showed the marker. A calculation marker continues to the
    calculation walk; a source marker answers here with the source, its licence note, and
    every claim in this run the verifier checked against it; an unresolvable citation is
    stated in exactly the words the document used.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)
    research_request = await mandate_of(session, job)
    if research_request is None:  # pragma: no cover -- a job cannot exist without its request
        return _problem(request, "This run has no research request.", status=HTTP_404_NOT_FOUND)

    document = await _run_document(session, job=job, research_request=research_request)
    if number < 1 or number > len(document.citations):
        return _problem(
            request,
            f"This document has {len(document.citations)} note(s); there is no note {number}.",
            status=HTTP_404_NOT_FOUND,
        )
    return await _footnote_answer(
        request,
        session,
        job=job,
        research_request=research_request,
        number=number,
        reference=document.citations[number - 1],
    )


async def _footnote_answer(
    request: Request,
    session: AsyncSession,
    *,
    job: Job,
    research_request: ResearchRequest,
    number: int,
    reference: Any,
) -> Response:
    """One marker's answer: the walk on, the evidence, or the honest dead end."""
    identifier = _uuid_or_none(reference.identifier)

    if reference.kind == "calculation":
        calculation = await session.get(Calculation, identifier) if identifier else None
        if calculation is not None:
            # The calculation walk already exists and already renders the DAG to its
            # leaves; a second copy of it here would be the page that drifts.
            return RedirectResponse(
                f"/calculations/{calculation.id}", status_code=HTTP_303_SEE_OTHER
            )
        return _unresolved_footnote_page(request, job=job, number=number, reference=reference)

    source = await provenance.source_detail(session, identifier) if identifier else None
    if source is None:
        return _unresolved_footnote_page(request, job=job, number=number, reference=reference)

    claims = await provenance.claims_citing(session, job_id=job.id, source_document_id=source.id)
    rows = [
        {
            "claim": claim,
            # Only this source's citations: a claim resting on two documents is walked
            # one document at a time, and the other document has its own marker.
            "citations": [c for c in claim.citations if c.source.id == source.id],
        }
        for claim in claims
    ]
    page: Response = render(
        request,
        "runs/footnote.html",
        {
            "job": job,
            "research_request": research_request,
            "number": number,
            "label": reference.label,
            "source": source,
            "rows": rows,
            "unresolved": None,
            "verdict": verdicts.sentence(
                [
                    f"resolved to {source.title or source.url}",
                    verdicts.Count(
                        len(rows),
                        "claim in this run was checked against it",
                        "claims in this run were checked against it",
                    ),
                ],
                when_none="This marker resolved to a source",
                tone=vocabulary.Tone.SUCCESS,
            ),
        },
    )
    return page


def _unresolved_footnote_page(
    request: Request, *, job: Job, number: int, reference: Any
) -> Response:
    """The honest dead end, in the document's own words."""
    footnote = UnresolvedFootnote(
        number=number,
        kind_label=("calculation" if reference.kind == "calculation" else "source document"),
        identifier=reference.identifier,
    )
    page: Response = render(
        request,
        "runs/footnote.html",
        {
            "job": job,
            "research_request": None,
            "number": number,
            "label": reference.label,
            "source": None,
            "rows": [],
            "unresolved": footnote,
        },
    )
    return page


def _uuid_or_none(identifier: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(identifier)
    except (ValueError, AttributeError, TypeError):
        return None


@router.get("/runs/{job_id}/valuation", response_class=HTMLResponse, summary="The valuation")
async def valuation_page(
    request: Request,
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The phase's user-visible outcome: both terminal methods, the grid, the comps.

    **Read back from the run's own ledger, never recomputed.** A page that re-ran the
    valuation would show today's answer against yesterday's report, and both would look
    authoritative. Where a figure is absent this says the run did not produce it.

    Server-rendered with no script of its own, in the pattern task 20 established, so it works
    with JavaScript off.
    """
    job = await _owned_job(session, job_id=job_id, user=user)
    if job is None:
        return _problem(request, f"No run {job_id}.", status=HTTP_404_NOT_FOUND)

    view = await valuation_view(session, job)
    research_request = await mandate_of(session, job)

    # The comps table is rendered at INTERNAL because this page is not exported. The Markdown
    # report is the shareable artefact and takes a `WithheldComps` instead -- ADR 0034.
    table = view.comps if isinstance(view.comps, CompsTable) else None
    rows = (table.subject, *table.peers) if table is not None else ()

    # The licensed charts render here and only here: their builders mark them
    # non-exportable and the report assembler refuses them, so this page is the one
    # surface that can carry them (ADR 0043).
    internal_charts = (
        await internal_charts_for(session, job=job, request=research_request)
        if research_request is not None
        else ()
    )

    # The stored grid, drawn by the same builder the report's exhibits use — deterministic,
    # byte-identical for identical rows, every cell a recorded calculation. The full table
    # renders beside it; the figure is a reading aid, never the record.
    heatmap = await sensitivity_chart(session, job=job) if view.grids else None

    style = HouseStyle()
    if view.sector and view.sector.blocks_the_dcf:
        lead = verdicts.sentence(
            ["no discounted cash flow is shown, because none was run for this sector"],
            when_none="No discounted cash flow is shown, because none was run for this sector",
            tone=vocabulary.Tone.REFUSAL,
        )
    elif not view.has_valuation:
        lead = verdicts.sentence(
            ["this run recorded no valuation, and nothing is computed here to fill the gap"],
            when_none="This run recorded no valuation",
            tone=vocabulary.Tone.MUTED,
        )
    else:
        per_share = []
        for outcome in (view.gordon, view.exit_multiple):
            if outcome.value_per_share is None:
                continue
            shown = display.scalar(
                outcome.value_per_share.value,
                style=style,
                unit=outcome.value_per_share.unit,
                label="value per share",
            )
            per_share.append(f"{shown} per share by {outcome.label}")
        lead = verdicts.sentence(
            per_share,
            when_none="This run recorded a valuation",
            tone=vocabulary.Tone.INFO,
        )

    page: Response = render(
        request,
        "runs/valuation.html",
        {
            "job": job,
            "research_request": research_request,
            "view": view,
            "outcomes": (view.gordon, view.exit_multiple),
            "rows": (
                ("enterprise_value", "Enterprise value"),
                ("equity_value", "Equity value"),
                ("terminal_share", "Terminal value share"),
                ("value_per_share", "Value per share"),
            ),
            "comps_rows": rows,
            # Grouped rather than listed per peer: eight companies excluded for the same
            # one reason must not read as eight repeated paragraphs (polish P4).
            "comps_excluded": grouped_exclusions(table.excluded) if table is not None else (),
            "comps_keys": tuple(
                (definition.key, definition.label) for definition in MULTIPLE_DEFINITIONS
            ),
            "internal_charts": [
                {"key": chart.key, "title": chart.title, "uri": svg_data_uri(chart.svg)}
                for chart in internal_charts
                if not chart.placeholder
            ],
            "verdict": lead,
            "heatmap": (
                {
                    "uri": svg_data_uri(heatmap.svg),
                    "title": heatmap.title,
                    "caption": heatmap.caption,
                }
                if heatmap is not None and not heatmap.placeholder
                else None
            ),
            "disagreement_display": (
                f"{view.methods_disagree * Decimal('100'):.1f}"
                if view.methods_disagree is not None
                else None
            ),
        },
    )
    return page


@router.get(
    "/calculations/{calculation_id}",
    response_class=HTMLResponse,
    summary="One figure, and what it rests on",
)
async def calculation_detail(
    request: Request,
    calculation_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The second click: the arithmetic, and every input's origin.

    Ownership is checked through the calculation's job rather than assumed. A calculation id
    is a UUID somebody could guess at, and this page would otherwise show one run's figures to
    another operator.
    """
    calculation = await session.get(Calculation, calculation_id)
    if calculation is None:
        return _problem(request, f"No calculation {calculation_id}.", status=HTTP_404_NOT_FOUND)

    job = await _owned_job(session, job_id=calculation.job_id, user=user)
    if job is None:
        return _problem(request, f"No calculation {calculation_id}.", status=HTTP_404_NOT_FOUND)

    tree = await calculation_service.lineage(session, calculation_id)

    # The tranche-1 gap, closed where the plan said it would be: the handler builds a
    # figure from each lineage node, so the template formats no Decimal and composes no
    # origin link — the value arrives in house style with its drill-down attached.
    style = HouseStyle()
    rows = [
        {
            **row,
            "figure": figures.lineage_figure(
                row, request_id=job.work_order_id, job_id=job.id, style=style
            ),
        }
        for row in lineage_rows(tree)
    ]
    page: Response = render(
        request,
        "calculations/detail.html",
        {
            "calculation": calculation,
            "lineage": rows,
            "request_id": job.work_order_id,
            "job_id": job.id,
            "back_href": f"/runs/{job.id}/valuation",
            "shown_output": display.scalar(
                calculation.output_value,
                style=style,
                unit=calculation.output_unit,
                label=calculation.name,
            ),
            "verdict": verdicts.sentence(
                [
                    "calculated by code, with the formula and every input recorded",
                    verdicts.Count(
                        len(calculation.inputs or []),
                        "recorded input directly beneath it",
                        "recorded inputs directly beneath it",
                    ),
                ],
                when_none="Calculated by code, with the formula recorded",
                tone=vocabulary.Tone.INFO,
            ),
        },
    )
    return page


@router.get("/settings", response_class=HTMLResponse, summary="Change models and budgets")
async def settings_page(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Cost and method, editable. Credentials, shown as present or absent and nothing more."""
    del user
    token = new_csrf_token(settings)
    context = await _settings_context(session, settings, token=token)
    context["saved"] = request.query_params.get("saved") == "1"
    page: Response = render(request, "settings/index.html", context)
    set_csrf_cookie(page, token)
    return page


@router.post("/settings", summary="Save one setting")
async def save_settings(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Store one override, or re-render saying why it was refused.

    One field per submission rather than a single save-everything form: a routing table that
    fails to parse must not silently discard a budget change made at the same time.
    """
    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was changed.",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        await configuration.save_override(
            session,
            key=submitted.get("key", ""),
            raw=submitted.get("value", ""),
            actor=user,
        )
    except ValidationError as refused:
        token = new_csrf_token(settings)
        context = await _settings_context(session, settings, token=token)
        context["error"] = refused.message
        rejected: Response = render(request, "settings/index.html", context)
        rejected.status_code = HTTP_400_BAD_REQUEST
        set_csrf_cookie(rejected, token)
        return rejected

    await session.commit()
    return RedirectResponse("/settings?saved=1", status_code=HTTP_303_SEE_OTHER)


async def _settings_context(
    session: DbSession, settings: Settings, *, token: str
) -> dict[str, Any]:
    """What the settings form renders from: current effective values, and what is overridden."""
    effective = await configuration.effective_settings(session, settings)
    overrides = await configuration.current_overrides(session)
    return {
        "overridable": configuration.OVERRIDABLE,
        "overrides": overrides,
        "values": {
            "model_routes": json.dumps(
                {role: route.model_dump() for role, route in effective.model_routes.items()},
                indent=2,
                sort_keys=True,
            ),
            "per_run_budget_gbp": str(effective.per_run_budget_gbp),
            "monthly_budget_gbp": str(effective.monthly_budget_gbp),
            "budget_warn_ratio": str(effective.budget_warn_ratio),
            "house_style": json.dumps(
                effective.house_style.model_dump(mode="json"), indent=2, sort_keys=True
            ),
        },
        "secrets": configuration.secret_presence(effective),
        "saved": False,
        "error": None,
        "csrf_field": CSRF_FIELD_NAME,
        "csrf_token": token,
    }


@router.get("/costs", response_class=HTMLResponse, summary="What the platform has spent")
async def costs_page(
    request: Request,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Spend, and whether the prompt cache is earning its keep.

    The second half is the point. A14 asks every call for a cache; whether any call *gets*
    one is invisible from the code, because every way of missing is silent — a prefix under
    the model's minimum, a dictionary serialised in a different order, a per-call string in
    front of the shared block. The hit rate is the only evidence, so it is on the page
    rather than in a log line somebody would have to know to look for.
    """
    del user  # scoped by the single-user deployment; see A5

    summary = await spend_summary(session)
    page: Response = render(
        request,
        "spend/index.html",
        {
            "summary": summary,
            "roles": await spend_by_role(session),
            "runs": await recent_runs(session),
        },
    )
    return page


@router.get("/reports", response_class=HTMLResponse, summary="Report history")
async def reports_index(
    request: Request,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Every report this account has produced, grouped by company, newest first.

    Drafts appear marked as such — this is the account's own work list — but the
    *history* surfaces (the company page, the API, the comparison section) show approved
    reports only; the grouping here links through to those.
    """
    company_filter = str(request.query_params.get("company", "")).strip()

    fetched = await session.execute(
        select(Report, ResearchRequest)
        .join(ResearchRequest, ResearchRequest.id == Report.request_id)
        .join(WorkOrder, WorkOrder.id == Report.request_id)
        .where(WorkOrder.user_id == user.id)
        .order_by(Report.as_of_date.desc(), Report.created_at.desc())
    )
    rows: list[tuple[Report, ResearchRequest]] = [(report, req) for report, req in fetched.tuples()]
    if company_filter:
        needle = company_filter.lower()
        rows = [
            (report, req)
            for report, req in rows
            if needle in req.ticker.lower() or needle in req.company_name.lower()
        ]

    # What each report's run cost, in one grouped query — the history row answers "was
    # this conclusion worth what it cost?" without a click per report.
    job_ids = [report.job_id for report, _ in rows if report.job_id is not None]
    spend_by_job: dict[uuid.UUID, Decimal] = {}
    if job_ids:
        totals = await session.execute(
            select(JobStep.job_id, func.sum(JobStep.cost_gbp))
            .where(JobStep.job_id.in_(job_ids))
            .group_by(JobStep.job_id)
        )
        spend_by_job = {
            job_id: Decimal(total) for job_id, total in totals.tuples() if total is not None
        }

    groups: dict[str, dict[str, Any]] = {}
    for report, req in rows:
        label = f"{req.company_name} ({req.ticker})"
        group = groups.setdefault(label, {"label": label, "company_id": None, "reports": []})
        if report.company_id is not None:
            group["company_id"] = report.company_id
        group["reports"].append(
            {
                "report": report,
                "request": req,
                "spend_display": (
                    figures.pounds(spend_by_job[report.job_id])
                    if report.job_id in spend_by_job
                    else None
                ),
            }
        )

    page: Response = render(
        request,
        "reports/index.html",
        {
            "groups": list(groups.values()),
            "company_filter": company_filter,
            "total": len(rows),
        },
    )
    return page


@router.get("/knowledge", response_class=HTMLResponse, summary="What the platform knows")
async def knowledge_page(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,  # noqa: ARG001 -- authentication; the graph is the installation's
) -> Response:
    """The knowledge graph measured: size, shape, coverage, freshness, vault health.

    Not scoped to the signed-in account, deliberately. The graph is built from every
    approved report in the database, and a per-user view of a shared graph would report a
    connectivity that does not exist.
    """
    stats = await knowledge_stats(session, settings=settings)

    stale = len(stats.freshness.stale)
    windows = len(stats.freshness.closed_windows)
    if not stats.size.approved_reports:
        lead = verdicts.sentence(
            [
                "knowledge is empty; approved research reports create companies, claims, "
                "themes and relations here"
            ],
            when_none="Knowledge is empty",
            tone=vocabulary.Tone.MUTED,
        )
    elif stale or windows:
        lead = verdicts.sentence(
            [
                verdicts.Count(
                    stale, "company has stale research", "companies have stale research"
                ),
                verdicts.Count(
                    windows,
                    "closed catalyst window needs its outcome recorded",
                    "closed catalyst windows need their outcomes recorded",
                ),
            ],
            when_none="Useful and current",
            tone=vocabulary.Tone.WARNING,
        )
    else:
        lead = verdicts.sentence(
            [
                "current enough to use: no catalyst window awaits an outcome and nothing "
                "covered has gone stale"
            ],
            when_none="Current enough to use",
            tone=vocabulary.Tone.SUCCESS,
        )

    page: Response = render(request, "knowledge/index.html", {"stats": stats, "verdict": lead})
    return page


@router.get("/knowledge/graph", response_class=HTMLResponse, summary="The knowledge graph, drawn")
async def knowledge_graph_page(
    request: Request,
    session: DbSession,
    user: CurrentUser,  # noqa: ARG001 -- authentication; the graph is the installation's
) -> Response:
    """The in-app picture (K4b): confirmed relations laid out server-side as static SVG.

    Unscoped for the same reason the measurements are, and drawn entirely in Python — the
    page carries coordinates, not a script, so what the browser shows is exactly what the
    rows say.
    """
    picture = await graph_picture(session)
    page: Response = render(request, "knowledge/graph.html", {"picture": picture})
    return page


@router.get(
    "/companies/{company_id}", response_class=HTMLResponse, summary="A company's research history"
)
async def company_page(
    request: Request,
    company_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The section 2.7 page: timeline, valuation history, prior catalysts and what happened.

    Approved reports only — the history a decision could rest on. The valuation chart is
    the deterministic exportable builder salted with the company id, so the page shows
    the same bytes on every load.
    """
    company = await history_service.company_for_user(
        session, company_id=company_id, user_id=user.id
    )
    if company is None:
        return _problem(request, f"No company {company_id}.", status=HTTP_404_NOT_FOUND)

    views = await history_service.valuation_history_for(session, company_id=company.id)

    chart = valuation_history(
        ValuationHistoryInput(
            currency=next(
                (view.valuation_currency for view in views if view.valuation_currency), ""
            ),
            points=tuple(
                ValuationRangePoint(
                    as_of=view.as_of_date,
                    low=Decimal(view.valuation_low),
                    high=Decimal(view.valuation_high),
                )
                for view in views
                if view.valuation_low is not None and view.valuation_high is not None
            ),
        ),
        hashsalt=str(company.id),
    )

    today = datetime.now(UTC).date()
    catalyst_rows: list[Any] = []
    for view in reversed(views):  # newest report's catalysts first
        prior = await session.get(Report, view.report_id)
        if prior is None:  # pragma: no cover -- the view was built from this row
            continue
        catalyst_rows.extend(
            await history_service.catalyst_outcomes_for(session, prior=prior, as_of=today)
        )

    resolutions = await catalyst_service.resolutions_for(session, company_id=company.id)
    # The labels a resolution may attach to: passed or undated windows nobody has
    # answered yet. Pending ones wait — resolving a window that has not closed would be
    # recording the future.
    unresolved = sorted(
        {
            outcome.label
            for outcome in catalyst_rows
            if outcome.status != "pending" and outcome.label not in resolutions
        }
    )
    timeline = list(reversed(views))
    # The wording avoids "as of": that phrase is the timeline link's, and a page test pins
    # it appearing exactly once per approved report.
    if not timeline:
        lead = verdicts.sentence(
            [
                "no approved view exists for this company yet; drafts and rejected runs do "
                "not appear here"
            ],
            when_none="No approved view exists yet",
            tone=vocabulary.Tone.MUTED,
        )
    else:
        newest = timeline[0]
        clauses: list[verdicts.Count | str] = [
            f"the last approved view is {newest.rating or 'no view reached'}, "
            f"{newest.valuation_range}, dated {newest.as_of_date.isoformat()}",
            verdicts.Count(
                len(unresolved),
                "catalyst window has since closed and needs its outcome recorded",
                "catalyst windows have since closed and need their outcomes recorded",
            ),
        ]
        lead = verdicts.sentence(
            clauses,
            when_none="One approved view exists",
            tone=vocabulary.Tone.WARNING if unresolved else vocabulary.Tone.INFO,
        )

    token = new_csrf_token(settings)
    page: Response = render(
        request,
        "companies/detail.html",
        {
            "company": company,
            "verdict": lead,
            "timeline": timeline,
            "chart_uri": svg_data_uri(chart.svg),
            "chart_caption": chart.caption,
            "chart_is_placeholder": chart.placeholder,
            "catalyst_outcomes": catalyst_rows,
            "resolutions": resolutions,
            "unresolved_labels": unresolved,
            "outcome_kinds": list(CatalystOutcomeKind),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(page, token)
    return page


@router.post(
    "/companies/{company_id}/catalyst-resolutions",
    response_class=HTMLResponse,
    summary="Record what happened to a catalyst",
)
async def resolve_catalyst(
    request: Request,
    company_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The operator's answer to a closed window (K4). Never a model's.

    The service validates everything that matters — the label must name a catalyst an
    approved report proposed, the reason must not be blank — so this route decides
    nothing beyond ownership and the CSRF token, the export form's own division.
    """
    company = await history_service.company_for_user(
        session, company_id=company_id, user_id=user.id
    )
    if company is None:
        return _problem(request, f"No company {company_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was recorded.",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        outcome = CatalystOutcomeKind(submitted.get("outcome", ""))
    except ValueError:
        return _problem(
            request,
            "The outcome must be one of: occurred, did not occur, superseded.",
            status=HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        await catalyst_service.record_catalyst_resolution(
            session,
            company_id=company.id,
            label=submitted.get("label", ""),
            outcome=outcome,
            reason=submitted.get("reason", ""),
            actor=user,
        )
    except ValidationError as exc:
        return _problem(request, exc.message, status=HTTP_422_UNPROCESSABLE_CONTENT)

    await session.commit()
    return RedirectResponse(f"/companies/{company_id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/reports/{report_id}", response_class=HTMLResponse, summary="A finished report")
async def report_detail(
    request: Request,
    report_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The report as approved, with its hash and a link to the archived bytes."""
    report = await session.scalar(
        select(Report)
        .join(WorkOrder, WorkOrder.id == Report.request_id)
        .where(Report.id == report_id, WorkOrder.user_id == user.id)
    )
    if report is None:
        return _problem(request, f"No report {report_id}.", status=HTTP_404_NOT_FOUND)

    content: dict[str, Any] = dict(report.content or {})
    research_request = await session.get(ResearchRequest, report.request_id)
    exports = list(
        await session.scalars(
            select(ObsidianExport)
            .where(ObsidianExport.report_id == report.id)
            .order_by(ObsidianExport.exported_at.desc())
        )
    )

    token = new_csrf_token(settings)
    detail: Response = render(
        request,
        "reports/detail.html",
        {
            "report": report,
            "research_request": research_request,
            "markdown": str(content.get("markdown", "")),
            "section_keys": list(content.get("sections", [])),
            "exports": exports,
            "vault_configured": settings.obsidian_vault_root is not None,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(detail, token)
    return detail


@router.post("/reports/{report_id}/export-obsidian", summary="Export a report to the vault")
async def export_obsidian_page(
    request: Request,
    report_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Project the approved report into the vault, on explicit request.

    Nothing exports automatically; this form and the CLI are the only two doors, and the
    exporter itself re-checks every rule — approval, containment, the reserved personal
    tree — so this route decides nothing beyond ownership and the CSRF token.
    """
    report = await session.scalar(
        select(Report)
        .join(WorkOrder, WorkOrder.id == Report.request_id)
        .where(Report.id == report_id, WorkOrder.user_id == user.id)
    )
    if report is None:
        return _problem(request, f"No report {report_id}.", status=HTTP_404_NOT_FOUND)

    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem(
            request,
            "This form's security token was missing or had expired. Nothing was exported.",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        await export_report(session, settings=settings, report_id=report.id)
    except (ObsidianExportError, VaultWriteError) as exc:
        return _problem(request, exc.message, status=HTTP_422_UNPROCESSABLE_CONTENT)

    await session.commit()
    return RedirectResponse(f"/reports/{report_id}", status_code=HTTP_303_SEE_OTHER)


@router.get(
    "/reports/{report_id}/preview",
    response_class=HTMLResponse,
    summary="A finished report, as the document",
)
async def report_preview(
    request: Request,
    report_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """The report's HTML notation, carrying the view and date the report row recorded.

    Re-assembled from the run's stored rows rather than replayed from an archive — the
    archived Markdown remains the hashed record of what was approved, and task 48 will
    archive these bytes too, at which point the stored HTML becomes the PDF's input. Until
    then this is a reading surface, and the row's ``created_at`` is stamped on it so the
    document carries the date it was produced rather than the date it was viewed.
    """
    report = await session.scalar(
        select(Report)
        .join(WorkOrder, WorkOrder.id == Report.request_id)
        .where(Report.id == report_id, WorkOrder.user_id == user.id)
    )
    if report is None:
        return _problem(request, f"No report {report_id}.", status=HTTP_404_NOT_FOUND)

    job = await session.get(Job, report.job_id)
    research_request = await session.get(ResearchRequest, report.request_id)
    if job is None or research_request is None:  # pragma: no cover -- FK-guaranteed rows
        return _problem(request, f"No report {report_id}.", status=HTTP_404_NOT_FOUND)

    company = (
        await session.get(Company, report.company_id) if report.company_id is not None else None
    )
    comps = await comps_note_for(session, job=job, request=research_request)
    document = await assemble_document(
        session,
        job=job,
        request=research_request,
        company=company,
        sector=await sector_note_for(session, job=job),
        comps=comps,
        style=await configuration.effective_house_style(session),
        charts=await exportable_charts_for(
            session,
            job=job,
            request=research_request,
            licence_note=comps.licence_note if comps else "",
        ),
        rating=report.rating,
        confidence=report.confidence,
        generated_at=report.created_at,
    )
    return HTMLResponse(render_html(document))


# -- Internals ---------------------------------------------------------------------------


async def _owned_job(session: AsyncSession, *, job_id: uuid.UUID, user: Any) -> Job | None:
    """The run, if it belongs to this user.

    ``None`` for both "does not exist" and "is not yours", for the same reason the JSON API
    returns one status for both: distinguishing them lets a caller enumerate which ids
    exist by watching which ones answer differently.
    """
    job: Job | None = await session.scalar(
        select(Job)
        .join(WorkOrder, WorkOrder.id == Job.work_order_id)
        .where(Job.id == job_id, WorkOrder.user_id == user.id)
    )
    return job


async def _claim_is_visible(
    session: AsyncSession, *, claim_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Whether this claim belongs to a run of the asking user's own request.

    Mirrors the JSON API's check rather than sharing it, because the two answer differently
    — this one renders a problem page and that one raises. What they must not differ on is
    *who* may see a claim, which is why both are one query against the same join.
    """
    owner = await session.scalar(
        select(WorkOrder.user_id)
        .join(Job, Job.work_order_id == WorkOrder.id)
        .join(ReportSection, ReportSection.job_id == Job.id)
        .join(Claim, Claim.report_section_id == ReportSection.id)
        .where(Claim.id == claim_id)
    )
    return owner == user_id


async def _step_output(
    session: AsyncSession, *, job_id: uuid.UUID, step_key: str
) -> dict[str, Any] | None:
    """What a step recorded, from the step row rather than from a re-run.

    The latest attempt, because a retried step's earlier attempt describes a state the run
    is no longer in. ``None`` when the step has not completed.
    """
    output: dict[str, Any] | None = await session.scalar(
        select(JobStep.output_ref)
        .where(JobStep.job_id == job_id, JobStep.step_key == step_key)
        .order_by(JobStep.attempt.desc())
        .limit(1)
    )
    return output


def _extraction_counts(produced: Mapping[str, Any]) -> dict[str, int]:
    """Context for the financials gate, beside the figure it actually approves.

    **"0 facts written" is true and reads as a disaster.** Facts are deduplicated on the
    observation itself, so the second run of a company supplies eighteen thousand and
    inserts none — and the gate said ``0``, which any reader takes for a failed extraction
    rather than for a cache hit. What the operator is judging is whether the extraction is
    sound, and that question is answered by how many facts the filing carries, with the
    insert count as the footnote it always was.

    Read from the step's own output rather than added to the gate payload, because the
    payload is hashed and an approval is an approval *of that hash*. Context beside it is
    free; a new key inside it would refuse every approval of a plan gated before the change.
    """
    available = int(produced.get("facts_chosen", 0))
    written = int(produced.get("facts_written", 0))
    return {
        "available": available,
        "already_held": max(0, available - written),
        "look_ahead": int(produced.get("rejected_for_look_ahead", 0)),
    }


async def _payload_for(session: AsyncSession, *, job: Job, gate: GateKind) -> dict[str, Any]:
    """Exactly what this run's workflow puts at that gate.

    Through the registry rather than by importing a builder, so a page rendering an
    approval does not have to know which workflow raised it. A run whose workflow this
    build no longer has renders an empty payload rather than raising: the page's job is to
    show what was approved, and "this build cannot read that workflow" is a better answer
    than a 500 (ADR 0071).
    """
    try:
        builder = resolve_workflow(job.workflow_version).gate_payload()
    except WorkflowRegistryError:
        return {}
    return dict(await builder(session, job=job, gate=gate.value))


def _problem(request: Request, message: str, *, status: int) -> Response:
    response: Response = render(
        request, "runs/problem.html", {"message": message}, status_code=status
    )
    return response
