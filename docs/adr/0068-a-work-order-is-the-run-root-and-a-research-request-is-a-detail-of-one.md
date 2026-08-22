# ADR 0068 — A work order is the run root and a research request is a detail of one

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** ADR 0067, which registers a tool as a capability. A registry with no
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
`portfolio_context`, the operator preferences, `resolved` and `company_id`. The 19 files
that read a ticker keep reading a ticker. They read it from the row whose subject it is.

**The alternative was to make `jobs.request_id` nullable**, and it is rejected on invariant
6. A cap that only warns is a cap that does not work; a cap that can be NULL is worse,
because the guard would then have to choose between refusing every unattended run and
inventing a default nobody set. A supertype gives every run a cap by construction.

## A subject reference without a foreign key

`(subject_kind TEXT, subject_id UUID)`, no foreign key, resolved through a resolver
registered per kind on the `ToolDefinition` of ADR 0067.

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
`SourceRef.fact(observation.id)` over a `macro_observations` row. `_resolve_input` branches
on three kinds and falls through to `stored.missing(...)`, so every macro-sourced
calculation input renders as a dangling node in the provenance viewer today. ADR 0072 fixes
it. Repeating that mistake at the root of every run would be considerably more expensive.

**What stops it is that the resolver is registered, not inferred.** The defect above is not
a missing foreign key — a constraint would not have helped, because the id was valid and
pointed at a real row in the wrong table. The defect is a hardcoded `if`-chain that grew a
fourth case in a different module and a fallthrough that swallowed it silently. A kind with
no registered resolver is refused at registration, the way `RoleDefinition.output_schema()`
refuses a reference the code has lost, and by the same argument ADR 0035 made for
capability: one table that is looked at, rather than a branch in a file that a
capability-hungry change would edit anyway.

## `EvidenceScope` keeps ADR 0061's rule and drops its type

`EvidenceScope(as_of_date, point_in_time, subject_kind, subject_id)` — a frozen value —
replaces the `ResearchRequest` argument in the three guarded doors: `services/facts.py`
`visible_facts`, `services/sources.py` `visible_sources`, and `verify/citations.py`
`_refuse_if_out_of_time`.

ADR 0061 established that both predicates live in one place each and every consumer calls
them, because three copies of a predicate is how the first two diverged. That rule is
preserved exactly; what changes is what a caller must hold to use it. `visible_facts` today
takes `(request, company_id)` and reads the request only for two fields — a signature that
invites a caller to pass a request it happens to have for some other reason — and
`_refuse_if_out_of_time` performs `session.get(ResearchRequest, source.request_id)` to
recover a date and a boolean. Both go to the mandate table for a scope. A value with four
fields cannot be half-supplied and carries no ticker to be tempted by.

ADR 0061 rejected an alternative on the grounds that *"the next feature that legitimately
touches a second company — an acquirer and a target, a parent and a subsidiary — would meet
the same failure with nothing in place to catch it"*, and left open what the subject means
when it is not one company. A portfolio is that feature at volume. **This ADR does not
answer the question; it gives it somewhere to be answered.** `visible_facts` scoped to one
`company_id` stays a single-subject predicate, and a set-valued subject is a change to
`EvidenceScope` and its three callers rather than to sixty files. The two-clock question
that a portfolio subject also raises is ADR 0071's.

## The migration, which is the risky part

Four steps, and only the first three are in this revision.

1. Create `work_orders`; add `jobs.work_order_id` nullable.
2. Backfill: one work order per existing `research_requests` row, `tool = 'research'`,
   `subject_kind = 'company'`, `subject_id = research_requests.company_id`, copying
   `user_id`, `as_of_date`, `point_in_time`, `max_cost_gbp`, `status` and `archived_at`
   verbatim. Then set `jobs.work_order_id` from `jobs.request_id`.
3. `SET NOT NULL` on `jobs.work_order_id`.
4. **A later revision** drops `jobs.request_id` and the duplicated columns from
   `research_requests`, once no code reads them.

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
`research_requests.company_id`, which is NULL until `acquire` resolves the ticker, and which
migration 0042 deliberately left NULL rather than guess an attribution. A work order with no
subject sees no facts, by construction, exactly as ADR 0061 arranged. This migration
inherits that refusal instead of reopening it.

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

**Most of the 59 files change by one type name in a signature.** The 21 that never read a
field and the 18 that read only the run-root fields are mechanical. The remainder keep the
mandate and reach it through the detail row.

**The freeze rule now guards two tables.** ADR 0014's `immutable_reason` holds that what
freezes a request is what a run left behind, and `as_of_date` and `point_in_time` — the two
fields its docstring singles out as the ones a retrospective edit would falsify — are moving
to the work order. `_EDITABLE_FIELDS` splits with them, and the test that checks that tuple
against `_apply`'s own assignments has to cover both halves or the audit diff silently stops
covering a field.

**Approvals and audit events keep `request_id` for now.** `audit_events.request_id` feeds a
hash chain; rewriting a correlation column whose value is inside `this_hash` is a different
decision with a different risk profile, and the generic subject correlation the design note
asks for is separate work that must land before the first ledger row, not with this one.

**This does not settle what a cap means for work that never ends.** A `BudgetExceededError`
pauses a run for a human decision, which is the right answer at a console and no answer at
all for a monitor running nightly across forty names. Moving the cap to a tool-agnostic row
makes that question askable; it does not answer it.
