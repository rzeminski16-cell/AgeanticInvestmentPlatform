"""Spans for a run, so a live one can be watched rather than reconstructed from logs.

Gap A13. Structured JSON logs are the whole of the observability story today, which is why
debugging a live run means reading worker output by hand and joining it up in your head. A
run is a tree — steps inside a job, model calls and fetches inside steps — and that shape is
exactly what a log stream loses.

**Off unless an endpoint is configured.** With ``AER_OTEL_ENDPOINT`` unset, spans are still
created but go to a no-op tracer: no exporter, no background thread, no connection attempt,
and nothing to run alongside the application. That default matters more than it looks. A
local-first tool that needs a collector before it will start is a tool nobody starts, and a
tracing layer that quietly retries a dead endpoint on every span is worse than none.

**Never a reason for a run to fail.** Tracing is instrumentation, not a control. Every entry
point here is wrapped so that a broken exporter, a bad endpoint or a missing dependency
degrades to no tracing rather than to a failed research run. The one thing that would make
this worth removing is if it could take the platform down with it.

**No payloads on spans.** Attributes carry identifiers, counts and durations — never a
prompt, a document, or anything a credential could hide in. Traces leave the machine when an
endpoint is set, so anything on a span is data the operator has exported, and `aer.logging`'s
redaction does not reach here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final

import structlog

__all__ = ["configure_tracing", "is_tracing", "shutdown_tracing", "span"]

_log = structlog.get_logger("aer.tracing")

ENDPOINT_VARIABLE: Final = "AER_OTEL_ENDPOINT"
SERVICE_NAME: Final = "aer"


@dataclass(slots=True)
class _State:
    """Where the configured tracer lives.

    A holder rather than two module globals: `span` is called from a dozen places that have
    no business knowing whether tracing is on, so the state has to be module-level — but
    rebinding globals from two functions is the part worth avoiding, and mutating one
    object's attributes says the same thing without it.
    """

    provider: Any | None = None
    tracer: Any | None = None


_state = _State()


def is_tracing() -> bool:
    """Whether spans are being exported anywhere."""
    return _state.tracer is not None


def configure_tracing(*, endpoint: str | None = None, service_version: str = "") -> bool:
    """Start exporting spans, if an endpoint is configured. Returns whether it did.

    Reads ``AER_OTEL_ENDPOINT`` when ``endpoint`` is not given. Absent, this does nothing at
    all and :func:`span` stays a no-op — which is the normal case for a local install.

    Never raises. A misconfigured endpoint, a missing exporter package or an unreachable
    collector all end the same way: a warning, no tracing, and a platform that runs.
    """
    target = endpoint if endpoint is not None else os.environ.get(ENDPOINT_VARIABLE, "")
    if not target.strip():
        return False

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

        resource = Resource.create(
            {"service.name": SERVICE_NAME, "service.version": service_version or "unknown"}
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=target)))
        _state.provider = provider
        _state.tracer = provider.get_tracer(SERVICE_NAME)
    except Exception as exc:
        _log.warning("tracing.unavailable", endpoint=target, error=f"{type(exc).__name__}: {exc}")
        _state.provider = None
        _state.tracer = None
        return False

    _log.info("tracing.configured", endpoint=target)
    return True


def shutdown_tracing() -> None:
    """Flush and stop. Safe to call when tracing was never started."""
    provider = _state.provider
    _state.provider = None
    _state.tracer = None
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception as exc:
        _log.warning("tracing.shutdown_failed", error=f"{type(exc).__name__}: {exc}")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Record one span, or do nothing at all when tracing is off.

    The no-op path is deliberately the cheapest thing available — a bare yield — because
    this wraps every step, model call and fetch, and the overwhelming majority of installs
    will never configure an endpoint.

    An exception inside the block is recorded on the span and re-raised unchanged. Tracing
    observes; it never swallows.
    """
    tracer = _state.tracer
    if tracer is None:
        yield
        return

    # Started by hand rather than with `with`, so that a failure in the tracing machinery
    # and a failure in the traced block stay clearly apart. Wrapping both in one `try` reads
    # more neatly and is wrong: the recovery path would yield a second time, and a generator
    # context manager that yields twice raises from the contextmanager decorator itself —
    # turning a tracing hiccup into a failure of whatever was being traced.
    started = _start(tracer, name, attributes)
    if started is None:
        yield
        return

    manager, active = started
    try:
        yield
    except BaseException as failure:
        _record_failure(active, failure)
        _finish(manager, failure)
        raise
    _finish(manager, None)


def _start(tracer: Any, name: str, attributes: Mapping[str, Any]) -> tuple[Any, Any] | None:
    """Open a span, or return ``None`` if the tracing stack could not."""
    try:
        manager = tracer.start_as_current_span(name)
        active = manager.__enter__()
        _set(active, attributes)
    except Exception as exc:
        _log.warning("tracing.span_failed", span=name, error=f"{type(exc).__name__}: {exc}")
        return None
    return manager, active


def _finish(manager: Any, failure: BaseException | None) -> None:
    try:
        if failure is None:
            manager.__exit__(None, None, None)
        else:
            manager.__exit__(type(failure), failure, failure.__traceback__)
    except Exception as exc:
        _log.warning("tracing.span_close_failed", error=f"{type(exc).__name__}: {exc}")


def _set(active: Any, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        if value is None:
            continue
        active.set_attribute(key, value if isinstance(value, (bool, int, float)) else str(value))


def _record_failure(active: Any, failure: BaseException) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode  # noqa: PLC0415

        active.set_status(Status(StatusCode.ERROR, str(failure)))
        active.record_exception(failure)
    except Exception:  # noqa: S110 -- recording a failure must not add one
        pass
