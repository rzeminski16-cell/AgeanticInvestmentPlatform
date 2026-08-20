"""The worker's arq entry point.

Not a test of what a run does — ``test_workflow.py`` covers that by calling the same
service the worker calls. What is tested here is the thing between arq and that service:
whether ``uv run arq aer.worker.WorkerSettings`` can actually start.

It could not, for as long as the worker existed. ``redis_settings`` was assigned a
*function*, intending lazy resolution, and arq reads the class's ``__dict__`` directly, so
it received the function and died with ``'function' object has no attribute 'host'``. Every
test passed throughout, because every test drove the workflow directly and none ever asked
arq to read the settings. The worker was the one component with no test of its own, and it
was the one component that did not work.

So these tests use arq's own ``get_kwargs`` and ``create_worker`` rather than inspecting the
class. The question is not "does this attribute look right" but "can arq build a worker from
it", and only arq's own code answers that.

**The import is inside the fixture, not at the top.** :mod:`aer.worker` resolves its Redis
settings when it is imported, which is what makes them a real value in ``__dict__`` where
arq can find them. That means the module needs configuration to import at all — true of the
worker process too, and a fair price — so it cannot be imported at collection time, before
the hermetic-environment fixtures have run.
"""

from __future__ import annotations

from typing import Any

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker, create_worker, get_kwargs

from aer.queue import RUN_RESEARCH_TASK, redis_settings_from


@pytest.fixture
def worker_settings(settings_env: pytest.MonkeyPatch) -> Any:
    """arq's settings class, imported once the environment is valid."""
    from aer.worker import WorkerSettings  # noqa: PLC0415 -- see the module docstring

    return WorkerSettings


class _SessionFactoryStub:
    """A factory whose session is never used, for the paths that return before touching it."""

    def __call__(self) -> _SessionFactoryStub:
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@pytest.fixture
def worker_context(settings_env: pytest.MonkeyPatch) -> dict[str, Any]:
    """The arq context, with no database and no Redis behind it.

    Enough for the paths that answer before either is reached — which, since the
    existence check moved to the top of the handler, is what a discarded job takes.
    """
    from aer.config import load_settings  # noqa: PLC0415

    return {
        "settings": load_settings(),
        "session_factory": _SessionFactoryStub(),
        "aer_redis": None,
    }


class TestArqCanStartTheWorker:
    def test_arq_reads_real_settings_and_not_a_callable(self, worker_settings: Any) -> None:
        """The exact failure: a function where arq expects a ``RedisSettings``."""
        kwargs = get_kwargs(worker_settings)

        assert isinstance(kwargs["redis_settings"], RedisSettings)
        assert not callable(kwargs["redis_settings"])

    async def test_a_worker_can_be_built(self, worker_settings: Any) -> None:
        """The whole contract, checked by the code that has to satisfy it.

        ``create_worker`` is what ``arq aer.worker.WorkerSettings`` calls. It builds the
        worker without opening a connection — the pool is created later, in ``main`` — so
        this exercises the startup path that was broken, without needing a live Redis.

        Async because ``Worker.__init__`` reaches for the running event loop, and asking
        for one outside a coroutine is deprecated — which ``filterwarnings = ["error"]``
        turns into a failure that has nothing to do with what is being tested.
        """
        assert isinstance(create_worker(worker_settings), Worker)

    async def test_the_registered_task_is_the_one_the_web_process_enqueues(
        self, worker_settings: Any
    ) -> None:
        """A producer and a consumer that disagree produce a queue nothing ever drains."""
        worker = create_worker(worker_settings)
        registered = {function.name for function in worker.functions.values()}

        assert RUN_RESEARCH_TASK in registered

    def test_it_runs_one_job_at_a_time(self, worker_settings: Any) -> None:
        """A second concurrent run would double the rate against every data provider."""
        assert get_kwargs(worker_settings)["max_jobs"] == 1

    def test_a_failed_run_is_not_retried_automatically(self, worker_settings: Any) -> None:
        """It has already spent money; repeating it spends the same again on the same
        failure. Resuming is deliberate, and the engine makes it cheap."""
        assert get_kwargs(worker_settings)["max_tries"] == 1

    def test_a_run_is_given_time_to_finish(self, worker_settings: Any) -> None:
        """A real run is twenty to sixty minutes. A timeout at the median would kill
        exactly the runs with the most work in them."""
        assert get_kwargs(worker_settings)["job_timeout"] >= 3600


class TestTheEnqueueSettings:
    """What the web process derives from its own Redis client, rather than re-reading the
    environment — so the two processes cannot end up pointed at different instances."""

    def test_host_port_and_database_come_from_the_client(self) -> None:
        from redis.asyncio import Redis  # noqa: PLC0415

        settings = redis_settings_from(Redis.from_url("redis://127.0.0.1:6380/3"))

        assert settings.host == "127.0.0.1"
        assert settings.port == 6380
        assert settings.database == 3

    def test_a_client_with_no_pool_falls_back_to_the_local_default(self) -> None:
        """Rather than raising. The enqueue path already tolerates an absent queue."""
        settings = redis_settings_from(object())

        assert settings.host == "127.0.0.1"
        assert settings.port == 6379


class TestAJobWhoseRunIsGone:
    """A queue outlives the rows it points at, and that is not a failure (gap A57).

    `reset-research` removes the runs; Redis keeps the entries. A worker started
    afterwards was replaying each dead job as an error with a full traceback — and
    raising made arq retry it — so twenty stale jobs bought two minutes of noise in
    which a real failure would have been invisible. There is nothing to recover: the run
    the job names is gone, and its absence is the answer.
    """

    @pytest.fixture
    def vanished(self, settings_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch) -> Any:
        """A worker context whose run lookup refuses, as a deleted run's would."""
        from aer.errors import ValidationError  # noqa: PLC0415
        from aer.services import runs as run_service  # noqa: PLC0415

        async def refuse(*args: Any, **kwargs: Any) -> Any:
            message = "No run 00000000-0000-0000-0000-000000000000."
            raise ValidationError(message, context={})

        monkeypatch.setattr(run_service, "run_state", refuse)
        return refuse

    async def test_the_job_is_discarded_rather_than_failed(
        self, vanished: Any, worker_context: Any
    ) -> None:
        from aer.worker import run_research  # noqa: PLC0415

        outcome = await run_research(worker_context, "00000000-0000-0000-0000-000000000000")

        assert outcome["status"] == "discarded"

    async def test_it_never_reaches_the_run_service(
        self, vanished: Any, worker_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raising is what made arq retry; executing anyway would be worse still."""
        from aer.services import runs as run_service  # noqa: PLC0415
        from aer.worker import run_research  # noqa: PLC0415

        async def never(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("a vanished run must not be executed")

        monkeypatch.setattr(run_service, "execute", never)

        await run_research(worker_context, "00000000-0000-0000-0000-000000000000")
