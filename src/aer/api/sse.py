"""Live run progress over server-sent events.

A run takes twenty to sixty minutes and the operator wants to watch it. SSE rather than
WebSockets because the traffic is entirely one-way — the server reports, the browser
listens — and SSE is a plain HTTP response that reconnects by itself, needs no protocol
upgrade, and survives a proxy that has never heard of it.

**Polled from the database, not pushed from the worker.** A pub/sub channel would be
lower-latency and would also mean an event published during an uncommitted transaction
could describe a state no reader can see, and that a subscriber connecting slightly late
would miss everything before it. Polling the committed state is a second behind and always
right — and it makes the reconnect story free, because there is no backlog to replay.

**Only changes are sent.** The state is hashed each tick and emitted when it differs, with
a heartbeat in between so an idle connection is not mistaken for a dead one by whatever
sits between the browser and the server.

**The stream ends when the run does.** A terminal run emits a final event and closes,
rather than leaving the browser holding a connection open against a job that will never
change again.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.core.hashing import canonical_json, sha256_hex
from aer.errors import AerError
from aer.services import runs as run_service

__all__ = ["SSE_MEDIA_TYPE", "event_stream", "format_event"]

_log = structlog.get_logger("aer.api.sse")

SSE_MEDIA_TYPE: Final = "text/event-stream"

# How often the state is re-read. One second is well inside human patience for a job whose
# steps take tens of seconds, and cheap: the query is a primary-key lookup plus a small
# indexed scan.
POLL_SECONDS: Final = 1.0

# A comment line every fifteen seconds. Proxies and load balancers close connections that
# look idle, and a colon-prefixed line is a comment in the SSE grammar -- the browser
# ignores it, and the connection stays alive.
HEARTBEAT_SECONDS: Final = 15.0

# A ceiling on how long one stream lives. The browser reconnects automatically, so this
# costs nothing and stops a forgotten tab holding a connection open indefinitely.
MAX_STREAM_SECONDS: Final = 3600.0


def format_event(event: str, data: Any, *, event_id: str | None = None) -> str:
    """One SSE frame.

    The wire format is exact: ``field: value`` lines, a blank line to terminate. A missing
    blank line means the browser buffers the event forever waiting for more, which looks
    exactly like a server that has stopped sending.
    """
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    # Multi-line payloads need one `data:` per line. JSON is emitted compactly so this is
    # one line in practice, and the split is here for the case where it is not.
    payload = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    lines.extend(f"data: {line}" for line in payload.split("\n"))
    return "\n".join(lines) + "\n\n"


async def event_stream(
    session_factory: async_sessionmaker[Any],
    *,
    job_id: uuid.UUID,
    poll_seconds: float = POLL_SECONDS,
    max_seconds: float = MAX_STREAM_SECONDS,
) -> AsyncIterator[str]:
    """Yield SSE frames describing a run until it reaches a terminal state.

    A short-lived session per poll rather than one held open for the life of the stream:
    an hour-long transaction would pin a connection and hold back vacuum for no benefit.
    """
    last_digest: str | None = None
    elapsed = 0.0
    since_heartbeat = 0.0

    while elapsed < max_seconds:
        try:
            async with session_factory() as session:
                state = await run_service.run_state(session, job_id=job_id)
                payload = state.as_dict()
                terminal = state.is_terminal
        except AerError as exc:
            yield format_event("error", {"code": exc.code, "message": exc.message})
            return
        except Exception as exc:
            # By the time a generator is yielding, the response headers are long gone: an
            # exception here cannot become a 500, it just truncates the body. Reporting it
            # as an event is the only way the browser learns anything.
            _log.warning("sse.stream_failed", job_id=str(job_id), error=str(exc))
            yield format_event("error", {"code": "stream_failed", "message": str(exc)})
            return

        digest = sha256_hex(canonical_json(payload))
        if digest != last_digest:
            yield format_event("state", payload, event_id=digest[:16])
            last_digest = digest
            since_heartbeat = 0.0

        if terminal:
            yield format_event("done", {"status": payload["status"]})
            return

        if since_heartbeat >= HEARTBEAT_SECONDS:
            # A comment, not an event. Keeps the connection from being reaped without
            # making the browser think anything happened.
            yield ": keep-alive\n\n"
            since_heartbeat = 0.0

        # Cancellation is *not* suppressed. When the browser navigates away the server
        # cancels this task, and swallowing that would leave the generator polling the
        # database for the rest of its hour-long lifetime -- holding a connection for a
        # reader that has gone. A closed tab must end the stream.
        # Cancellation is *not* suppressed. When the browser navigates away the server
        # cancels this task, and swallowing that would leave the generator polling the
        # database for the rest of its hour-long lifetime -- holding a connection for a
        # reader that has gone. A closed tab must end the stream.
        await asyncio.sleep(poll_seconds)
        elapsed += poll_seconds
        since_heartbeat += poll_seconds

    # The cap, reached. The browser reconnects on its own, so this is a pause rather than
    # an ending, and saying so is better than closing silently.
    yield format_event("timeout", {"reason": "stream lifetime reached; reconnecting"})
