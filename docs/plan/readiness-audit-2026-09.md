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

*State: reproduced; to fix.*

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

### F-09 — What the code audit reported, pending adversarial verification

The subsystem readers of the WP1 workflow (four of thirteen completed before the session's
usage limit) reported 41 findings. Each is being verified by three independent refuters
before it is counted; the ones their finders marked **blocking** are listed here so they are
not lost if verification lags:

- A step that raises after flushing rows has those rows committed with its FAILED status,
  and `render` becomes unrecoverable (`workflow/engine.py:832`).
- The peer-set and theme-set gates cannot be passed once the operator adds a peer or theme
  of their own: the page hashes the whole set, the engine and the decide pre-check hash the
  step's proposal only (`vertical_slice_v1.py:3482`).
- Every stale-approval pause except the FINAL seal-drift case is a dead end: the run tells
  the operator to "decide again", which `record_decision` refuses (`:2183`).

*State: verification running.*

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

## 6. The harness's own defects, for honesty

The driver, not the platform, caused two stops on MSFT #1: a duplicated keyword in the
screenshot hook at the first gate, and a poll a moment after enqueueing that read the old
pause as a new one and stopped the worker mid-step. Both are fixed and committed; the second
is how F-08 was observed at all.

## 7. The spend ledger ⏳

## 8. What only the operator can decide ⏳

## 9. What this pass does not establish ⏳
