# The readiness audit — is the research tool ready for use?

*An assurance and audit pass over the equity research tool alone — the `vertical_slice_v1`
workflow and everything it reaches — run from a cloud session on branch
`claude/cool-noether-fj0y7b` against commit `82616f1`, starting 2026-09-11. The question is
the operator's: **reliable, accurate, budget friendly, complete**, held against the same
research done in the Claude console with an optimised set-up. This document is the record:
what was measured, what was found, what was fixed, and what only the operator can decide.*

*Every number here is read from `audit/out/` or from the repository, never remembered.*

---

## 1. The verdict

**Not ready for general use; ready for one thing, and good at it.**

Four criteria, measured in §3:

| Criterion | Threshold | Measured | Verdict |
|---|---|---|---|
| **Accurate** | 0 figures contradicting the filing | **0 of 169, 0 of 188, 0 of 165** checkable numerals; every blocking metric 0 or 100 %; 160 of 160 citations verified across three runs | **yes** |
| **Budget friendly** | ≤ £10 per report | **£7.48, £6.80, £6.83** — average £7.04; no cap raised, no run breached | **yes** |
| **Reliable** | a report without rescue; nothing lost; the same numbers twice | 0 sections lost and 0 figures re-paid — but **two of four subjects never reached a report**, and 837 calculation rows reproduced except where a model-proposed assumption moved (value per share $485.29 against $512.50) | **not yet** |
| **Complete** | every section, every expected figure, parity with the console | **18 of 18 sections every time** — and **no peer multiple on any run**, no segment revenue, no guidance, and on one subject no valuation at all | **no** |

Two halves that disagree, which is why the pass has both. **Technically** the core is
sound: the arithmetic is deterministic and replayable, every figure traces to hashed bytes,
the money is metered and capped, and an independent recomputation of the filings found
nothing wrong in any report. **Practically** the documents do not yet do a reader's job:
three independent judges read each report and each console note blind, and **all six
comparisons chose the console**, on every dimension, because the platform's report states
no view, answers the brief's questions "partly" or not at all, and withholds the figures a
research note is read for.

So: **use it today to build and audit an evidence base on a US filer**, where nothing else
here can match a footnote that resolves to bytes and a calculation that re-executes. **Do
not use it yet** to reach a view, to research a bank or a domestic UK filer, to answer a
question under time pressure, or to hand somebody a document that stands on its own.

The pass found 23 findings and fixed 17 of them with regression tests, including two that
were blocking and live: the refresh run's gate refused itself over a leaked identifier
(F-22), and no run ever discovered what kind of business it was researching, so a bank took
the model that ADR 0029 exists to forbid (F-23). Fourteen decisions that are not mine to
take are in §8.

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

## 3. Part A — the technical assessment

Five live runs, three console baselines. MTB appears twice: once as the product stood
(`mtb-unclassified`, stopped for the operator at the assumptions gate), once on the fixed
classification (`mtb`). Every number below is read from `audit/out/`.

### 3.1 Reliable

| Measure | MSFT #1 | MSFT #2 | AZN | MTB (as found) |
|---|---|---|---|---|
| Reached an approved, rendered report | **yes** | **no** — gate 2 refused on `presentation_integrity` (F-22) | **yes** | **no** — stopped for the operator at the assumptions gate (F-23) |
| Steps failed | 0 | 0 | 1 (`red_team`, retried and passed — F-14) | 0 |
| Non-gate retries | 1 (`extract`, caused by the audit's own driver — §6) | 1 (`draft`, the kill drill) | 2 (`red_team`, `revise`) | 0 |
| Sections lost | **0** | 0 | 0 | — |
| Sections degraded | 0 | 1 (`earnings_quality`, shortened to its budget) | 2 (`valuation_dcf` withheld — F-12; `catalysts`) | — |
| Resumes needed | 0 | 1 (F-08's fix, live) | 1 (F-08's fix, live) | 0 |
| Acceptance / replay / artefacts / audit chain | all pass; 852 calculations, 69 citations, 14 artefacts re-derived | fails only on the refused check; 857 calculations, 57 citations re-derived | all pass; 152 calculations, 34 citations | — |

**Run-to-run, the measure that matters.** MSFT twice, one UTC day apart, same filings:
**837 calculation rows compared, 178 keys identical, 18 keys differing (527 rows)** — and
every differing key is downstream of exactly three inputs: the day's closing price
(market capitalisation, the multiple), the beta regression's extra day
(1.0646 → 1.0670, so WACC 9.3212 % → 9.3311 %), and **one model-proposed assumption the
operator confirmed — the exit multiple, 14× against 15×**. Facts chosen, rejected and
unmapped are identical to the row (18,610 / 14,061 / 496); so are all nine other derived
drivers. The front page moved with them: **value per share $485.29 against $512.50, 5.6 %
apart**, which is the exit multiple's 7.1 % net of the rest.

So the arithmetic is reproducible and the *report* is not, because one of its inputs is a
model's opinion confirmed at a gate. That is the design (ADR 0046), and it means "the same
subject twice gives the same numbers" holds for everything except the assumptions — where
it is the operator's confirmation, not the platform, that fixes the answer.

**Verdict on reliability: not yet.** Nothing was lost and nothing was paid for twice, but
two of four subjects did not reach a report — one refused by the platform's own
presentation check on every refresh run (F-22), one stopped because a bank was never
classified as one (F-23). Both are fixed here; neither fix has a completed live run behind
it beyond the re-measurement in §5.

### 3.2 Accurate

| Measure | Threshold | MSFT #1 | MSFT #2 | AZN |
|---|---|---|---|---|
| `cited_figure_agreement` | 0 | **0** | **0** | **0** |
| `figure_plausibility` | 0 | **0** | **0** | **0** |
| `numerical_consistency` | ≤ 0.5 % | **0** | **0** | **0** |
| `citation_accuracy` | ≥ 98 % | **100 %** (69 of 69 verified) | **100 %** (57 of 57) | **100 %** (34 of 34) |
| `hallucinated_citation_rate` | 0 | **0** | **0** | **0** |
| `temporal_compliance` | 100 % | **100 %** | **100 %** | **100 %** |
| The audit's own matcher, against the filing | 0 contradicted | **0 of 169** checkable numerals | **0 of 188** | **0 of 165** |
| Claims / of which numeric | — | 280 / 182 | 279 / 197 | 243 / 175 |

**`look_ahead_recall` is "not exercised" on every run**, because nothing published after the
as-of date was offered to a claim — the honest reading is that the check had no population,
not that it passed.

Held against the console: **0 contradicted of 154 checkable numerals** (MSFT) and **1 of
178** (AZN — capital expenditure stated including intangibles, where the filing's figure is
property alone). On stated figures the two are a draw, and both are good. The difference is
not accuracy: it is what each will state at all, which is §3.4 and §4.

**Verdict on accuracy: yes.** No figure in any run contradicted the filing it came from, by
the platform's measures or by an independent recomputation that does not trust them.

### 3.3 Budget friendly

| Measure | MSFT #1 | MSFT #2 | AZN | MTB (as found) |
|---|---|---|---|---|
| Run spend | **£7.48** | **£6.80** | **£6.83** | £2.03 (stopped early) |
| Against the £10 target | **inside** | **inside** | **inside** | inside |
| The guard's own estimate for the plan | £9.31 | £9.31 | £9.31 | £9.30 |
| Dearest steps | `draft` £3.71, `revise` £1.09 | `draft` £2.31, `revise` £1.34 | `draft` £2.83, `revise` £0.97 | — |
| Worst per-step estimate against actual | `research_industry` **1.78×** | `research_macro` **1.45×** | `research_industry` **1.91×** | — |
| Cache hit rate | 10.7 % | 10.3 % | 7.6 % | — |
| Paid replies the platform could not read | **1** (1,431 output tokens) | **3** (7,366) | **5** (18,706, including the red team's 9,919) | 0 |
| Web searches billed | £0.04 | £0.04 | £0.06 | — |
| Effort as requested | Opus high ×24, Sonnet medium ×31, **Haiku low ×2 — dropped at the API** (F-05) | ×23 / ×32 / ×2 | ×20 / ×31 / ×3 | — |

Three finished runs at **£7.48, £6.80 and £6.83** against a £10 target and a £12 hard cap:
no cap was raised, no run breached, and the monthly cap never bound. The console cost
**£7.63, £11.03 and £6.71** for the same three briefs, so on money the two are level and
the platform is the more predictable of them.

Two real leaks, both small and both metered: **every Haiku call was priced at Opus rates**
until F-13 was fixed (£0.28 charged on MSFT #1 for what Haiku's list price makes about
£0.06), and **schema-rejected replies are routine** — one to five per run, up to 18,706
output tokens paid for and discarded, which the run's own ledger carries but no surface
sums up.

**Verdict on budget: yes.** £7.04 a report on average, inside the operator's £10, with the
cap mechanism never needed and the arithmetic of the ledger checkable row by row.

### 3.4 Complete

| Measure | MSFT #1 | MSFT #2 | AZN |
|---|---|---|---|
| Sections generated | **18 / 18** | **18 / 18** | **18 / 18** |
| Report length | 13,993 words, 142 footnotes | 15,297 words | 12,548 words, 114 footnotes |
| Facts chosen / rejected | 18,610 / 14,061 | 18,610 / 14,061 | 4,181 / 5,217 |
| Unmapped tags at the gate | 496 (2 refused) | 496 | 322 |
| Market capitalisation | present | present | **withheld** |
| DCF value per share | present | present | **withheld** — no share count in the IFRS tags (F-12) |
| **EV/EBITDA, P/E** | **withheld** | **withheld** | **withheld** |
| Peers priced in the comps table | **0 of 8** | 0 of 8 | **0 of 8** |

**No run produced a single peer multiple.** The model proposes eight comparables, the
operator confirms them, the comps table is built — and every peer is excluded with the same
sentence: *"this research holds no filings and no price series for it, and a peer multiple
needs both."* Nothing acquires a peer's financials (ADR 0059, as amended), so on any
database that has not researched the peer itself the table is empty by construction, and
EV/EBITDA and P/E are withheld on every run. That is the largest completeness gap the pass
found, and it is not a defect in the sense of a bug: it is the consequence of a decision,
and it is in §8.

Against the console on the fixed checklist, scored blind by three judges per document
(§4): the platform is **ahead on one item of ten** — the risk factors the company itself
names, which the console omitted on both subjects — and **behind on five**: segment
revenue, management guidance, named competitors, a bear case with a number, and (on AZN) a
valuation with a per-share result.

**Verdict on completeness: no.** Every section is written and every figure it states is
traceable, but the figures a reader expects from a research note — segment revenue,
guidance, peer multiples, and on one subject any valuation at all — are absent, and the
absences are structural rather than incidental.

## 4. Part B — the practical assessment

### 4.1 What three judges made of the documents

Each report and each console note was read by three independent judges under a fixed
rubric — an investor deciding with their own money, a reader handed only the file, and a
sceptic checking the work — blind to which tool produced which document, and then the pair
was compared blind. Twelve reads, six comparisons; the raw rubrics are in
`audit/out/judges/reads.json`.

| Question | Platform (2 documents, 6 judges) | Console (2 documents, 6 judges) |
|---|---|---|
| Would you act on it? | **no — 6 of 6** | **with reservations — 6 of 6** |
| Is an investment view stated? | no (5 of 6) | yes (6 of 6) |
| Is the view argued rather than recited? | no (5 of 6) | yes (6 of 6) |
| Is the bear case in the body? | yes (6 of 6) | yes (6 of 6) |
| Are the numbers explained? | mixed (3 of 6) | yes (6 of 6) |
| Minutes to check three figures | 10–30 | 10–15 |
| Reading time | 30–45 min | 50–60 min |

**All six blind comparisons chose the console**, on answering the brief, on the focus
questions, on recent developments, on argument and on the checklist; on verifiability the
console won four and the other two were called equal. The reasons the judges gave were the
same three every time: the platform's header says *"Non-binding view: no view reached"*, so
there is no thesis to act on; the focus questions are answered "partly" or "no" because the
evidence rules exclude what would answer them; and on AZN the valuation is withheld
altogether.

**The verifiability result deserves care.** The platform's footnotes resolve to hashed bytes
and the console's are URLs that may or may not still say what was quoted — 46 URLs cited on
MSFT of which **33 resolved**, 58 on AZN of which **37 resolved**, and the response carried
no citation blocks at all, so the references are the model's own prose. Yet judges reading
the *exported Markdown alone* found the console no slower to check, because a URL can be
opened and a footnote to `source_document:<uuid>` cannot. **The platform's verifiability
advantage lives in the interface, not in the document it exports** — which is exactly
persona 2's complaint, and use case 9's row below.

### 4.2 The journeys, as walked

| Journey measure | What the audit recorded |
|---|---|
| Gate stops per run | **6** (plan, peers, themes, unmapped concepts, assumptions, final) — 7 with the sector gate the fix adds for a specialist filer |
| Wall-clock, commission to gate 2 | 31–42 minutes of step time (MSFT #1 42 min, MSFT #2 36 min, AZN 31 min); the audit's own stops added hours and are not the product's |
| Operator attention | six decisions, each on a page that shows what it hashes; the unmapped-concepts page carried **105 items** on MSFT and 51 on AZN |
| Values the operator had to supply | **2 on MSFT** (risk-free rate, equity risk premium), **5 on AZN**, **6 on a bank** — each with a justification the page requires |
| Anything that needed the terminal | **yes**: a stranded run needed `aer resume` before F-08's fix; a refused presentation check has no re-measure anywhere (F-22) |
| Developer vocabulary reaching the reader | **21 raw UUIDs** in the refresh run's own comparison table (F-22), and the coverage notice names metrics by their code identifiers |
| Reading time of the result | 30–45 minutes, 12.5–15.3k words |

### 4.3 The suitability matrix

| # | Use case | Supported? | What the audit found |
|---|---|---|---|
| 1 | First deep dive on a US large cap | **partly** | A complete, accurate, £7.48 report in 42 minutes of step time — with no view, no segment revenue, no guidance and no peer multiple. Good as an evidence base; not a decision document. |
| 2 | Refreshing a company already researched | **no** | The refresh section printed the prior report's UUID 21 times and the presentation check refused the run (F-22). Fixed here; the £6.80 run cannot be re-measured. |
| 3 | A domestic UK filer (LSE) | **no** | Acquisition resolves every subject against EDGAR; the Companies House client nobody calls (F-04). Proved offline. |
| 4 | A UK plc through its 20-F | **partly** | AZN ran to an approved report at £6.83 — with the DCF withheld for want of a share count in the IFRS tags (F-12, fixed) and every peer multiple absent. |
| 5 | A bank | **no, as found** | No run ever resolved a SIC code, so the bank met no sector gate, took the standard model, and was asked for an EBIT margin (F-23). Fixed here; the re-run is in §7. |
| 6 | A company with a material recent event | **partly** | Recent developments came from filings alone: MSFT's 8-K of 2 September 2026 is cited, its substance "not before us". Every built-in section's tier ceiling is T4, so news never reaches one (F-21). The console's web search is the whole of the difference. |
| 7 | Research under time pressure | **no** | 31–42 minutes of step time plus six human decisions; the console answered the same brief in 14–19 minutes with one prompt. Nothing useful exists at ten minutes. |
| 8 | A question-driven brief | **no** | Of eighteen judge-readings of the three focus questions, the platform answered **none** "yes"; the console answered four "yes" and the rest "partly". |
| 9 | Sharing the result | **partly** | The PDF renders and carries its disclaimer, but a reader outside the interface cannot follow a footnote to anything (§4.1), and a refresh report would have shown them UUIDs. |
| 10 | Checking the work | **yes, in the interface** | Every figure walks to a hashed artefact and every objection to its basis; replay re-derived 852, 857 and 152 calculations and re-verified every citation. This is the platform's strongest result, and it is the one the console cannot match. |
| 11 | Recovering from a problem mid-run | **partly** | Three stranded runs, all recovered; before F-08 the only way on was the terminal, and the console now offers *Continue* with the evidence. A refused check still has no path (F-22). |
| 12 | Living with it | **partly** | £7 a report and about 40 minutes of attention; bring-up on a clean machine took one session and a local Postgres and Redis; the Windows path is untested here (§9). |

**Who should use it today, and for what.** For building and auditing an evidence base on a
US filer — every figure traceable, every citation verified, nothing invented — it already
works, and no console session can be re-derived the way these runs can. For reaching a
view, for anything needing segment detail, guidance, peer multiples or news, and for a
bank, a domestic UK filer or a fast answer, the console is the better tool today, and six
of six blind judgements said so.

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

### F-22 — The refresh run's own comparison table printed the prior report's identifier

*Presentation and practicality. Major. Found live, fixed with a test, and the fix proved on
the run that failed.*

MSFT's second run — the refresh use case, commissioned the next UTC day — reached gate 2
with ten of eleven checks green and `presentation_integrity` failing on **twenty-one raw
UUIDs**, all one string: the prior report's id. Every row of `prior_research_comparison`
carries a `prior_report_id` (its contract requires one), the renderer hides
`financial_fact_id` and `extraction_id` as provenance a reader cannot follow, and nobody
had added this key to that list — so the section rendered a *Prior Report Id* column of
UUIDs, and its own commentary invited the reader to follow them. Only a second run on a
company reaches this section with rows in it, so the first run of every subject passes and
the refresh fails.

The platform does not bar approval on a failed check: the failure banners at the gate and
lands on the coverage notice, and the operator decides. So the honest operator refuses —
which is what the audit's policy did — and there is **no way to re-measure the check after
a fix**: the metrics are written at `validate`, the run waits at gate 2 for ever, and no
CLI command or page re-evaluates a job. A £6.80 run therefore has two ends: approve a
document with a column of machine identifiers in it, or pay again. That is the finding
behind use case 2's row in §4.

*Fix: `prior_report_id` joins the renderer's provenance keys, so the id stays in the run
export and the interface (which can resolve it) and leaves the document; the section's
sentence now says where to find it. `tests/test_report_document.py::TestAPriorReportIdIsProvenanceNotAColumn`
holds the three parts of it — the id absent, the reader's columns intact, and the check
passing. Proved live: the same job's document, re-assembled from its own record under the
fix, scores `presentation_integrity` **0 failures** against 21 before, with all 18 sections
and 15,297 words unchanged. The run itself stays unapproved, because re-measuring it is
exactly what the platform cannot do.*

### F-23 — No run ever discovered what kind of business it was researching, so a bank was valued as an ordinary company

*Completeness and correctness. Blocking. Found live, fixed with tests.*

ADR 0029's acceptance criterion is that **a bank ticker cannot produce a discounted cash
flow by any route**, and the type-level block delivers exactly that — for a run whose
company is classified. No run was. `classify` proposes from `Company.sic`; the `acquire`
step fetches *companyfacts*, which carries no industry code; and nothing else wrote that
column. So every run through the product's only workflow classified nothing, met no sector
gate, and took the standard model.

M&T Bank's run is what that looks like from the outside: plan approved, 23,174 facts
chosen, 852 unmapped tags, **no sector gate**, and then the assumptions gate asking the
operator for **the EBIT margin of a bank** — the discounted cash flow's driver, on a filer
whose deposits are raw material rather than financing. The audit's policy supplies only the
six residual-income inputs for a bank, so it declined to state one and stopped the run for
the operator, £2.03 spent (`audit/out/mtb-unclassified/`). An operator who typed a number
there would have got a DCF of a bank, which is the one outcome ADR 0029 exists to prevent,
and the 172.1 % net margin in this repository's own record came from an MTB run.

*Fix: the submissions index — already fetched, for the filings — carries `sic` and
`sicDescription`, and `aer.services.filings` now records them on the company, so the code
costs no extra request. An index with no code leaves the column alone: the permissive state
is reached by the data saying nothing, never by a later fetch overwriting what an earlier
one knew. `tests/test_filings_acquisition.py::TestTheFilerSaysWhatKindOfBusinessItIs` holds
the three cases, the second of them a bank's 6022 reaching the classifier as `banks`.*

*The consequence, stated plainly: an ordinary run now meets the sector gate whenever the
filer's code matches a seeded profile — Microsoft's 7372 is the early-stage-technology
profile's own prefix, so the fake scene's MSFT run meets it too, and the shared gate
mapping the test drivers read includes it now. That is the designed flow (a SIC code
proposes, a person confirms), and it exposes a second thing: **the sector gate offers
"Approve and continue" or "Reject and stop this run" and nothing else**, so an operator
looking at a wrong classification cannot correct it. For a filer whose profile blocks a
model that is the safe direction; for Microsoft-as-early-stage-technology it is a gate with
no right answer. §8 carries the decision.*

### F-24 — A bank that reports no total-revenue caption gets fee income as its revenue, and every margin on it is impossible

*Correctness. Blocking. Reproduced live and independently.*

This repository's record already carries the symptom: an M&T run once published a **172.1 %
net margin**, and gap A62's fix ranked the revenue tags so a total beats an ASC 606
component and added `RevenuesNetOfInterestExpense` as a bank's total-revenue caption. The
ranking is right. The hole it leaves is a filer that reports **no** total-revenue caption at
all, and M&T is one: its own companyfacts carry `Revenues` through FY2023 and then stop.

For FY2024 and FY2025 the only tag that maps to `revenue` is
`RevenueFromContractWithCustomerExcludingAssessedTax` — **$1,541m and $1,657m**, which is
fee income from contracts with customers, not the revenue of a bank. Against net income of
$2,588m and $2,851m that is a net margin of **168 % and 172 %**, and the run recorded
exactly those two figures (`net_margin` FY2024 1.6794, FY2025 1.7206) beside sane margins
of 24–31 % for FY2021 to FY2023, when the caption existed. The audit's **independent**
recomputation from the archived companyfacts reaches the same two numbers, so this is the
data and the selection rule, not the platform's arithmetic.

The components are all there and all mapped: `InterestIncomeExpenseNet` $6,948m (net
interest income), `NoninterestIncome` $2,742m, `InterestAndDividendIncomeOperating`
$10,486m. A bank's total revenue is the first two added — which is precisely what the
caption `RevenuesNetOfInterestExpense` means — giving $9,690m and a net margin of 29.4 %.
Nothing sums them, because a sum is a calculation rather than a fact and no calculation
claims that definition.

*State: not fixed, and it is §8's to decide.* The guard added after the first incident does
its job — `figure_plausibility` refuses a margin above one, so the run stops at gate 2
rather than publishing the number (§7 records what M&T's run did with it) — so no wrong
figure reaches a reader. But the honest position is that **a bank cannot presently produce
an approvable report**: the sector block now routes it to residual income correctly (F-23),
and the ratio suite still computes margins on a revenue that is not one. The fix is a
traced calculation, `revenue = net interest income + non-interest income`, taken for a
filer the sector gate has confirmed as a bank and used where the caption is absent; that is
a modelling definition, and §8 carries it.

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
- The run-to-run comparison keyed a calculation by name, period and case and kept one row
  per key — but one key holds many rows (a sensitivity grid records `present_value` once
  per cell, 117 times; one year's `days_outstanding` covers receivables, inventory and
  payables), and the rows are not emitted in a stable order. Its first answer on the two
  MSFT runs was "31 keys differ", of which most were a grid read against itself out of
  order, and a second version comparing row by row made it "642 rows of 837". Comparing the
  multiset of values under each key is the measure that matches the claim, and it is what
  §3.1 reports; `tests/audit/test_variance.py` holds it.

## 7. The spend ledger

Every platform figure is the sum of that run's own `costs` rows, read from the database
rather than from the driver's notes; every baseline figure is the vendor's usage priced by
the platform's own table at the same rate the platform uses.

| Side | Item | Spend |
|---|---|---|
| Platform | MSFT #1 — approved and rendered | £7.48 |
| Platform | MSFT #2 — refused at gate 2 (F-22) | £6.80 |
| Platform | AZN — approved and rendered | £6.83 |
| Platform | MTB as found — stopped for the operator (F-23) | £2.03 |
| Platform | MTB on the fixed classification | *see below* |
| Baseline | MSFT — Opus 5 high, 25 searches, 12,967 words | £7.63 |
| Baseline | AZN — 25 searches, 12,907 words | £11.03 |
| Baseline | MTB — 25 searches, 11,225 words | £6.71 |
| — | The M&T baseline turn the container cut off, billed and unrecorded | unknown |
| — | `just test-live`, WP0 | < £0.05 |

By category across every platform run: **output tokens £13.91, input £5.92, cache writes
£3.23, cache reads £0.09, web search £0.17** — so three quarters of the bill is what the
models wrote, and the cache is a cost rather than a saving at this hit rate (§3.3).

The judged reads and the code reading were paid for in this session's own tokens, not in
pounds from the £100.



## 8. What only the operator can decide

Each of these is confirmed in the code and each changes a recorded decision, a modelling
definition or what a run does. They are listed in the order I would take them.

1. **Peer multiples, or no peer multiples.** Nothing acquires a peer's financials, so the
   comps table is empty on every run and EV/EBITDA and P/E are withheld from every report
   (§3.4). Acquiring eight peers' companyfacts is free from EDGAR and slow rather than
   expensive; pricing them needs the feed the run already has. The alternatives are to
   acquire them, to drop the multiples from the spine, or to keep proposing peers for the
   record and say in the report that the table is structurally empty. Today the report says
   the peers were "excluded", which reads as a judgement about the peers.
2. **A bank's revenue** (F-24). M&T reports no total-revenue caption after FY2023, so
   revenue resolves to ASC 606 fee income and every margin computed on it is impossible.
   The components are mapped; the definition is not. Adopting `revenue = net interest
   income + non-interest income` for a confirmed bank is the fix, and it is a modelling
   definition rather than a bug fix — which is why a bank cannot produce an approvable
   report until you take it.
3. **The sector gate needs a third answer** (F-23). "Approve" grants a mandate and "Reject"
   kills the run; an operator who thinks the classification is wrong has nowhere to say so.
   The narrow fix is a "not this sector" decision that records the correction and runs the
   standard model; the wider question is whether a proposal that blocks no model should
   stop a run at all, which is what `sector_gate_required`'s own docstring argues.
4. **A stale approval is a dead end** (F-16). The gate refuses a decision on a payload that
   has moved and refuses a second decision on the same gate, so the run has no exit. A
   superseding decision is an approvals-model change and needs an ADR.
5. **Cost rows inside a step, or at its end** (F-21). Sibling calls in the research wave and
   the draft fan-out cannot see each other's spend, and a step that dies mid-flight loses
   the rows it flushed. Both fixes publish cost before the step commits, which amends ADR
   0016's publication rule.
6. **A filer with long-term debt and no short-term line** is valued as debt-free (F-21).
   Refusing the valuation, or asking for the missing half at the assumptions gate, are both
   changes to what a run does.
7. **Three modelling definitions** (F-21): scenarios keep the base WACC while reporting a
   rate override as argued about; the DCF's working capital includes cash and short-term
   debt; a bank's opening book value includes preferred stock and non-controlling
   interests. Each is defensible and none is stated to the reader.
8. **Two guards that do not exist** (F-21): a fact-backed numeric claim is never checked
   against its fact's value, and the reading ladder discards the scale word, so "$331.8
   million" reads as the right number. ADR 0060's territory.
9. **`cited_figure_agreement` is measured before `revise` rewrites the claims** (F-21), and
   nothing re-measures a failed check on a finished run (F-22). Both are the same question:
   what re-runs after a late change?
10. **The citation override the gate message promises exists on no surface** (F-21). Decide
   whether it should exist at all; today an unverified citation at gate 2 has no recovery.
11. **News never reaches a built-in section** (F-21, use case 6). Every built-in section's
    tier ceiling is T4, so the T5 documents a run admits are cited nowhere. Raising the
    ceiling for named sections is a change to the evidence policy.
12. **Effort on Haiku** (F-05): retire the setting on those four routes, or move them to a
    model that honours it.
13. **A UK acquisition path** (F-04): wiring Companies House into `acquire` is a source
    adapter and an ADR, not a bug fix, and the product's documents claim "UK or US" today.
14. **Whether any of the audit's scorers should become a permanent metric.** The numeral
    matcher against the filing (§2) and the peer-multiple completeness check are the two
    that earned their place here.
15. **The call on "ready".** The numbers in §3 and §4 are mine; the judgement is yours.



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
