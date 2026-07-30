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

import uuid
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_CONTENT,
)

from aer.api.deps import CurrentUser, DbSession, RedisClient, SettingsDep
from aer.core.enums import Decision, GateKind, JobStatus
from aer.db.models import (
    Claim,
    Company,
    Job,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchRequest,
)
from aer.errors import ConflictError, ValidationError
from aer.queue import enqueue_run
from aer.render.markdown import render_markdown
from aer.services import approvals as approval_service
from aer.services import cancellation as cancellation_service
from aer.services import provenance
from aer.services import runs as run_service
from aer.services.approvals import payload_hash_for
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.templating import render
from aer.workflow.workflows.vertical_slice_v1 import final_gate_payload, plan_gate_payload

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

    payload = plan_gate_payload(plan)
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

    decided = await _decision_for(session, job_id=job_id, gate=GateKind.FINAL)
    token = new_csrf_token(settings)

    response: Response = render(
        request,
        "runs/review.html",
        {
            "job": job,
            "sections": payload["sections"],
            "escalations": payload["escalations"],
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


@router.get("/reports/{report_id}", response_class=HTMLResponse, summary="A finished report")
async def report_detail(
    request: Request,
    report_id: uuid.UUID,
    session: DbSession,
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

    detail: Response = render(
        request,
        "reports/detail.html",
        {
            "report": report,
            "research_request": research_request,
            "markdown": str(content.get("markdown", "")),
            "section_keys": list(content.get("sections", [])),
        },
    )
    return detail


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
