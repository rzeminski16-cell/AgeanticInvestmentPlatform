# 14. What may change after the fact

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Two features arrived together because an operator asked for both in one sentence: a way to
stop a run that is going, and a way to correct or remove a saved request before running it.
They look like ordinary CRUD. They are not, and treating them as CRUD would quietly break
the property the whole platform exists to provide.

The platform's claim is that a report can be reproduced: every number traces to a formula,
every fact to a hashed artefact, and every decision to a recorded approval of a specific
payload. That claim rests on nothing upstream of a report changing after the fact. A
research request is the root of that chain — it fixes the as-of date, the point-in-time
rule, the currency and the cost ceiling that everything downstream was gathered under.

So the question is not "should these be editable" but **where the line is between intent
that has not been acted on and a record of something that happened**.

There was also a concrete engineering discovery. The obvious way to cancel a run is a
column on `jobs`. It does not work, and the reason is not subtle: `aer.services.runs.execute`
sets `jobs.status = RUNNING` and the worker commits once, at the end of the run. Postgres
holds that row's lock for the transaction's life, so an `UPDATE jobs` from the web process
blocks until the run it is trying to stop has finished on its own. This was measured with
two `psql` sessions, not assumed — the second session fails with `canceling statement due
to lock timeout`.

## Decision

### The line is: what a run left behind

A research request may be edited or deleted until a run has produced something durable.
The check lives in `aer.services.requests.immutable_reason`, which returns the sentence
explaining the refusal rather than a boolean — the API puts it in the problem detail and
the detail page puts it where the buttons would be. Five things freeze it, each named
explicitly in the message:

| Condition | Why it freezes the request |
|---|---|
| A run that is not terminal | A worker may be reading these values right now |
| A report exists | The report cites the terms it was researched under |
| A source document exists | The as-of date and point-in-time rule are what admitted it |
| A cost exists | Deleting would erase spend; editing means it was made against other terms |
| An approval exists | A decision would end up attached to something you never saw |

**This is a correction.** The first version froze a request as soon as a `jobs` row
existed, using that as a proxy for "research happened". The proxy is wrong at exactly the
boundary that matters, and using the feature found it within the hour: cancel a run and the
request became permanently uneditable and undeletable, while `start_run` returned the dead
job so it could not be re-run either. A request you can neither run, fix nor throw away.

A job that gathered nothing, cited nothing and spent nothing leaves nothing an edit could
falsify. The invariant was never "a job existed" — it was "nothing that was researched can
be rewritten", and the conditions above are that invariant stated directly instead of
approximated.

Editing is a **whole-payload replace**, validated by exactly the code that validates a
creation. A partial update would need a rule for what an absent field means, and "leave it"
and "clear it" are both defensible — which is precisely why neither should be guessed. A
rule enforced only at creation is a rule anyone can get around by creating something valid
and then editing it.

### One *report* per request, not one job

`start_run` returns the existing job for a run that is live or that produced a report, and
creates a new one when the newest job is terminal with no report. The Phase 1 rule was
always about reports — a second run of a request that already produced one needs a story
about which report is current, and there still is not one. A cancelled or failed run
produced no report, so there is nothing to choose between.

Superseding creates a **new** job rather than resurrecting the old one, for two reasons
that are both fatal to reuse: the row says it finished, with a time, and rewriting that
would contradict the audit record; and a cancelled job still carries its row in
`job_cancellations`, so the engine would stop it again on its first step.

### Deleting refuses rather than cascading

`research_requests` cascades to jobs, plans, sources and reports. A delete allowed after a
run would take the evidence with it. Refusing is threat T16's retention rule arriving early
in its safest possible form: **nothing with evidence behind it can be removed by any code
path in the platform.** A real retention policy — deleting a report *and* its artefacts,
provably, with a record — remains Phase 6 work.

The audit entry outlives the row. `audit_events.request_id` is deliberately a plain column
with no foreign key, so "this request existed and was deleted, and here is what it was"
survives the deletion.

### Cancelling is a request, recorded in its own table

`job_cancellations` holds one row per job: who asked, why, and when. The engine reads it
before each step and stops.

- **Its own table, because of the lock.** Nobody else writes this row, so the web process
  never waits. That is the entire justification, and it is empirical.
- **Between steps, not during one.** An HTTP fetch or a model call already in flight cannot
  be interrupted from another process without abandoning work already paid for. So a step
  that has started finishes, and only the next one is skipped. This is the finest
  granularity that can be reported *honestly*.
- **Two timestamps, both kept.** `requested_at` on the cancellation, `finished_at` on the
  job. The gap between them is real, and a console claiming the run stopped the instant the
  button was pressed would be lying for as long as the current step took.
- **A finished run cannot be cancelled.** Refused with a 409 — not because the write would
  fail, but because it would put something in the audit trail that did not happen.

### A new error class for "the state is wrong, not the body"

`ConflictError` (409) joins `ValidationError` (422). The distinction earns its place: a 422
tells the caller to change what they sent, and resubmitting the same body would be
pointless. A 409 tells them the body was never the problem — a run had already finished, a
request had already been researched — and only the resource's state could change the
answer.

## Consequences

**Good.**

- A mistyped ticker costs nothing to fix, which is the difference between a tool people use
  and a tool people work around.
- Cancelling is instant to record and does not fight the worker for a lock.
- The audit trail gains two event types that answer questions timestamps cannot:
  `request.edited` carries before-and-after for each changed field, and
  `run.cancellation_requested` carries the run's status at the moment the operator asked.
- The reproducibility claim is unweakened: nothing that was researched can be edited, and
  nothing with evidence can be deleted.

**Costs, accepted.**

- "Fix a typo in a request that has already produced something" is not supported. The
  answer is a new request. That is the right answer, and it will still occasionally be
  annoying.
- Cancellation costs one extra `SELECT` per step. Steps take seconds to minutes; this is
  not measurable.
- A run cancelled mid-step still pays for that step. Refunding it is not possible and
  pretending otherwise would misreport the spend. In practice this is the common case: the
  planner usually runs before anyone presses stop, so a cancelled request is usually frozen
  by its *spend* rather than freed by having gathered nothing.
- `immutable_reason` issues up to five queries where it used to issue one. They are indexed
  lookups on a page an operator opens a handful of times a day, and the first is skipped
  entirely when no job exists — which is the common case for a draft.
- Two clicks on "Start a new run" in the same instant can create two jobs, where the old
  return-the-existing behaviour was idempotent. The window is one round trip and the second
  click sees a queued job, so it closes itself; a unique constraint would be the fix if it
  ever stops being theoretical.

**Deliberately not built.**

- **Pause and resume on demand.** `docs/PLAN.md` §2.7 lists it beside cancel. It needs a
  story about what happens to a half-gathered evidence set, and inventing one now would be
  guessing. The gates already pause runs where pausing has a defined meaning.
- **Editing a request that has run, into a new one.** "Clone and edit" is an obvious
  convenience and a separate decision; it must produce a *new* request, and how much of the
  original's context it should carry is not obvious enough to settle here.
