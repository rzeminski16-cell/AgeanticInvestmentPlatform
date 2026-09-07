"""The market-data client, built by the bundle, reaches the step that asks for it.

Found on the first stepped run after a clean slate, 2026-09-07: ``acquire_prices`` said "no
market-data subscription is configured" with the key set and preflight agreeing it was. The
client existed — ``build_services`` had made one — and the step asked for it by name as an
optional service, but ``execute`` never placed it in the engine's services and neither the
worker nor ``aer step`` passed it. Two callers, each listing the bundle's fields by hand,
had each left the same one out. These tests hold the executor to the parameter, and both
callers to spreading the bundle's own list rather than writing their own.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from aer.core.enums import JobStatus
from aer.runtime import ServiceBundle
from aer.services import runs as run_service

pytestmark = pytest.mark.anyio

_PRICE_CLIENT: Any = object()
_ANY: Any = object()


def _bundle(settings: Any, **_kwargs: Any) -> ServiceBundle:
    """A bundle whose only meaningful member is the price client."""
    return ServiceBundle(
        settings=settings,
        provider=_ANY,
        router=_ANY,
        store=_ANY,
        sec_client=_ANY,
        fetcher=_ANY,
        eodhd_client=_PRICE_CLIENT,
    )


class _StopError(Exception):
    """Raised by the fake engine once it has seen its services."""


class _Session:
    """Enough of a session for a path that reaches the executor and nothing else."""

    def __init__(self, job: Any) -> None:
        self.job = job

    async def get(self, _model: Any, _key: Any) -> Any:
        return self.job

    async def commit(self) -> None:
        return None


class _Factory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self) -> _Factory:
        return self

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _quiet(*_args: Any, **_kwargs: Any) -> None:
    return None


async def _same_settings(_session: Any, base: Any) -> Any:
    return base


class TestTheExecutorTakesTheClient:
    def test_the_bundle_lists_exactly_what_execute_accepts(self, workflow_settings: Any) -> None:
        """The bundle's list and the executor's signature are the same set: a service added
        to one without the other is a service the run never sees."""
        accepted = {
            name
            for name, parameter in inspect.signature(run_service.execute).parameters.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        } - {"job", "settings", "stop_after", "session_factory"}

        assert set(_bundle(workflow_settings).for_execution()) == accepted
        assert "eodhd_client" in accepted

    async def test_the_engine_is_handed_the_client(
        self, workflow_settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What ``acquire_prices`` reads through ``optional_service`` is what the caller passed."""
        seen: dict[str, Any] = {}

        class _Engine:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            async def run(self, _session: Any, *, services: Any, **_kwargs: Any) -> Any:
                seen.update(services)
                raise _StopError

        async def a_request(_session: Any, _job: Any) -> Any:
            return _ANY

        def a_workflow(_version: str) -> Any:
            return SimpleNamespace(build_steps=list)

        monkeypatch.setattr(run_service, "WorkflowEngine", _Engine)
        monkeypatch.setattr(run_service, "mandate_of", a_request)
        monkeypatch.setattr(run_service, "resolve_workflow", a_workflow)
        job = SimpleNamespace(id=uuid.uuid4(), status=JobStatus.QUEUED, workflow_version="x")

        with pytest.raises(_StopError):
            await run_service.execute(
                _Session(job),  # type: ignore[arg-type]
                job=job,  # type: ignore[arg-type]
                settings=workflow_settings,
                provider=_ANY,
                store=_ANY,
                sec_client=_ANY,
                eodhd_client=_PRICE_CLIENT,
            )

        assert seen["eodhd_client"] is _PRICE_CLIENT


class TestBothCallersSpreadTheBundle:
    """The worker and ``aer step`` are the two places a run is executed from, and the two
    that had each listed the bundle by hand."""

    @pytest.fixture
    def captured(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        from aer.services import configuration  # noqa: PLC0415

        seen: dict[str, Any] = {}

        async def execute(_session: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return SimpleNamespace(status=JobStatus.PAUSED, spend_gbp=0, is_waiting=True)

        monkeypatch.setattr(run_service, "execute", execute)
        monkeypatch.setattr(configuration, "effective_settings", _same_settings)
        monkeypatch.setattr("aer.runtime.build_services", _bundle)
        return seen

    async def test_the_worker_forwards_the_client(
        self, captured: dict[str, Any], settings_env: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from aer import worker  # noqa: PLC0415
        from aer.config import load_settings  # noqa: PLC0415

        job = SimpleNamespace(id=uuid.uuid4(), status=JobStatus.QUEUED)

        async def state(_session: Any, *, job_id: Any) -> Any:
            assert job_id == job.id
            return SimpleNamespace(job=job)

        monkeypatch.setattr(run_service, "run_state", state)
        monkeypatch.setattr(worker, "effective_settings", _same_settings)
        monkeypatch.setattr(worker, "build_services", _bundle)
        settings = load_settings()
        context = {
            "settings": settings,
            "session_factory": _Factory(_Session(job)),
            "aer_redis": None,
        }

        await worker.run_research(context, str(job.id))

        assert captured["eodhd_client"] is _PRICE_CLIENT
        assert set(_bundle(settings).for_execution()) <= set(captured)

    async def test_the_step_command_forwards_the_client(
        self, captured: dict[str, Any], settings_env: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from aer import cli  # noqa: PLC0415
        from aer.api import deps  # noqa: PLC0415
        from aer.config import load_settings  # noqa: PLC0415
        from aer.services import resume  # noqa: PLC0415

        job = SimpleNamespace(id=uuid.uuid4(), status=JobStatus.PAUSED)

        async def a_user(_session: Any) -> Any:
            return SimpleNamespace(email="owner@example.invalid")

        async def a_readout(_session: Any, *, job_id: Any) -> Any:
            return SimpleNamespace(status=JobStatus.PAUSED, job_id=job_id)

        def an_engine(_settings: Any) -> Any:
            return SimpleNamespace(dispose=_noop)

        def a_factory(_engine: Any) -> _Factory:
            return _Factory(_Session(job))

        monkeypatch.setattr(deps, "current_user_or_none", a_user)
        monkeypatch.setattr(resume, "set_step_mode", _noop)
        monkeypatch.setattr(cli, "run_diagnostic", a_readout)
        monkeypatch.setattr(cli, "_print_stepped_outcome", _quiet)
        monkeypatch.setattr(cli, "create_engine", an_engine)
        monkeypatch.setattr(cli, "create_session_factory", a_factory)

        await cli._step_once(load_settings(), job_id=job.id)

        assert captured["eodhd_client"] is _PRICE_CLIENT
        assert set(_bundle(load_settings()).for_execution()) <= set(captured)
