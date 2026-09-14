# The data model

*What the schema already holds, and the six things it does not. Read from the live database on
14 September 2026 — 64 tables — rather than designed on paper.*

**The headline, and it is the same one the audit kept finding: almost all of it exists.** The
judgement layer — theses, premises with their predicates, decisions including a pass, reviews
with a process-quality verdict, the monitor's findings — is built, migrated and unused. A
developer starting from the feature specifications alone would design half a dozen tables that
are already there, with different names.

**So this document's job is to stop that.** Part 1 is what exists and what it means. Part 2 is
the six genuine gaps.

---

# Part 1 — What exists

## 1.1 The shape

```
                          judgements
                    (kind: premise | decision | review)
                     held_by · held_at · basis
                     supersedes_id · withdrawn_at
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
      premises            decisions            reviews
   metric·comparator    action·size·        process_quality
   threshold·unit       horizon·exit_plan   outcome·lessons
   review_by                  │                   │
          │                   │            review_verdicts
          └──── thesis_id ────┴──── thesis_id ──── (per premise)
                              │
                           theses
                 subject · title · report_id
                 written_at · retired_at
                              │
                          findings
              (the monitor's output, per thesis)
```

**A judgement is the base record and carries the honesty.** `held_by`, `held_at` (when the
operator formed the view) and `recorded_at` (when they wrote it down, which is not the same
moment), a mandatory `basis`, a `supersedes_id` for revision, and `withdrawn_at` with
`withdrawn_reason`. **Nothing deletes.** Revision is a new judgement pointing at the old one;
withdrawal is a timestamp and a reason.

**A thesis is not a judgement — it is the container.** The judgements are the premises inside
it. That is ADR 0102 exactly: *a thesis is premises, and a premise is the judgement.*

## 1.2 Table by table

### `judgements`
`id · kind · held_by · held_at · recorded_at · basis · supersedes_id · withdrawn_at ·
withdrawn_reason`

The supersession chain and withdraw-with-a-reason that F9 and F10 need are **already here**. Do
not build a second one.

### `theses`
`id · user_id · subject_kind · subject_id · title · report_id · created_at · written_at ·
retired_at · retirement_reason`

`subject_kind`/`subject_id` is a polymorphic subject, so a thesis can be about something other
than a company later. `report_id` is the report it rests on. Retirement is separate from a
premise being withdrawn: the whole view can be retired with a reason.

### `premises`
`judgement_id · thesis_id · position · statement · metric · comparator · threshold · unit ·
review_by`

**This is the predicate model, complete.** `comparator` is an enum of `at_least · at_most ·
above · below`. A premise with a `metric`, `comparator`, `threshold` and `unit` is machine
testable; one with only a `review_by` date is the honest untestable case. Both are first-class,
exactly as the thesis screen draws them.

### `decisions`
`judgement_id · thesis_id · portfolio_id · security_id · action · statement · size_statement ·
horizon_months · exit_plan · review_by`

`action` is an enum: **`buy · add · trim · sell · hold · pass`**. The pass I specified as a
first-class outcome in F10 **is already in the enum**. `thesis_id` is not nullable in practice —
the "every decision names a thesis" rule has a column to enforce it.

### `reviews` and `review_verdicts`
`reviews`: `judgement_id · portfolio_id · security_id · opened_on · closed_on · thesis_id ·
job_id · process_quality · lessons · outcome · proposal`
`review_verdicts`: `review_id · premise_id · position · statement · verdict · note`

`process_quality` is an enum of **`sound · questionable · flawed`**, and `outcome` is JSON. F14's
four named combinations are therefore **derivable rather than new**: process quality on one axis,
the realised outcome on the other. `review_verdicts` records a verdict per premise, which is how
*"which premises did I get wrong"* becomes answerable across many reviews.

### `findings`
`id · thesis_id · judgement_id · job_id · kind · status · justification ·
source_document_ids · observed · window_from · window_to · opens_gate · created_at`

**This is the monitor's output table and it already exists.** `kind` is an enum of `reading ·
stopped` — a measurement taken, or a check that could not be taken. `observed` is JSON,
`source_document_ids` records what it read, and `window_from`/`window_to` is the *"since the
premise was last read"* window. `opens_gate` means the finding is significant enough to stop
something.

### `watchlist_entries`
`id · user_id · company_name · ticker · exchange · why · followed_at · withdrawn_at ·
withdrawn_reason`

`why` is the *"why it is here"* column the Companies screen draws. Withdrawal carries a reason,
like everything else.

### `reports`
`id · job_id · request_id · company_id · as_of_date · rating · confidence · valuation_low ·
valuation_base · valuation_high · valuation_currency · content · content_hash ·
markdown_artefact_id · pdf_artefact_id · html_artefact_id · approved_by · approved_at ·
immutable · created_at`

**`rating` and `confidence` are columns that exist and are never written.** F13 does not need a
migration for the composed view; it needs code that fills them. `valuation_low/base/high` are
likewise present.

### The rest, in one line each
`portfolios` · `transactions` — the book, recomputed rather than stored.
`risk_scenarios` · `risk_scenario_shocks` — the operator's stated shock (F12).
`scenarios` · `sensitivities` · `sensitivity_cells` · `scenario_overrides` — the valuation grid
the workbook exports and Ask tier 1 re-runs.
`disagreements` — the adversary's output (F2).
`attestations` — the operator's own book as evidence (ADR 0073).
`work_orders` · `watchlist_commissions` — standing instructions to research something.

---

# Part 2 — The six gaps

Everything else in V1.0 is code over the schema above. These six need a migration.

## Gap 1 — A report does not know it was superseded

**Needed by.** F4 (refresh), and the "which report is current" hole a critic found in every plan.

```sql
ALTER TABLE reports
  ADD COLUMN superseded_by uuid REFERENCES reports(id),
  ADD COLUMN superseded_at timestamptz,
  ADD COLUMN supersession_reason text;
CREATE UNIQUE INDEX reports_one_current_per_company
  ON reports (company_id)
  WHERE immutable AND superseded_by IS NULL AND superseded_at IS NULL;
```

**Semantics.** Superseding never mutates the old report — `immutable` stays true and every
artefact, citation and calculation it points at is untouched. The partial unique index is what
makes *"the current report for this company"* a question with one answer, which nothing enforces
today.

**Also needed.** A withdraw/correct path: a report may be marked withdrawn by the operator with a
mandatory reason, which is a third state alongside current and superseded.

## Gap 2 — A run does not know it is a refresh

**Needed by.** F4.

```sql
ALTER TABLE jobs
  ADD COLUMN refreshes_report_id uuid REFERENCES reports(id),
  ADD COLUMN refresh_kind text;          -- 'full' | 'refresh'
CREATE TABLE report_changes (
  id uuid PRIMARY KEY,
  report_id uuid NOT NULL REFERENCES reports(id),
  prior_report_id uuid NOT NULL REFERENCES reports(id),
  kind text NOT NULL,                    -- 'fact' | 'calculation' | 'section' | 'premise'
  name text NOT NULL,
  period text,
  prior_value numeric, prior_unit varchar,
  new_value numeric,   new_unit varchar,
  change_pct numeric,
  material boolean NOT NULL,
  narrative text NOT NULL,
  created_at timestamptz NOT NULL
);
```

`report_changes` **is** the change summary — composed deterministically by comparing two runs'
stored rows, not written by a model. The model writes prose *from* these rows, never instead of
them.

## Gap 3 — The monitor cannot record a price move

**Needed by.** F11's second alert kind.

`findings.kind` is an enum of `reading · stopped`. A price move is neither: nothing was filed and
no premise was read.

```sql
ALTER TYPE finding_kind ADD VALUE 'price_move';
ALTER TABLE findings
  ADD COLUMN security_id uuid REFERENCES securities(id),
  ADD COLUMN dismissed_at timestamptz,
  ADD COLUMN dismissed_reason text;
```

A price-move finding has no `thesis_id` requirement (a watched company need not have a thesis),
carries its measurement in `observed`, and **dismissal takes a reason** — which is what makes the
"too many dismissals mean the threshold is wrong" feedback possible at all.

## Gap 4 — Nothing holds a cadence or a threshold

**Needed by.** F11, F15.

```sql
ALTER TABLE watchlist_entries
  ADD COLUMN cadence text NOT NULL DEFAULT 'monthly',   -- 'monthly' | 'quarterly'
  ADD COLUMN price_move_threshold_pct numeric,          -- null = use the account default
  ADD COLUMN price_move_window_days integer NOT NULL DEFAULT 7,
  ADD COLUMN last_checked_at timestamptz,
  ADD COLUMN next_check_at timestamptz;
```

`next_check_at` being a stored column rather than a computed one is deliberate: the scheduler
reads it with an index, and a missed window is visible rather than inferred.

## Gap 5 — Ask has nowhere to record itself

**Needed by.** F6.

```sql
CREATE TABLE questions (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id),
  company_id uuid REFERENCES companies(id),
  report_id uuid REFERENCES reports(id),
  asked_at timestamptz NOT NULL,
  question text NOT NULL,
  tier smallint NOT NULL,                -- 1 recompute | 2 re-read | 3 research
  tier_rationale text NOT NULL,
  approved_at timestamptz,               -- tier 3 only; null means it never ran
  estimated_cost_gbp numeric,
  actual_cost_gbp numeric,
  answer text,
  answered_at timestamptz,
  documents_added jsonb                  -- tier 3: the source_document ids it contributed
);
```

**`documents_added` is the point of the feature.** It is the evidence that a question left the
record larger than it found it, and it is what the company page counts when it says the record
grew.

## Gap 6 — The workbook is not an artefact kind

**Needed by.** F5.

The workbook is a generated file that must be content-addressed like every other output, so it
joins `reports`' artefact columns:

```sql
ALTER TABLE reports ADD COLUMN workbook_artefact_id uuid REFERENCES artefacts(id);
```

No new table. A workbook is a rendering of a report, exactly as the PDF is.

---

# Part 3 — Rules that hold across the model

1. **Nothing in the judgement layer is deleted.** Revision supersedes; removal withdraws with a
   reason. The tables have no column that could hold a deletion and none should be added.
2. **Every figure in the book is recomputed from `transactions`.** No stored totals, no cached
   valuations beyond a dated price row.
3. **A decision names a thesis and a report.** Both columns exist; the constraint is the
   application's to enforce, and it should be enforced at the database where it can be.
4. **`held_at` and `recorded_at` are different moments** and both matter to F14: a decision
   recorded three weeks late is a different artefact from one recorded in the moment, and a
   review should be able to see which it was.
5. **The retrieval timestamp on every source document survives F1.** Point-in-time enforcement
   goes; the record of when a document was fetched stays, because the review stage cannot tell a
   good decision from a lucky one without it.

## Gap 7 — one column, for the UK path (F19, ADR 0121)

```sql
ALTER TABLE companies ADD COLUMN sic_scheme varchar(16);   -- 'us_sic' | 'uk_sic_2007'
```

**Needed by.** F19. Every `SectorProfile.sic_prefixes` is a US SIC code (`602` banks, `6798`
REITs, `737` software); UK SIC 2007 is a different scheme with different codes. Without a column
saying which scheme a code belongs to, a UK bank's code matches nothing, the sector gate does
not fire, and it takes the standard model — which is how M&T published a 172.1% net margin.

**Backfill.** Every existing row is `us_sic`, which is true of all three stored subjects. The
column is nullable so a company whose code was never resolved stays honest about that.

**And the identifier needs nothing.** `companies.company_number` already exists — `String(16)`,
unique, reserved for the UK adapter — and the table's check constraint is:

```sql
CheckConstraint("cik IS NOT NULL OR company_number IS NOT NULL", name="has_a_registry_identifier")
```

**This plan twice said that constraint fails for a CIK-less UK company.** It does not; it was
written for exactly this case. The correction is recorded here because it made the UK path look
like a schema change when it is a column and some wiring.

## What a developer should not build

A supersession mechanism · a premise predicate model · a pass action · a process-quality
enum · a per-premise review verdict · a monitor findings table · a watchlist `why` column ·
a rating column on reports · **a company-number column and a constraint that permits it**.
**All nine exist.** The gaps are the seven above and nothing else.
