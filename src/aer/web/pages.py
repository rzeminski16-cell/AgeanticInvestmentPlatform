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
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
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
from aer.calc.comps import MULTIPLE_DEFINITIONS, CompsTable
from aer.charts import (
    ValuationHistoryInput,
    ValuationRangePoint,
    svg_data_uri,
    valuation_history,
)
from aer.config import Settings
from aer.core.enums import Decision, GateKind, JobStatus
from aer.core.escalation import COST_ALERT_RATIO
from aer.db.models import (
    Calculation,
    Claim,
    Company,
    Job,
    JobStep,
    ObsidianExport,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
)
from aer.errors import ConflictError, ValidationError
from aer.obsidian import ObsidianExportError, VaultWriteError, export_report
from aer.queue import enqueue_run
from aer.render.document import UnresolvedFootnote, assemble_document
from aer.render.html import render_html
from aer.render.markdown import render_markdown
from aer.render.summary import summary_document
from aer.services import approvals as approval_service
from aer.services import calculations as calculation_service
from aer.services import cancellation as cancellation_service
from aer.services import configuration, provenance
from aer.services import history as history_service
from aer.services import runs as run_service
from aer.services.approvals import payload_hash_for
from aer.services.comps import (
    PEER_SET_STEP,
    peer_set_payload,
    peer_set_required,
)
from aer.services.comps_run import grouped_exclusions
from aer.services.disagreements import disagreements_for_job
from aer.services.escalation import cost_scene_for_job
from aer.services.evaluations import evaluations_for_job, section_coverage_for_job
from aer.services.exhibits import exportable_charts_for, internal_charts_for
from aer.services.run_replay import replay_run
from aer.services.sectors import (
    CLASSIFY_STEP,
    classification_payload,
    sector_gate_required,
)
from aer.services.spend import recent_runs, spend_by_role, spend_summary
from aer.services.valuation_view import lineage_rows, valuation_view
from aer.skills.resolution import pinned_skills_for_plan
from aer.storage.local import LocalArtefactStore
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render
from aer.workflow.workflows.vertical_slice_v1 import (
    ASSUMPTIONS_STEP,
    assumptions_gate_payload,
    assumptions_gate_required,
    comps_note_for,
    final_gate_payload,
    plan_gate_payload,
    sector_note_for,
    unmapped_gate_payload,
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
    if found is None or found.user_id != user.id:
        return _problem(request, f"No research request {request_id}.", status=HTTP_404_NOT_FOUND)

    job = await run_service.start_run(session, request=found)
    await session.commit()
    await enqueue_run(redis, job.id)

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
    research_request = await session.get(ResearchRequest, job.request_id)
    report = await session.scalar(select(Report).where(Report.job_id == job_id))
    pending = await approval_service.pending_gate(session, job)
    cancellation = await cancellation_service.cancellation_for(session, job_id=job_id)

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "runs/console.html",
        {
            "job": job,
            "research_request": research_request,
            "cancellation": cancellation,
            "can_cancel": job.status not in cancellation_service.TERMINAL_STATUSES,
            "state": state.as_dict(),
            "steps": state.steps,
            "spend_gbp": state.spend_gbp,
            "is_terminal": state.is_terminal,
            "awaiting": job.status is JobStatus.AWAITING_APPROVAL,
            "budget_exceeded": job.status is JobStatus.BUDGET_EXCEEDED,
            "budget_scope": state.budget_scope,
            "pending_gate": pending.value if pending else None,
            "report_id": str(report.id) if report else None,
            "poll_seconds": POLL_SECONDS,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


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
        .where(ResearchPlan.request_id == job.request_id)
        .order_by(ResearchPlan.created_at.desc())
    )
    if plan is None:
        return _problem(
            request,
            "This run has not produced a plan yet. There is nothing to approve.",
            status=HTTP_404_NOT_FOUND,
        )

    pins = await pinned_skills_for_plan(session, plan_id=plan.id)
    payload = plan_gate_payload(plan, pins)
    decided = await _decision_for(session, job_id=job_id, gate=GateKind.PLAN)
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
            "decided": decided,
            "gate": GateKind.PLAN.value,
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

    payload = unmapped_gate_payload(produced)
    decided = await _decision_for(session, job_id=job_id, gate=GateKind.UK_FINANCIALS)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/financials.html",
        {
            "job": job,
            "payload": payload,
            "counts": _extraction_counts(produced),
            "payload_hash": payload_hash_for(payload),
            "decided": decided,
            "gate": GateKind.UK_FINANCIALS.value,
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
    decided = await _decision_for(session, job_id=job_id, gate=GateKind.SECTOR_SPECIALIST)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/sector.html",
        {
            "job": job,
            "payload": payload,
            "payload_hash": payload_hash_for(payload),
            "decided": decided,
            "gate": GateKind.SECTOR_SPECIALIST.value,
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
        return _problem(
            request,
            "This run proposed no comparable companies, so this gate does not apply to it. "
            "No comparables table will be produced and the report says so." + tried,
            status=HTTP_404_NOT_FOUND,
        )

    payload = peer_set_payload(produced)
    decided = await _decision_for(session, job_id=job_id, gate=GateKind.PEER_SET)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/peers.html",
        {
            "job": job,
            "payload": payload,
            "payload_hash": payload_hash_for(payload),
            "refused": [item for item in produced.get("refused", []) if isinstance(item, dict)],
            "decided": decided,
            "gate": GateKind.PEER_SET.value,
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

    **The gate shipped without this page.** The workflow paused, the console showed the
    banner, and every review link it offered belonged to a different gate — so a run that
    stopped here could not be cleared from a browser at all. A gate an operator cannot
    clear is a run that pauses and never resumes.
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

    payload = assumptions_gate_payload(produced)
    decided = await _decision_for(session, job_id=job_id, gate=GateKind.ASSUMPTIONS)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/assumptions.html",
        {
            "job": job,
            "payload": payload,
            "payload_hash": payload_hash_for(payload),
            "decided": decided,
            "gate": GateKind.ASSUMPTIONS.value,
            # Where an operator goes to change one before agreeing to the set. The
            # assumptions surface is per-request, which is why this page links out to it
            # rather than editing in place.
            "request_id": str(job.request_id),
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

    payload = await final_gate_payload(session, job_id=job_id)
    # Rows exist from the moment a plan is approved; content arrives only when the draft
    # step runs. Testing for rows rather than for content would show an empty document and
    # invite an approval of nothing.
    if not any(section["content"] for section in payload["sections"]):
        return _problem(
            request,
            "This run has drafted nothing yet. There is nothing to approve.",
            status=HTTP_404_NOT_FOUND,
        )

    research_request = await session.get(ResearchRequest, job.request_id)
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
    disagreements = await disagreements_for_job(session, job.id)
    calculations = list(
        await session.scalars(
            select(Calculation)
            .where(Calculation.job_id == job.id)
            .order_by(Calculation.created_at, Calculation.id)
        )
    )
    cost = await cost_scene_for_job(session, job=job, request=research_request)

    decided = await _decision_for(session, job_id=job_id, gate=GateKind.FINAL)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/review.html",
        {
            "job": job,
            "sections": payload["sections"],
            "escalations": payload["escalations"],
            "triggers": payload["triggers"],
            "evaluations": evaluations,
            "coverage": coverage,
            "disagreements": disagreements,
            "calculations": calculations,
            "cost": cost,
            "cost_alert_gbp": cost.cap_gbp * COST_ALERT_RATIO,
            "markdown": preview.markdown,
            "footnote_count": preview.footnote_count,
            "payload_hash": payload_hash_for(payload),
            "decided": decided,
            "gate": GateKind.FINAL.value,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(response, token)
    return response


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

    research_request = await session.get(ResearchRequest, job.request_id)
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

    research_request = await session.get(ResearchRequest, job.request_id)
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

    page: Response = render(request, "runs/replay.html", {"job": job, "report": report})
    return page


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
    research_request = await session.get(ResearchRequest, job.request_id)

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
    research_request = await session.get(ResearchRequest, job.request_id)

    page: Response = render(
        request,
        "runs/claims.html",
        {
            "job": job,
            "research_request": research_request,
            "claims": claims,
            "unsupported": sum(1 for claim in claims if not claim.is_supported),
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

    page: Response = render(request, "claims/detail.html", {"claim": view})
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
    research_request = await session.get(ResearchRequest, job.request_id)
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
    research_request = await session.get(ResearchRequest, job.request_id)

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

    page: Response = render(
        request,
        "calculations/detail.html",
        {
            "calculation": calculation,
            "lineage": lineage_rows(tree),
            "request_id": job.request_id,
            "job_id": job.id,
            "back_href": f"/runs/{job.id}/valuation",
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
        .where(ResearchRequest.user_id == user.id)
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

    groups: dict[str, dict[str, Any]] = {}
    for report, req in rows:
        label = f"{req.company_name} ({req.ticker})"
        group = groups.setdefault(label, {"label": label, "company_id": None, "reports": []})
        if report.company_id is not None:
            group["company_id"] = report.company_id
        group["reports"].append({"report": report, "request": req})

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


@router.get(
    "/companies/{company_id}", response_class=HTMLResponse, summary="A company's research history"
)
async def company_page(
    request: Request,
    company_id: uuid.UUID,
    session: DbSession,
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

    page: Response = render(
        request,
        "companies/detail.html",
        {
            "company": company,
            "timeline": list(reversed(views)),
            "chart_uri": svg_data_uri(chart.svg),
            "chart_caption": chart.caption,
            "chart_is_placeholder": chart.placeholder,
            "catalyst_outcomes": catalyst_rows,
        },
    )
    return page


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
        .join(ResearchRequest, ResearchRequest.id == Report.request_id)
        .where(Report.id == report_id, ResearchRequest.user_id == user.id)
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
        .join(ResearchRequest, ResearchRequest.id == Report.request_id)
        .where(Report.id == report_id, ResearchRequest.user_id == user.id)
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
        .join(ResearchRequest, ResearchRequest.id == Report.request_id)
        .where(Report.id == report_id, ResearchRequest.user_id == user.id)
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
        .join(ResearchRequest, ResearchRequest.id == Job.request_id)
        .where(Job.id == job_id, ResearchRequest.user_id == user.id)
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
        select(ResearchRequest.user_id)
        .join(Job, Job.request_id == ResearchRequest.id)
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


async def _decision_for(session: AsyncSession, *, job_id: uuid.UUID, gate: GateKind) -> str | None:
    """What was already decided at this gate, if anything.

    Drives the page's read-only state. A gate that has been decided shows the decision
    rather than a live form, because the service will refuse a second one and offering a
    button that cannot work is worse than offering none.
    """
    decisions = await approval_service.approvals_for_job(session, job_id)
    for approval in decisions:
        if approval.gate is gate:
            return approval.decision.value
    return None


def _problem(request: Request, message: str, *, status: int) -> Response:
    response: Response = render(
        request, "runs/problem.html", {"message": message}, status_code=status
    )
    return response
