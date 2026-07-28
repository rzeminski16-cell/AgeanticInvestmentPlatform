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
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
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
from aer.errors import AerError, ValidationError
from aer.services import requests as request_service
from aer.version import build_identity
from aer.web.csrf import CSRF_FIELD_NAME, csrf_is_valid, new_csrf_token, set_csrf_cookie
from aer.web.forms import ParsedForm, parse_request_form
from aer.web.templating import render

__all__ = ["router"]

router = APIRouter(include_in_schema=False)

# Offered in the exchange select. Sorted so the order is stable between renders rather
# than following set iteration order, which would reshuffle the dropdown on every restart.
_EXCHANGE_CHOICES = sorted(SUPPORTED_EXCHANGES)
_CURRENCY_CHOICES = sorted(SUPPORTED_CURRENCIES)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _form_context(
    *,
    csrf_token: str,
    parsed: ParsedForm | None = None,
    banner: str | None = None,
    oob_csrf: bool = False,
) -> dict[str, Any]:
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
        "values": parsed.values if parsed else {},
        "banner": banner,
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
        problem = (
            "The database is not reachable. Start it with `just up`, then reload this "
            "page. /readyz reports which dependencies are answering."
        )

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
    response: Response = render(request, "requests/new.html", _form_context(csrf_token=token))
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
    form = await request.form()
    submitted = {key: str(value) for key, value in form.multi_items() if isinstance(value, str)}

    if not csrf_is_valid(request, submitted.get(CSRF_FIELD_NAME), settings):
        # Re-rendered rather than refused outright. The overwhelmingly likely cause is a
        # form left open past the token's lifetime, and throwing away a page of carefully
        # written focus questions to protect against that would be its own kind of damage.
        parsed = ParsedForm(payload=None, values=submitted)
        return _render_failure(
            request,
            settings,
            parsed,
            status=HTTP_403_FORBIDDEN,
            banner=(
                "This form's security token was missing or had expired. Nothing was "
                "submitted. Your answers are below — please submit again."
            ),
        )

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
            destination = f"/requests/{created.id}"
            if _is_htmx(request):
                # HTMX swaps a fragment into the page; a 303 would be followed and the
                # whole detail page swapped into the error container. HX-Redirect tells
                # it to navigate instead.
                return Response(status_code=204, headers={"HX-Redirect": destination})
            # 303 rather than 302: it forces the follow-up to be a GET, so a refresh on
            # the detail page cannot resubmit the form.
            return RedirectResponse(destination, status_code=HTTP_303_SEE_OTHER)

    return _render_failure(request, settings, parsed, status=HTTP_422_UNPROCESSABLE_CONTENT)


@router.get(
    "/requests/{request_id}", response_class=HTMLResponse, summary="Research request detail"
)
async def request_detail(
    request: Request,
    request_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    found = await request_service.get_request(session, request_id, user_id=user.id)
    if found is None:
        response: Response = render(
            request,
            "requests/not_found.html",
            {"request_id": str(request_id)},
            status_code=HTTP_404_NOT_FOUND,
        )
        return response
    detail: Response = render(request, "requests/detail.html", {"item": found})
    return detail


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
    template = "requests/_form_errors.html" if htmx else "requests/new.html"
    token = new_csrf_token(settings)
    response: Response = render(
        request,
        template,
        _form_context(csrf_token=token, parsed=parsed, banner=banner, oob_csrf=htmx),
        status_code=status,
    )
    set_csrf_cookie(response, token)
    return response
