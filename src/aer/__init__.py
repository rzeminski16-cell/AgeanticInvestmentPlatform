"""Tracework Invest.

A local-first, auditable equity research platform for UK and US listed equities.

The organising principle of this codebase, stated once here and enforced throughout:
**deterministic Python owns every number and every fact; the language model owns
planning, interpretation, comparison, adversarial challenge and writing.** Retrieval,
parsing, arithmetic, date handling, citation verification, rendering and cost metering
are all ordinary, testable code. See ``docs/adr/0003``.
"""

from __future__ import annotations

import os
from typing import Final

# Every environment variable a BLAS implementation might read to size its worker pool.
# The whole family is set rather than just OpenBLAS's, because which library numpy is
# built against is a property of the wheel, not of this project, and a wheel swap should
# not quietly re-arm the deadlock described in :func:`_pin_blas_to_one_thread`.
_BLAS_THREAD_VARIABLES: Final[tuple[str, ...]] = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

# The escape hatch, should a future dependency genuinely need a threaded BLAS. Deliberately
# not a `Settings` field: this has to be decided before the first `import numpy`, which
# happens long before configuration is loaded.
_ALLOW_BLAS_THREADS: Final[str] = "AER_ALLOW_BLAS_THREADS"


def _pin_blas_to_one_thread() -> None:
    """Stop numpy's bundled BLAS starting a worker thread per core.

    **This prevents a deadlock, not a performance problem.** No module in this package
    does linear algebra, but numpy arrives transitively anyway — ``arelle`` needs it for
    iXBRL, ``fakeredis`` for its vector commands — and numpy's bundled OpenBLAS starts one
    ``blas_thread_server`` per core the moment the shared library loads.

    With those threads present, :func:`asyncio.create_subprocess_exec` in the parse sandbox
    can wedge permanently: the child watcher calls ``Thread.start()``, the operating system
    thread is never created even though ``_start_new_thread`` returns cleanly, and
    ``start()`` blocks forever on ``self._started.wait()``. The hang happens *before* the
    sandbox arms its own timeout, so the parse timeout cannot rescue it — the process is
    simply gone. It reproduces reliably in the test suite and the worker carries the same
    threads, so it is a production risk too. See ``docs/adr/0047``.

    Called at import time because OpenBLAS reads these variables once, when it loads, and
    ignores them afterwards. Setting them from a startup function would be too late for
    any process that had already touched numpy.
    """
    if os.environ.get(_ALLOW_BLAS_THREADS) == "1":
        return
    for variable in _BLAS_THREAD_VARIABLES:
        os.environ[variable] = "1"


_pin_blas_to_one_thread()

from aer.version import __version__, build_identity, git_sha, version  # noqa: E402

__all__ = ["__version__", "build_identity", "git_sha", "version"]
