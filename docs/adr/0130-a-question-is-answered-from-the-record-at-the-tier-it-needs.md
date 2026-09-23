# ADR 0130 — A question is answered from the record at the tier it needs, and the tier is priced before it runs

- **Status:** Accepted
- **Date:** 2026-09-23
- **Applies:** ADR 0035 (a role is admitted by a record), ADR 0053 (every ceiling is priced in
  pounds), ADR 0072 (a work order is the run root), ADR 0076 (a source reference names its
  relation), ADR 0106 (an operator's stated number in its own relation), ADR 0112 (the menu is
  grouped by what you are doing), ADR 0119 (a printed excerpt re-enters as data), ADR 0120 (an
  account owns what it asks), ADR 0129 (a stated number enters a ledger as an assumption)
- **Builds:** F6, *Ask, in three tiers* (`docs/V1.0_Alpha/04-feature-specifications.md`),
  page specification §13, data model Gap 5, mechanisms §2

## Context

F6 asks for a question box over a company's record that resolves each question into one of
three tiers, states the tier and the price before it runs, and never guesses. Tier 1 re-runs a
stored model with a changed input, for nothing. Tier 2 is one metered model pass over what the
record already holds, with no fetching. Tier 3 fetches new material, hashes it, cites it and
adds it to the company's record, and runs only after an explicit approval carrying a price.
Every answer carries the report's evidence drawer, and no answer may come from the model's own
knowledge. The mechanism (`08-mechanisms.md` §2) adds that resolution is deterministic first
and may err upward only, and that a tier-2 answer citing nothing is a mis-resolution to be
discarded rather than shown.

Mapping that onto the code found six things the specification does not settle.

**A tier-1 re-run has no stored inputs to reload.** The value step records counts and per-share
figures as strings (`ValuationOutcome.as_dict`), never the `DcfInputs` it valued. It rebuilds
everything each time — the analysis from the stored facts, the mandate from the confirmed
classification, the market capitalisation from the price step's record — and
`value_the_business` then strikes the base case, every scenario and both grids in one call
and persists all of it. Nothing re-runs the base case alone, and nothing re-runs it under a
different job. The valuation page is *read back, never recomputed*, on purpose.

**Nothing lists what the record holds for a company.** `gather_evidence` takes a research
request and scopes its documents by that request's work order; facts are company-wide
(ADR 0061) but documents and excerpts are a run's. A question is over a company, across every
run that fetched for it, and the only company-wide query of documents today is a count in the
price-alert service.

**The evidence drawer resolves inside a run only.** `/runs/{job_id}/footnotes/{number}`
rebuilds the run's document to find the marker, and needs the run's research request. A
question has no research request and no document. The calculation walk,
`/calculations/{id}`, checks ownership through the work order and works for any tool's job.

**A priced approval outside a gate has no record.** `record_decision` enforces gate order per
job over the nine `GateKind`s, and the budget approval is a paused run. A tier-3 approval is
not a gate: nothing is paused, the price is what is approved, and the same question may be
approved once and never again.

**Resolution as specified spends money before a tier is known.** Mechanisms §2.2 has a model
propose the entities a question names before code checks the record for them. That call is
metered, so a tier-3 question would spend before the approval the tier exists to demand, and
§2.5's *nothing is spent before approval* could not be kept to the letter.

**Every model role is admitted by a record and bound by a registry** (ADR 0035): a new role
needs an ADR, a `RoleDefinition`, a route, and an output schema the registry pins. Ask's
tier-2 reader is a new role.

## Decision

### 1. A question is a record, over one company, owned by the account that asked it

`questions` is Gap 5's table with three corrections. `company_id` is **not null**: a question
is answered from a company's record, and a question over nothing has no record to be answered
from. `job_id` is added: the question's own run root (ADR 0072), a work order with the tool
`ask` and the company as its subject, under which its calculations, its model calls and its
cost rows are written and by which the calculation walk checks ownership. `content` is added,
JSON: the structured answer — the figures with their calculation ids, the paragraphs with the
citations they rest on — from which the page and the drawer are composed. `answer` stays as
the specification has it, the prose alone, so a reader of the table sees what was said without
parsing anything.

The rest is the specification's: `user_id`, `report_id` (the report whose run a tier-1 answer
re-struck, when one exists), `asked_at` on the database clock, `question`, `tier`,
`tier_rationale`, `approved_at`, `estimated_cost_gbp`, `actual_cost_gbp`, `answered_at`,
`documents_added`. A question is never edited after it is answered, and never deleted while
its job holds cost rows.

### 2. Resolution is deterministic, spends nothing, and errs upward only

`aer.core.ask.resolve` is a pure function over the question's text and a `HeldRecord` — what
the record holds, as code read it: the subject's names, the confirmed assumptions a stored
model can vary, whether the run reached a valuation, the document titles, the fiscal and
publication years, whether a quarter is held. It returns a tier and the sentence that says why.

- **Tier 1 is a match, not a judgement.** The question names a registered recomputable
  operation and a parameter with a value and a unit: a discount rate half a point higher, a
  terminal growth rate of two per cent, an exit multiple of ten times. The registered
  operations are the discounted cash flow's inputs — the confirmed assumptions the run holds,
  and the discount rate the grids already vary. A parameter with no value, or a value with no
  unit the parameter is measured in, is not tier 1.
- **Tier 2 is a scope question.** Everything the question names is held: a year within the
  record's, a document kind whose title the record carries, a quarter where a quarterly filing
  is held, the subject by its own name. A name the record does not hold, a period it does not
  reach, or a temporal reference it cannot satisfy — *since the last results*, *has anything
  changed* — is not tier 2.
- **Everything else is tier 3**, the only tier that asks permission.

Where the specification had a model propose the entities before the tier was known, the
model's opinion arrives one call later, inside the tier-2 pass, as a field naming what the
question needed that the material did not hold. It can only push a question upward, and it is
one of the two checks in §4 that discard a tier-2 answer. So the deterministic resolver is the
one that decides, and a question it resolves too low is caught after one pass costing pennies
and refused with the tier-3 price — which is what mechanisms §2.4 calls the whole safety net.
A question it resolves too high costs the operator an approval they need not have given, which
is the direction the rule permits. A property test holds the resolver to it: no edit that
removes something the record holds ever lowers the tier.

### 3. Tier 1 is the run's own base case, struck again on the question's ledger with one input changed, and no model is called

The value step's input assembly is lifted into a function of its own, `base_case_inputs`,
which both the step and the question call: the cost of capital and the `DcfInputs`, from the
confirmed assumptions, the stored facts and the price step's recorded capitalisation. The
question re-strikes the base case twice on a ledger of its own — once as the run held its
inputs, once with the one change applied — and persists both under the question's job. Every
figure in the answer is a calculation row with a lineage, and the answer sets the two beside
the figure the report printed, read back from the run's ledger with its own id.

The changed value enters the ledger as an assumption in the question's own relation:
`SourceTable.QUESTIONS` and `SourceRef.question(question_id)`, on the terms ADR 0106 set for a
scenario shock and ADR 0129 for a planned weight — a number somebody chose, neither published
nor a fact about the book, with no run to be confirmed against, and a lineage node that names
the question it was stated in. A change to a driver applies to every forecast year, and the
answer says so. A run whose valuation model is not the discounted cash flow — a bank's
residual income — refuses tier 1 by name until its inputs are lifted the same way.

The prose is composed by code from the figures, as the closing section's rows are: which
input moved, from what to what, and what the value per share came to under each terminal
method. No model writes it, because there is nothing for a model to add that a figure with a
formula does not already say.

### 4. Tier 2 is one metered pass by a role that sees only what it was dealt and may cite only that

`ask_reader` is the role, admitted by this record, routed to the workhorse model, with no
tools. It is dealt the company's held record within a fixed token budget, ranked by the
question's own words: the admissible source documents across every run of the account's that
fetched for this company, their extracted excerpts in the untrusted channel labelled with the
ids to cite them by (invariant 8), the company's facts, and the calculations of the run the
current report was written from. It returns paragraphs, each with the ids it rests on; what
the question needed that the material does not hold; and whether, in its own view, the
material answers the question at all.

Code then applies two checks, and a third the platform contract already imposes:

- a citation naming anything outside the dealt pack is dropped, and an answer left with no
  citation is discarded;
- an answer whose reader says the material does not answer the question, or names something
  it needed and did not hold, is discarded;
- a numeral in the prose that no dealt figure or cited excerpt reads as is a figure of the
  model's own, and the answer is discarded.

A discarded answer is not shown. The question is recorded as tier 3 with the words *That is
not in this record*, the pass's cost is recorded against it, and the tier-3 price follows.
The cost of a kept answer is read from the cost rows and shown after, as the specification
asks.

### 5. Tier 3 is priced before, approved on the question's own row with the price it was shown, and runs through the acquisition path

The estimate is deterministic and shown with the tier: how many searches the question's
entities need, how many documents at most they may add, and what the searches, the reading
turns and the answering pass come to at the routed models' published prices. The sentence is
the specification's: *This needs new material. About £X, and up to N new documents will be
added to {company}'s record. Go ahead?*

The approval is a row on the question, not a gate: `approved_at`, set by a form post that
carries the hash of the estimate it was shown and is refused when the row's estimate differs.
One approval per question; a question approved once cannot be approved again, and one never
approved never runs. On approval the question's work order carries the account's excluded
domains, every fetch goes through the normal path — hashed, tiered, policy-checked, stored
against the company — and the documents added are recorded on the row.

**The acquisition half of this tier lands after F4**, the refresh, which builds the path that
fetches for a company outside a research run and that tier 3 shares. Until it does, a tier-3
question is resolved, priced and recorded, and the page says that researching it is not yet
available here. It offers no control, because a control that goes nowhere is the failure the
tools registry exists to refuse. The approval row, the hash check and the run are this
record's, so that build adds the path and nothing else.

**Amended 23 September 2026, on building the acquisition half** (after F4, ADR 0131). Three
things the paragraph above left to the build are now fixed, and one sentence in it is
corrected. The acquisition is **rooted on the company's current report's own research
request**, not on the question's work order: the established hosts, the operator's
exclusions, the held-document checks and the citation scope are that run's, so a fetch here
is admitted exactly as a research worker's would be, and every document added is recorded
under that request with the question's own job as the fetcher of record — which is what makes
"added to the company's record" true rather than said. The question's work order still carries
the run's job, its step and its costs. A question on a company with no current report cannot
be approved, and one whose report is withdrawn between approval and run finishes without
running, saying so. The **research worker** takes the question as its brief (a `question`
topic on the `analysis` role, prompt version 6), within bounds set from the estimate the
operator agreed to: one turn per search, one fetch per document the estimate allowed. What it
fetched is **excerpted** on the way in, so the reader can cite it; the reader then answers as
tier 2 does, over the grown record. **Nothing useful is an answer that says so** — the
documents stay, the cost is reported, the question is answered — never a failure. The
estimate's searches are capped at the worker's own bound and its turns are priced at the
analysis route. The go-ahead is a form post carrying the estimate's hash; the run is queued
to the worker, which holds the fetcher; the page says the question is being researched
until it answers, and lists what was added beside the answer.

### 6. Every answer carries the drawer, and the drawer is the question's own

A tier-1 figure links to the calculation walk, which already checks ownership through the
job's work order. A tier-2 paragraph's citations are numbered notes on the answer page, each
resolved by `/ask/{question_id}/notes/{number}`: an excerpt shows the extraction as stored,
subject to ADR 0119's licence rule, with the source it came from; a fact shows its figure and
source; a calculation continues to the walk. A tier-3 answer will list the documents it added,
each on the company's record. The markers are meaningful for the reason the report's are: the
note is resolved from the stored content, not rebuilt from a model's text.

### 7. Ask is a working tool under *Research*, and its spend is its own

Ask joins the tools registry as a working tool at `/ask`, under the *Research* heading
(ADR 0112): a question is asked over a research record, and its third tier commissions
research. Its work order's cap is the account's per-run budget and the monthly cap applies on
top; the guard runs before a tier-2 call with the pass's estimate, and a refusal fails the
question's job with the reason rather than pausing for a decision nobody is awake to make
(ADR 0078). Tier 1 makes no model call and writes no cost row.

## What was rejected

**A model-judged resolver.** It would spend before a tier-3 approval, it would be a
classifier that guesses, and the property the testing strategy asks for — that the resolver
errs upward only — cannot be stated over a model's output.

**Tier 2 over the raw documents.** A single filing is a hundred thousand tokens, and a pass
over every document the record holds is pounds, not pennies. The excerpts the platform already
extracted, hashed and located are what the sections are dealt, and an answer that cites one
resolves to the same stored row the report's notes do.

**Answering from a research request's evidence.** `gather_evidence` is scoped to one run,
and a question is over a company across every run. Reusing it would answer from whichever run
was chosen and say nothing about the rest.

**Recording the tier-3 approval as a gate decision.** A gate is a paused run with an ordered
place in a workflow; a tier-3 approval pauses nothing and orders nothing. Forcing it into
`approvals` would need a tenth `GateKind` that no run reaches and a job whose workflow has no
such step.

**A tier-1 answer written by the model.** The specification's *for nothing* is exact, and a
sentence composed from figures with formulas needs no interpretation.

**Building tier 3's acquisition now, ahead of F4.** The plan sequenced tiers 1–2, then the
refresh, then tier 3, because the refresh builds the fetch-for-a-company path both need. Building
it twice, or first here, would be folding a later item's work into an earlier one.

## Consequences

- Migration 0085 adds `questions`. `Question` is the model; `SourceTable.QUESTIONS` and
  `SourceRef.question` the reference; `_question_node` the lineage leaf.
- `aer.core.ask` holds the pure resolver and the operation registry; `aer.services.ask` reads
  the record, strikes tier 1, runs tier 2, prices tier 3 and records the question;
  `aer.agents.ask_reader` is the role; `aer.web.ask` the pages.
- `aer.services.valuation_run.base_case_inputs` is the value step's input assembly, called
  from the step and from the question.
- `08-mechanisms.md` §2.2 is corrected in place: the model's entity proposal arrives inside
  the tier-2 pass. `07-data-model.md` Gap 5 is corrected for the three columns.
- Tier 3's acquisition is this record's open item, closed by the build after F4.
