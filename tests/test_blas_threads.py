"""The BLAS thread pool is pinned to one thread, and why that is a correctness control.

Gap A16 presented as "``tests/test_extraction.py`` deadlocks in a full-suite run", which
reads like a test-ordering nuisance. It is not. numpy's bundled OpenBLAS starts a worker
thread per core when it loads, and with those threads present the parse sandbox's
``asyncio.create_subprocess_exec`` can wedge for good: the child watcher calls
``Thread.start()``, the operating system thread is never created, and ``start()`` blocks
forever on ``self._started.wait()``. That happens *before* the sandbox arms its parse
timeout, so nothing rescues it.

The worker imports numpy too — transitively, through ``arelle`` — so the same three BLAS
threads sit in the process that stores artefacts and then parses them. These tests hold the
guard that keeps them out. See ``docs/adr/0047``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from aer import _ALLOW_BLAS_THREADS, _BLAS_THREAD_VARIABLES, _pin_blas_to_one_thread

# Long enough for an interpreter start plus a numpy import on a loaded machine, short enough
# that a returning deadlock is reported as a failure rather than a hung CI job.
_CHILD_TIMEOUT_SECONDS = 120


def _run_child(body: str, *, env: dict[str, str] | None = None) -> str:
    """Run a snippet in a fresh interpreter and return its stdout.

    A subprocess rather than an in-process check because the thing under test happens at
    library load time: once numpy is imported into *this* process, its thread pool is
    already sized and no amount of environment fiddling changes it.

    The child starts with every BLAS variable **removed**, not inherited. This process has
    them pinned already — importing ``aer`` is what these tests exist to check — so a child
    that inherited them would report one thread whether or not the pin still worked, and
    the counterfactual below would be unable to fail.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _BLAS_THREAD_VARIABLES and key != _ALLOW_BLAS_THREADS
    }
    if env:
        environment.update(env)

    completed = subprocess.run(  # noqa: S603 -- our own snippet, our own interpreter
        [sys.executable, "-c", textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=_CHILD_TIMEOUT_SECONDS,
        check=True,
        env=environment,
    )
    return completed.stdout.strip()


class TestThePin:
    """The function itself, in isolation."""

    def test_every_blas_variable_is_pinned_to_one(self) -> None:
        # Asserted against the real environment: importing `aer` is what set these, and a
        # test that called the function first would be testing the call, not the import.
        for variable in _BLAS_THREAD_VARIABLES:
            assert os.environ[variable] == "1", variable

    def test_an_inherited_thread_count_is_overridden_not_respected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stray ``OMP_NUM_THREADS=8`` in an operator's shell must not re-arm the hang.

        The tempting implementation is "set it if it is unset". That would leave the
        deadlock live on exactly the machines whose owners had tuned their environment for
        some other project, which is the worst possible distribution of a footgun.
        """
        for variable in _BLAS_THREAD_VARIABLES:
            monkeypatch.setenv(variable, "8")

        _pin_blas_to_one_thread()

        for variable in _BLAS_THREAD_VARIABLES:
            assert os.environ[variable] == "1", variable

    def test_the_escape_hatch_leaves_the_environment_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_ALLOW_BLAS_THREADS, "1")
        for variable in _BLAS_THREAD_VARIABLES:
            monkeypatch.setenv(variable, "8")

        _pin_blas_to_one_thread()

        for variable in _BLAS_THREAD_VARIABLES:
            assert os.environ[variable] == "8", variable

    def test_the_escape_hatch_needs_the_exact_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "true", "yes" and "0" all mean "I did not think about this". Only "1" opens it.
        monkeypatch.setenv(_ALLOW_BLAS_THREADS, "true")
        monkeypatch.setenv("OPENBLAS_NUM_THREADS", "8")

        _pin_blas_to_one_thread()

        assert os.environ["OPENBLAS_NUM_THREADS"] == "1"


class TestTheProcessItActuallyProtects:
    """What the pin is for: a process that imports numpy stays single-threaded."""

    def test_importing_aer_before_numpy_leaves_one_os_thread(self) -> None:
        threads = _run_child("""
            import aer  # noqa: F401  -- the import under test
            import numpy  # noqa: F401
            import os
            print(len(os.listdir("/proc/self/task")))
        """)

        assert threads == "1"

    @pytest.mark.skipif(
        (os.cpu_count() or 1) < 2,
        reason="OpenBLAS starts no worker threads on a single-core machine, so the "
        "counterfactual cannot distinguish a working pin from a broken one",
    )
    def test_numpy_without_the_pin_does_start_worker_threads(self) -> None:
        """The counterfactual, without which the test above proves nothing.

        If numpy were quietly no longer pulling in a threaded BLAS, the assertion that a
        pinned process has one thread would pass for the wrong reason and keep passing
        after somebody deleted the pin.
        """
        threads = _run_child(
            """
            import numpy  # noqa: F401
            import os
            print(len(os.listdir("/proc/self/task")))
            """,
            env={_ALLOW_BLAS_THREADS: "1"},
        )

        assert int(threads) > 1

    def test_the_worker_process_carries_no_blas_threads(self) -> None:
        """The arq worker is the process that stores artefacts and then parses them.

        That is the exact sequence that deadlocks, so it is the process worth naming in a
        test rather than leaving to inference from the library-level check above.
        """
        threads = _run_child("""
            import aer.worker  # noqa: F401
            import os
            print(len(os.listdir("/proc/self/task")))
        """)

        assert threads == "1"
