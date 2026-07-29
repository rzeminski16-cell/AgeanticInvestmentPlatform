# 16. A run publishes itself, step by step

- **Status:** Accepted
- **Date:** 2026-07-29
- **Amends:** ADR 0014 (what may change after the fact)

## Context

An operator started a real run, watched the provider bill them for it, and watched the run
console say `QUEUED` for the whole minute the planner took. Their question was the right one:
*should this not update?*

It should. The cause was a single line in `aer.worker.run_research`:

```python
async with session_factory() as session:
    outcome = await run_service.execute(...)
    await session.commit()  # once, at the end
```

The engine only ever called `session.flush()`. Postgres publishes nothing until commit, so
every state a run reached — `jobs.status = RUNNING`, each `job_steps` row, each `costs` row —
was invisible to the web process until the run stopped at a gate. The console then jumped
from `QUEUED` straight to **Waiting for you**, skipping everything it exists to show.

**This is the second consequence of a fact ADR 0014 already recorded.** That ADR measured the
`jobs` row lock with two `psql` sessions and concluded, correctly, that a cancellation could
not be an `UPDATE` on `jobs` — the worker holds the lock, so the web process would block.
What it did not follow through: a row that is locked and uncommitted cannot be *read* as
changed either. One measurement, two consequences, and only one of them was acted on. The
cancellation design was right; the reporting consequence went unnoticed until a live run
surfaced it.

Two further defects fell out of the same boundary once it was examined.

**A failure was rolled back along with everything else.** `WorkflowEngine._fail` recorded
`FAILED`, then re-raised. The exception left the worker's `async with` without reaching its
commit, so SQLAlchemy rolled the session back on the way out and the database went on saying
`QUEUED` for a run that had died. The log line was the only evidence it had happened — and on
Windows that is a console scrollback buffer. The operator sees a run that will never move and
never explain itself. This was live too: the planner failed on a schema constraint, and the
page did not change.

**A crash between gates re-spent money.** The worker's docstring justified one transaction by
saying a run that dies "resumes from the last step that succeeded". Between gates that was
false. `WorkflowEngine._completed` skips a step whose row says `SUCCEEDED`, so the skip
depends entirely on the row surviving — and a rollback took every completed step with it. The
next attempt started from the first step and paid for the same fetches and the same model
calls again. The sentence was true only across a gate, because the gate's pause happened to be
followed by the worker's commit.

## Decision

### The step is the unit of publication

`WorkflowEngine._publish` commits, and it is called at every boundary where the run's recorded
state is whole: a step finished, a gate reached, a cancellation honoured, a failure recorded.
`aer.services.runs.execute` does the same for the two transitions it owns itself, `RUNNING`
and `SUCCEEDED`. The rule is uniform and easy to check: **whoever changes a run's state
publishes it.**

The objection the old docstring raised — "a half-written step's output could be read as
complete" — is real but does not apply. It describes a commit *inside* a step, which nothing
here does. A step's row, its `costs` rows and its `agent_runs` row all reach their final
values before `_publish`, so a reader on another connection sees a finished step or no step.

That leaves the actual trade-off, which is smaller than the old comment implied: a run
abandoned mid-step leaves that step's row saying `RUNNING` with no `finished_at`. It is
retried on resume, because `_completed` only skips `SUCCEEDED`. A stale `RUNNING` row is a
better artefact than a silently vanished one — it records that an attempt happened.

### The lock argument survives, with a smaller window

Committing per step releases the `jobs` row lock at each boundary, so the web process could
now update `jobs` between steps. `job_cancellations` stays a table of its own regardless: a
step is a model call or an HTTP fetch, up to minutes long, and the lock is still held for all
of it. A cancel that wrote to `jobs` would still block for the length of the step it was
trying to stop, which is precisely the interval in which cancelling is worth anything. ADR
0014's reasoning holds; only the size of the window changed.

### Testing it requires a second connection, and that is the whole point

`tests/test_run_visibility.py` reads every assertion through its own session on its own
connection, because a test that asserted on the session the engine wrote to would pass with
the commit in either place. That is exactly how three defects survived 1,369 tests: the suite
drove runs through a single session and checked the results in it.

Two of the tests need a view from *inside* a run, since anything observed after `execute`
returns is published by the worker's final commit and therefore looks identical under both
designs:

| Test | How it sees mid-run |
|---|---|
| `RUNNING` is visible before the first step ends | A provider wrapper reads the committed status from another connection during the model call |
| Steps before a failure survive | `_calculate` is made to raise on the second leg, leaving `acquire` and `extract` behind it |

All five fail with the commits reverted to flushes and pass with them in place. That was
checked by reverting them, not assumed.

## Consequences

**Good.**

- The console reports what is happening, which is the only reason it exists.
- A failed run says so, in the database, with its error detail — so the page explains itself
  without the operator going to the worker's terminal.
- A resume no longer re-spends. This is money, not tidiness: on a £100/month ceiling, re-running
  a leg's fetches and model calls after a crash is a measurable fraction of a report.
- Cancellation is honoured and published at the same instant, so the console cannot show a run
  as live after the engine has stopped it.

**Costs, accepted.**

- More commits per run — one per step rather than one per leg. A step takes seconds to minutes;
  the commit is not measurable against it.
- A run abandoned mid-step leaves a `RUNNING` step row behind, as described above. No cleanup
  sweep exists yet; the row is overwritten on the next attempt, and `attempt` records that it
  happened twice.
- `execute` now commits, so it is no longer safe to call inside a caller's own transaction and
  expect to roll it back. Nothing does — the worker owns its session, and the test harness uses
  `join_transaction_mode="create_savepoint"` so a commit releases a savepoint inside a
  transaction that is still rolled back. Both are deliberate and both are load-bearing.

**Not fixed here, and stated rather than left to be discovered.**

- The browser suite leaks a Postgres connection somewhere in its live-server harness. With
  `filterwarnings = ["error"]` the resulting `ResourceWarning` surfaces as a failure or an
  error on whichever test happens to trigger the collection, so the e2e run reports one
  failure and two errors that move between tests. `-W ignore::ResourceWarning` makes all of
  them pass, and the same failures appear with this ADR's changes stashed, so it is neither
  caused nor fixed by them. It needs its own look at engine disposal across the server
  thread's loop — the harness is already built around that problem and its comments say so.
