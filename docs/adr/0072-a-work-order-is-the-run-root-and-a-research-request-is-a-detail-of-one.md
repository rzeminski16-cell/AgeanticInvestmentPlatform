# ADR 0072 — A work order is the run root and a research request is a detail of one

**Status.** Accepted
**Date.** 2026-08-22
**Required by.** ADR 0071, which registers a tool as a capability. A registry with no
tool-agnostic row to hang a run on registers nothing that can run.
**Amends.** ADR 0061. Its rule is untouched; what carries the rule stops being a research
request — see below.

## Context

`Agent._refuse_what_cannot_be_afforded` prices a call's worst case and compares it against
the run's cap. To find the cap it walks `job_step → Job → ResearchRequest`
(`src/aer/agents/base.py:431`), and if either row is absent it raises `BrokenRecordError`:

> Referential breakage, not a budget question — and a guard that shrugged here would be a
> guard any orphaned step walks straight past.

That comment is right and the guard should keep refusing. But `jobs.request_id` is
`NOT NULL` with a foreign key to `research_requests`, so what the walk actually asserts is
stronger than it looks: **an equity research mandate — a ticker, an exchange, an investment
horizon — is a precondition for every model call in the platform, for every role.** A
thesis monitor waking at 03:00 to read one filing cannot call a model until that changes,
and neither can anything else that is not a company report.

**The other half of invariant 6 never had this problem.** `WorkflowEngine`'s `BudgetGuard`
(`src/aer/workflow/engine.py:204`) takes `per_run_cap_gbp: Decimal` — a number. It has
never heard of a request. `services/runs.py:171` reads `request.max_cost_gbp` and hands the
value over, and the coupling stops at the call site. The per-call guard arrived later under
ADR 0053 with no such value in hand, went to the database for the same number, and found
exactly one table that had it. The equity mandate became load-bearing for model spend by
accident of which table the column happened to live on.

**The coupling is wide but shallow.** 59 of 279 source files name `ResearchRequest`. Sort
them by which attributes they actually read — every `request.<field>` and
`ResearchRequest.<field>` in the file, against the mandate fields on one side and
`id`, `user_id`, `as_of_date`, `point_in_time`, `max_cost_gbp`, `status` and `archived_at`
on the other — and the split is **23 / 19 / 17**: twenty-three read at least one mandate
field, nineteen read only run-root fields, and seventeen read no field at all.

The seventeen name the type in a signature, a relationship or a `TYPE_CHECKING` import and
never open it; `sections/deterministic.py:142` carries
`request: ResearchRequest,  # noqa: ARG001 -- the builder signature is uniform`, a mandate
threaded through a builder that has no use for one. The nineteen are the ones that matter to
this record. `services/facts.py`, `services/analysis.py`, `services/filings.py` and
`verify/citations.py` read `as_of_date` and `point_in_time` and nothing else;
`agents/base.py` reads `max_cost_gbp` and nothing else; six API route modules read `id` and
`user_id` to check ownership. **Thirty-six of fifty-nine files depend on the mandate table
for none of the mandate** — they depend on who asked, what the run may spend, and what date
the evidence is judged against, which are properties of *a run*, not of an equity report.

## Decision

**`work_orders` is the run root.** One row per unit of approved, budgeted, dated work:
`user_id`, `tool`, `subject_kind`, `subject_id`, `as_of_date`, `point_in_time`,
`max_cost_gbp`, `status`, `archived_at`. `jobs` gains a `NOT NULL work_order_id` and the
budget guard walks to that instead. The refusal is unchanged; only the table it reaches is.

**`research_requests` is demoted to a 1:1 detail row** holding what is genuinely the equity
mandate and nothing else — `ticker`, `exchange`, `isin`, `company_name`, `base_currency`,
`reporting_currency`, `investment_horizon_months`, `horizon_label`, `analysis_mode`,
`portfolio_context`, the operator preferences, `resolved` and `company_id`. Every file that
reads a ticker keeps reading a ticker. It reads it from the row whose subject it is.

**The alternative was to make `jobs.request_id` nullable**, and it is rejected on invariant
6. A cap that only warns is a cap that does not work; a cap that can be NULL is worse,
because the guard would then have to choose between refusing every unattended run and
inventing a default nobody set. A supertype gives every run a cap by construction.

## A subject reference without a foreign key

`(subject_kind TEXT, subject_id UUID)`, no foreign key, resolved through a resolver
registered per kind on the `ToolDefinition` of ADR 0071.

This follows the precedent the schema already sets where a record must outlive what it
describes. `audit_events.job_id` and `audit_events.request_id`
(`src/aer/db/models/audit_event.py:46-47`) are UUID columns with no constraint, and the model
says why — *"An audit record must survive the thing it describes -- including a deleted
request -- or the log would quietly lose exactly the entries most worth keeping."* A work
order for a watchlist entry the operator later deleted is still a run that happened and cost
money.

`approvals.job_id` (`src/aer/db/models/approval.py:44`) is the same shape for a different
recorded reason, and it is worth separating the two rather than collecting them as one
precedent. Its comment says *"the plan gate is decided before any job exists. A FK would
force either a placeholder job row or a nullable-with-FK that implies an ordering the schema
does not actually have."* That is a column dropping a constraint because the referent does
not exist **yet**; `audit_events` drops one because the referent may cease to exist. Only the
second is the argument for `subject_id`. The first is a useful reminder of how often this
schema has found a foreign key to be the wrong tool, and no more than that.

**The honest counter-evidence is that loose polymorphism has already rotted here once.**
`SourceKind.FACT` is documented as generic; `_load_fact` in `services/calculations.py:409`
is `await session.get(FinancialFact, parsed)`; and `services/macro.py:201` mints
`SourceRef.fact(observation.id)` over a `macro_observations` row. Those two cannot both be
right, and where they meet the lineage walk draws a dangling node. They have not met yet —
nothing under `src/` imports `aer.services.macro` — so the defect is written down rather than
firing, which is ADR 0076's subject and its correction to an earlier draft. Repeating that
mistake at the root of every run would be considerably more expensive, and would not have the
courtesy of staying dormant.

**What stops it is that the resolver is registered, not inferred.** The defect above is not
a missing foreign key — a constraint would not have helped, because the id was valid and
pointed at a real row in the wrong table. The defect is a hardcoded `if`-chain that grew a
fourth case in a different module and a fallthrough that swallowed it silently. A kind with
no registered resolver is refused at registration, the way `RoleDefinition.output_schema()`
refuses a reference the code has lost, and by the same argument ADR 0035 made for
capability: one table that is looked at, rather than a branch in a file that a
capability-hungry change would edit anyway.

## `EvidenceScope` carries ADR 0061's asymmetry, run identity included

`EvidenceScope(work_order_id, as_of_date, point_in_time, subject_kind, subject_id)` — a
frozen value — replaces the `ResearchRequest` argument in the three guarded doors:
`services/facts.py` `visible_facts`, `services/sources.py` `visible_sources`, and
`verify/citations.py` `_refuse_if_out_of_time`.

**The run identity is in the value because ADR 0061's rule is asymmetric, and the asymmetry
is the substance of it.** That record decided: *"A fact is scoped by company. A source
document is scoped by company and by request."* Both halves are live in the code.
`visible_facts` (`src/aer/services/facts.py:85-95`) filters `FinancialFact.company_id` and,
under point-in-time, `filed_date <= as_of_date` — **the run appears nowhere in it, on
purpose**, because facts deduplicate on an observation key that excludes the source document
and therefore hang off the *first* run's document; re-adding the run would hide every fact
that run wrote. `visible_sources` (`src/aer/services/sources.py:73-76`) filters
`SourceDocument.request_id == request.id` **and** the company, because "what did this run
acquire?" is exactly the question a sources page asks and a document some other run fetched
is not part of the answer.

So a four-field scope would not be carrying 0061 forward. It would carry the fact half and
drop the source half, leaving `visible_sources` to reach for a run identity the value no
longer holds — which means going back to `source.request_id` and the mandate table, behind a
value object introduced to remove exactly that reach. **Carrying the asymmetry forward is the
fix; claiming there is no asymmetry is the bug.** Facts ignore the run. Source documents are
defined by it. The scope holds both because it has to serve both predicates.

**The run identity being a work order means `source_documents` moves in this migration
too.** `source_documents.request_id` is a `NOT NULL` foreign key to `research_requests`
(`src/aer/db/models/source_document.py:66`) and it is the column `visible_sources` compares
against. The backfill is 1:1, so repointing it is mechanical; `uq_source_acquisition` and
`uq_source_document_per_artefact` move with it, as do
`ix_source_documents_request_id_publication_date`, `ix_source_documents_request_id_company_id`
and the partial `ix_source_documents_quarantined`. This is stated here rather than left to be
noticed later because a scope carrying a `work_order_id` with no column to compare it to is a
value that cannot answer its own question.

ADR 0061 also established that both predicates live in one place each and every consumer
calls them, because three copies of a predicate is how the first two diverged. That part is
untouched; what changes is what a caller must hold to use them. `visible_facts` today takes
`(request, company_id)` and reads the request for two fields — a signature that invites a
caller to pass a request it happens to have for some other reason — and
`_refuse_if_out_of_time` performs `session.get(ResearchRequest, source.request_id)`
(`src/aer/verify/citations.py:209`) to recover a date and a boolean. Both go to the mandate
table for a scope. A value with five fields cannot be half-supplied and carries no ticker to
be tempted by.

ADR 0061 rejected an alternative on the grounds that *"the next feature that legitimately
touches a second company — an acquirer and a target, a parent and a subsidiary — would meet
the same failure with nothing in place to catch it"*, and left open what the subject means
when it is not one company. A portfolio is that feature at volume. **This ADR does not
answer the question; it gives it somewhere to be answered.** `visible_facts` scoped to one
`company_id` stays a single-subject predicate, and a set-valued subject is a change to
`EvidenceScope` and its three callers rather than to sixty files. The two-clock question
that a portfolio subject also raises is ADR 0075's.

## The migration, which is the risky part

Four steps, and only the first three are in this revision.

1. Create `work_orders`; add nullable `work_order_id` to `jobs`, `approvals` and
   `source_documents`; add `subject_kind` and `subject_id` to `audit_events`.
2. Backfill: one work order per existing `research_requests` row, `tool = 'research'`,
   `subject_kind = 'company'`, `subject_id = research_requests.company_id`, copying
   `user_id`, `as_of_date`, `point_in_time`, `max_cost_gbp`, `status` and `archived_at`
   verbatim. Then set each new `work_order_id` from its own table's `request_id`, which is
   exact because the backfill is 1:1, and repoint `plan_skill_pins` through
   `research_plans.request_id` to the same row.
3. `SET NOT NULL` on the three `work_order_id` columns, and **`DROP NOT NULL` on
   `jobs.request_id`, `approvals.request_id` and `source_documents.request_id`** — kept for
   the transition, no longer required. This is not the alternative the Decision rejected:
   what was rejected was a nullable *cap*, and after this step the cap hangs off a column
   that is `NOT NULL`. A nullable pointer to a mandate that genuinely does not exist is the
   opposite case.
4. **A later revision** drops those three `request_id` columns, their foreign keys, and the
   duplicated columns from `research_requests`, once no code reads them.

**The two unique constraints on `source_documents` move at step 3, not step 4, and the
reason is a Postgres detail worth writing down.** `uq_source_acquisition` and
`uq_source_document_per_artefact` are both keyed on `request_id`, and Postgres treats NULLs
in a unique key as distinct — so the moment step 3 relaxes that column, a run with no
research request writes rows that no unique constraint can collide with. Both were put there
against a live duplicate: the A43 pre-read and then a parallel research node recording its
own 10-Q twice, because "the database is the only participant that sees both writers". A
transition window in which the first monitor run has that protection quietly switched off is
not a window worth opening. The plain indexes may wait for step 4; the constraints may not.

**The split has to be real, and the reason is a test.**
`tests/test_migrations.py::TestSchemaMatchesModels` runs the whole chain and then
`compare_metadata` against one `Base.metadata`, with `compare_type` and
`compare_server_default` on. A column dropped in a migration but still declared on a model
is not a warning and not a slow drift; it is a red build in the same commit. So the model
and the migration move together, and the only way to drop a column *later* is for the model
to keep it *now* — a duplicated `as_of_date` living on two tables for one revision, read
from the work order and written to both. That is ugly and it is the price of a cut that
cannot half-land.

`test_every_revision_has_a_downgrade` reads the source and fails a downgrade whose body
opens with `pass`, and `TestRoundTrip` downgrades to base and upgrades again on a throwaway
database. A backfill's downgrade is the honest hard case, and here it is genuinely lossless:
dropping `work_orders` discards nothing that `research_requests` does not still hold —
which is true precisely because step 4 has not run. The staging is what makes the reversal
real rather than declared.

**`subject_id` is nullable, and means what `company_id` already means.** It backfills from
`research_requests.company_id`, which is NULL until `acquire` resolves the ticker. Migration
0042 faced the same column and chose to fill it, matching `companies` on ticker and exchange,
under a comment that rejects the alternative in terms worth repeating: a NULL subject "makes
every fact query on that request return nothing, which would blank the report the operator
already has". This migration copies that resolved id rather than re-deriving it, so a request
0042 attributed stays attributed and a request it could not attribute stays NULL. A work order
with no subject sees no facts, by construction, exactly as ADR 0061 arranged — the emptiness
is the guard working, not the backfill failing.

## `approvals` repoints to the work order, because a gate with no mandate must be writable

`approvals.request_id` is `NOT NULL`, a foreign key to `research_requests`, `ON DELETE
CASCADE` (`src/aer/db/models/approval.py:37-39`). Every approval row this platform has ever
written therefore asserts that an equity mandate exists.

ADR 0078 decides that a thesis monitor's `contradicted` status is the one monitor outcome
that opens a gate — rare enough to afford one, consequential enough to deserve one. **A
monitor run has no research request, so that row cannot be written at all.** Not written
awkwardly: `record_decision` builds `Approval(request_id=job.request_id, …)`
(`src/aer/services/approvals.py:127-128`) and a `NOT NULL` constraint refuses the alternative.
The one monitor outcome 0078 is careful to preserve as a genuine human judgement is the one
outcome the schema forbids recording.

That is this record's problem rather than 0078's. **This ADR owns the run root, and a gate
hangs off the run root.** So `approvals.work_order_id` is added, backfilled and made `NOT
NULL` in the same four steps as `jobs.work_order_id`, `approvals.request_id` is relaxed at
step 3 and dropped at step 4, and `ix_approvals_request_id_gate` moves with the column. The
CASCADE follows too: deleting a work order deletes its approvals, exactly as deleting a
request does today.

**The blast radius is three call sites.** An `Approval` is constructed in exactly one place,
`services/approvals.py:127`, and `Approval.request_id` is read in exactly two, both in
`vertical_slice_v1` (`:1222` and `:1311`), where the value comes from
`context.job.request_id` and becomes `context.job.work_order_id`. The column carrying the
strongest evidentiary claim this platform makes is touched by less code than most of the
schema — which is what makes moving it cheap now and expensive once the first monitor gate
has been decided against it.

**ADR 0078's tiering decision presumes this migration.** Four statuses becoming findings and
one opening a gate is unimplementable until `approvals` has a tool-agnostic parent.

## `audit_events` gains a subject correlation, and the columns must precede the first row

`audit_events` correlates by `job_id` and `request_id` and by nothing else
(`src/aer/db/models/audit_event.py:46-47`). This migration adds `subject_kind` and
`subject_id` beside them in the same shape — nullable, no foreign keys, indexed as a pair
with `id` the way the existing two are — resolved through the same registry as
`work_orders.subject_kind`.

**The argument for it is made in ADR 0078 and settled here**, because this is already the
migration that moves the run root and a correlation vocabulary is a property of that root.
0078's case is that a trade entry, a position correction, a thesis edit and an
attention-item resolution would every one of them chain against `NULL, NULL` — present in
the ordering, counted by the verifier, and unreachable by any query asking what has happened
to a position. The current vocabulary was built when every consequential record was a
research record, and it stops being defensible the moment the consequential records are the
ones about money.

**Adding the columns is safe; adding the values later is not, and the reason is not the one
it looks like.** `this_hash` is `chain_hash(prev_hash, payload)`
(`src/aer/db/models/audit_event.py:92`, and `sha256(previous_hash || canonical_json(payload))`
at `src/aer/core/hashing.py:80-89`), so the correlation columns sit *outside* the digest.
Two columns can be added by migration without disturbing a single existing chain, which is
precisely why this is affordable in the same revision as everything else here.

What cannot be done later is populating them. An event written before the columns exist
recorded its subject nowhere, so there is nothing to derive one from and a backfill would be
an invention — written by `UPDATE`, against a table whose whole discipline is that rows are
appended and never altered, and whose model already flags database-level enforcement of that
as deferred rather than absent. The columns are cheap today, honest tomorrow, and
unavailable the day after. They go in now.

## `plan_skill_pins` moves in the same migration

`plan_skill_pins.plan_id` is repointed from `research_plans.id` to the work order, and
`uq_plan_skill_pins_one_pin_per_skill` moves with it.

It is a small change and it is what makes the skills subsystem usable by anything other than
an equity report. A pin records which immutable skill versions a run executed under — the
report's provenance answer, and the surface where invariant 7's additive-only guarantee is
cashed. As the schema stands it hangs off `research_plans`, so a thesis monitor, having no
research plan, cannot pin a skill at all: the platform's one governed instruction mechanism
would be available to exactly one tool.

**The cost is real and is recorded here rather than discovered later.** A request may hold
several plans, so pins become one set per work order rather than one per plan, and a
re-planned work order can no longer say which of two sets a given job ran under. If that
becomes a live need, the answer is the `supersedes_id` idiom this repository already uses
for `assumption_proposals` → `assumptions`, not a second foreign key — two columns claiming
to own one pin is how a provenance answer becomes ambiguous.

## Consequences

**A model call no longer requires an equity mandate, and still requires a cap.** That is the
whole of the change at the guard: an unattended monitor has a work order with a
`max_cost_gbp`, the walk finds it, and `BrokenRecordError` still fires for an orphaned step.
Nothing is relaxed; the precondition is moved to a row that is not about a company.

**Most of the 59 files change by one type name in a signature.** The 17 that never read a
field and the 19 that read only run-root fields are mechanical. The 23 that read a mandate
field keep the mandate and reach it through the detail row.

**The freeze rule now guards two tables.** `immutable_reason` holds that what freezes a
request is what a run left behind rather than that a run existed, and it reaches that answer
by walking `Job.request_id` — a walk that becomes `Job.work_order_id`. `_EDITABLE_FIELDS`
(`src/aer/services/requests.py:143`) splits with the columns: `as_of_date` and
`point_in_time` are in that tuple today and are moving to the work order. Its own comment
says what is at stake — *"a field missing here would be edited without the change ever
appearing in the audit trail"* — and the test that checks the tuple against `_apply`'s
assignments has to cover both halves, or the audit diff silently stops covering a field.

**This migration is now five tables wide, and that is the honest cost of it.** `jobs`,
`approvals`, `source_documents` and `plan_skill_pins` repoint, and `audit_events` gains two
columns. Splitting it would be worse rather than safer: each of the five is repointing at the
*same* new row through the same 1:1 backfill, so one revision holds one correspondence to
check, and four revisions hold four chances for the correspondence to drift between them
while `compare_metadata` is red in every intermediate state.

**This does not settle what a cap means for work that never ends.** A `BudgetExceededError`
pauses a run for a human decision, which is the right answer at a console and no answer at
all for a monitor running nightly across forty names. Moving the cap to a tool-agnostic row
makes that question askable; it does not answer it.
