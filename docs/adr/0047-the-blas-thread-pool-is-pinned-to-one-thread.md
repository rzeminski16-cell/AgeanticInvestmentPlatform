# 0047 — The BLAS thread pool is pinned to one thread

Date: 2026-08-09
Status: Accepted

## Context

`src/aer/__init__.py` sets `OPENBLAS_NUM_THREADS` and its siblings to `1` at import time.
That is an unusual thing for an application package to do to its own environment, so it
needs recording.

No module in this package does linear algebra. numpy arrives transitively anyway:
`arelle-release` requires it for iXBRL, and under test `fakeredis` imports it eagerly for
its vector commands. numpy's bundled OpenBLAS starts one `blas_thread_server` per core the
moment the shared library loads, so `aer.worker` and `aer.api.app` each run with three
worker threads on a four-core machine, doing nothing, for the life of the process.

Those idle threads are not merely wasteful. With them present, `asyncio.create_subprocess_exec`
in the parse sandbox can wedge permanently. The failure was captured under gdb:

```
threading.py:999 in start              <- self._started.wait(), blocked forever
asyncio/unix_events.py:1393 in add_child_handler
asyncio/subprocess.py:224 in create_subprocess_exec
src/aer/extract/sandbox.py:194 in _run_child
```

`ThreadedChildWatcher` starts a thread per child to reap it. `_start_new_thread` returns
without raising, but `/proc/<pid>/status` reports `Threads: 4` — main plus the three
OpenBLAS servers. **The waitpid thread is never created**, so nothing ever sets `_started`
and `Thread.start()` blocks for good.

The critical consequence: this happens *before* the sandbox arms its parse timeout. The
timeout in `_run_child` guards `process.communicate()`, one statement later. A control
that cannot run is not a control, so a document that triggers this takes the process with
it and `ParseTimeoutError` is never raised.

Recorded as gap A16, and mis-scoped there as a test-ordering nuisance —
"`tests/test_extraction.py` must be run alone". It is not only that. The worker stores
artefacts through `LocalArtefactStore`, whose every filesystem operation goes through
`asyncio.to_thread`, and then parses them through the sandbox. That is precisely the
sequence that deadlocks, in the process that does the real work.

### What was measured

A prefix bisect over the 41 test files that sort before `tests/test_extraction.py` found
the boundary at seven files, and the minimal reproduction is two:
`tests/test_artefact_store.py` followed by `tests/test_extraction.py`.

The cure was then checked in both directions, five runs:

| Configuration | Outcome |
|---|---|
| `OPENBLAS_NUM_THREADS=1`, three runs | 108 passed, 3.5s / 3.9s / 3.6s |
| Default BLAS threading, two runs | hung both times, killed at 200s |

**The mechanism is not fully characterised.** A standalone reproduction — numpy imported,
400 `asyncio.to_thread` calls and two subprocess spawns per loop, forty loops — completes
every time. So thread churn plus a fork plus OpenBLAS is not by itself sufficient; some
further ingredient of the pytest process is involved and has not been isolated. What is
established is the failure mode, and that removing the BLAS threads removes it reliably.

## Decision

`aer/__init__.py` sets the whole family of BLAS thread-count variables to `1` before
anything can import numpy, overriding any inherited value.

**Overriding rather than defaulting.** `os.environ.setdefault` would leave the deadlock
live on exactly those machines whose owners had set `OMP_NUM_THREADS` for some other
project — the worst possible distribution of a footgun.

**At import, not at startup.** OpenBLAS reads these variables once, when the shared library
loads. A `configure()` call in an entry point would already be too late for any process
that had touched numpy on the way in.

**The whole family, not just OpenBLAS.** Which BLAS numpy is built against is a property
of the wheel. A wheel swap should not silently re-arm this.

**One escape hatch**, `AER_ALLOW_BLAS_THREADS=1`, exactly that value, for a future
dependency that genuinely needs a threaded BLAS. Not a `Settings` field: the decision has
to be made before configuration exists.

## Consequences

The parse sandbox's timeout can now actually fire, which is the point. The full unit suite
runs in one process, so `tests/test_extraction.py` no longer needs separate handling — and
`just test` and the CI test step, which never excluded it, stop being intermittently
dependent on the random test order for whether they hang.

If this project ever does adopt numpy for real numerical work, the pin will make it
single-threaded. That is the correct default here regardless — a report is one company at
a time, and the arithmetic that matters is `Decimal`, not matrices — but it is a decision
to revisit deliberately rather than discover.

The pin is a workaround for a defect that has not been root-caused. It is honest to say so:
if the underlying interaction is ever identified and fixed upstream, this becomes
unnecessary, and `tests/test_blas_threads.py` is where to start reading.
