# 0047 — A memory cap is a one-way door, so only the child may walk through it

Date: 2026-08-09
Status: Accepted

*The first version of this ADR, written earlier the same day, blamed numpy's OpenBLAS
thread pool. That was wrong, and the wrong diagnosis is kept visible below because the way
it fooled a two-file experiment is the useful part.*

## Context

Gap A16 recorded that `tests/test_extraction.py` deadlocks in a full-suite run, attributed
to "the parse sandbox's child and pytest's threads blocking each other", with the mitigation
"it must be run alone".

The real cause is one line of test code. `tests/test_extraction.py` asserted the memory cap
by calling the child's helper directly, in the pytest process:

```python
assert _child._apply_memory_cap(1 << 30) is (sys.platform != "win32")
```

`_apply_memory_cap` calls `resource.setrlimit(RLIMIT_AS, (limit, limit))`. It sets the
**hard** limit as well as the soft one, and an unprivileged process can never raise a hard
limit again. So one assertion capped the whole pytest session's address space at 1 GiB,
permanently.

Nothing fails at that moment, which is what made it hard to find. Failure arrives later,
when the process grows past the cap — measured at `VmSize: 1225312 kB` by the time the
suite reaches the sandbox tests, comfortably over 1 GiB. From there every `mmap` fails.

What needs `mmap` first is not allocation but **thread creation**: `pthread_create` cannot
obtain a stack. `_thread.start_new_thread` returns without raising, no operating system
thread appears — `/proc/<pid>/status` reports `Threads: 1` — and `Thread.start()` blocks
for ever on `self._started.wait()`:

```
threading.py:999 in start              <- self._started.wait()
asyncio/unix_events.py:1393 in add_child_handler
asyncio/subprocess.py:224 in create_subprocess_exec
src/aer/extract/sandbox.py:194 in _run_child
```

The victim is `test_a_child_that_does_not_answer_in_time_is_killed`, two classes below the
test that set the cap, because `ThreadedChildWatcher` starts a thread per child. It wedges
at `sandbox.py:194`, one statement *before* the `asyncio.wait_for` that arms the parse
timeout, so the sandbox's own control cannot fire. A memory limit therefore presents as an
unkillable hang rather than a `MemoryError`.

### The wrong turn, and why it was convincing

The investigation first found that numpy's bundled OpenBLAS starts a worker thread per
core, and that pinning it to one thread made a two-file reproduction
(`test_artefact_store.py` + `test_extraction.py`) pass three times against two hangs. That
looked conclusive and was not: OpenBLAS reserves a large amount of address space, so
removing it simply kept that short run *under* the 1 GiB cap. The cap was still being set;
nothing had been fixed. The full suite hung exactly as before, with the process now
starting single-threaded — which is what finally disproved it.

Two lessons worth keeping. A cure that works on a shrunken reproduction can be acting on
the size of the reproduction rather than on the defect. And a resource limit is invisible
until something crosses it, so "what changed just before the failure" points at the wrong
place by construction — the change was forty test files earlier.

## Decision

**The POSIX assertion runs in a child process.** That is where `_apply_memory_cap` runs in
production, so testing it anywhere else was testing it in the wrong place to begin with.
The child now also reports the limit it ended up with, so the test checks the cap was
applied rather than only that a boolean came back. The Windows branch stays in-process: it
returns before touching `resource`, so there is no limit to leak.

**`_apply_memory_cap` documents that it is a one-way door**, and why a longer-lived caller
sees a hang instead of a `MemoryError`.

**The BLAS pin from the first version of this ADR is retained**, on its own smaller merits
rather than as a fix. No module here does linear algebra; numpy arrives only transitively,
through `arelle` for iXBRL and `fakeredis` under test, and the worker was carrying three
idle `blas_thread_server` threads and their address space for nothing. Removing that is
worth keeping. It is not what closes A16, and this ADR no longer claims it is.

## Consequences

`tests/test_extraction.py` no longer poisons the process it runs in, so the full unit suite
runs in one process. `just test` and the CI test step never actually excluded that file —
both run `pytest --ignore=tests/e2e` — so this was not merely inconvenient: it was a
deadlock the suite could hit whenever ordering brought the address space past the cap
before the sandbox tests.

The general rule this leaves behind: **a test for a process-wide, irreversible side effect
belongs in a subprocess.** `setrlimit`, `setsid`, `chdir` to a deleted directory, signal
disposition, `sys.setrecursionlimit` downward — all of them outlive the test that set them,
and pytest offers no fixture that can undo a lowered hard limit.
