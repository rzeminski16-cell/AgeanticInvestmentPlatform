# ADR 0093 — A work order roots the book's own acquisitions

**Status.** Accepted
**Date.** 2026-08-29
**Amends.** ADR 0072. Its record says the work order is the *run* root; this widens it to
the acquisition root. Nothing 0072 decided is reversed — the widening is what its
`(tool, subject_kind)` columns were built to permit.
**Enables.** Roadmap §3.1's third door: a ticker the platform has never seen becomes
dealable at first sight, or is refused with the vendor's reason.

## Context

The portfolio form resolves a typed ticker against the securities the platform holds and
refuses one it does not, telling the operator to commission a research report — because a
`Security` row exists only where a priced research run created one. On a machine whose runs
were unpriced, the portfolio tool is unusable. That is roadmap §3.1's blocked third door,
and it is blocked on a decision rather than plumbing.

Verifying a new ticker means fetching its price series, and a fetched series is an
externally derived fact — invariant 1 says it was hashed and stored, with a provenance row
a claim can point at. The machinery for that is `services.acquisition.record_acquisition`,
and it requires a `ResearchRequest`: it reads the point-in-time setting and the as-of date
off the mandate row, and the source document needs a root. A portfolio has neither.

`price_bars.source_document_id` is nullable and writing `NULL` there would compile, but its
nullability means something else — `ON DELETE SET NULL` for the licensed-payload purge
under ADR 0031, so the column reads "the bytes are gone", never "there were never any".
Using it to mean the second would quietly retire invariant 1 for every price the portfolio
touches.

Three candidates were weighed on 2026-08-25 and the decision recorded in the roadmap: a
work order roots the book's own acquisitions. Loosening `record_acquisition` to take the
point-in-time flag directly leaves the source document scoped to nothing in particular; a
synthetic research request per portfolio puts a row in `research_requests` that nobody
commissioned and every "how many reports have I run?" query then has to know to exclude.
The work order is the one consistent with where the schema already went: ADR 0072 made it
the run root, and `source_documents.work_order_id` is already the column the sources
predicate compares against.

## Decision

**A *portfolio data acquisition* is a work order whose subject is the book.** One row per
acquisition act — a dated, budgeted, auditable unit of work — with:

| Column | Value | Why |
|---|---|---|
| `tool` | `"portfolio"` | Which tool owns the act. The registry's vocabulary, as 0072 arranged |
| `subject_kind` | `"portfolio"` | What the act is about |
| `subject_id` | the `portfolios.id` | Resolved at creation — a book, unlike a typed ticker, already exists |
| `as_of_date` | the day of the act | The clock the acquisition reads |
| `point_in_time` | `FALSE`, always | See below |
| `max_cost_gbp` | `0` | See below |
| `status` | `RUNNING` → `COMPLETED` or `FAILED` | The act's own lifecycle, in the existing vocabulary |

**`(tool, subject_kind)` are the distinguisher, and no new column is added.** This was
0072's stated first question. The columns exist precisely so that a second tool's work is
distinguishable in the table rather than by inference, and a second kind arriving is the
design being used, not stretched. A new `kind` column would be a second answer to the
question `tool` already answers, and two columns claiming to own one distinction is how a
query ends up filtering on the wrong one.

**One work order per acquisition act, not a standing root per book.** A standing root would
have to carry an `as_of_date` that is forever wrong and a status that means nothing. An act
has a date, an outcome and a cost; those are exactly the columns. The book itself is the
*subject*, reached through `subject_id`, and its identity does not need a second table to
say so.

**A book acquisition is inherently not point-in-time.** The operator wants today's close —
that is the whole point of verifying a ticker at first sight — and refusing today's bars as
post-dated would be enforcing a rule nobody set. `point_in_time = FALSE` is written on the
order, `record_acquisition` reads it from there, and the admissibility decision follows as
it does for any run. Invariant 4 is untouched: point-in-time stays enforced at acquisition,
in code, off the root's own flag — this root simply sets the flag the only honest way.

**The cap is zero, and the check constraint relaxes to permit exactly that.**
`ck_work_orders_cost_is_positive` requires `max_cost_gbp > 0`, which was right when every
work order was a model-calling run. A data acquisition is budgeted at zero model spend *by
design*: no step under it may call a model, and a zero cap is the enforcement rather than
the declaration — ADR 0072's budget guard walks to the root, finds `0`, and refuses any
call some future change wires in by mistake. Zero is a real cap that refuses everything; it
is the opposite of the nullable cap 0072 rejected. The constraint becomes
`max_cost_gbp >= 0`, by migration, and the invariant-6 posture is strengthened rather than
relaxed: the one kind of order that must never spend now structurally cannot.

**`record_acquisition` and `record_source_document` read the clock off the root.** The
`request: ResearchRequest` parameter becomes `work_order: WorkOrder`, and the run call
sites pass the work order their job already points at. This is the change 0072's own
transition anticipated — the mandate row's duplicated `as_of_date` and `point_in_time`
columns are scheduled to drop at its migration step 4, and acquisition reading them was a
reach into the mandate table for run-root fields, the exact coupling 0072 exists to remove.

**`source_documents.request_id` is written only under a research root.** The column is
transitional (nullable since 0072's step 3, dropped at its step 4) and carries a foreign
key to `research_requests`; a portfolio work order has no such row, and writing its id
there would violate the constraint. The rule is the distinguisher earning its keep: a root
whose `tool` is `"research"` shares its id with its mandate row by 0072's backfill, so
`request_id = work_order.id` there and `NULL` otherwise. When 0072's step 4 drops the
column, this rule goes with it.

**`company_id` on a book acquisition's source document is `NULL` when no company row
exists.** A never-researched ticker has no `companies` row, and inventing one from a vendor
symbol would put an unverified identity in the registry a research run trusts. The document
stays attributable to the book through the work order; a later research run that resolves
the company does its own acquisition under its own root, exactly as two research runs
already do.

## What this deliberately does not touch

**`EvidenceScope` and the two predicates.** `visible_facts` and `visible_sources` are
unchanged. A portfolio work order writes price bars and their provenance; it writes no
facts and drafts no sections, so the fact predicate never sees it and the source predicate
scopes its documents to it exactly as it scopes a run's. The set-valued-subject question
0072 left open stays open; a book acquisition has a single subject, the book.

**The two doors that already work.** Resolution of held tickers and listing-via-research
are untouched. The third door is additive: the branch that today refuses an unknown ticker
gains the alternative of verifying it once.

**ADR 0031's erasure semantics.** `price_bars.source_document_id` keeps meaning what it
means. Every bar the third door records points at a real source document under a hashed
artefact, and a later licence purge sets it `NULL` through the same event it always did.

## Consequences

- `add_listing(session, ticker, exchange, client)` becomes writable: create the act's work
  order, resolve the vendor symbol, fetch a short window of bars, record the artefact, the
  source document, the security and the bars under the order — or mark the order `FAILED`
  and refuse with the vendor's reason. No job, no workflow, no model call.
- The portfolio form's unknown-ticker branch becomes a verification rather than a dead end,
  and its refusals stay refusals with reasons: vendor returns nothing, no subscription
  configured, ambiguous listing.
- One migration: the cost check widens to `>= 0`. No new columns, no backfill, and the
  downgrade is exact.
- The five run call sites of `record_acquisition` pass a `WorkOrder` where they passed a
  `ResearchRequest`. Under 0072's 1:1 backfill the ids are shared, so the rows written are
  byte-identical for every existing path; what changes is which table supplies the clock.
- A model call attempted under a portfolio work order is refused by the budget guard on a
  cap of zero — a failure mode that previously required review to notice now cannot happen
  silently.
