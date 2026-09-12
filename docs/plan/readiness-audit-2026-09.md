# The readiness audit — is the research tool ready for use?

*An assurance and audit pass over the equity research tool alone — the `vertical_slice_v1`
workflow and everything it reaches — run from a cloud session on branch
`claude/cool-noether-fj0y7b` against commit `82616f1`, starting 2026-09-11. The question is
the operator's: **reliable, accurate, budget friendly, complete**, held against the same
research done in the Claude console with an optimised set-up. This document is the record:
what was measured, what was found, what was fixed, and what only the operator can decide.*

*Working draft. Sections marked ⏳ are still being measured; every number here is read from
`audit/out/` or the repository, never remembered.*

---

## 1. The verdict

⏳ Written last, from the scorecard below. Two halves: Part A (technical, per criterion
against its threshold) and Part B (practical, the suitability matrix).

## 2. Method

**Two assessments.** Part A measures the four criteria mechanically from what the platform
records — the `costs` table, the step rows, the evaluations, the export — and from an
independent recomputation of the subject's filed figures. Part B walks real use cases with
real personas and asks whether the tool fits how research is commissioned, read and acted on.

**The baseline** is the same brief given to Claude Opus 5 at high effort with the vendor's
server-side web search tool through the API — a repeatable proxy for the console whose cost
is a number, not the console itself.

**The harness** is `audit/` — a package beside the product that drives runs through the
platform's own services (the same gate payloads, the same hashes, the same
`record_decision` the web route calls), clears every gate by a policy written down before
any run (`audit/driver/policy.py`), supplies the operator's stated assumptions with their
sources (`audit/subjects.py`), and stops for the operator on anything it will not decide.
`audit/smoke.py` proved it end to end on the suite's fake scene before a penny moved.

**Subjects:** Microsoft (MSFT, NASDAQ) twice — the second the next UTC day as the refresh
case; AstraZeneca (AZN, on the NYSE per EDGAR's own ticker file, a 20-F under IFRS); M&T
Bank (MTB, NYSE, the residual-income path). Each request capped at £10 with one automatic
raise to £12; the audit's own ledger holds the £100 ceiling across platform and baseline.

**Deviations from the plan, and why.** The four platform runs and three baselines were made
(the plan's fourth baseline, a second MSFT console run, was dropped when the first three
cost £7.6, £11.0 and £6.7 against the £3 each the ledger had assumed; the platform's
run-to-run variance is the reliability check, the console's is not). The adversarial
verification of the code reading — three refuters per finding — was launched twice and lost
every refuter to the account's session limit both times; the finders' 89 findings (six
blocking, 47 major, 36 minor) were instead verified by hand in the main loop against the
code and the live runs, and §5 says for each what was read and what was seen. Four of the
thirteen subsystem readers never completed (render, observability, the GUI, the documents);
the live findings F-08, F-10, F-11 and F-02 are what the audit has for those areas, and §9
says so. The container was stopped four times by the same limit; each stop killed the
worker under whatever run was in flight, which is how F-08 was met three times and its fix
proved once (§5). The audit's own defects are in §6.

**The accuracy matcher.** `audit/scoring/` extracts every numeral in a report or a console
answer with the platform's own numeral scanner, attributes a concept and a period from the
table row or the words beside it, and classifies it against the filing (companyfacts, read
from the run's archived artefact, with the latest-filed annual observation per fiscal year
and a small set of derived margins). It was calibrated on the platform's reports, whose
figures are code-produced: every "contradiction" it raised there was a misread (a ratio
beside the word "revenue", a table cell under the wrong column, an operand read as a
result, a quarter-end column read as a year end), and each was closed by a rule that
applies to both sides. It sets aside what it cannot attribute — qualified measures
(constant currency, "core", a segment, a quarter), deltas, hypotheticals, ranges, arithmetic
— and the counts it reports are of what it could check. It shares one blind spot with the
platform's own reading ladder: a figure stated at the wrong scale word reads as the right
number (F-21).

## 3. Part A — the technical assessment ⏳

### 3.1 Reliable

| Measure | MSFT #1 | MSFT #2 | AZN | MTB |
|---|---|---|---|---|
| Reached an approved, rendered report | ⏳ | | | |
| Steps failed / non-gate retries | ⏳ | | | |
| Sections lost / degraded | ⏳ | | | |
| Resumes needed | ⏳ | | | |
| Replay, artefacts, audit chain all hold | ⏳ | | | |

### 3.2 Accurate

⏳ `cited_figure_agreement`, `figure_plausibility`, citations verified, the independent
basket against the platform's facts and calculations, the baseline's numerals against the
same basket.

### 3.3 Budget friendly

⏳ Cost per run against £10; by step, role and category; estimate against actual; cache hit
rate; the baseline's priced usage.

### 3.4 Complete

⏳ Sections of 18; headline figures; unmapped concepts; peers priced; the checklist against
the baseline.

## 4. Part B — the practical assessment ⏳

The suitability matrix, the journeys as walked, the practitioner reads.

## 5. Findings

Numbered in the order found. Each carries what it costs the operator, how it was
established, and its state. *Severity* is the acceptance pass's rule: **blocking** puts a
wrong number in front of somebody, loses money uncapped or leaves a run unrecoverable;
**major** loses a section or a figure, or misleads a spending decision; **minor** otherwise.

### F-01 — CI has been red on every run since 2026-09-09, and the acceptance pass called the static gates clean

*Reliability of the record. Minor as a defect, major as a signal.*

`ruff format --check .` fails on the Python snippets quoted in
`docs/plan/acceptance-2026-09-08.md` (the formatter reformats fenced Python in Markdown).
The "Lint and types" job has failed on every CI run since, including the merge commit at
HEAD (`82616f1`, run 432); the test and browser jobs pass. The acceptance pass's row 5 —
"static gates clean" — was recorded as `pass` against a commit whose CI said otherwise.
`ruff check`, mypy (410 files, not the 353 the runbook pins), both suite processes (6,865
and 183) are green here.

*State: to fix — format the snippets and pin the runbook's counts to what is true.*

### F-02 — The documentation and the code disagree about the evaluation gate

*Reliability of the record. Minor.*

"Eight blocking metrics" (justfile, CI, testing docs, the scorecard) against ten in
`aer.eval.metrics.BLOCKING`; "thirty golden calculations" against 39; "recorded cassettes"
against `respx` over constructed fixtures and no cassette anywhere; "eight rows per run"
against eleven `RUN_TIME` metrics; 353 mypy files against 410.

*State: to fix in the documents.*

### F-03 — The suite's answer depends on file order, and more widely than recorded

*Reliability of the evidence the platform offers about itself. Major.*

The recorded pair reproduces exactly: `tests/test_gates.py` then
`tests/test_every_page_renders.py` errors with "No research request"; the reverse order
passes. `just test-shuffled 20260811` — the seed the justfile names as having found an
ordering defect before — fails at HEAD: **28 failed, 7 errors, 6,830 passed**, across
`test_db_schema.py`, `test_decisions.py` and `test_thesis_monitor.py`'s page tests, not
only the recorded pair.

*Fix: the engine fixtures empty the database on the way in (`tests/db_fixtures.py`,
`tests/api_fixtures.py`, through the existing `tests/db_cleanup.py`), at setup rather than
teardown, so what an earlier test committed is never what the next test's application
reads. The recorded pair passes both ways, the six affected modules pass in both orders, and
the second shuffled seed (20260909) ran 6,881 tests with two failures — both of which turned
out to be the audit's own doing: `inspect.getsource` pins in `test_section_spine` and
`test_planner_salvage` read the workflow file from disk after a commit made during the run
had moved the pinned functions by two lines (§6). The seed is re-run on the final commit.*

### F-04 — A UK company with no SEC filings cannot be researched, and the refusal can name the wrong company

*Completeness and accuracy. Major.*

`acquire` resolves every subject against EDGAR's ticker file and nothing else
(`vertical_slice_v1.py:2226` → `sources/sec/client.py:214`). The Companies House client
exists, is credential-wired in `runtime.py`, and is called by no step. The product
documentation says "UK or US" and lists Companies House and UK inline XBRL among the sources
a run fetches.

Driven offline through the real EDGAR client, a request for **Tesco PLC, TSCO, LSE** fails
at `acquire` with: *"TSCO is in the SEC's ticker file, but on NASDAQ rather than LSE. Check
the exchange on the request."* — TSCO on NASDAQ is Tractor Supply Company. An operator who
"checks the exchange" as told would commission research on a different company.

*State: established (`audit/smoke.py`). The UK path is an ADR-shaped addition for the
operator to decide; the misleading message is a defect to fix.*

### F-05 — Four roles ask for low effort on a model that has no effort parameter

*Budget. Minor.*

`source_triage`, `web_search`, `verdict` and `challenge_brief` route `claude-haiku-4-5` at
`effort="low"`; `_MODELS_ACCEPTING_EFFORT` (`providers/anthropic.py:82-93`) excludes that
model, so the parameter is silently dropped and the call runs at the API default.
`agent_runs` records both `effort` and `effort_applied`, so the divergence is auditable.

*State: to decide — retire the setting on those routes, or route them to a model that
honours it.*

### F-06 — Non-model acquisition spend is off the ledger

*Budget completeness. Minor.*

`CostCategory.DATA_API` is defined and never written. SEC, EODHD, FRED and ECB fetches
produce no `costs` row; EODHD is bounded separately by a weighted-call allowance in Redis.
The `cost.py` docstring describes a row nothing creates.

*State: to document, or to meter.*

### F-07 — The audit chain is application-writable

*Integrity. Minor.*

Migration 0001 notes that UPDATE and DELETE were never revoked on `audit_events` for the
application role, and `verify_audit_chain` documents that a full rewrite with recomputed
hashes is undetectable.

*State: to record; to close if cheap.*

### F-08 — A run whose worker dies mid-step is left RUNNING with no exit in the product

*Reliability and practicality. Major. Observed live.*

On MSFT #1 the audit's own driver stopped the worker while `extract` was running (the
driver's defect, recorded in §6). The job stayed `RUNNING` with the step row `RUNNING` at
attempt 0. Restarting the worker did not pick it up (arq does not retry, by design:
`max_tries=1`). `resume_run` refuses a `RUNNING` job — "It is running now; there is nothing
to continue." — so the console's *Continue* does nothing for it, and `aer resume` refuses
the same way. The only way on was to re-enqueue the job id directly, which the driver did
and which no surface offers an operator. The engine then re-ran the step as attempt 1 and
continued correctly.

*Fix: `services/resume.py` now decides whether a RUNNING run is *stranded* from the
worker's own health record — no worker has reported within the interval, or a worker
reports nothing in flight while the run's step has been recorded as running for longer
than the interval — and `resume_run(..., stranded=True)` continues it as itself, the audit
event saying so. The console offers *Continue* with the evidence (`id="stranded-run"`),
the resume form and `aer resume` attest it, and a run a worker is executing is still
refused with what was observed. `tests/test_stranded_run.py` (13 tests) holds it to that.
The residual: a worker that is alive with one other job in flight cannot be told apart
from one executing this run, so that case still refuses, with the reason.*

### F-09 — What the code reading found, verified by hand

Nine of the thirteen subsystem readers completed (acquisition, budget, calculations,
citations, engine, gates, provider, retries, sections) and reported 89 findings: six
blocking, 47 major, 36 minor. The planned three refuters per finding were lost to the
session limit twice, so each blocking and major finding was verified in the main loop by
reading the cited code and, where a live run could show it, the run. The outcome:

- **Six blocking, all confirmed.** Four are fixed with tests (F-15 the gate funnel, F-18
  the render step, F-19 the growth rate, and the fact that a failed step publishes its
  flushed rows — closed at the render step, the one place it stranded a run); two are the
  operator's to decide (F-16 the stale approval; a filer with lone long-term debt valued as
  debt-free, F-21).
- **Of the 47 major: 24 confirmed** (13 fixed with tests, F-12 to F-20; eleven put to the
  operator in F-21), **one partly** (a raised cap reaches the next node rather than the next
  call — fixed anyway), **none refuted**, and **22 not read** — plausible claims about the
  retry ladders, the evidence packs, the quarterly point-in-time key and the tie-break
  between a parent-attributable and a consolidated profit line. They are listed with their
  file and line in the results folder (`code-audit-findings.json`) as work the next pass
  should take up, not as defects the platform has.
- **The 36 minor findings were not verified** and are carried the same way.

### F-10 — The financials gate page raised on the first real filing

*Reliability and practicality. Major. Observed live; fixed.*

MSFT #1 stopped at the unmapped-concepts gate, as a real 10-K should, and the page that
exists to clear it (`/runs/{job_id}/financials`) answered 500. The template built the
"earlier periods" disclosure label as `line.observations - 1 ~ " earlier period"`, and
Jinja binds `~` tighter than `-`, so the expression was an integer minus a string. Every
concept a real filing reports for more than one year takes that branch; the suite's
unmapped fixture (`companyfacts_unmapped.json`) reports each concept once, so no test had
ever rendered it, and the ordinary MSFT fixture maps every tag, so the page 404s on it by
design. An operator using the product as documented cannot clear the gate: the API route
still answers, and the audit's driver cleared it through the service, which is why the run
went on at all.

*Fix: parenthesised in `runs/financials.html`; regression test
`test_a_concept_with_earlier_periods_still_renders` drives a run whose filing carries two
periods of revenue and renders the page (fails on the old template with the exact
`TypeError`, passes on the new one).*

### F-11 — The front page calls a forecast "Free cash flow"

*Accuracy and practicality. Major. Observed on MSFT #1; fixed.*

The report's headline table reads `Free cash flow | — | $139,225m`. That figure is the
discounted-cash-flow model's **final explicit forecast year** of free cash flow to the firm
(`aer.calc.dcf:free_cash_flow`, NOPAT + depreciation − capex − working-capital change; the
five base-case years run $43.1bn, $94.6bn, $107.6bn, $122.4bn, $139.2bn). Microsoft's
filed free cash flow for FY2026 is $66,987m (operating cash flow $182,935m less additions
to property and equipment $115,948m), which the report's own cash-flow section states
correctly two pages later. The figure is traceable, footnoted and arithmetically right;
it is the label and the missing period that mislead: a reader takes the front page as
the trailing year and sees a company generating twice the cash it does.

The cause is a name collision. The front page (`render/glance.py`, `_HEADLINE_CALCULATIONS`)
shows "the latest run of each" named calculation, and the only calculations named
`free_cash_flow` are the DCF's forecast rows — the platform records no historical free
cash flow at all (68 calculation names on this run; `cash_conversion` and
`distributions_to_operating_cash_flow` are the nearest). The baseline note, for what it is
worth, states the filed figure correctly.

*Fix: the ratio suite now records `free_cash_flow` for every reported year (operating
cash flow − capital expenditure, with the sign convention `core/concepts.py` documents),
the DCF's projections are recorded as `forecast_free_cash_flow` so no surface can mistake
one for the other, and the front page shows the reported year with its period. Because
the calculation registry keys on the name, replay now resolves a stored row by its
`function_ref` first, so runs recorded before the rename (MSFT #1 among them) still
re-run as themselves. Tests: `test_ratios` (a hand-worked answer), `test_report_document`
(the front page with both rows present), `test_eval_replay` (an old row re-runs by its
reference; a row whose reference and name are both gone still fails).*

### F-12 — The IFRS alias table named elements the taxonomy does not have, so a 20-F filer got no valuation

*Completeness and accuracy. Blocking for the UK-plc-through-its-20-F use case. Observed live on AZN; fixed.*

Seven keys in `core/concepts.py`'s `IFRS_ALIASES` were spelt the way us-gaap would spell
them — `AdjustmentsForShareBasedPayments`, `PurchaseOfPropertyPlantAndEquipment`,
`IncomeTaxesPaidClassifiedAsOperatingActivities`, the weighted share counts — and matched
nothing. AstraZeneca's companyfacts carry the real elements
(`PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`,
`AdjustmentsForSharebasedPayments`, `WeightedAverageShares`, `AdjustedWeightedAverageShares`,
`LongtermBorrowings`, `CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings`), all of
which sat in the run's 322 unmapped tags. The run therefore had no capital expenditure, no
share count and neither half of debt: the assumptions gate asked the operator for capex
intensity and the tax rate, and the valuation step recorded "the valuation needs a share
count for the year ending 31 December 2025 and the filings carry none". The report's
valuation section is a coverage notice. The rest of the report — 18 sections, 34 of 34
citations verified, 0 contradicted figures — is sound; the number an investor wants most is
absent for a reason that was a spelling.

*Fix: the aliases now name the elements the taxonomy has (`tests/test_ifrs_aliases.py`
holds them against the tags AstraZeneca files). Unproven live until AZN is run again on the
fixed code; that run is the audit's fifth if the ledger allows.*

### F-13 — Every Haiku call was metered at Opus rates

*Budget. Major. Observed live on MSFT #1; fixed.*

The API echoes a model's dated snapshot id (`claude-haiku-4-5-20251001`) where the route
named the alias; the price table held only aliases and the fallback for an unknown model is
the dearest one. MSFT #1's `costs` rows for Haiku sit at $5 and $25 per million tokens
(£0.28 charged where Haiku's $1 and $5 make about £0.06). Small in pounds, fivefold in
rate, and it is the number both caps read. The same fallback priced a route to the tier
above Opus at half its bill. *Fix: the lookup strips the snapshot suffix; the Fable tier is
listed so the unknown-model fallback overstates, which is the documented safe error.*

### F-14 — A reply the adversary paid for and the platform could not read failed the run

*Reliability. Major. Observed live on AZN; fixed.*

The red team returned nine challenges against a schema ceiling of eight. The provider
refused the reply as unusable (a bound the API does not enforce), the refusal escaped the
step's one-retry loop, and the run stopped FAILED at `red_team` with £0.34 spent on it.
The audit's driver continued the run the way the console's *Continue* does and the second
attempt succeeded; an operator would have seen a failed run for a reply that only needed
asking again. *Fix: an unusable reply is retried once with the problem stated
(`tests/test_red_team.py::TestAnUnusableReplyIsRetriedOnce`).*

### F-15 — Adding a peer or a theme on the review page made the gate impassable

*Reliability and practicality. Blocking for the operator who uses the control. Confirmed in code; fixed.*

The review pages hash the whole set — the step's proposal plus the operator's additions —
and so does the service that confirms it; the engine's gate and the `gate_payload` entry
point hashed the step's frozen proposal alone. An approval taken from the page after an
addition was recorded against content the run never verified against, the run stayed at
the gate, and a second decision is refused (F-16). The first acceptance pass asked for the
"add a comparable" control; nobody could have passed the gate after using it. *Fix: one
funnel — the gate verifies against the services' payload and the entry point returns it
(`tests/test_comps_service.py::…the_gate_hashes_the_set_the_page_shows…`).*

### F-16 — A stale approval is a dead end

*Practicality. Major. Confirmed in code; a decision for the operator.*

When an approval's hash matches neither the seal nor the live page, the run pauses saying
"open the review page again and decide on what it shows now" — and a second decision at a
decided gate is refused ("an approval is a decision, not a state to be re-asserted"), the
page renders no form, and `aer reseal` acts on the final gate only. Amending an assumption
after approving the assumptions gate is the ordinary way in. *Not fixed: allowing a
superseding decision when the recorded one no longer matches the live payload changes the
approvals model (ADR 0090's vocabulary) and needs an ADR; the alternative is to refuse
amendments after approval. §8 puts the choice.*

### F-17 — A wave failure was hidden behind a later pause

*Reliability. Major. Confirmed in code; fixed.*

After a node failed in a parallel wave, an independent sibling could run on to a gate and
pause; the serial path returned there without raising, leaving the job AWAITING_APPROVAL
with a FAILED row nobody was shown. *Fix: the failure is the run's state and the gate waits
behind it (`tests/test_workflow_dag.py::TestAWaveFailureIsNotHiddenByALaterPause`).*

### F-18 — A PDF renderer that raised once left a run that could not be finished

*Reliability. Blocking when it happens. Confirmed in code; fixed.*

The render step flushed the report row before rendering the PDF; a failed step's flushed
rows are committed with its FAILED status; `reports.job_id` is unique. Every resume then
failed on a second insert and a fresh run was refused because a report existed. *Fix: the
step reuses the run's row (`tests/test_run_api.py::TestTheRenderStepIsReEntrant`).*

### F-19 — The headline revenue growth rate could compound a year into a quarter

*Accuracy. Blocking when it happens. Confirmed in code and in the store; fixed.*

`_revenue_growth` took the earliest and latest consolidated revenue rows by period end with
no fiscal-period filter, and the store holds every quarter a 10-Q filed (MSFT: Q1–Q3
rows; AZN: Q2 rows). MSFT #1 and AZN escaped because their newest row was the annual
one; a September run on a June-quarter filer would have recorded a three-month figure as
the end of the series, sourced and footnoted. *Fix: the endpoints are chosen exactly as the
analysis chooses fiscal years (`tests/test_revenue_growth.py`).*

### F-20 — Fifteen further findings from the code reading, fixed with tests

*Confirmed against the code; none observed live.* Each has a regression test in the file
named.

- The JSON API recorded a decision against any 64-character hash; it now checks the hash
  against what the gate shows, as the web route does (`test_run_api.py`).
- `execute` ran a job whatever its status, so a job queued twice ran twice; it now declines
  a RUNNING job, and a stranded run is continued through the resume service (`test_stranded_run.py`).
- The per-call spend guard read the cap from a row the session had loaded at the start of a
  long node, so a cap raised mid-run was not seen until the node ended; it reads the column
  (`test_cap_raise.py` covers the engine's guard; the per-call guard's read is the same select).
- Sensitivity-grid cells were recorded as case "base", and the front page and the football
  field read the base band off the latest such row (`test_dcf.py`).
- A derived driver outside its plausibility band failed the assumptions step; it now goes to
  the gate outstanding, with the reason (`test_assumption_proposals.py`).
- The bank model range-checked neither the return on equity nor terminal growth
  (`net_income_from_roe(book=100, roe=12)` returned 1,200) (`test_residual_income.py`).
- Cancelling a run waiting at a gate recorded a request nothing would act on; it ends the run
  at once (`test_cancellation.py`).

### F-21 — Confirmed, not fixed: the decisions the operator has to take

Each is true in the code; each changes a documented decision or a modelling definition, so
it is put in §8 rather than changed here.

- **Sibling spend is invisible to the per-call guard** inside the research wave and the
  draft fan-out (five and four sessions each seeing only committed rows), so the per-run cap
  can be jointly overshot by up to (N−1) × one call's worst case; and **cost rows flushed by
  a step that dies mid-flight are lost**, consistent with AZN's `revise` attempt 0 (a
  minute of calls, no rows). Both fixes commit cost rows inside a step, which amends ADR
  0016's publication rule.
- **A filer with long-term debt and no short-term line is valued as debt-free** (an
  all-equity WACC and net debt of −cash), because `total_debt` derives only from both halves
  and the valuation substitutes a sourced zero. Refusing the valuation, or asking for the
  missing half at the assumptions gate, are both changes to what a run does for such a filer.
- **Scenarios keep the base WACC while reporting an override of beta or the risk-free rate as
  "argued about"**; the DCF's **working capital includes cash and short-term debt**; the
  bank model's **opening book value includes preferred stock and non-controlling
  interests**. Modelling definitions, each defensible, each unstated to the reader.
- **A fact-backed numeric claim is never checked against its fact's value**: the claim's
  statement lends cover to the content's numerals and `cited_figure_agreement` joins on
  calculations only. The live runs show no such error (0 contradicted of 169 and 165
  checkable figures), so the writer was faithful; the guard is absent. **The reading ladder
  discards the scale word**, so "$331.8 million" reads as 331.8 billion. Both are in
  `core/figures.py` and ADR 0060's territory.
- **`cited_figure_agreement` is measured at `validate`, before `revise` replaces a section's
  claims**; revised claims are never re-measured.
- **The citation override the gate message and the runbook promise exists on no surface**:
  `override_citation` has no caller in `src/`. An unverified citation at the final gate has
  no recovery.
- **News and web sources never reach a built-in section**: every built-in section's tier
  ceiling is T4 and MSFT #1's one admitted T5 document was cited nowhere. Use case 6 turns
  on this.

## 6. The harness's own defects, for honesty

The driver, not the platform, caused two stops on MSFT #1: a duplicated keyword in the
screenshot hook at the first gate, and a poll a moment after enqueueing that read the old
pause as a new one and stopped the worker mid-step. Both are fixed and committed; the second
is how F-08 was observed at all. Three more, found on the later runs:

- The screenshot capture waited for the network to fall idle, which the console's event
  stream never lets it do, so every console screenshot on MSFT #1 and AZN timed out. It
  waits for the document now; the gate pages were captured throughout.
- The kill-and-resume drill on MSFT #2 called the resume service the way the product did
  before F-08's fix, was refused, and — because `execute` now declines a job another
  execution holds — the driver's fallback re-enqueue did nothing and the driver stopped,
  honestly, with "the worker died and the run did not move". The run was continued through
  `aer resume` (which attested the stranding) and the driver was taught the same path. The
  drill therefore proved F-08's fix twice, once by accident.
- The audit committed to `src/` while the second shuffled suite was running, and two tests
  that pin a function's source by line number read the moved file (F-03). The suite's
  verdict on that seed is repeated on the final commit.
- The first M&T baseline was cut off by the container stopping mid-stream and re-run the
  next morning; whatever the vendor billed for the cut-off turn is not in the ledger's
  numbers and is noted there.

## 7. The spend ledger ⏳

## 8. What only the operator can decide ⏳

## 9. What this pass does not establish

- **Anything about Windows.** Every run here was on Linux; the operator's own pass found
  two of three defects that CI could not see by construction (§4.10 of the roadmap), and
  nothing here changes that.
- **A domestic UK filer, live.** The LSE refusal is proved offline on the fake scene (F-04);
  no money was spent to watch it happen.
- **The console itself.** The baseline is the same brief through the API with the same
  model, effort and search tool; the console adds a person's follow-up questions and
  subtracts the repeatability. Its minutes-to-answer here (16, 19, 14) are the API's.
- **The four subsystems nobody read** (render, observability, the GUI, the documents) beyond
  what the live runs showed of them.
- **Whether the fixes hold under a full suite in every order.** Both suite processes were
  run on the audited commit; two shuffled seeds were run, the first reproducing the
  recorded order dependence and the second, after the fix, leaving a residual pair
  (`test_section_spine`'s red-team refill and `test_planner_salvage`'s plan salvage) that
  pass alone and failed in that order once. Not root-caused.
- **The judges' reads as a measure.** Three model judges per document answered a fixed
  rubric blind; their agreement is reported and their disagreement listed. They are
  advisory. The operator's own reads are the column §4 leaves open.
- **The matcher's recall.** It judges what it can attribute and sets the rest aside; a wrong
  figure in a sentence it could not read is not counted. Its precision was calibrated, its
  recall was not measured.
- **Anything a second run of AZN on the fixed alias table would show.** The IFRS fix is
  proved against the archived tags, not by a run, unless the ledger allowed the fifth run
  (§7 says).
