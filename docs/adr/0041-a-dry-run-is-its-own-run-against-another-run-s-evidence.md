# 0041 — A dry run is its own run against another run's evidence

Date: 2026-08-06. Status: accepted.

## Context

§2.12's authoring loop — write a section, enable it, see it appear with its own cited
evidence — costs a full research run per iteration. Nobody iterates at that price, so in
practice skills would be written once, badly, and left. Task 43's dry run makes the loop
minutes: execute one section against evidence a previous run already acquired.

That raises a question the rest of the platform has not had to answer. Every other piece
of work belongs to the run that produced it. A dry run *reads* one run and *produces*
something that is not part of it, and both halves have to be true at once: it must see
what the section would really see, and it must not be able to write into a run whose
report may already have been approved.

## Decision

**A dry run is a real run of one section, in its own job.** It creates a job, a plan, a
pin and a section row of its own, marked `skill_dry_run_v1`, and executes through
`execute_custom_section` — the same executor, the same composer, the same claim and
citation services, the same renderer a report uses. Isolation is therefore structural
rather than careful: nothing it writes carries the source run's id, because it never has
the source run's id to write. The alternative considered was executing against the source
job inside a transaction rolled back at the end, which is smaller but wrong in the way
that matters — rollback isolation is a discipline every future edit can break silently,
and it would have to keep the cost rows anyway, so the "nothing persists" simplicity is
not real.

**Evidence comes from the source run, and the executor is told so explicitly.** Facts and
sources belong to a *request* and are visible to any job under it; recorded calculations
belong to a *job*. So `execute_custom_section` gained an `evidence_job_id` argument — the
run whose figures this section may cite — defaulting to the executing job, which is the
only answer a real run has. An explicit argument rather than a widened query, because
"which run's numbers is this section allowed to cite?" is exactly the question a reader
of a citation is entitled to have answered, and a query that quietly matched more would
be answering it differently without saying so.

**It spends real money, so it is metered and capped like everything else.** The model call
is a real call: the same `BudgetGuard` runs before it against the same per-request cap,
the same meter writes the same `costs` rows, and the spend counts towards the request. A
rehearsal whose cost was invisible would be the one reliable way under a cap.

**The web process may spend for this, and only for this.** Every other spending path is
enqueued to the worker, because a request handler that starts a research run is a handler
that times out halfway through one. A dry run is a single bounded call whose entire point
is that the author waits for it, so the provider is built lazily in the web process — on
first use, never at start-up, so a deployment with no key still serves every page that
does not need one.

**The editor's preview is the composer, not a description of it.** `compose_for_version`
became public and the preview calls it over a version row built exactly as the save path
builds one. A preview computed by a second implementation would eventually disagree with
what a run composes, and the symptom would be a section behaving unlike its preview with
nothing to point at. A test holds the preview against a pin a real plan produced, rather
than against the composer called twice.

**Import shows a diff and requires confirming it** (threat T20). The confirmation hash
covers the key, the incoming file *and* the stored version it replaces, so a confirmation
made against a diff that has since gone stale is refused — the same shape, and the same
argument, as a gate approval carrying a payload hash.

## Consequences

The loop is a minute and a few pence. The dry-run job is a first-class, inspectable
object: its plan says what it was a rehearsal of, its pin says which skill version and
what policy, its costs say what it spent. Because it is marked, every surface that groups
runs can exclude it, and the editor's own run picker does.

The costs: two extra rows per rehearsal (a plan and a job) on the request's history, and a
web process that holds a provider. The known limit is that a dry run sees only what the
chosen run acquired — a skill whose section needs evidence no previous run gathered will
look thinner in rehearsal than in a real run that would have gone and fetched it. That is
the honest shape of the trade, and the section's insufficiency banner says so in the
rehearsal exactly as it would in the report.
