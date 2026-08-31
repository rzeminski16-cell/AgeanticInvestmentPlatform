# Troubleshooting

The failures that are expected rather than exceptional, and what each one means.

---

## Nothing happens when I start a run

**The worker is not running.** The web process enqueues; the worker executes. Both must be
up:

```bash
uv run aer serve                        # or: just dev
uv run arq aer.worker.WorkerSettings    # or: just worker
```

The console will sit at "queued" indefinitely with no worker attached. It is not broken and
nothing is lost — start the worker and it picks the job up.

## The console has not changed in five minutes

Check two things on the page before assuming a failure:

1. **Is a step marked as running**, with a ticking clock? A step that calls a model
   genuinely changes nothing for minutes at a time. Drafting is the longest.
2. **What does "server last checked at…" say?** That line is driven by a heartbeat on the
   event stream. If it is current, the server is alive and the run is thinking. If it has
   stopped advancing, the worker has died.

**The worker's log is the terminal running it.** There is no log file. A failure appears
there first and in full, with the traceback the console can only summarise.

## The run failed

Look at the worker terminal first — the full traceback is there and nowhere else. Then:

- **A failed run is resumable.** Steps are recorded as they complete. Restart the worker
  and it resumes from the last completed step rather than the beginning.
- **A terminal run with no report can be superseded**, which re-runs the plan step on the
  same work order. If you fixed a skill in between, the re-plan picks up the fix rather
  than reusing the version you replaced.
- Every response carries an `X-Request-ID`, and that same id appears in every log line for
  that request and in the body of every error. An error you can see is an error you can
  trace.

## "no user exists" or a startup refusal

```bash
uv run aer seed-user --email you@example.com
```

Deliberate errors return their message, because "run `aer seed-user`" is the entire value
of that error. Unexpected ones return a generic message and the request id, never an
internal message or a stack trace — the full traceback goes to the log.

## The application will not start

**`AER_HTTP_USER_AGENT` is not set.** It is the only required setting and it has no
default: the SEC requires a descriptive User-Agent identifying the operator, and a shared
placeholder would get everybody using it blocked together.

**`AER_SECRET_KEY` with `AER_APP_ENV=production`.** Required there, because an ephemeral
key would differ between workers and change on every deploy.

Run `just config` to see the effective configuration, secrets masked.

## `/readyz` returns 503

It returns a per-dependency breakdown. Usually Postgres or Redis is not up:

```bash
just up        # start both
just health    # pg_isready + redis ping
```

`/healthz` is different: it answers 200 while the process can answer at all, and touches
nothing external. The landing page also renders with the database down and says what is
wrong, which is exactly the case it was built for.

## A migration error after pulling

```bash
uv run alembic upgrade head    # or: just migrate
just migrate-status            # what revision am I on?
```

## An API key error mid-run

API keys are deliberately not required at startup, so a missing one fails at the point the
provider is used, naming the variable to set. You are never blocked on credentials for a
service you have not reached yet — but you can be stopped mid-run by one. The plan at gate
1 names the sources it intends to use; that is the moment to notice.

## The run stopped: the Anthropic account is out of credit

The console names it, and the sentence is the whole of the diagnosis:

> The Anthropic account the API key belongs to is out of credit, so no model call can be
> made — not even the free token count this failed on.

**This is not one of the platform's own budget caps.** Those stop a run *before* a call and
say so (below). This is the provider refusing the account, and no setting here clears it.

Two things make it look like it recurs after a top-up:

- **The credit went somewhere else.** A key belongs to one organisation and one workspace,
  and a balance added to a different one leaves this failing identically. Check the key in
  `.env` against the organisation you topped up, at
  [platform.claude.com](https://platform.claude.com/settings/keys). This is by far the
  commonest cause of a second identical failure.
- **Nothing has run since.** Continuing re-queues the run; a worker executes it. If the
  worker is stopped — which stepping through a run under developer mode requires — the run
  sits queued and the step keeps the error from its last attempt. The console no longer
  raises the red alert once a run has been re-queued, but the step row still shows what
  happened last time, because that is what happened last time.

Retrying costs nothing while the balance is empty: the first thing every model call does is
count its tokens, that count is free, and it is what fails. So the cheapest confirmation
that a top-up landed is to continue the run.

## The run stopped: an Anthropic usage limit was reached

> You have reached your specified API usage limits. You will regain access on
> 2026-09-01 at 00:00 UTC.

**"Specified" means specified by you.** This is not a subscription quota, and prepaid
credit does not exempt you from it: an Anthropic account carries *spend limits* — one on the
organisation, and optionally one per workspace — which are monthly budgets. They exist so a
runaway job cannot drain a balance, and they turn over at the start of the UTC month. That
is where the reset date comes from, and why a date appears on an account you top up rather
than subscribe to.

It is also not one of this platform's caps. Those refuse a step *before* the call and name
the cap and the scope; this one arrives from the other side of the wire, and raising a
budget here will not release it.

Either raise or clear the limit at
[platform.claude.com/settings/limits](https://platform.claude.com/settings/limits) — check
the workspace as well as the organisation, since the tighter of the two wins — or wait for
the reset the message names. The run keeps every completed step in the meantime.

## The run refuses to start a step: budget

The engine refuses to start a step whose projected cost would break the run's cap or the
month's. This is a cap, not a warning. Either raise the ceiling deliberately or accept the
run stops here.

## A figure is missing from the report

This is usually correct behaviour, not a fault. See
[reading a report → reading a refusal](reading-a-report.md#reading-a-refusal). The short
version: the page always states its reason, and the reasons are different from each other —
withheld for implausibility, not meaningful for the sector, genuinely not in the filing,
or a thin evidence pack.

## Comparables are empty

Either the peer set has not been confirmed at its gate, or the licensed feed is not
configured. Comparables are computed only behind a human-confirmed peer set.

## PDF rendering fails

WeasyPrint needs its native stack present — Pango, cairo, GDK-PixBuf. On Windows that is
the GTK runtime; on Debian and Ubuntu,
`libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0`. The Markdown and HTML
archives are unaffected.

## Checking the platform itself

```bash
just verify-artefacts   # re-read every archived artefact; does it still hash to its name?
just verify-audit       # does every audit record still link to the one before it?
just replay-run <id>    # re-derive a run from its own record
just verify-backup <d>  # re-hash a backup against its manifest; needs no database
```

If any of those disagree with what is stored, that is a real problem and worth stopping
for.

---

If none of this covers it, [`../developers/testing-by-hand.md`](../developers/testing-by-hand.md)
walks the whole system from a clean checkout and will usually isolate where it broke.
