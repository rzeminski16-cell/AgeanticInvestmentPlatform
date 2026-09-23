# ADR 0131 — A refresh is a second run on the same request that carries the last run's decisions forward and pays only for what moved

- **Status:** Accepted
- **Date:** 2026-09-23
- **Applies:** ADR 0016 (the vertical slice), ADR 0072 (a work order is the run root),
  ADR 0091 (the critic and the revise pass), ADR 0110 (a run is dated by the platform),
  ADR 0115 (the adversary and the appendix), ADR 0116 (a report is superseded with a reason
  and stays readable), ADR 0119 (a printed excerpt re-enters as data), ADR 0123 (a decision at
  a gate is superseded, never re-asserted), ADR 0125 (the document is checked against itself)
- **Builds:** F4, *The refresh* (`docs/V1.0_Alpha/04-feature-specifications.md`), mechanism §1
  of `08-mechanisms.md`, data model Gap 2, page specifications §5.6, §8 and §9

## Context

F4 asks for a way to update a company already researched without paying for a full run:
acquire only what is new, recompute every calculation, re-draft only the sections whose inputs
moved, lead with a change summary composed from the diff, and supersede the prior report rather
than overwrite it. The mechanism fixes what *moved* means — a relative change of two per cent,
a sign change, an anchor figure changing at all, a premise threshold crossed, a figure appearing
or disappearing — and expects three to six sections of eighteen to be re-drafted on an ordinary
quarter. The bar is under £2 and under ten minutes, and nothing at all when nothing is new.

Mapping that onto the code found where the design has to bend.

**A run's decisions are per job, and the final gate needs the first.** Every approval is
recorded against a `job_id`, the gate order puts the plan first, and the plan gate is not
conditional — a decision at the final gate on a job with no plan approval is refused. A refresh
that re-asked the planner would be a full run; one with no plan approval could not be approved.

**Only the plan step creates a run's sections**, and the draft step already keeps a section
that is generated rather than writing it again. That is the carry-forward seam, and it means a
carried section needs no new status: `SectionStatus` is a Postgres enum, and the draft step's
own record already says which sections this attempt wrote.

**The evidence a section was dealt is never persisted** — only its counts. Whether a section's
inputs moved cannot be read off the prior run; it has to be decided from what the section's
prior claims name and what its pack would be dealt now.

**Acquisition fetches every wanted filing again and deduplicates afterwards**, by artefact
digest, by document per work order, by extraction locator. Nothing filters the submissions
index against accessions already held, although the index lists them before any fetch.

**The calculations index is not a diff source.** `indexed_calculations` keeps one row per
figure at its newest period; a diff needs every period. Nothing in the repository compares two
runs' figures.

**The vertical slice's own estimates exceed a £2 ceiling.** The draft step is projected at
£5 and the revise pass at £1.50; a refresh under those estimates would pause at its cap before
drafting a word.

**Sources are visible per work order.** A refresh on a new request could not see the
documents the first run fetched without fetching them again; on the same request it sees all
of them, and the confirmed assumptions — which are the request's — with them.

## Decision

### 1. A refresh is a job on the same research request, and the request is re-dated

A refresh is a second `Job` on the request whose report is current, with
`workflow_version = "refresh_v1"`, `refresh_kind = "refresh"` and `refreshes_report_id` naming
the report it updates. It is commissioned from that report's own page by a control that states
its price. At commission the request's work order is re-dated to the day (ADR 0110 dates a run
by the platform's clock, and this is a run); the prior report keeps the date it carries. It is
refused when the report is not current, when the request has a live run, and when the report
is not this account's (ADR 0120). `start_run`'s rule that a request with a current report may
not start again stands for full re-runs; the refresh is the one path that creates such a job,
and it creates it itself.

### 2. The priced go-ahead is the plan gate, and the other decisions are carried

Approving the price records a **plan approval** on the new job: its payload is the carried plan
and the estimate, hashed as every gate payload is. The classification, the peer set and the
theme set the prior run confirmed are **re-asserted** on the new job as decisions with the prior
payload hashes and a note naming the run they came from — so every reader that asks *was this
confirmed on this job?* finds the answer on the job it asks about, and ADR 0123's rule that a
decision is never re-asserted on the *same* job is untouched. The confirmed assumptions are the
request's rows, read as they stand. No gate is re-asked. The unmapped-concepts gate keeps its
conditional place: a tag the extractor cannot place stops a refresh exactly as it stops a run,
unless the refresh's extraction produced the very payload the prior run's operator decided on
— then that decision is re-asserted on the new job with its hash, as the three above are,
because the same tags with the same figures are not a new question.

### 3. The steps, with the slice's keys where a reader expects them

`refresh_v1` declares, in order: `carry_forward`, `acquire`, `acquire_macro`, `classify`,
`propose_peers`, `propose_themes`, `extract`, `gate_unmapped_concepts`, `acquire_prices`,
`calculate`, `comps`, `propose_assumptions`, `value`, `diff`, `draft`, `validate`, `revise`,
`gate_final`, `render`. The free steps run the slice's own functions. The four named after
model steps carry the prior job's frozen output under the same key, because the value step,
the comps step and the render step read those outputs by key and must not learn a second
name. `revise` seals and nothing else: it records the cost scene and the final gate's payload
hash without a revise pass. There is no planner, no critic, no research worker, no red team, no
verdict and no challenge briefs. The refreshed document's appendix says the adversary did not
run on the refresh, and the prior report's challenges stand at its own address (ADR 0115's
deterministic half stays honest about what did not happen).

### 4. Acquire only what is new, and stop at nothing

The acquire step is handed the accessions the request already holds and skips them before any
fetch; the company-facts aggregate is fetched and compared by digest; prices are fetched from
the last close held. The step's output says what was read for the first time. **When nothing
is new the run completes at no spend**: the diff is empty, the change summary says so, no
section is drafted, no document is rendered and the prior report stays current. A quiet
quarter costs nothing.

### 5. The diff is pure, keyed, and material by the mechanism's table

`aer.calc.changes` compares the two ledgers' calculations — every period, every case, never a
sensitivity cell — keyed by name, distinguishing inputs, period and case, and applies the
mechanism's rules: a relative change of two per cent or more, any change to an anchor figure,
a sign change, an appearance or a disappearance, and a prior of zero. A premise whose predicate
the new figure crosses is material however small the move, decided with the monitor's own
`predicate_holds`. Documents read for the first time are rows of their own. The rows are
written to `report_changes` by the diff step, each with a deterministic sentence; the model
never selects, orders or judges them.

### 6. Re-draft only what moved; carry the rest with their citations re-verified

A section is re-drafted when a material row names a figure among its prior claims — the
calculations and facts its recorded claims point at, which is the one record of what the
section rested on (its evidence pack is never persisted, and rebuilding every pack to ask is
most of a draft's time for none of its words) — or when the prior run never wrote it.
Otherwise it is **carried**: the prior content is copied to the new section, its
claims are re-pointed at the new ledger's equivalent rows where the key resolves and kept
where it does not, and its citations are verified at the final gate with everything else's —
carrying prose forward never carries a citation forward unchecked. A re-draft that fails
carries the prior text and is marked **stale** in the draft step's record and in the change
summary, with the date of the text and the reason. Deterministic and platform-filled sections
are refilled, as they are free. If more than twelve sections moved the refresh drafts nothing,
completes with its summary, and says a full run is what is needed.

The refresh ceiling is a setting, `refresh_budget_gbp`, £2 by default, enforced in the draft
step before each section on top of the request's cap and the month's: a section the ceiling
will not carry is left carried rather than drafted, and the summary says which.

### 7. The change summary is a section at the head of the document

`change_summary` is a seeded, platform-filled section at position 50, ahead of everything the
spine seeds, created by the carry step alone — its applicability predicate names a property no request satisfies, so a full run
never resolves it. Its rows come from `report_changes` in the mechanism's order: what broke,
what moved materially, what is new, what did not move; the writer's commentary is the one
model call the summary costs and is held to the closing section's rules. On a refresh with
nothing new it is the whole result.

### 8. Supersession says `refreshed`, and the summary has a read mark

The render step supersedes the prior report with a reason that leads with the vocabulary word
ADR 0116 reserved: *Refreshed on {date}: {n} figures moved, {k} sections re-drafted.*
`jobs.changes_read_at` is the mark the work list's row will read (page specification §1.2);
the Today band that reads it is built with the absent surfaces.

### 9. Priced before it starts

The control's price is the expected sections to re-draft — the mechanism's six — at the draft
step's own per-section estimate, plus the validate step, rounded up to the penny. It is an
estimate said as one; the ceiling, not the estimate, bounds the spend.

## What was rejected

**A refresh as a new request.** The record's documents are the work order's, the confirmed
assumptions are the request's, and a refresh that could not see either would fetch and ask
again — a full run with a different name.

**Re-running the planner and the critic.** The plan is what the operator approved; asking it
again is the full run's first pound.

**A fourth section status.** A Postgres enum change for a fact the draft step's own record
already carries; the render reads the record.

**Re-drafting everything.** The specification's economics are step four.

**Running the adversary on the refresh.** Its spend is a third of a full run and its challenges
stand on the prior report; a refresh that re-argued the case would not be one.

**A model-judged diff.** Materiality is a table, and a table is code.

## Consequences

- Migration 0086: `jobs.refresh_kind`, `jobs.refreshes_report_id`, `jobs.changes_read_at`;
  `report_changes`; the seeded `change_summary` section.
- `aer.calc.changes` (pure), `aer.services.refresh`, `aer.workflow.workflows.refresh_v1`
  registered beside the slice, `aer.sections.change_summary` as an augmenter.
- `acquire_filings` learns which accessions are held; the render step learns the refreshed
  reason; `refresh_budget_gbp` joins the settings.
- The report page gains the priced control; the library already groups by company and shows
  the state.
- Open: the Today band's "review a completed refresh" row, with the absent surfaces.
