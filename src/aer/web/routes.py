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

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
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

from aer.api.deps import CurrentUser, DbSession, SettingsDep, get_current_user
from aer.core.enums import AnalysisMode
from aer.core.schemas.request import (
    SUPPORTED_CURRENCIES,
    EsgSensitivity,
    FieldProblem,
    RiskTolerance,
)
from aer.core.universe import SUPPORTED_EXCHANGES
from aer.db.models import Report
from aer.db.schema_check import schema_drift
from aer.errors import AerError, ConflictError, ValidationError
from aer.services import requests as request_service
from aer.services import runs as run_service
from aer.version import build_identity
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.forms import ParsedForm, form_values_from, parse_request_form
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

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


@router.get("/", response_class=HTMLResponse, summary="Landing page")
async def index(request: Request, session: DbSession) -> Response:
    """The landing page, which renders whether or not the database is up.

    It shows recent requests when it can, and says what is wrong when it cannot. A local
    tool whose front page is a blank 500 because you forgot to start Postgres tells you
    nothing; the most likely reason you are looking at it is that something is not
    working.

    The database is therefore optional *here specifically*. Every other page needs it and
    fails loudly without it — degrading a page that shows data would mean showing an empty
    list as though it were the truth.
    """
    recent: list[Any] = []
    problem: str | None = None
    try:
        # Before the query, not after. A schema two migrations behind can leave *this*
        # page working perfectly — nothing here touches the newest tables — while the run
        # console returns an opaque 500. Checking eagerly is what makes this the page that
        # tells you, which is the only reason it degrades instead of failing.
        #
        # It costs an inspection of every table on each load. On a single-user local tool
        # that is a few tens of milliseconds a handful of times a day, and the alternative
        # is caching an answer that would keep complaining after you fixed it.
        drift = await schema_drift(session)
        if not drift.is_clean:
            problem = drift.as_message()

        user = await get_current_user(session)
        recent = list(await request_service.list_requests(session, user_id=user.id, limit=5))
    except AerError as exc:
        # A configuration problem the operator can act on, such as no user having been
        # seeded. Its message says how to fix it, so show it.
        problem = exc.message
    except (SQLAlchemyError, OSError):
        # OSError as well as SQLAlchemyError: a refused connection surfaces as a bare
        # ConnectionRefusedError, because asyncpg raises it while *creating* the
        # connection, before there is a DBAPI error for SQLAlchemy to wrap. Catching only
        # SQLAlchemyError here would miss the single most common failure — Postgres not
        # started. Nothing else in this handler does I/O, so the breadth costs nothing.
        problem = await _database_problem(session)

    response: Response = render(
        request,
        "index.html",
        {"build": build_identity(), "recent_requests": recent, "problem": problem},
    )
    return response


@router.get("/requests", response_class=HTMLResponse, summary="Your research requests")
async def list_requests_page(request: Request, session: DbSession, user: CurrentUser) -> Response:
    rows = await request_service.list_requests(session, user_id=user.id, limit=200)
    response: Response = render(request, "requests/list.html", {"requests": rows})
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


_NOT_REACHABLE = (
    "The database is not reachable. Start it with `just up`, then reload this page. "
    "/readyz reports which dependencies are answering."
)


async def _database_problem(session: DbSession) -> str:
    """Say *which* database problem this is, now that there are two worth telling apart.

    "Not reachable" and "reachable but two migrations behind" have completely different
    fixes, and reporting the second as the first sends the operator to restart a container
    that was working perfectly. The failed statement has poisoned the transaction, so the
    rollback is not optional — without it the drift query fails too and every problem looks
    like an outage again.
    """
    try:
        await session.rollback()
        drift = await schema_drift(session)
    except (SQLAlchemyError, OSError):
        return _NOT_REACHABLE
    return _NOT_REACHABLE if drift.is_clean else drift.as_message()


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
