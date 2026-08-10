"""Spans for a run, and the property that matters more than any span: it cannot break one.

Gap A13. Tracing is instrumentation, so the risk is not that it reports badly — it is that
it takes the platform with it when something about it goes wrong. A collector that stops
answering, an endpoint typed wrongly into `.env`, a version of the SDK that raises somewhere
new: none of those may turn into a failed research run.

So most of what follows breaks the tracing stack on purpose and asserts that the traced code
still runs and still returns. The spans themselves are checked through an in-memory exporter,
which is why none of this needs a collector.
"""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aer import tracing
from aer.tracing import ENDPOINT_VARIABLE, configure_tracing, is_tracing, shutdown_tracing, span


@pytest.fixture
def recorded() -> Any:
    """Tracing wired to an in-memory exporter, torn down afterwards."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    tracing._state.provider = provider
    tracing._state.tracer = provider.get_tracer("test")
    yield exporter
    tracing._state.provider = None
    tracing._state.tracer = None


class TestTheDefaultIsOff:
    def test_no_endpoint_means_no_tracing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A local install must not need a collector before it will start."""
        monkeypatch.delenv(ENDPOINT_VARIABLE, raising=False)
        tracing._state.tracer = None

        assert configure_tracing() is False
        assert is_tracing() is False

    def test_a_blank_endpoint_is_the_same_as_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`AER_OTEL_ENDPOINT=` in a .env file must not half-enable anything."""
        monkeypatch.setenv(ENDPOINT_VARIABLE, "   ")
        tracing._state.tracer = None

        assert configure_tracing() is False

    def test_spans_are_free_when_tracing_is_off(self) -> None:
        tracing._state.tracer = None

        with span("step.plan", **{"aer.job_id": "abc"}):
            outcome = 2 + 2

        assert outcome == 4

    def test_shutdown_without_startup_is_harmless(self) -> None:
        tracing._state.tracer = None
        shutdown_tracing()


class TestWhatGetsRecorded:
    def test_a_span_carries_its_name_and_attributes(self, recorded: Any) -> None:
        with span("step.acquire", **{"aer.job_id": "j1", "aer.step_key": "acquire"}):
            pass

        finished = recorded.get_finished_spans()
        assert [s.name for s in finished] == ["step.acquire"]
        assert finished[0].attributes["aer.job_id"] == "j1"

    def test_spans_nest_into_the_tree_a_run_actually_is(self, recorded: Any) -> None:
        """The shape a log stream loses: model calls belong inside the step that made them."""
        with span("step.draft"), span("model.report_writer"):
            pass

        finished = {s.name: s for s in recorded.get_finished_spans()}
        inner, outer = finished["model.report_writer"], finished["step.draft"]
        assert inner.parent is not None
        assert inner.parent.span_id == outer.context.span_id

    def test_a_failure_is_recorded_and_still_raised(self, recorded: Any) -> None:
        """Tracing observes. It must never swallow the thing it is observing."""
        with pytest.raises(ValueError, match="boom"), span("step.calculate"):
            raise ValueError("boom")

        finished = recorded.get_finished_spans()
        assert finished[0].status.is_ok is False

    def test_none_attributes_are_dropped_rather_than_stringified(self, recorded: Any) -> None:
        """`"None"` as an attribute value is worse than an absent one."""
        with span("step.value", **{"aer.model": None, "aer.role": "planner"}):
            pass

        attributes = recorded.get_finished_spans()[0].attributes
        assert "aer.model" not in attributes
        assert attributes["aer.role"] == "planner"


class TestItCannotBreakARun:
    """Each of these is a tracing stack that has gone wrong in a different way."""

    def test_a_tracer_that_raises_on_start_does_not_stop_the_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Exploding:
            def start_as_current_span(self, name: str) -> Any:
                raise RuntimeError("the collector is on fire")

        monkeypatch.setattr(tracing._state, "tracer", Exploding())
        ran = False

        with span("step.plan"):
            ran = True

        assert ran, "the traced block must still run"

    def test_a_span_that_raises_on_close_does_not_stop_the_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class BadExit:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: Any) -> None:
                raise RuntimeError("export failed")

            def set_attribute(self, key: str, value: Any) -> None:
                return None

        class Tracer:
            def start_as_current_span(self, name: str) -> Any:
                return BadExit()

        monkeypatch.setattr(tracing._state, "tracer", Tracer())

        with span("step.plan"):
            pass

    def test_the_body_still_raises_its_own_error_when_closing_also_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The caller's exception is the one that matters; tracing's must not mask it."""

        class BadExit:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: Any) -> None:
                raise RuntimeError("export failed")

            def set_attribute(self, key: str, value: Any) -> None:
                return None

            def set_status(self, *args: Any) -> None:
                return None

            def record_exception(self, *args: Any) -> None:
                return None

        class Tracer:
            def start_as_current_span(self, name: str) -> Any:
                return BadExit()

        monkeypatch.setattr(tracing._state, "tracer", Tracer())

        with pytest.raises(ValueError, match="the real problem"), span("step.plan"):
            raise ValueError("the real problem")

    def test_an_unreachable_endpoint_configures_to_off_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad endpoint in .env is a typo, not a reason the platform will not start.

        The exporter is lazy about connecting, so this asserts the weaker true thing: that
        configuring against a dead address returns rather than raising, and that shutdown
        afterwards is clean.
        """
        monkeypatch.setenv(ENDPOINT_VARIABLE, "http://127.0.0.1:9/v1/traces")

        configured = configure_tracing()
        shutdown_tracing()

        assert configured in {True, False}
        assert is_tracing() is False
