"""Consistent error responses, in RFC 9457 Problem Details form.

Every failure — a domain rule, a 404, a budget cap, a bug — leaves by one of the handlers
below and arrives as the same JSON shape with the same media type. Clients get one thing
to parse; the GUI gets one thing to render; tests assert on ``code`` rather than on
prose.

The line this module draws:

* **Errors we raised on purpose** (:class:`~aer.errors.AerError` and its subclasses) carry
  a message written for a human, and that message is returned. Hiding it would be
  actively unhelpful — "run ``aer seed-user``" is the whole value of the error.
* **Errors we did not expect** return nothing but a request id. The message of an
  unhandled exception is written by whichever library raised it, and library messages
  routinely contain connection strings, file paths and query fragments. The full detail
  is logged with its traceback; none of it is sent.
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT, HTTP_500_INTERNAL_SERVER_ERROR

from aer.api.middleware import REQUEST_ID_HEADER, get_request_id
from aer.errors import AerError
from aer.logging import redact_value

__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "problem_response",
    "register_exception_handlers",
]

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"

# RFC 9457 wants a URI reference identifying the problem type. A relative one keeps the
# body free of hostnames -- which change between local, CI and any future deployment --
# and points at documentation we control.
_TYPE_PREFIX: Final = "/errors/"

_GENERIC_DETAIL: Final = (
    "An unexpected error occurred. The failure has been logged; quote the request id when "
    "reporting it."
)

_log = structlog.get_logger("aer.api.errors")


def _title_from_code(code: str) -> str:
    return code.replace("_", " ").capitalize()


def problem_response(
    *,
    status: int,
    code: str,
    detail: str,
    title: str | None = None,
    context: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a Problem Details response.

    ``context`` passes through :func:`aer.logging.redact_value` on the way out. Error
    context is not supposed to contain credentials in the first place, but this is the
    last point at which one could still be caught, and a masked field in a response body
    is a far better outcome than a leaked key.
    """
    request_id = get_request_id()
    body: dict[str, Any] = {
        "type": f"{_TYPE_PREFIX}{code}",
        "title": title or _title_from_code(code),
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": request_id,
    }
    if context:
        body["context"] = redact_value(context)

    # Set here rather than left to the middleware. The handler for an unexpected
    # exception runs in ServerErrorMiddleware, which is the outermost layer -- outside
    # RequestContextMiddleware, whose `send` wrapper therefore never sees this response.
    # A 500 without the id in its header is exactly the response you most want to trace.
    response_headers = dict(headers or {})
    if request_id:
        response_headers[REQUEST_ID_HEADER] = request_id

    return JSONResponse(
        status_code=status,
        content=body,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=response_headers,
    )


async def _handle_aer_error(_request: Request, exc: Exception) -> Response:
    error = exc if isinstance(exc, AerError) else AerError(str(exc))
    status = error.http_status

    # Client errors are the caller's business and expected in normal operation; server
    # errors are ours, and want a traceback to act on.
    if status >= HTTP_500_INTERNAL_SERVER_ERROR:
        _log.error("aer_error", exc_info=error, error_code=error.code, http_status=status)
    else:
        _log.warning("aer_error", error_code=error.code, http_status=status)

    return problem_response(
        status=status,
        code=error.code,
        detail=error.message,
        context=error.context,
    )


async def _handle_request_validation_error(_request: Request, exc: Exception) -> Response:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    # `input` echoes the submitted value and `ctx` can carry the offending object.
    # Reflecting either would put whatever the user typed -- a password pasted into the
    # wrong field, an API key in a query string -- straight back into a response body and
    # into any log that records one.
    safe = [
        {
            "location": list(item.get("loc", ())),
            "message": item.get("msg", ""),
            "type": item.get("type", ""),
        }
        for item in errors
    ]
    _log.warning("request_validation_failed", error_count=len(safe))
    return problem_response(
        status=HTTP_422_UNPROCESSABLE_CONTENT,
        code="request_validation_error",
        detail="The request body or parameters did not match the expected schema.",
        context={"errors": safe},
    )


async def _handle_http_exception(_request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover -- defensive
        return await _handle_unexpected_error(_request, exc)

    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return problem_response(
        status=exc.status_code,
        code=f"http_{exc.status_code}",
        detail=detail,
        # Starlette attaches headers that are part of the protocol response -- `Allow` on
        # a 405, `WWW-Authenticate` on a 401 -- and dropping them makes the status
        # meaningless.
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    _log.error(
        "unhandled_exception",
        exc_info=exc,
        exception_type=type(exc).__name__,
        http_path=request.url.path,
    )
    return problem_response(
        status=HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        detail=_GENERIC_DETAIL,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler. Order does not matter; Starlette dispatches by exact class
    then by MRO."""
    app.add_exception_handler(AerError, _handle_aer_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    # Starlette lifts a handler registered for bare `Exception` out of the routing layer
    # and gives it to ServerErrorMiddleware, the outermost layer. That is what makes it a
    # true catch-all -- and also why it runs outside RequestContextMiddleware, so the
    # request id has to come from the context variable rather than the request.
    app.add_exception_handler(Exception, _handle_unexpected_error)
