"""Server-rendered pages.

Every page here calls the same service functions the JSON API calls. Nothing in this
module decides whether a request is valid — that would be a second implementation of the
rules, and the copy that drifts is always the one attached to the form.

**The form works without JavaScript.** A plain ``POST`` followed by a redirect is the
real path; HTMX only changes where the response is rendered. That is not nostalgia: a
form whose validation depends on a script is a form that silently accepts anything the
moment the script fails to load, and this one commissions spending.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_CONTENT,
)

from aer.api.deps import (
    CurrentUser,
    DbSession,
    RedisClient,
    SettingsDep,
    current_user_or_none,
)
from aer.api.routes.assumptions import ProposeRequest, assumptions_payload
from aer.core.assumption_scales import UNIT_CHOICES
from aer.core.enums import AnalysisMode
from aer.core.schemas.request import (
    SUPPORTED_CURRENCIES,
    EsgSensitivity,
    FieldProblem,
    RiskTolerance,
)
from aer.core.universe import SUPPORTED_EXCHANGES
from aer.db.models import Assumption, Report
from aer.errors import ConflictError, ValidationError
from aer.services import assumptions as assumption_service
from aer.services import requests as request_service
from aer.services import runs as run_service
from aer.services import scenarios as scenario_service
from aer.services.approvals import payload_hash_for
from aer.services.assumption_gate import outstanding_for
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.forms import ParsedForm, form_values_from, parse_request_form
from aer.web.shell import GUIDANCE_COOKIE
from aer.web.shell.badges import cached_counts_for
from aer.web.templating import render
from aer.workflow.workflows.vertical_slice_v1 import FORECAST_YEARS

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

_log = structlog.get_logger("aer.web.routes")

# Offered in the exchange select. Sorted so the order is stable between renders rather
# than following set iteration order, which would reshuffle the dropdown on every restart.
_EXCHANGE_CHOICES = sorted(SUPPORTED_EXCHANGES)
_CURRENCY_CHOICES = sorted(SUPPORTED_CURRENCIES)


@dataclass(frozen=True, slots=True)
class _FormPage:
    """Which of the two form pages a submission belongs to.

    Carried around so that re-rendering a failed submission lands back on the page it came
    from. Without it, a rejected edit would re-render as the "new request" form and the
    operator's next submission would silently create a second request instead of fixing the
    one they were editing.
    """

    template: str
    action: str
    submit_label: str
    cancel_href: str
    error_summary_heading: str
    extra: dict[str, Any] = field(default_factory=dict)


_NEW_PAGE = _FormPage(
    template="requests/new.html",
    action="/requests/new",
    submit_label="Save draft request",
    cancel_href="/requests",
    error_summary_heading="This request was not created",
)


def _edit_page(item: Any) -> _FormPage:
    return _FormPage(
        template="requests/edit.html",
        action=f"/requests/{item.id}/edit",
        submit_label="Save changes",
        cancel_href=f"/requests/{item.id}",
        error_summary_heading="This request was not saved",
        extra={"item": item},
    )


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _form_context(
    page: _FormPage,
    *,
    csrf_token: str,
    parsed: ParsedForm | None = None,
    banner: str | None = None,
    oob_csrf: bool = False,
    values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Everything ``requests/_form.html`` needs, for both the new and the edit page.

    One builder rather than two, so the two pages cannot end up offering different exchange
    lists or different currency options. Only what genuinely differs between them lives in
    the :class:`_FormPage`.
    """
    return {
        "csrf_field": CSRF_FIELD_NAME,
        "csrf_token": csrf_token,
        "oob_csrf": oob_csrf,
        "exchanges": _EXCHANGE_CHOICES,
        "currencies": _CURRENCY_CHOICES,
        "analysis_modes": list(AnalysisMode),
        "risk_tolerances": list(RiskTolerance),
        "esg_sensitivities": list(EsgSensitivity),
        "today": datetime.now(UTC).date().isoformat(),
        "errors": parsed.errors if parsed else {},
        # A rejected submission wins over the stored row: re-rendering the saved values
        # would throw away everything the operator just typed and silently undo the edit
        # they were trying to make.
        "values": parsed.values if parsed else (values or {}),
        "banner": banner,
        "form_action": page.action,
        "submit_label": page.submit_label,
        "cancel_href": page.cancel_href,
        "error_summary_heading": page.error_summary_heading,
        **page.extra,
    }


@router.get("/requests", response_class=HTMLResponse, summary="Your research requests")
async def list_requests_page(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    archived: bool = False,
) -> Response:
    rows = await request_service.list_requests(
        session, user_id=user.id, limit=200, archived=archived
    )
    # The other list's size, so the link to it can say how many are over there. A link to an
    # empty page is a link nobody should have been offered.
    counterpart = await request_service.count_requests(
        session, user_id=user.id, archived=not archived
    )

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "requests/list.html",
        {
            "requests": rows,
            "archived": archived,
            "counterpart": counterpart,
            "csrf_token": token,
            "csrf_field": CSRF_FIELD_NAME,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/requests/new", response_class=HTMLResponse, summary="New research request")
async def new_request_form(request: Request, settings: SettingsDep) -> Response:
    token = new_csrf_token(settings)
    response: Response = render(
        request, _NEW_PAGE.template, _form_context(_NEW_PAGE, csrf_token=token)
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/requests/new", summary="Submit a research request")
async def submit_request_form(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Validate and create, then redirect to the new request.

    Returns the same page with inline errors on failure, so nothing the operator typed is
    lost. When the submission came from HTMX only the error fragment is re-rendered; the
    validation performed is identical either way.
    """
    submitted = await _submitted_values(request)

    rejected = _csrf_failure(request, settings, submitted, page=_NEW_PAGE)
    if rejected is not None:
        return rejected

    parsed = parse_request_form(submitted)
    if parsed.payload is not None:
        try:
            created = await request_service.create_request(
                session,
                user=user,
                payload=parsed.payload,
                limits=request_service.limits_from(settings),
            )
        except ValidationError as exc:
            parsed.add_problems(_problems_from(exc))
        else:
            await session.commit()
            return _go_to(request, f"/requests/{created.id}")

    return _render_failure(
        request, settings, parsed, page=_NEW_PAGE, status=HTTP_422_UNPROCESSABLE_CONTENT
    )


@router.get(
    "/requests/{request_id}/edit", response_class=HTMLResponse, summary="Edit a draft request"
)
async def edit_request_form(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The same form as ``/requests/new``, prefilled from the stored row.

    Refused once a run exists, with the reason on the page rather than a bare 409: an
    operator who followed a stale bookmark needs to know that the request is now a record
    of something that happened, not that a route disagreed with them.
    """
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    reason = await request_service.immutable_reason(session, request=found)
    if reason is not None:
        return _immutable(request, item=found, reason=reason, verb="edited")

    page = _edit_page(found)
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        page.template,
        _form_context(page, csrf_token=token, values=form_values_from(found)),
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/requests/{request_id}/edit", summary="Save an edited request")
async def submit_edit_form(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Validate and save, then redirect back to the request.

    Exactly the shape of the create handler, calling the same validation through the same
    service. The only difference is which service function it ends in.
    """
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    page = _edit_page(found)
    submitted = await _submitted_values(request)

    rejected = _csrf_failure(request, settings, submitted, page=page)
    if rejected is not None:
        return rejected

    parsed = parse_request_form(submitted)
    if parsed.payload is not None:
        try:
            await request_service.update_request(
                session,
                request=found,
                actor=user,
                payload=parsed.payload,
                limits=request_service.limits_from(settings),
            )
        except ConflictError as exc:
            # A run was started between loading the form and submitting it. The edit is
            # gone either way; saying so beats re-rendering a form that will keep failing.
            return _immutable(request, item=found, reason=exc.message, verb="edited")
        except ValidationError as exc:
            parsed.add_problems(_problems_from(exc))
        else:
            await session.commit()
            return _go_to(request, f"/requests/{found.id}")

    return _render_failure(
        request, settings, parsed, page=page, status=HTTP_422_UNPROCESSABLE_CONTENT
    )


@router.post("/requests/{request_id}/delete", summary="Delete a draft request")
async def delete_request_action(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Delete a draft request that has never been run, then return to the list.

    A POST, and CSRF-checked, because it destroys something. Refused outright once a run
    exists — see :func:`aer.services.requests.delete_request` for why that guard is in the
    service rather than only on this button.
    """
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    form = await request.form()
    token = form.get(CSRF_FIELD_NAME)
    if not csrf_is_valid(request, str(token) if isinstance(token, str) else None, settings):
        return _immutable(
            request,
            item=found,
            reason=(
                "This page's security token was missing or had expired, so nothing was "
                "deleted. Reload the request and try again."
            ),
            verb="deleted",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        await request_service.delete_request(session, request=found, actor=user)
    except ConflictError as exc:
        return _immutable(request, item=found, reason=exc.message, verb="deleted")

    await session.commit()
    return _go_to(request, "/requests")


# -- Getting a request out of the way, and destroying one ----------------------------------
#
# Two controls, and the asymmetry between them is the design. Archiving is one click with no
# dialogue, because it is reversible by one more click. Purging gets its own page, listing
# what will be destroyed by table before anything happens — a destructive action whose
# confirmation says only "are you sure?" is asking the operator to agree to a number nobody
# has shown them.


@router.post("/requests/{request_id}/archive", summary="Archive a request")
async def archive_request_action(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Hide a request from the list. Accepted whatever state it is in — nothing is lost."""
    return await _set_archived(
        request, request_id, session, settings, user, archive=True, verb="archived"
    )


@router.post("/requests/{request_id}/restore", summary="Restore an archived request")
async def restore_request_action(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    return await _set_archived(
        request, request_id, session, settings, user, archive=False, verb="restored"
    )


async def _set_archived(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    *,
    archive: bool,
    verb: str,
) -> Response:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    if not await _csrf_ok(request, settings):
        return _immutable(
            request,
            item=found,
            reason=(
                f"This page's security token was missing or had expired, so nothing was "
                f"{verb}. Reload the list and try again."
            ),
            verb=verb,
            status=HTTP_403_FORBIDDEN,
        )

    change = request_service.archive_request if archive else request_service.restore_request
    try:
        await change(session, request=found, actor=user)
    except ConflictError as exc:
        return _immutable(request, item=found, reason=exc.message, verb=verb)

    await session.commit()
    # Back to the list the operator was looking at. Archiving from the archive view would be
    # odd, but restoring from it is the normal path and dumping them on the live list would
    # lose their place.
    return _go_to(request, "/requests?archived=1" if not archive else "/requests")


@router.get(
    "/requests/{request_id}/remove",
    response_class=HTMLResponse,
    summary="Confirm destroying a request",
)
async def confirm_removal(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The confirmation page, listing exactly what would be destroyed.

    A page rather than a browser dialogue, because the counts have to be read before the
    decision and a `confirm()` box cannot hold them.
    """
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    token = new_csrf_token(settings)
    response: Response = render(
        request,
        "requests/remove.html",
        {
            "item": found,
            "removed": await request_service.removal_preview(session, request=found),
            "spend": await request_service.request_spend(session, request=found),
            "csrf_token": token,
            "csrf_field": CSRF_FIELD_NAME,
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/requests/{request_id}/remove", summary="Destroy a request and its research")
async def remove_request_action(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Irreversible. The audit chain, the spend ledger and the artefacts survive."""
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    if not await _csrf_ok(request, settings):
        return _immutable(
            request,
            item=found,
            reason=(
                "This page's security token was missing or had expired, so nothing was "
                "removed. Reload the page and try again."
            ),
            verb="removed",
            status=HTTP_403_FORBIDDEN,
        )

    try:
        await request_service.purge_request(session, request=found, actor=user)
    except ConflictError as exc:
        return _immutable(request, item=found, reason=exc.message, verb="removed")

    await session.commit()
    return _go_to(request, "/requests")


async def _csrf_ok(request: Request, settings: SettingsDep) -> bool:
    """Whether the submitted form carried a valid token."""
    form = await request.form()
    token = form.get(CSRF_FIELD_NAME)
    return csrf_is_valid(request, str(token) if isinstance(token, str) else None, settings)


@router.get(
    "/requests/{request_id}", response_class=HTMLResponse, summary="Research request detail"
)
async def request_detail(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    # A run already exists, or there is a button to start one. Both are the same page: the
    # operator's question is "where is this up to?", and the answer differs only in whether
    # anything has started.
    job = await run_service.latest_run(session, request_id=request_id)
    report = (
        await session.scalar(select(Report.id).where(Report.job_id == job.id).limit(1))
        if job is not None
        else None
    )

    token = new_csrf_token(settings)
    detail: Response = render(
        request,
        "requests/detail.html",
        {
            "item": found,
            "job": job,
            # A cancelled or failed run produced no report, so there is nothing to choose
            # between and starting again is the obvious next move. Without this the page
            # offered only "open the run" and the request was a dead end.
            "can_start_again": job is not None and job.status.is_terminal and report is None,
            # The page shows edit and delete, or says why it cannot. Asked as a question
            # about the request rather than inferred from `job` being None, so the one rule
            # lives in the service and the template only renders the answer.
            "immutable_reason": await request_service.immutable_reason(session, request=found),
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(detail, token)
    return detail


async def _submitted_values(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}


def _go_to(request: Request, destination: str) -> Response:
    """Send the browser to ``destination``, however the submission arrived.

    HTMX swaps a fragment into the page, so a 303 would be *followed* and the whole
    destination page swapped into the error container. ``HX-Redirect`` tells it to navigate
    instead. For a plain submission, 303 rather than 302 forces the follow-up to be a GET,
    so a refresh cannot resubmit the form.
    """
    if _is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": destination})
    return RedirectResponse(destination, status_code=HTTP_303_SEE_OTHER)


# The one other page that carries the assumptions forms: the gate an operator clears them
# from (gap A52). Anchored to exactly that shape so a crafted `return_to` cannot steer the
# redirect anywhere else.
_GATE_RETURN = re.compile(r"^/runs/[0-9a-fA-F-]{36}/assumptions$")


def _assumptions_destination(submitted: dict[str, str], request_id: uuid.UUID) -> str:
    """Where a save lands afterwards: the surface it was posted from.

    The assumptions gate embeds this surface's forms so an operator can supply a missing
    value where the decision is being made; a save from there returns there, refreshed —
    the live run's operator saved values and was shown a page that still called them
    missing, which reads as a save that failed.
    """
    wanted = submitted.get("return_to", "")
    if _GATE_RETURN.fullmatch(wanted):
        return wanted
    return f"/requests/{request_id}/assumptions"


def _request_not_found(request: Request, request_id: uuid.UUID) -> Response:
    response: Response = render(
        request,
        "requests/not_found.html",
        {"request_id": str(request_id)},
        status_code=HTTP_404_NOT_FOUND,
    )
    return response


def _immutable(
    request: Request,
    *,
    item: Any,
    reason: str,
    verb: str,
    status: int = HTTP_409_CONFLICT,
) -> Response:
    response: Response = render(
        request,
        "requests/immutable.html",
        {"item": item, "reason": reason, "verb": verb},
        status_code=status,
    )
    return response


def _csrf_failure(
    request: Request,
    settings: SettingsDep,
    submitted: dict[str, str],
    *,
    page: _FormPage,
) -> Response | None:
    """Re-render the form when the CSRF token is missing or stale, or ``None`` if it is fine.

    Re-rendered rather than refused outright. The overwhelmingly likely cause is a form left
    open past the token's lifetime, and throwing away a page of carefully written focus
    questions to protect against that would be its own kind of damage.
    """
    if csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return None
    return _render_failure(
        request,
        settings,
        ParsedForm(payload=None, values=submitted),
        page=page,
        status=HTTP_403_FORBIDDEN,
        banner=(
            "This form's security token was missing or had expired. Nothing was "
            "submitted. Your answers are below — please submit again."
        ),
    )


def _problems_from(exc: ValidationError) -> list[FieldProblem]:
    raw = exc.context.get("problems", [])
    return [
        FieldProblem(
            field=str(item.get("field", "")),
            message=str(item.get("message", "")),
            code=item.get("code"),
        )
        for item in raw
        if isinstance(item, dict)
    ]


def _render_failure(
    request: Request,
    settings: SettingsDep,
    parsed: ParsedForm,
    *,
    page: _FormPage,
    status: int,
    banner: str | None = None,
) -> Response:
    """Re-render the form, or just its errors for an HTMX submission.

    A fresh CSRF token is issued and sent both as a cookie and — for the fragment path —
    as an out-of-band swap of the form's hidden input. Both halves have to move together:
    rotating only the cookie leaves the form carrying a token the server will now reject,
    which is a form that looks perfectly normal and can never be submitted again.
    """
    htmx = _is_htmx(request)
    template = "requests/_form_errors.html" if htmx else page.template
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        template,
        _form_context(page, csrf_token=token, parsed=parsed, banner=banner, oob_csrf=htmx),
        status_code=status,
    )
    set_csrf_cookie(response, token)
    return response


# ==========================================================================================
# Assumptions
#
# The surface `docs/archive/phase-3-plan.md` task 24 asks for: every assumption a run rests on,
# editable before the valuation runs. Nothing here decides anything — amending and confirming
# call the same service the JSON API calls, so the page and the API cannot disagree about
# what a confirmation means.
# ==========================================================================================


@router.get(
    "/requests/{request_id}/assumptions",
    response_class=HTMLResponse,
    summary="The assumptions a request rests on",
)
async def assumptions_page(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    rows = await assumption_service.assumptions_for_request(session, request_id)
    payload = assumptions_payload(rows)

    cases = []
    for scenario in await scenario_service.scenarios_for_request(session, request_id):
        state = await scenario_service.resolve(session, scenario=scenario)
        cases.append(
            {
                "label": scenario.label,
                "description": scenario.description,
                "overridden": list(state.overridden),
            }
        )

    token = new_csrf_token(settings)
    page: Response = render(
        request,
        "assumptions/list.html",
        {
            "research_request": found,
            "payload": payload,
            # The hash of exactly the list rendered. Carried back by each confirm form, so
            # confirming a page that has since changed is refused rather than recorded.
            "payload_hash": payload_hash_for(payload),
            "unconfirmed": sum(1 for row in rows if not row.approved),
            # The unit vocabulary the form offers (gap B14). `pure` first because every
            # assumption a forecast needs is dimensionless; the currencies are here for a
            # name outside that vocabulary, not as an invitation.
            "unit_choices": UNIT_CHOICES,
            # What the forecast still has no value for at all — each name prefills the
            # create form below, which is what lets the assumptions gate pause over a gap
            # rather than proceeding without a valuation (gap S2).
            "outstanding": list(outstanding_for(rows, years=FORECAST_YEARS)),
            "scenarios": cases,
            "csrf_field": CSRF_FIELD_NAME,
            "csrf_token": token,
        },
    )
    set_csrf_cookie(page, token)
    return page


@router.get(
    "/requests/{request_id}/assumptions/{assumption_id}",
    response_class=HTMLResponse,
    summary="Every value proposed for one assumption",
)
async def assumption_detail(
    request: Request,
    request_id: uuid.UUID,
    assumption_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    assumption = await session.scalar(
        select(Assumption).where(
            Assumption.id == assumption_id, Assumption.request_id == request_id
        )
    )
    if assumption is None:
        return _problem_page(
            request, f"No assumption {assumption_id} on this request.", HTTP_404_NOT_FOUND
        )

    proposals = await assumption_service.history_of(session, assumption_id)
    page: Response = render(
        request,
        "assumptions/detail.html",
        {
            "research_request": found,
            "assumption": assumption,
            "proposals": list(proposals),
        },
    )
    return page


@router.post(
    "/requests/{request_id}/assumptions/{assumption_id}/confirm",
    summary="Agree that an assumption may be used",
)
async def confirm_assumption_page(
    request: Request,
    request_id: uuid.UUID,
    assumption_id: uuid.UUID,
    *,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """Confirm against the hash of the list that was displayed, then return to it."""
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    form = await request.form()
    submitted = {k: str(v) for k, v in form.multi_items() if isinstance(v, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem_page(
            request,
            "This form's security token was missing or had expired. Nothing was confirmed.",
            HTTP_403_FORBIDDEN,
        )

    assumption = await session.scalar(
        select(Assumption).where(
            Assumption.id == assumption_id, Assumption.request_id == request_id
        )
    )
    if assumption is None:
        return _problem_page(
            request, f"No assumption {assumption_id} on this request.", HTTP_404_NOT_FOUND
        )

    rows = await assumption_service.assumptions_for_request(session, request_id)
    if payload_hash_for(assumptions_payload(rows)) != submitted.get("payload_hash", ""):
        return _problem_page(
            request,
            "The assumptions changed after this page was rendered, so confirming would "
            "agree to something other than what was shown. Reload and look again.",
            HTTP_409_CONFLICT,
        )

    try:
        await assumption_service.confirm(session, assumption=assumption, actor=user)
    except ValidationError as refused:
        return _problem_page(request, refused.message, HTTP_409_CONFLICT)

    await session.commit()
    return _go_to(request, _assumptions_destination(submitted, request_id))


@router.post(
    "/requests/{request_id}/assumptions/{assumption_id}/amend",
    summary="Replace an assumption's value",
)
async def amend_assumption_page(
    request: Request,
    request_id: uuid.UUID,
    assumption_id: uuid.UUID,
    *,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    form = await request.form()
    submitted = {k: str(v) for k, v in form.multi_items() if isinstance(v, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem_page(
            request,
            "This form's security token was missing or had expired. Nothing was amended.",
            HTTP_403_FORBIDDEN,
        )

    assumption = await session.scalar(
        select(Assumption).where(
            Assumption.id == assumption_id, Assumption.request_id == request_id
        )
    )
    if assumption is None:
        return _problem_page(
            request, f"No assumption {assumption_id} on this request.", HTTP_404_NOT_FOUND
        )

    try:
        value = Decimal(submitted.get("value", ""))
    except InvalidOperation:
        return _problem_page(
            request,
            f"{submitted.get('value', '')!r} is not a number.",
            HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        await assumption_service.amend(
            session,
            assumption=assumption,
            value=value,
            justification=submitted.get("justification", ""),
            actor=user,
            unit=submitted.get("unit") or None,
            # The operator ticking "I mean this figure" against the plausible range (B14).
            accepted_anyway=bool(submitted.get("accepted_anyway")),
        )
    except ValidationError as refused:
        return _problem_page(request, refused.message, HTTP_422_UNPROCESSABLE_CONTENT)

    await session.commit()
    return _go_to(request, _assumptions_destination(submitted, request_id))


@router.post(
    "/requests/{request_id}/assumptions/create",
    summary="Put a value forward for an assumption no run could derive",
)
async def create_assumption_page(
    request: Request,
    request_id: uuid.UUID,
    *,
    session: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Response:
    """The half the surface was missing, and the reason the gate could not pause.

    A run that cannot derive a risk-free rate or a beta names the gap and used to proceed
    without a valuation, because pausing over a row the operator could not create left the
    run stopped for nothing (gap S2). Creating goes through the same ``propose`` the model
    uses — the result is a proposal, never a confirmation, because typing a value and
    agreeing the run may rest on it are separate acts.
    """
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        return _request_not_found(request, request_id)

    form = await request.form()
    submitted = {k: str(v) for k, v in form.multi_items() if isinstance(v, str)}
    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        return _problem_page(
            request,
            "This form's security token was missing or had expired. Nothing was created.",
            HTTP_403_FORBIDDEN,
        )

    # The JSON surface's own validation, so the two ways of typing a value cannot drift:
    # the same bounded name vocabulary, the same refusal for a name no valuation reads.
    try:
        proposed = ProposeRequest(
            name=submitted.get("name", "").strip(),
            value=submitted.get("value", ""),  # type: ignore[arg-type]
            unit=submitted.get("unit", "").strip(),
            justification=submitted.get("justification", ""),
            accepted_anyway=bool(submitted.get("accepted_anyway")),
        )
    except PydanticValidationError as invalid:
        first = invalid.errors()[0]
        return _problem_page(request, str(first["msg"]), HTTP_422_UNPROCESSABLE_CONTENT)

    try:
        await assumption_service.propose(
            session,
            request_id=request_id,
            name=proposed.name,
            value=proposed.value,
            unit=proposed.unit,
            justification=proposed.justification,
            proposed_by=user.email,
            by_human=True,
            accepted_anyway=proposed.accepted_anyway,
        )
    except ValidationError as refused:
        return _problem_page(request, refused.message, HTTP_422_UNPROCESSABLE_CONTENT)

    await session.commit()
    return _go_to(request, _assumptions_destination(submitted, request_id))


def _problem_page(request: Request, message: str, status: int) -> Response:
    page: Response = render(request, "runs/problem.html", {"message": message}, status_code=status)
    return page


@router.get("/_shell/badges", summary="The counts beside the navigation")
async def shell_badges(
    request: Request,
    session: DbSession,
    redis: RedisClient,
) -> Response:
    """The numbers for every registered badge, as out-of-band swaps.

    Its own request because a count belongs to the tool that registers it and the sidebar
    belongs to all of them: computed inline, one tool's slow query would be paid for on
    every other tool's first paint, invisibly. The nav ships empty slots and this fills
    them (`web/shell/badges.py`).

    Nothing here decides what to count. The registry does, so a second tool contributes a
    provider and this handler is not touched.

    **The operator is looked up here rather than injected**, which is the one thing about
    this handler that is not like every other page's. `CurrentUser` raises when it cannot
    reach the database, and a dependency raises before a handler can decide anything — so
    with Postgres down this fragment answered 500 on every load of the landing page, which
    is the one page in the product built to render in exactly that state. An empty set of
    counts is the honest answer to "what is waiting for you" when nothing can be asked.
    """
    try:
        user = await current_user_or_none(session)
    except (SQLAlchemyError, OSError) as unavailable:
        # Both, because there are two ways to be unable to ask. asyncpg raises the
        # operating system's error directly when nothing is listening, before SQLAlchemy
        # has anything to wrap; a database that *is* listening with a schema two migrations
        # behind raises `ProgrammingError` from the same statement. The first was caught
        # here and the second was not, so a fragment that fires on every page answered 500
        # on exactly the machine the front page is written to help — one that has not run
        # `alembic upgrade head`. Chrome either way, so it is a log line rather than a page.
        _log.info("shell.badges_unavailable", error=str(unavailable))
        user = None

    if user is None:
        empty: Response = render(request, "_shell/badges.html", {"badges": ()})
        return empty

    badges = await cached_counts_for(redis, session, user_id=user.id)
    fragment: Response = render(request, "_shell/badges.html", {"badges": badges})
    return fragment


@router.post("/_shell/guidance", summary="Turn guidance mode on or off")
async def toggle_guidance(request: Request, settings: SettingsDep) -> Response:
    """Flip the guidance flag and return to the page that asked.

    A form POST that redirects, so it works with scripting off — ADR 0006's binding rule,
    which htmx may improve on but never replace. The flag is server state under ADR 0077:
    a reload that lost it would be noticed, which is the test for what the client may own.

    Stored in a cookie rather than on `users`. It is a preference, not a record: nothing
    cites it, no figure depends on it, and a migration on a table documented as holding one
    row is a large price for remembering whether somebody wants callouts.

    **The destination is checked rather than trusted.** `next` arrives from a form field and
    a redirect that followed it anywhere would be an open redirect — a page on this origin
    that forwards to somebody else's. Only a same-site absolute path is honoured.
    """
    form = await request.form()
    if not await _csrf_ok(request, settings):
        # No flash and no error page: the worst case is a preference that did not change,
        # and a security-token page for a cosmetic toggle would be a worse answer than
        # simply not toggling.
        return RedirectResponse("/", status_code=HTTP_303_SEE_OTHER)

    wanted = str(form.get("guidance") or "") == "on"
    raw = str(form.get("next") or "/")
    destination = raw if raw.startswith("/") and not raw.startswith("//") else "/"

    response = RedirectResponse(destination, status_code=HTTP_303_SEE_OTHER)
    response.set_cookie(
        GUIDANCE_COOKIE,
        "on" if wanted else "off",
        httponly=True,
        samesite="strict",
        # Not Secure, for the reason `web/csrf.py` gives about its own cookie: this is
        # served over plain HTTP on loopback, and a Secure cookie would simply never be
        # sent. Revisit with TLS, and revisit both together.
        secure=False,
        max_age=60 * 60 * 24 * 365,
        path="/",
    )
    return response
