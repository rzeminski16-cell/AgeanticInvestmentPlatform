# ADR 0103 — The monitor measures the crossing, and the model reads the rest

**Status.** Accepted
**Date.** 2026-09-02
**Required by.** Roadmap §3.6, and by ADR 0035, which holds that a new agent role needs a
record before it needs a registry row.
**Extends.** ADR 0079, which admitted the `thesis_monitor` role and settled what it may say,
and ADR 0078, which settled where its output lands. Both are taken as written; this record
decides the three things they left to the build — what code measures before the model is
asked anything, what a finding is as a row, and how a finding is closed.

## Context

ADR 0079 gave the monitor one job: read one premise against the evidence that arrived after
it was written, and return a status from a closed enum with a justification naming source
documents. ADR 0078 tiered the statuses — `contradicted` opens a gate, the rest are findings
with no approval semantics — and required that a finding be closed only by an explicit act
with a reason, never by the condition lifting, and that an unattended run breaching its cap
stop with a finding rather than pause for nobody.

Between those two records and a working tool sit three questions neither answers.

**What does "code hands the model a crossing" mean when the metric is free text?**
`premises.metric` is a string the operator typed — "revenue growth", "operating margin",
"capital allocation score" — and ADR 0102 kept it free deliberately, because closing the
vocabulary in the table "would put the platform's current reach in the operator's mouth".
So something has to decide, per premise, whether the platform can measure what it names.

**Where does a finding live, and what stops it becoming an approval?** ADR 0078 wanted "a
separate table rather than a nullable column on the existing one", and a resolution "as an
appended record … not a flag flipped on the item". The shapes were argued; the columns were
not.

**What does the operator decide at the one gate the monitor opens?** Every existing gate
decides something about a run that then continues. A contradicted premise is not a run, and
"approve" is the wrong verb for it.

## Decision

### 1. Code resolves the metric, measures it and decides the crossing; the model interprets

Before any model is consulted, deterministic code:

1. **Resolves the metric name** to something the platform computes. The string is slugged
   (`"Revenue growth"` → `revenue_growth`) and matched, in order, against three vocabularies:
   a **growth** of a canonical concept (`<concept>_growth`, through `calc/basic.growth_rate`
   over two consecutive fiscal years); a **ratio** from `calc/ratios.RATIO_DEFINITIONS`
   (`operating_margin`, `return_on_equity`, …); and a **level** of a canonical concept
   (`revenue`, `net_income`, …) read straight off the statement line. A name that matches none
   is **unobservable**, and the finding says which names it would have understood.

2. **Normalises the threshold's unit** without coercing the fact's. `percent`, `%`, `per
   cent` and `pct` mean the threshold is a fraction written a hundred times larger (ADR 0027)
   and it is divided by a hundred once, here, before it meets a dimensionless ratio;
   `ratio`, `pure`, `x` and `times` are dimensionless as written; anything else goes through
   `Unit.parse`, so `USD` compares only with a line in dollars. A threshold whose unit the
   platform cannot parse, or that does not match the metric's, is **unobservable with the
   mismatch named** — `Quantity.__ge__` raises `UnitMismatchError` rather than guessing,
   exactly as ADR 0079 promised, and the monitor reports the refusal instead of hiding it.

3. **Decides whether the premise holds** — `observed <comparator> threshold`, through
   `Quantity`'s own comparisons — and records the observation: the metric, the value, its
   unit, the period it describes, the threshold, the comparator, and the calculation or fact
   row the value came from.

Only then is the model called, and what it is handed is the premise, the observation *with
the verdict already in it*, and the facts of the periods that arrived since the premise was
last read. **The model's status is bounded by the crossing.** A premise the filing has
defeated is `contradicted` whatever the model says; a premise the filing confirms is never
`contradicted` and never `unobservable`, because code just observed it. Within those
bounds the model chooses — `weakened` for a margin that held above its floor but fell
towards it, `strengthened` for one that widened, `unchanged` for the rest — and writes the
justification. A reply outside the bounds is corrected by code and the correction logged;
it is not an error, because the tier is code's and always was (ADR 0078: "assigned by code
at the point the status is written").

**A premise nothing new bears on is not read.** The window is the fiscal years whose facts
were filed after the premise was held, or after its last reading. A monitor pass over a
thesis whose company has filed nothing since makes no model call, spends nothing and writes
nothing — "no news" is not a finding, and a queue that filled with it would be the alert feed
ADR 0079 refuses.

**A premise with no predicate is not read either.** ADR 0079: "there is no trigger at all,
and the item gets a scheduled human review instead." The monitor tool's contribution for
those is the review date — a premise past its `review_by` is an attention item leading to
the thesis, never a model call.

### 2. A finding is a row of its own, and a resolution is an appended record

`findings` holds what the monitor noticed: the thesis and, for a reading, the premise; the
run it happened in; a `kind` — `reading` or `stopped` — and for a reading a `status` from
ADR 0079's enum; the justification; the source document ids the justification names,
validated by code against the window and with anything else dropped; the observation, as
JSON; the window's bounds; and whether the row **opens a gate**, a stored column pinned by a
check constraint to `status = 'contradicted'` so the tier is a fact of the row and not of
whichever template renders it.

`finding_resolutions` is append-only: one row per act, carrying the action (`dismissed`,
`withdrawn`, `reopened`), a reason that may not be blank, the actor, and — for the gate —
the `approvals` row that witnessed it. A finding is open when it has no resolution or its
latest is a reopening. Nothing on `findings` is updated when it is resolved; the history is
the rows.

Every write goes on the audit chain with the thesis as its subject, through the correlation
ADR 0072 added for exactly this.

### 3. The gate asks what you do about the premise, and the decision is what you did

A `contradicted` finding opens `GateKind.THESIS`. It is a gate in the full sense ADR 0078
means — an `approvals` row with an actor, a `payload_hash` of exactly what was displayed and
a chained audit event — and it is unlike the other eight in three ways that are written
into the code rather than remembered:

- **It is decided on a finding, never on a run step.** `services/approvals.record_decision`
  refuses it, because that function enforces the research run's gate order and
  once-per-gate-per-job, and neither applies to a gate a monitor pass may open several
  times. The monitor service writes the row itself, keyed to the monitor's own work order
  and job, so the budget guard, the cost ledger and the audit chain all see one run root.
- **The question is what to do about the premise.** *Withdraw the premise* records
  `Decision.APPROVED` — the finding is accepted — and withdraws the premise through the
  theses service with the operator's reason, so the thesis page shows it struck through with
  that reason. *Keep the premise* records `Decision.REJECTED` — the finding is rejected —
  with the reason, and the premise stands. Both close the finding with a resolution row
  pointing at the approval. "I saw this and chose to do nothing" is a row with a reason and
  a hash, which is what ADR 0078 said it had to be.
- **It is not in the research journey.** `GateCertainty` gains `ON_FINDING` — "not part of a
  research run at all" — so the gate appears in the shared vocabulary and the consequence
  table every gate must be in, and never in a run console's sequence.

### 4. Cost changes shape, as ADR 0078 said it would

A monitor pass is a work order with `tool="monitor"`, the thesis as its subject, no mandate,
today's date and `point_in_time=False` — it reads everything the store holds — capped at the
platform's per-run default. Each premise read is a job step, so the base agent's two
refusals (ADR 0053) bind every call against that cap and the month's. A refusal does not
pause: the run is marked `FAILED` with the refusal in its error, a `stopped` finding names
the cap and the premise it was reading, and the pass ends. Nothing is left in
`BUDGET_EXCEEDED` or `AWAITING_APPROVAL` for a person who is asleep.

## What was rejected

**Letting the model decide the status alone, as the output contract's shape suggests.** The
contract has a `status` field and ADR 0079 says the model returns one. But it also says the
model "does not decide whether the threshold was crossed", and the only way to hold both is
for code to bound what the field may hold. A model asked "was this contradicted?" about a
number it did not compute would sometimes say no about a crossing it was shown, and the
premise the position rests on would stay held on a courtesy.

**A closed metric vocabulary on the premise form.** The operator would then write only what
the platform can read, and ADR 0102 refused that on purpose. The vocabulary lives in the
resolver, the `unobservable` finding names it, and the form's help text lists it — which is
the shape ADR 0079 asked for: "a premise about a line it cannot yet read is still a premise".

**Testing against price.** Rejected in ADR 0079 at length; the evidence window here is
`financial_facts` and nothing else, and the observation JSON has no field a price could
occupy.

**An auto-clearing finding.** A weakened premise re-read as unchanged a quarter later is two
findings, not none. The second does not close the first; only a person does.

## Consequences

The monitor is the fourth working tool: a run that can be started from the page, the CLI
or the worker; a page of open findings grouped by thesis with the resolution forms; an
attention provider that puts a contradicted premise in front of the operator as *waiting for
you*, a stopped pass as *needs diagnosis*, and an unread finding or an overdue review as
*not started*; and a gate kind that fires seldom.

What the monitor can measure today is what `calc/` computes from annual facts: growth of a
line, the ratio suite, a line's level. Segment lines — "Azure revenue" — are dimensioned
facts the analysis deliberately excludes, so a premise about one is `unobservable` until a
later change reads dimensions, and the finding says so rather than reading the consolidated
line in its place. That is the honest edge of the tool and it is written on the finding.

The loop ADR 0079 declined to close stays open. Nothing here reads a position, a decision or
a neighbouring thesis; a finding reaches nothing that moves without a person.
