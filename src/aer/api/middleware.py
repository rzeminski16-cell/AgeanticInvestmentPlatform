"""Request identity, access logging and timing.

Written as raw ASGI rather than Starlette's ``BaseHTTPMiddleware``. That base class wraps
each request in an anyio task group so it can present a ``Request``/``Response`` API, and
that wrapper is what breaks streaming responses, complicates background tasks and makes
exception propagation harder to reason about. None of it buys anything here — this
middleware needs one header in, one header out and a timer.

**Why a request id at all.** Everything this application does is auditable by design, but
an audit trail you cannot join to a log line is only half a trail. One id, generated at
the edge, bound to the logging context, echoed in the response and quoted in every error
body, is what turns "it failed" into "here is exactly what happened".
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterable
from contextvars import ContextVar
from typing import Final

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "REQUEST_ID_HEADER",
    "RequestContextMiddleware",
    "get_request_id",
]

REQUEST_ID_HEADER: Final = "X-Request-ID"

# An inbound id is echoed back and written into every log line for the request, so it is
# accepted only in a shape that cannot corrupt either. Anything else is replaced rather
# than rejected: a malformed header is not worth failing a request over.
_SAFE_REQUEST_ID: Final = re.compile(r"\A[A-Za-z0-9._\-]{1,64}\Z")

# Health probes run continuously and say nothing when they succeed. Logging them buries
# the requests that matter; failures still surface because they are logged at WARNING or
# above by the handler, and readiness failures are visible in the response.
_QUIET_PATHS: Final = frozenset({"/healthz", "/readyz"})

_CLIENT_ERROR_STATUS: Final = 400
_SERVER_ERROR_STATUS: Final = 500

_request_id: ContextVar[str] = ContextVar("aer_request_id", default="")

_log = structlog.get_logger("aer.api.access")


def get_request_id() -> str:
    """The current request's id, or ``""`` outside a request.

    Exception handlers use this: Starlette runs the handler for an unexpected exception
    in ``ServerErrorMiddleware``, which sits *outside* this middleware, so the id has to
    travel by context rather than by argument.
    """
    return _request_id.get()


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)

        # Cleared at the start rather than reset at the end. An unexpected exception
        # propagates past this frame to a handler that still needs the id, so a `finally`
        # that unbinds would blank the context exactly when it is most wanted. Clearing
        # on entry gives the same isolation without that hole.
        structlog.contextvars.clear_contextvars()
        _request_id.set(request_id)
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )

        started = time.perf_counter()
        status_holder = {"status": 0}

        header_name = REQUEST_ID_HEADER.lower().encode()

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                # Only if absent. The error handlers set it themselves, because the
                # handler for an unexpected exception runs in ServerErrorMiddleware --
                # outside this middleware, where this wrapper never sees the response.
                # Appending unconditionally would send the header twice on every handled
                # error, and a client reading it would get "id, id".
                if not any(name == header_name for name, _ in headers):
                    headers.append((header_name, request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # The response is produced above this middleware, so the status is not
            # observable here; 500 is what Starlette will send. Re-raised rather than
            # swallowed, because turning an exception into a log line is the exception
            # handler's job, not the access log's.
            _log_access(
                path=scope.get("path", ""),
                status=_SERVER_ERROR_STATUS,
                started=started,
                failed=True,
            )
            raise

        _log_access(
            path=scope.get("path", ""),
            status=status_holder["status"],
            started=started,
            failed=False,
        )


def _resolve_request_id(scope: Scope) -> str:
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers", [])
    wanted = REQUEST_ID_HEADER.lower().encode()
    for name, value in headers:
        if name.lower() != wanted:
            continue
        candidate = value.decode("latin-1", errors="replace")
        if _SAFE_REQUEST_ID.match(candidate):
            return candidate
        break
    return uuid.uuid4().hex


def _log_access(*, path: str, status: int, started: float, failed: bool) -> None:
    if path in _QUIET_PATHS and not failed and status < _CLIENT_ERROR_STATUS:
        return

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    event = "request.failed" if failed else "request.completed"
    level = "warning" if failed or status >= _SERVER_ERROR_STATUS else "info"
    # `method` and `path` are already bound into the logging context for this request, so
    # repeating them here would put the same value in the line under two different names.
    getattr(_log, level)(event, status=status, duration_ms=duration_ms)
