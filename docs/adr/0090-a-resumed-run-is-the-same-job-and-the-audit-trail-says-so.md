# ADR 0090 — A resumed run is the same job, and the audit trail says so

**Status.** Accepted
**Date.** 2026-08-28
**Extends.** ADR 0016 (a run publishes itself, step by step)
**Resolves.** Roadmap §2.3, which was recorded Open. Required by roadmap §3.15.
**Amends.** Nothing in ADR 0014: what may change after the fact is unchanged, because
nothing here changes after the fact — see the decision on the audit record.

## Context

The engine has resumed runs since Phase 1, and does it well: every step commits its own
outcome, `_completed` skips a step whose row says `SUCCEEDED`, and a run that dies at step
nine re-executes step nine and nothing else. But that machinery has only ever served the
*accidental* case — the worker died — because nothing offers the deliberate one. The only
operator-facing path after a terminal failure is superseding, which creates a **new** job
precisely because the old one is a finished audit record. On the 2026-08-24 MSFT run a
failure at the red-team step, one step from the end, cost the entire run again: £8 of
research and drafting to recover a £1 step.

§2.3 named the actual work: not the plumbing, but a decision about the audit record. A job
row that says it failed, then later says it succeeded, is not obviously honest.

§3.15 then asks for the deliberate case in its strongest form — pause after *every* step,
print a diagnostic, and let whoever is at the terminal confirm before the next step spends
anything — and it depends on the same unanswered question: what does a deliberately paused,
not-failed job's own status record say?

Two facts about the existing vocabulary matter. `JobStatus.PAUSED` has been in the enum —
and in the native Postgres enum, and in the web console's vocabulary as "Paused" with a
warning tone — since Phase 1, documented as "resumable after a human decision", and nothing
has ever set it. And the honesty question was answered once already, for cancellation: a
cancellation is an appended row plus a hash-linked `audit_events` entry, never a rewrite of
what the run had said about itself.

## Decision

### `status` is where the run is now; history lives where history already lives

The apparent dishonesty of FAILED-then-SUCCEEDED dissolves once the column's meaning is
stated: `jobs.status` is the run's **current** state, not a summary of everything the run
has been. The record of what happened is already elsewhere, and none of it is rewritten by
resuming:

- every step keeps its row, its `attempt` counter, its per-attempt `error`, its timestamps
  and its costs — a step that failed four times and passed on the fifth says exactly that;
- every model call keeps its archived request and response;
- the hash-linked audit chain gains an event, below, recording that a person chose to
  continue the run and from what state.

A job row frozen at `FAILED` for ever would not be more honest. It would assert that the
run stopped for good, which stops being true the moment the operator resumes it.

### Resuming a run is a recorded decision, not a rewrite

`aer.services.resume.resume_run` is the supported way to re-enqueue the **same** job. It
appends a `run.resumed` event to the audit chain — the actor, the status the run is being
resumed from, and the reason if one was given — and returns the job to `QUEUED` for the
worker. Nothing else about the job changes; the engine's existing skip-what-succeeded rule
is what makes the continuation cheap.

What may be resumed follows from what resuming means:

- **`FAILED`** — the §2.3 case. Allowed, and the reason this ADR exists.
- **`PAUSED`, `AWAITING_APPROVAL`, `BUDGET_EXCEEDED`** — allowed; these were always
  documented as resumable, and the gates re-check their own approvals on re-entry.
- **`QUEUED`** — allowed. Enqueueing is deliberately non-fatal when Redis is away
  (`aer.queue.enqueue_run` returns `None`), so a recorded-but-never-queued job is a real
  state and this is its remedy.
- **`SUCCEEDED`** — refused. The run finished; running "again" is superseding, which exists.
- **`CANCELLED`** — refused. A standing cancellation is an operator's recorded decision,
  and the engine honours it at the first boundary anyway; a fresh run is superseding's job.
- **`RUNNING`** — refused. A worker may be mid-step, and a second execution of the same job
  would race the first over the very rows that make resumption safe.

### A deliberately paused, not-failed job says `PAUSED`

Settled once, for §2.3 and §3.15 both. `PAUSED` is non-terminal, so nothing supersedes the
run, `current_run` still points at it, cancellation still applies to it, and the console
renders the state it has always known how to render. The steps say nothing at all, because
nothing happened to a step: a deliberate pause is a fact about the *job*, and a pause that
wrote an error-shaped record onto a succeeded step would be the collapse of "waiting" into
"broken" that `JobStatus`'s own docstring warns against.

### Step mode is a property of the job, honoured by whoever executes it

`jobs.step_mode` is a boolean on the job row. When it is set, the engine pauses the run —
`PAUSED`, as above — after **every step that actually executes**, before the next one
spends anything. Steps already completed are skipped for free without pausing, exactly as
resumption always has; waves do not form, because a step-through that ran seven nodes at
once would have nothing coherent to confirm. It lives on the row rather than in the
invocation so that the pause holds wherever the run happens to execute: an operator who
approves a gate on the web while stepping in the terminal gets one more executed step and
another pause, not the rest of the run.

This is not a gate. Gates are domain approvals through `services/approvals`, each meaning
something specific; step mode is a breakpoint in the engine's own loop, applies to every
step — a wrong number out of `calculate` matters at least as much as a bad paragraph out of
`draft` — and records no approval, because confirming "continue" asserts nothing about the
content.

### The diagnostic is code, and it prints where the operator is sitting

`aer step`, `aer resume` and `aer diagnose` join the existing `job_id`-scoped CLI family
(`replay-run`, `acceptance`): typed readouts assembled entirely from what each step already
records — status, attempt, timing, cost, the step's stored output, its recorded error, and
each model call's tokens, stop reason and archived request/response hashes. No model judges
a step: an LLM here would add a paid call to steps that cost nothing, and the critique of
*content* is §3.13's, not this mechanism's.

## Consequences

- A late failure costs the failed step, not the run. The £8-to-recover-£1 shape of loss is
  gone, and the remedy is one command rather than a superseding run that re-spends.
- The audit chain now distinguishes three ways a run continues: crash recovery (no event —
  nothing was decided), a gate approval (the approval row), and a deliberate resume (the
  `run.resumed` event). A reader of the chain can reconstruct who chose to continue a
  failed run and when.
- `PAUSED` is set by the platform for the first time. Anything that treats the enum
  exhaustively was already obliged to handle it; the web vocabulary always has.
- Superseding narrows to its honest scope: running again after a report exists. It is no
  longer the workaround for a failure.
- A stepped run holds `PAUSED` between steps for as long as the operator thinks, which is
  the point. The SSE console treats it like any other non-terminal state and keeps showing
  the run.
