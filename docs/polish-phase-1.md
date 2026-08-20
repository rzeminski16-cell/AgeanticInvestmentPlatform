# Polish Phase 1 — task sequence (tasks P1–P11)

Everything here comes from one source: the first complete run of `vertical_slice_v1`
(job `ccf8dea0`, subject AMZN, code version `abee2ce`, 18 August 2026). That run finished
all twenty-five steps through six human gates for £6.26 and produced a report that is not
usable. This file is the dependency-ordered breakdown of putting that right.

The phase specification remains `docs/PLAN.md`; nothing here changes a Stage 4 task or
adds a capability. **This phase adds no features.** It closes the gap between what the
platform claims about itself and what it did.

## The rule this phase is mostly about

Invariant 1 of `CLAUDE.md` says every externally derived fact traces to a hashed artefact.
The run satisfied that invariant completely — and still printed another company's revenue
on the front page of an Amazon research note, with a correct footnote pointing at a real
Alibaba filing. **Provenance was intact; identity was not.**

So the rule this phase writes down is the one the invariants assumed and never stated:

> A fact's lineage answers *where did this come from*. It does not answer *who is this
> about*. Evidence must be scoped to the subject, and the scope must be enforced in the
> query, not in the prompt.

Everything in Task P1 follows from that sentence, and Tasks P3 and P5 are the same failure
in two other dimensions: a fact carrying the wrong *year*, and prose carrying a *method*
that no calculation performed.

---

## What the run proved

Recorded here because these are the acceptance baselines, and because a fix nobody can
measure against a known-bad run is a fix nobody can trust.

| Observation | Evidence |
|---|---|
| Peer facts entered the subject's evidence pool | Sources list of an AMZN note cites Walmart, Alibaba, eBay, JD.com, MercadoLibre, Target |
| 84% of the pool was not the subject | 77,900 peer facts persisted against 14,789 of Amazon's |
| The pool crowded out the subject entirely for annual sections | Report, page 9: *"They belong to Alibaba Group Holding, not to Amazon"* |
| Three issuers' share counts sat side by side | Red team: 50,696,802 / 445,000,000 / 10,786,313,572 within two weeks |
| Fiscal years are labelled a year late | All twelve "FY2022" ratios equal Amazon's actual FY2021 to the decimal |
| The peer set produced nothing | `comps.built peers=0 excluded=8`, `computed=0` |
| Sections are unreliable | 3 of 16 passed first time; 4 failed outright |
| Sections overrun length by a constant | 7 refusals, ratios 1.38–1.63, mean 1.46× |
| Estimates misprice individual steps | research 2.4× low, red_team 4× high; total 0.79× |

Two things the run also proved, which this phase must not damage:

- **Citation verification works.** 86 checked, 0 failed, twice.
- **The defences found the contamination.** The drafting model refused the foreign data and
  said why; the red team located it independently and named the conflicting facts; two
  metrics failed; the approval page said "contaminated" seven times before the operator
  approved. Every fix below must leave those behaviours exactly as they are.

## Decisions pre-made and recorded here

**1. Peer fact acquisition is withdrawn, not repaired.** Every multiple in
`MULTIPLE_DEFINITIONS` — `ev_ebitda`, `ev_sales`, `pe`, `p_b` — needs `enterprise_value`
or `price_per_share`. Both need market data. Without a price subscription the comps table
is uncomputable for *any* company, the subject included; the run confirms this with
`computed: 0` on the subject's own row. Acquiring eight companies' filings to feed a table
that cannot be built is pure cost, and it is the cost that caused the contamination.
Task P4 removes the acquisition and keeps the gate.

**2. The company filter is added anyway.** P4 removes today's contamination vector; P1
removes the class. When prices arrive and peer acquisition returns, it returns behind a
scoping rule that already exists and already has a test. Doing only P4 would leave a
loaded gun for whoever re-enables peers in six months.

**3. Nothing here waits on an external decision.** No new provider, no new key, no licence
determination. The EODHD subscription remains the gate on comps and it is out of scope.

**4. The four unmeasured metrics are not a defect.** `RUN_TIME` in `aer/eval/metrics.py`
deliberately holds nine of the thirteen; `injection_resistance`, `unit_integrity`,
`custom_section_contract_conformance` and `skill_privilege_containment` are corpus metrics
measured by the CI evaluation gate, not per run. The defect is presentational — the report
says "measured 9 metric(s)" and leaves a reader to wonder about the other four. Task P9
names them and says where they live.

## Why this order

P1 is first because six other findings are downstream of it. Three of the four section
failures are the numeral rule correctly refusing claims the model could not cite, from an
evidence pack that was 84% somebody else; `numerical_consistency` failed on two ratio sets
that the contamination produced. **Tuning prompts or budgets before P1 lands would be
tuning against noise.** P6 and P7 therefore come after, and both begin by re-measuring.

P4 lands immediately after P1 for a different reason: it is the change most likely to be
wanted urgently (it removes 26 MB and 78,000 rows from every run), and stacking it on the
scoping fix means the diff that removes peer acquisition never has to reason about
contamination.

P11 is last and is the phase's acceptance test: the same AMZN request, re-run, diffed
against the artefacts in this file.

---

## Task P1 — Evidence is scoped to the subject company

**Severity: critical.** Closes findings 1 and 2 of the post-mortem.
**Status: done** — ADR 0061, migration 0042, `tests/test_evidence_is_the_subjects.py`.

Three things were built differently from the sketch below, each because the code said so:

- **The predicates are shared helpers, not repeated clauses.** `aer.services.facts.
  visible_facts` and `aer.services.sources.visible_sources`, called by all four consumers.
  Three copies of a predicate is how the first two diverged, and `research.py` had already
  written the correct one — this promotes it rather than paraphrasing it a third time.
- **The subject is read from the request, not passed as a parameter.** A
  `subject_company_id` argument threaded through three call chains is an argument a caller
  can get wrong; `research_requests.company_id`, written once by `acquire`, cannot be.
- **The acquisition API takes `company_id`, not a `Company`.** It sits beside `job_id` and
  matches it, and it lets `research.py` stamp a regulator-named filing without loading a row
  it does not otherwise need.

### The defect

`peer_discovery._acquire_peer_facts` records each peer's companyfacts as a
`SourceDocument` carrying the *subject's* `request_id`. Both evidence builders then select
by request and never by company:

```
src/aer/sections/evidence.py:373
    .where(SourceDocument.request_id == request.id, FinancialFact.dimension_axis.is_(None))

src/aer/render/glance.py:136
    .where(SourceDocument.request_id == request.id, FinancialFact.dimension_axis.is_(None))
```

The `dimension_axis` guard shows the author was alert to a *segment* slice being mistaken
for the whole. The same reasoning was never applied to a different company, because until
ADR 0059 no other company's facts could exist under a request.

Two orderings turn the leak into a takeover. Facts sort `period_end DESC`, so Alibaba's
March-2026 year end outranks Amazon's December-2025 one and fills the 400-row pool first;
for a section whose `fact_basis` is `annual`, Amazon may not appear at all. Sources sort
`retrieved_at DESC`, and peers are fetched last, so they occupy the top of the 40-item
listing handed to every section.

### This was found once already, and not generalised

`aer/services/research.py` fixed exactly this bug for the research workers, and left the
reasoning in the code:

```
src/aer/services/research.py:525 — _visible_facts
    "Scoped by company, not by request, and that is the fix for a run that
     found nothing. […] every consumer here joined through source_documents
     to request_id, so those facts belonged to the earlier run's document and
     this run could not see one of them. Five workers spent sixty tool calls
     searching an empty table."
```

So the rule at the top of this file is not new. It was discovered under a different
symptom, applied to one module, and never propagated. That has two consequences for this
task.

**The pattern to copy already exists.** `_company_id_for` and `_visible_facts` are the
correct shape; `evidence.py` and `glance.py` simply predate them. The fix is adoption, not
invention.

**The second failure mode is worse than the first, and this run did not show it.** Facts
deduplicate on an observation key that excludes the source document, so the *second* run of
a company inserts nothing — visible in this run's log as MSFT's `supplied: 18588,
inserted: 0`. Those rows hang off an earlier run's source document. A request-scoped join
therefore cannot see them. **Re-running AMZN today would produce a report containing none
of Amazon's own facts at all** — not a diluted evidence pack, an empty one. Task P11's
rerun depends on this task, and would otherwise fail in a new and more confusing way.

### The sites

Three confirmed, not two. `research.search_sources` (line 155) filters on `request_id`
alone, so the workers' source search saw the peers' filings even though their fact search
did not.

### The change

`source_document` gains a nullable `company_id` FK — **migration 0042**. Nullable because
not every document is about a company: a macro series or a narrative page legitimately has
none, and forcing one would invite a wrong answer where an honest absence belongs.

| Site | Change |
|---|---|
| `aer/db/models/source_document.py` | `company_id: Mapped[UuidFk \| None]`, indexed with `request_id` |
| `aer/services/acquisition.py` | `record_acquisition` takes `company: Company \| None` and stores it |
| `aer/workflow/workflows/vertical_slice_v1.py` | subject acquisitions pass the subject company |
| `aer/services/peer_discovery.py` | peer acquisitions pass the peer company (until P4 removes them) |
| `aer/sections/evidence.py` | `evidence_for(...)` takes `subject_company_id`; fact query adds `FinancialFact.company_id == subject_company_id`; source query adds `SourceDocument.company_id.in_((subject_company_id, None))` |
| `aer/render/glance.py` | same predicate on `_consolidated_facts` |
| `aer/services/research.py` | `search_sources` adopts the same source predicate its `search_facts` already applies to facts |

**The subject is decided once.** Every other step reads
`context.output_of("acquire")["company_id"]`, which is the row `upsert_company` wrote and
is authoritative. `research._company_id_for` resolves by `ticker + exchange` instead — a
weaker key that a re-listed or re-used ticker would defeat. This task standardises all
three consumers on the acquire step's id and reduces `_company_id_for` to a fallback for
the pre-acquisition case it was written for.

Because facts are shared across runs by design, the fact predicate is `company_id` **only**
— it must not also join to `source_documents` for the request, or it reintroduces the
empty-pack failure `research.py` already fixed. The `request_id` scope stays where it
belongs: on the *source listing*, which is about what this run fetched.

### Tests

The predicate is two lines and easy to lose again; the test is the deliverable.

- **The invariant test.** Seed a request holding facts and source documents for two
  companies. Assert that no `EvidenceUnit` in any section's pack references a fact whose
  `company_id` is not the subject's, and that no source listed belongs to another company.
  Parameterised over every section in the spine, so a new section cannot opt out.
- **The ordering trap, pinned.** Give the non-subject company a *later* `period_end` and
  a *later* `retrieved_at` than the subject — the exact shape that produced this failure —
  and assert the subject's facts still appear.
- **The repeat-run test**, which nothing currently covers and which the rerun depends on:
  persist the subject's facts under a *first* request, then build an evidence pack for a
  *second* request for the same company, and assert the pack is not empty. This is the
  failure `research.py` met and fixed; it must not be possible to reintroduce it in the
  section layer.
- **The null case.** A source document with no company (macro) stays visible.
- **Glance.** Same two-company fixture; assert every at-a-glance row shares one company.
- Mutation check: delete each predicate in turn, confirm a named test fails for each.

### ADR

**ADR 0061 — evidence is scoped to the subject, not to the request.** Records the rule at
the top of this file, why `request_id` was a sufficient proxy until it was not, and why the
scope is enforced in the query rather than by asking a model to ignore what it was shown.

It must also record the thing that makes this more than a bug fix: the rule was already
derived once, in `research.py`, from a different symptom, and stayed local. An ADR is the
mechanism this repository has for a lesson learned in one module binding the next one — so
the ADR states the rule for **every** consumer of `financial_fact` and `source_document`,
present and future, and the invariant test is what enforces it.

### Acceptance

`evidence_for` on the two-company fixture returns only subject facts; the invariant test
fails when the predicate is removed; `mypy --strict` and `ruff` clean.

---

## Task P2 — The at-a-glance block refuses to mix issuers

**Severity: critical.** Belt and braces on P1, because this block is the first thing a
reader sees and it reached a signed PDF.
**Status: done** — the guard in `render/glance.py`, the reason carried on the
`CoverageNote`, and one detail sharpened in building it: the check is against the
*subject*, not for internal agreement, because a pool that is uniformly the wrong company
would pass a facts-agree-with-each-other test and still be wrong.

`glance.py` asserts that every fact feeding the block shares one `company_id`. If they do
not, the block does not render and states why. A front page that silently mixes issuers is
worse than no front page, and the run showed three issuers' figures presented as one
company's quarter — $550m of net income at $5.75 a share implies 96 million shares, and
Amazon has 10.7 billion.

The check is deliberately redundant with P1. P1 is a query predicate that a future refactor
can drop; this is an assertion at the point of rendering that fails loudly.

**Tests.** A mixed-company fact set produces no block and a stated reason; a clean set
renders normally; the reason reaches the report rather than only the log.

---

## Task P3 — Fiscal year comes from the period, not the filing

**Severity: high.**
**Status: done** — `fiscal_year_of` in `aer/core/dates.py`, both adapters converted,
migration 0043, ADR 0062. Two findings from the build: the correct rule already existed in
`segments.py` and had never been generalised (ADR 0061's shape, again); and the old code
made `analysis`'s period labels *nondeterministic* — a period's rows could carry two
different `fy` values, and the label picked whichever row iterated first. Interim rows
keep the filing's frame, deliberately; the ADR states why and what residual that leaves.

### The defect

```
src/aer/sources/sec/companyfacts.py:273
    fiscal_year=_parse_int(entry.get("fy"))
```

In SEC companyfacts, `fy` is the fiscal year of the *filing the observation appeared in*,
not of the period it describes. Amazon's FY2021 figures appear as comparatives inside the
FY2022 10-K and arrive tagged `fy: 2022`. Every comparative figure the platform stores is
labelled a year late — silently, and consistently enough to look right.

Confirmed against six independent ratios, all matching Amazon's actual FY2021 while
labelled FY2022: gross 42.0%, operating 5.29%, net 7.10%, ROE 24.1%, ROA 7.9%, current
1.14.

### The change

A pure helper in `aer/core/` — `fiscal_year_of(period_end: date) -> int` — returning the
calendar year in which the period ends, **except** when `period_end` falls in the first
seven days of January, where it returns the prior year. That exception is the 52/53-week
retail calendar: a year ending 2 January 2027 is FY2026 to the filer and to any reader.

Checked against the three shapes this platform actually meets:

| Filer shape | Period end | Correct label |
|---|---|---|
| December year end (Amazon, Alphabet) | 2025-12-31 | FY2025 |
| March year end (Alibaba) | 2026-03-31 | FY2026 |
| Late-January retail (Walmart) | 2026-01-31 | FY2026 |
| 52/53-week rolling into January (Target) | 2027-01-02 | FY2026 |

The filing's own `fy` is kept as provenance rather than discarded — it is the honest record
of what the source said — but it stops being the label.

**Existing rows are wrong.** Migration 0043 recomputes `financial_fact.fiscal_year` from
`period_end` in SQL using the same rule. The re-parse alternative was considered and
rejected: artefacts are content-addressed so re-parsing is cheap, but it would leave rows
from artefacts since pruned, and a data migration is the honest one-pass fix.

### Tests

- Property test over `fiscal_year_of`: monotonic in `period_end`, never more than one away
  from the calendar year, stable across the January boundary.
- Golden test per filer shape in the table above.
- A regression test built from this run: the ratio set that was labelled FY2022 must label
  FY2021 after the change.
- Migration test: rows written under the old rule are corrected, and re-running the
  migration is idempotent.

### ADR

**ADR 0062 — a fiscal year is a property of the period, not of the filing.** The 52/53-week
convention is a real choice with a defensible alternative, and the next person to meet a
week-based filer needs the reasoning rather than the constant.

---

## Task P4 — Peer acquisition withdrawn until prices exist

**Severity: medium**, and it removes today's contamination vector as a side effect.
**Status: done** — ADR 0059 amended; `_acquire_peer_facts` deleted from
`peer_discovery.py`. Three things settled against the code rather than the sketch:
`PeerProposal.period_end` became `date | None` (an unfetched peer has no period, and
inventing an alignment date would fabricate the comparison — the gate payload serialises
`None` as `""` so the approval hash stays string-built); a peer whose facts are *already*
stored keeps its company id and latest stored period, so a past subject still aligns; and
the one-reason rule landed as `UNACQUIRED_PEER_REASON` at source in `comps.build` plus
reason-grouping in `comps_run.as_dict`, not a table-level short-circuit — a dated peer
whose *pricing* failed keeps its own distinct reason, because for that one the filings are
held. `TestNothingIsFetchedForAPeer` pins the withdrawal with row-count and
unreachable-client proofs.

### The defect

`comps_run.py:138` passes an empty dictionary with a comment that is no longer true:

```
src/aer/services/comps_run.py:135
    # Empty, and that is the honest state rather than a stub. A peer's multiple
    # needs that peer's filings and prices, and this workflow acquires neither —
    peer_multiples={},
```

The workflow *does* acquire peer filings, and that acquisition is what caused P1. The run
fetched 26 MB, persisted 77,900 facts, and excluded all eight peers with a per-peer reason
— "the filings or the price series this platform holds do not cover this company" — that is
false about the filings and misleading about the cause.

### The change

- `peer_discovery` keeps resolving every proposed ticker against the EDGAR registry. That
  is what makes the gate meaningful and it costs one lookup against a file the run already
  holds; a hallucinated peer is still refused before any request is made for its filings.
- It stops fetching companyfacts, upserting the peer company and persisting its facts.
  `_acquire_peer_facts` is removed; `DiscoveredPeers` carries the resolved identity and the
  rationale, which is what the gate displays and what a future comps step will need.
- `comps_run` reports **one** blocking reason for the table — *no price series is
  subscribed, so no multiple is computable for any company, the subject included* — instead
  of eight per-peer exclusions that misdescribe why.
- The peer gate page says plainly that the confirmed set is recorded for when price data
  exists, so an operator is not led to expect a comps table this run cannot build.

### Tests

Existing peer tests keep their refusal cases (blank ticker, unresolvable, subject's own
CIK, duplicate) and lose their acquisition cases. Add: approving a peer set writes no
`FinancialFact` and no `SourceDocument`. That test is what stops the acquisition returning
by accident.

### ADR

Amend **ADR 0059** with the withdrawal, the measured reason (`computed: 0` for the subject
too), and the condition for return: peer acquisition comes back when a price feed makes a
multiple computable, and it comes back behind P1's scoping rule.

---

## Task P5 — The valuation method is rendered, not written

**Severity: high.** The most interesting finding in the run, because every existing defence
passed it.
**Status: done** — ADR 0063, migration 0044 (contract version 2),
`aer/sections/valuation_method.py`, and the `SectionAugmenter` mechanism in
`deterministic.py`/`writing.py`. Four things settled against the code rather than the
sketch: the block's fields live on the *stored contract* marked `platform_filled` (the
render order and headings come from the contract, so the fields had to), while the model
is bound by the contract minus them — its schema forbids unknown keys, so the method
fields are unrepresentable rather than discouraged; the ledger is read at the *first*
base-case row per name, because a sensitivity grid's cells are whole DCFs recorded after
the base run under the same `case="base"` label and the tail of the ledger is a grid
corner (mutation-verified); the terminal spread reaches the reader as the valuation's own
recorded caveats (`METHOD_DISAGREEMENT_CAVEAT` at the calc module's stated quarter) rather
than as a new number, which invariant 3 would have refused; and a failed commentary keeps
the rendered block under the failure banner, because the record is true whatever the model
did. The commentary check is a fixed word-boundary vocabulary — inputs never held (prices,
bond yields, return regressions) refused outright, component terms refused unless the
block states them — and the ADR records the paraphrase residual that accepts.

### The defect

The DCF section describes a methodology the run never executed:

| The report claims | The run did |
|---|---|
| Beta from five years of weekly returns | The operator typed 1.79 (`by_human: true`) |
| Risk-free rate = on-the-run 10-year Treasury | The operator typed 0.03 |
| Cost of debt from Amazon's own note coupons and traded yields | Derived from filings; no bond data in the run |
| Weights are market weights | `wacc basis: "book"` |
| AWS forecast on capacity and price per unit of compute | Six ratio drivers on consolidated revenue |
| Anthropic and OpenAI stakes added in the bridge | No such calculation exists |
| Implied return against the closing price on 18 August 2026 | The run holds no price data |

The numeral rule guards figures; this section states almost none. Citation verification
guards quotes; these are not quotes. **A section can evade the entire validation apparatus
by being confidently qualitative** — and qualitative prose about method is exactly where a
reader's trust is set.

### The change

The method description stops being model output. A deterministic builder in
`aer/sections/deterministic.py`, joining the two that already exist, renders from the
run's own records:

- every WACC component with its value, its `proposed_by`, and whether it was measured or
  typed by a person;
- the forecast drivers actually used, named as the assumptions they are;
- both terminal-value methods with their results and **the spread between them**;
- the share count used and where it came from.

That last point also closes a Low finding: Gordon growth produced $14.12 a share against
an exit multiple of $130.55 — a 9.2× spread, with 72% of value in the terminal — and it
appears in no section of the report. It is the most informative number the valuation
produced.

The model keeps a commentary field, constrained by its policy to interpreting the figures
rendered above it. It may say a WACC is high for the sector; it may not say how the WACC
was assembled, because the section already states that from the record.

### Tests

- The rendered block matches the recorded calculations for a fixture run, including the
  human-typed flags.
- A run whose beta was typed renders "set by the operator", never "estimated from returns".
- The terminal spread appears whenever the two methods differ by more than a stated factor.
- The `valuation_dcf` policy rejects a commentary that names a method input absent from
  the calculation store.

### ADR

**ADR 0063 — a claim about how a number was produced is a claim about a calculation.**
Extends the existing invariant from figures to methods, states the general rule (any
section whose subject is provenance is rendered rather than written), and records the trade:
less fluent prose, in exchange for prose that cannot describe work that did not happen.

---

## Task P6 — The four sections that did not generate

**Severity: medium.** Begins by re-measuring, because three of the four are probably
downstream of P1.
**Status: done, in its in-repo half** — the three likely-P1-downstream failures are
re-measured on the P11 rerun as this task specifies, and nothing was tuned for them. The
balance-sheet ceiling got neither a blind raise nor an offline guess at a schema split,
because the measurement this task asks for cannot be made without a live call — so the
system now measures itself: a reply that stops at the writer's 16,384 ceiling
(`stop_reason: max_tokens`, already distinguished by the provider) raises that one
instance's ceiling to `TRUNCATION_RETRY_CEILING` (32,768) for the retry, instead of
paying for the identical truncation twice as the live run did. The window and budget
guards price the raised figure too (`Agent.output_ceiling`), the standing ceiling stays
where it binds for everything else, and a retry that still truncates fails with the cause
on the record. The observability landed as `classify_refusals` in
`sections/evidence.py`: every attempt's refusals counted by cause — truncation, length,
gaps, numeral, citation, policy, method, schema — accumulated across retries (recovered
sections keep their first attempt's causes) and written to the draft step's
`builtin_sections`/`custom_sections` outcomes as `refusal_causes`.
`tests/test_refusal_causes.py` builds each cause through its real producer, so a reworded
refusal breaks the classifier's test rather than silently landing in the fallback bucket.

| Section | Failure | Expected after P1 |
|---|---|---|
| Balance Sheet & Liquidity | Output truncated mid-reply, twice | Unchanged — this is a token ceiling |
| Management & Governance | Cited extractions belonging to other source documents | Likely resolved |
| Capital Allocation | Three numeric claims with no citation | Likely resolved |
| Catalysts | Three claims naming zero figures | Likely resolved |

**The balance-sheet failure is the only one that is certainly independent.** The model ran
out of room producing valid JSON against a large schema; `report_writer` carries a 16,384
ceiling shared between thinking and visible output. Measure the actual output length for
that section, then either raise the ceiling for the role or split the schema. Raising it
blindly is not the fix — a ceiling that never binds is not a ceiling.

The other three are re-measured on the P1 rerun before anything is tuned. If they persist,
they are genuine section-policy problems and get their own diagnosis.

**Observability, added either way:** a per-section counter of refusal *causes* (length,
numeral, citation, schema, truncation) written to the run record. This run's causes had to
be reconstructed by reading a 3,000-line log, and the same question will be asked after
every future run.

---

## Task P7 — Word budgets recalibrated

**Severity: medium.**
**Status: done** — migration 0045 (the plan said 0044; P5 took that number). One
statement over every built-in row carrying a budget rather than a key list, so both
versions of `valuation_dcf` and any later-seeded section scale identically:
250→363, 300→435, 350→508, 400→580, 500→725. The downgrade divides back and was
verified to restore the exact seeded values. Nothing else changed — the 1.25 ceiling
factor and the prompt's `target_words` wording stand — and the experiment's reading
happens on the P11 rerun: overruns vanished means the budgets were wrong; overruns
rescaled to ~1.46x the new numbers means the prompt does not bind and the limit
becomes a hard constraint.

Seven sections were refused for length at ratios 1.38, 1.42, 1.43, 1.44, 1.46, 1.46 and
1.63 — mean 1.46× against a ceiling factor of 1.25. A model missing a target by a
consistent multiplier is not being careless; it is working to a different scale than the
one it was given.

Two candidate causes, and they are distinguishable by one experiment:

1. The budgets on `section_definition.word_budget` are below what these schemas can be
   written in, or
2. `target_words` in the prompt does not bind — it is stated once, among many constraints,
   and treated as advisory.

**The experiment:** multiply the built-in `word_budget` values by 1.45 (migration 0044) and
change nothing else. If the overruns vanish, the budgets were wrong. If they rescale — the
model again writing 1.46× the new number — the prompt is wrong, and the fix is to state the
limit as a hard constraint the schema itself carries. One run answers it, and the answer
should not be guessed at.

Run this **after** P1, so the sections are writing from an evidence pack that is about the
subject.

---

## Task P8 — Cost estimates recalibrated

**Severity: medium.**
**Status: done** — the seven constants in `vertical_slice_v1.py` now carry the table's
new figures with each measurement recorded beside it (total £6.94), and
`test_the_shipped_configuration_can_run_the_shipped_workflow` parses `.env.example` and
asserts the estimate sum fits the shipped per-run budget (mutation-verified: an £11
draft estimate fails it). One item was already closed by an earlier fix: `.env.example`
ships `AER_PER_RUN_BUDGET_GBP=12.00`, not the 2.50 this plan was written against, so no
raise to 10.00 was needed — the test is what keeps that true.

| Step | Estimate | Actual | New estimate |
|---|---|---|---|
| `plan` | £0.15 | £0.171 | £0.20 |
| `propose_peers` | £0.05 | £0.014 | £0.02 |
| `research_*` (each) | £0.10 | £0.083–0.241 | £0.25 |
| `propose_assumptions` | £0.20 | £0.065 | £0.10 |
| `draft` | £6.00 | £4.84 | £5.00 |
| `validate` | £0.05 | £0.00 | £0.02 |
| `red_team` | £1.00 | £0.251 | £0.35 |
| **total** | **£7.95** | **£6.26** | **£6.94** |

The total was close; the steps were not. The guard checks each step's projection before
running it, so an estimate 2.4× low is 2.4× less protection at that step. Estimates should
sit at or slightly above observed cost — the guard's job is to stop a run before a large
spend, and an estimate below the truth defeats it.

Two further changes:

- `.env.example` ships `AER_PER_RUN_BUDGET_GBP=2.50`, and request validation refuses a
  `max_cost_gbp` above the configured budget. **Anyone setting up from the example cannot
  request the workflow this repository is built around.** Raise it to `10.00`.
- A test asserting `sum(estimated_cost_gbp) <= the example's per-run budget`, so the shipped
  configuration can always run the shipped workflow. This is the kind of thing that only
  breaks for a new user, who is the least equipped to diagnose it.

---

## Task P9 — Presentation and observability papercuts

**Severity: low**, all of them, and all cheap. Grouped because they share a commit and none
justifies its own.
**Status: done**, all six. (1) A clamped score renders as "unbounded (clamped at 1e12)" —
`NUMERIC_CEILING` went public and the validation-row builder recognises it. (2) The
validation summary names the CI-only metrics, derived as `BLOCKING − RUN_TIME` so a
metric that moves between the gate and the runtime moves in the sentence without an
edit. (3) `Finding` gained `informational`; on a document carrying `<ix:` fact tags the
scanner marks `hidden_text`/`invisible_styling` informational — still stored for the
reviewer, but `record_findings` flags and warns only on full-weight findings, and the
detection cannot be bought by naming a content type because it reads the markup itself.
An instruction phrase inside an iXBRL filing keeps full weight. (4) `segment` joined the
dimensionless aliases. (5) Migration 0046 renames the gate to `UNMAPPED_CONCEPTS` —
`ALTER TYPE ... RENAME VALUE` carries the historical `approvals` rows through the
catalog, and the migration also renames the historical step keys, idempotency keys and
recorded step outputs, verified both directions against seeded old-named rows. (6) The
no-subscription price reason now names every consequence: no beta, no market
capitalisation, no multiple for the subject or any peer — the assumptions gate's
per-name explanation had already landed in an earlier fix.

1. **The saturation sentinel reaches print.** `_NUMERIC_CEILING` (`evaluations.py:99`) is
   `999999999999.99999999`, and it appeared in the published PDF against a threshold of
   0.005. The clamp is correct — the column is bounded — but the *rendering* should say
   "unbounded (clamped at 1e12)". A reader meeting twelve nines in a validation table
   reasonably concludes the validator crashed.

2. **Name the metrics measured elsewhere.** The validation section says "measured 9
   metric(s)" and stops. It should name `injection_resistance`, `unit_integrity`,
   `custom_section_contract_conformance` and `skill_privilege_containment` and say they are
   corpus metrics measured by the CI evaluation gate. Four guarantees a reader cannot
   account for is worse than four they can see are covered elsewhere.

3. **Injection heuristics fire on every clean filing.** `hidden_text` and
   `invisible_styling` produced 103, 73 and 71 findings on ordinary iXBRL documents,
   because hidden facts are how inline XBRL works. ADR 0019 already holds that containment
   is the control and detection is not the defence, so downgrading these two signals to
   informational for documents parsed as iXBRL costs nothing the security argument depends
   on — and warnings that fire on every clean run are warnings nobody reads on the day one
   matters. The signals stay at full weight for every other document type.

4. **`analysis.unit_unparsed` warns on the unit "segment"** for `NumberOfOperatingSegments`
   and `NumberOfReportableSegments`, six times a run. Add `segment` to the unit vocabulary
   as a dimensionless count.

5. **Rename the `UK_FINANCIALS` gate.** It fired for a US filer because 219 unmapped tags
   came out of the segment sweep; it has nothing to do with the United Kingdom. It is an
   unmapped-concepts gate and should be `UNMAPPED_CONCEPTS`. Native Postgres enum, so this
   is a migration plus 15 files; the historical `approvals` rows migrate with it. Worth
   doing now while there is one run's history to carry.

6. **`acquire_prices` reported success in 96 ms having fetched nothing.** The step is
   correctly conditional on the subscription, but "no price provider is configured, so
   beta, market capitalisation and every multiple are unavailable this run" belongs in the
   run record where the operator will see it — not inferred from a suspiciously fast step.
   The run then asked the operator to type a beta, which they did, with no statement
   connecting the two.

---

## Task P10 — Section drafting fans out

**Severity: low**, highest risk in the phase, and therefore last.

Drafting took 42 minutes of the run's 63, writing sixteen sections one after another. The
research workers already fan out from a shared prerequisite and the engine supports it;
sections have the same shape — each depends on the evidence pack and on nothing another
section produces.

The care needed is in what the concurrency touches, not in the fan-out itself:

- The budget guard reads `spend_so_far` from the `costs` table before each step. Under a
  fan-out, several sections check against the same total before any of them writes. The
  research fan-out already has this property, so the behaviour is established rather than
  new — but a phase that exists to make the cap trustworthy should not casually widen the
  window, so pick a bounded concurrency (4 is the obvious first choice) rather than sixteen
  at once.
- Each concurrent section needs its own session; sharing one is a defect waiting for a
  slow day.
- Ordering must stay deterministic in the stored output regardless of completion order —
  `report_sections` is already keyed by section, so this is an assertion rather than a
  change.

Expected: 42 minutes to roughly 12, and the drafting cost unchanged.

---

**Status: done** — `50329ec`. Section drafting fans out under a bounded semaphore, each section on its own session from the factory, which is the same shape task 34 gave the workflow engine. The serial path is unchanged where no factory is supplied, so every fixture-session test still exercises it.

## Task P11 — Re-run AMZN and diff

The phase's acceptance test. The same request, re-run against the fixed code, compared
against the artefacts in this file.

**Expect `facts.persisted` to report `inserted: 0` for Amazon**, exactly as this run
reported it for MSFT. That is the observation-key dedupe working, not a failed
acquisition — the facts are already stored from this run. It is also why the row below
measures the evidence pack rather than the insert count, and why Task P1 has to land
first: under request scoping, that dedupe is what would empty the pack.

**Must be true:**

| Check | This run | Required |
|---|---|---|
| Sources list | Six other issuers | Amazon only |
| Front-page revenue history | $105–159bn | Amazon's actual $514–638bn scale |
| At-a-glance issuers | Three | One |
| Ratio set label | FY2022 carrying FY2021 values | Correct year |
| `numerical_consistency` | fail | pass |
| `presentation_integrity` | fail | pass |
| Peer documents fetched | 8 (26 MB) | 0 |
| Facts in the evidence pack | mixed issuers | subject only, and non-empty |
| Sections generated | 12 of 16 | 15 of 16 or better |
| Citations verified | 86 / 86 | unchanged, all passing |
| Spend | £6.26 | within 15% |

**Must still be true** — the defences that worked, which no fix may quietly remove: the red
team still runs from a separate context and still records challenges; the approval page
still shows failed metrics, escalations and missing sections before approval; both salvages
still repair rather than discard; every model call still writes a cost row.

If the rerun is clean, this file's findings are closed and the artefacts are archived
beside `docs/manual-acceptance.md` as the platform's first end-to-end baseline.

**The diff half of this task is a command** (added 2026-08-20): `uv run aer acceptance
<job-id>` reads the finished run's rows and prints every mechanical check in this table
beside its requirement — sections, citations, the evaluation gate's verdicts, the issuers
the report actually cites, the front page, the spend — exiting non-zero on a failure. Two
of the rows moved under it since this table was written, deliberately: "peer documents
fetched: 0" predates ADR 0059 reinstating peer acquisition, so the check holds the
current invariant instead (the report's evidence chain reaches only the subject's
documents); and the spend band predates the P7/P8 recalibration, so spend is reported for
comparison rather than judged. What remains for the eye is what no query can see: whether
the prose reads like research.

---

**Status: the command is done (`c8f2f59`); the run itself is the operator's.** `aer acceptance` reads a finished job and prints the readout this task specifies, so the diff is a command rather than a manual comparison — but the AMZN re-run it compares against has to be made on a machine with outbound HTTPS and a funded key, which this environment is not.

## ADRs this phase adds

| ADR | Subject |
|---|---|
| 0061 | Evidence is scoped to the subject, not to the request |
| 0062 | A fiscal year is a property of the period, not of the filing |
| 0063 | A claim about how a number was produced is a claim about a calculation |
| 0059 (amended) | Peer acquisition withdrawn until a price feed makes a multiple computable |

## Migrations this phase adds

| Migration | Change |
|---|---|
| 0042 | `source_document.company_id` and `research_request.company_id`, nullable, backfilled |
| 0043 | Recompute `financial_fact.fiscal_year` from `period_end` |
| 0044 | Built-in `section_definition.word_budget` × 1.45 (Task P7 experiment) |
| 0045 | `GateKind.UK_FINANCIALS` → `UNMAPPED_CONCEPTS`, with `approvals` history |

## Verification

The full gate, unchanged, at every task boundary:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest --ignore=tests/e2e        # one process, one database
uv run pytest tests/e2e                 # separate process: Playwright's sync API
```

Both pytest processes, both green, on a database no other process is using — the rule
`CLAUDE.md` records after a red gate that turned out to be two runs sharing `aer_test`.

Task P1's invariant test and Task P3's golden tests are mutation-verified: remove the
predicate, remove the year rule, confirm each suite fails.

## Non-goals

- **No new data provider, key or licence determination.** The EODHD subscription remains the
  gate on comps and is out of scope; Task 25 (GBP risk-free rate) remains open under
  ADR 0026.
- **No new agent role**, no change to the router's vocabulary, no change to the section
  spine.
- **No prompt rewriting before P1 lands.** Three section failures and one failed metric are
  plausibly downstream of the contamination; tuning against them now would be tuning
  against noise, and would be undone by the fix.
- **No change to the gate sequence** beyond the `UK_FINANCIALS` rename.
- **A bare year is still a figure** under ADR 0054, unchanged.

## Risks

**Other consumers reach facts by other routes.** Eight modules filter on
`SourceDocument.request_id`. Three of the eight are now understood: `evidence`, `glance`
and `research.search_sources` are wrong and are fixed by P1; `research.search_facts` is
already correct and is the pattern. The remaining four — `evaluations`, `red_team`,
`escalation`, `sources` — plausibly *want* everything the run touched, since they report on
the run rather than about the company. Task P1 states the answer for each in the ADR rather
than leaving the next reader to re-derive it, because that re-derivation is precisely what
did not happen last time.

**Migration 0043 rewrites stored data.** It is the first migration in this repository to
correct values rather than change shape. It must be idempotent, and the run's existing rows
are the test fixture.

**Task P10 is the one that can make things worse.** Everything else either removes wrong
behaviour or adds a check. If the concurrency work looks unclear when it is reached, defer
it — a 42-minute drafting step is a cost, not a defect, and this phase has no other item
whose failure mode is a new class of bug.
