# Phase 3 — task sequence (tasks 22–32)

Continues from `docs/phase-2-plan.md`. The phase specification — objective, deliverables,
acceptance criteria — is `docs/PLAN.md` → Stage 3 → Phase 3, and it remains the authority.
This file is the dependency-ordered breakdown of it.

**Objective, restated.** The analytical core. Phase 2 made every fact traceable; Phase 3 makes
every *number derived from those facts* traceable too — ratios, earnings quality, a cost of
capital, a discounted cash flow, comparables — with each figure resolving to a recorded
calculation whose inputs are facts or written-down assumptions.

**The rule this phase is mostly about.** `CLAUDE.md` opens with it: *deterministic Python owns
every number*. Phase 3 is where that stops being cheap. A DCF is the single most tempting thing
in this project to hand to a model, and it is forty lines of Python with unit tests. Nothing in
this phase computes a figure in a prompt.

---

## What Phases 1 and 2 already banked

| Deliverable | Where it landed |
|---|---|
| `companies`, `financial_facts` with as-reported basis and `filed_date` | Task 8, migration 0004 |
| Calculation engine kernel: `Unit` algebra, `Quantity`, `SourceRef`, `@traced`, full provenance | Task 9, `aer/calc/` |
| `calculations` and `assumptions` tables | Task 9, migration 0005 |
| `GET /api/calculations/{id}` with opt-in lineage tree | Task 9 |
| Six calculations: growth, CAGR, ratio, margin, weighted average, year-on-year series | Task 9, `aer/calc/basic.py` |
| Concept vocabulary — 24 canonical concepts, us-gaap/IFRS/UK-FRC alias tables | Tasks 8 and 17, `aer/core/concepts.py` |
| `sector_profiles` table, seeded with eight profiles | Task 19, migration 0014 |
| Disagreement ladder — two sources, one number, no silent winner | Task 19, `aer/core/disagreement.py` |
| The blocking evaluation gate, with six metrics | Task 21, `aer/eval/` |

So this phase adds the *market* and *macro* sides, expands the concept map, and builds the
valuation stack on a kernel that already carries units and provenance.

## What must be obtained before the tasks that need it

- **A FRED API key** (free registration at fredapi.stlouisfed.org) — blocks task 25. A form,
  not a negotiation; get it early.
- **A decision on the EODHD subscription** — blocks tasks 29 and 30, and it is a spending
  decision rather than a technical one. €19.99/month for All World (US + a **direct LSE
  contract**, which is the reason it is the recommendation in `docs/PLAN.md` §1.4) against a
  stated budget of ≤£100/month that also has to cover model spend. Deliberately sequenced
  last, so that a decision to skip it costs two tasks and not the phase.
- **A licence determination for the Bank of England IADB and the ONS API** — needed by tasks
  22 and 25 respectively. Both are expected to be Open Government Licence, which permits
  commercial use with attribution, but *expected* is what task 18 also started as. The
  determination is written down before an adapter is built, and the same standing constraint
  applies: nothing that breaches terms of use, and nothing whose commercial-use rights this
  project does not hold.

**yfinance remains disqualified** and nothing in this phase changes that. `docs/PLAN.md` §1.4
records why: its own maintainers describe the endpoints as personal-use, and Yahoo's API terms
forbid deriving income without written permission.

## Why this order

Four constraints fix the sequence.

1. **Statements before ratios.** A ratio suite needs a balance sheet that balances and an
   income statement whose lines are the same concept across filers. The concept map is 24
   entries and the plan calls for 60; every ratio built before that expansion is a ratio that
   works on one filer's tagging.
2. **Assumptions before valuation.** A DCF is mostly assumptions. The record that holds them —
   with a justification, a confidence and an author — has to exist before the first model that
   would otherwise hard-code them, or they arrive as literals in Python and never leave.
3. **The paid dependency goes last.** Tasks 29 and 30 are the only ones that need a price feed.
   Everything before them works on filings and free macro data, so a decision not to subscribe
   ends the phase two tasks short rather than blocking it.
4. **Sector enforcement before the valuation surface.** A page that renders a DCF for a bank
   and then explains underneath why it is meaningless has already done the damage. The block
   comes first.

`beta` deserves its own note. Textbook CAPM regresses returns against an index, which needs the
price feed. **Beta is therefore a first-class assumption with an optional computed override**,
not a computed input with an assumption fallback — a documented, human-confirmed beta with a
stated justification is more auditable than a regression nobody inspects, and it keeps the cost
of capital available whether or not a subscription exists.

---

## Task 22 — Statements, the concept map at sixty, and currency

**Objective.** Turn a bag of facts into three statements a ratio can be computed from, in one
currency, with unmapped tags visible rather than silently absent.

**Build.**
- `aer/core/concepts.py` expanded to the top ~60 canonical concepts and their us-gaap, IFRS and
  UK-FRC aliases. The long tail is explicitly not chased — `docs/PLAN.md` names that as the
  phase's main risk and prescribes a clear "unmapped concept" surface instead.
- `aer/calc/statements.py` — pure assembly of an income statement, balance sheet and cash-flow
  statement from `financial_facts` for a company and period set. Returns `Quantity` values
  carrying their `SourceRef`, so every line already knows which fact it came from.
- **Identity checks as first-class output, not assertions.** Assets − liabilities − equity, and
  the cash-flow roll-forward. A filer whose statements do not close is a real and common
  condition; it produces a recorded discrepancy the operator sees, not an exception that ends
  the run.
- `aer/calc/fx.py` — currency normalisation. Rates from the Bank of England IADB (free, OGL,
  subject to the determination above), stored as facts with their own provenance and **clamped
  to the as-of date** like everything else. A conversion is a recorded calculation with the
  rate as an input, never an inline multiply.
- The `UK_FINANCIALS` gate: `POST /api/runs/{id}/gates/UK_FINANCIALS/decide`, raised when the
  iXBRL extractor reports unmapped tags (task 17 already computes `needs_confirmation`).

**Tests.** A US filer and a UK filer produce the same canonical lines from different tags; a
statement that does not balance is reported rather than raised; an unmapped tag reaches the gate
rather than being dropped; FX round-trips (USD→GBP→USD within a stated tolerance) and a rate
after the as-of date is refused.

**Acceptance.** 60 concepts mapped with both taxonomies; a UK and a US filing produce comparable
statements; no conversion happens without a recorded rate and a source.

**Non-goals.** Ratios, valuation, anything needing a price.

**Outcome (2026-08-05).** Done, with the FX **source** deferred and the vocabulary at 62.

- Statements, identities and the unmapped-tags gate are complete and wired end to end.
- The vocabulary is 62, not 60. The cash-flow roll-forward this task asks for could not be
  written down without `net_change_in_cash` and `effect_of_exchange_rate_on_cash`; adding two
  concepts was cheaper than dropping a named identity check.
- `aer/calc/fx.py` ships in full — selection, look-ahead refusal, staleness limit, traced
  conversion, inversion, round-trip. **No Bank of England adapter.** The licence is settled
  (Open Government Licence, commercial use permitted with attribution); the access route is
  not, and could not be checked from this environment. See ADR 0026 for what a person has to
  read to close it. Tasks 25 and 26 inherit the open question and must not assume it closed.
- Two mapping errors found and fixed: `TotalAssetsLessCurrentLiabilities` was mapped to
  `noncurrent_assets`, and the two variants of the cash movement would have been conflated.
- `pending_gate` skipped conditional gates entirely, so a run stopped at the financials gate
  was told it was waiting at the final one. Fixed.

---

## Task 23 — The ratio suite and earnings quality

**Objective.** Everything computable from filings alone, unit-safe and property-tested.

**Build.**
- `aer/calc/ratios.py` — margins (gross, operating, net, EBITDA), returns (ROE, ROA, ROIC),
  liquidity (current, quick), leverage (net debt/EBITDA, debt/equity), coverage (interest
  cover), efficiency (asset turnover, DSO, DIO, DPO, cash conversion cycle).
- `aer/calc/quality.py` — the earnings-quality set `docs/PLAN.md` names: accruals ratio,
  CFO/net income, working-capital trend, and capitalisation-policy flags (R&D and interest
  capitalisation, useful-life changes). These are the metrics that catch a company whose
  reported profit and its cash have parted company.
- A **margin bridge**: period-on-period movement decomposed into the lines that caused it, each
  carrying its own provenance.

**Tests.** `hypothesis` invariants — a ratio is scale-invariant in its unit, ROIC is undefined
rather than infinite at zero invested capital, the margin bridge's components sum to the
movement. Known-answer cases from a real filing. Unit mismatch raises, per invariant 5.

**Acceptance.** Every ratio resolves through `@traced` to facts; no ratio silently returns zero
for a missing input — an absent concept produces an absent ratio with a reason.

**Non-goals.** Anything requiring a market price or a forecast.

**Outcome (2026-08-05).** Done. Seventeen ratios across six families, eight quality signals,
two margin bridges.

- `RatioResult` and `QualitySignal` carry a value *or* a reason, never a zero. An undefined
  ratio — ROE on negative equity, ROIC at zero invested capital — is absent with the guard's
  own words; the primitive still raises, and the suite is what turns a refusal into a row.
- A unit mismatch is never swallowed. It means two lines of one statement disagree about what
  they measure, and hiding it inside the module whose job is to notice problems would be the
  worst possible place for it.
- Three signals `docs/PLAN.md` names are **not derivable** from a 62-concept vocabulary —
  development-cost capitalisation, stated useful lives, revenue-recognition policy. They are
  listed as `UNAVAILABLE_SIGNALS` with where to look instead, so "we checked" is
  distinguishable from "we never looked". Interest capitalisation *is* derivable, from the
  gap between cash interest paid and the charge to profit, and is computed.
- One precision defect found and fixed: `days_outstanding` computed `balance / flow * year`,
  which rounds the quotient at 34 digits and multiplies the error back up — a balance of one
  year's flow came back as 364.999999999999999999999999999999 days. Reordering puts the
  division last.
- Two provenance defects found by the tests: a negated bridge component and an inverted FX
  rate both lost their source, because plain quantity arithmetic has no calculation to point
  at. Both are now traced functions.
- Verified by sabotage: 36 deliberate breakages, all 36 caught.

---

## Task 24 — Assumptions, scenarios and sensitivities as data

**Objective.** The record that makes a valuation arguable, built before the valuation.

**Build.**
- `scenarios` and `sensitivities` tables, migration 0015. A scenario is a named set of
  assumption overrides (bear/base/bull); a sensitivity is a grid over two assumptions with a
  recorded output per cell.
- `aer/services/assumptions.py` — proposal, human amendment and confirmation. An assumption
  carries its value, unit, justification, confidence, source (model proposal or operator) and
  the calculation ids that used it.
- **A model may propose an assumption; only a person may confirm one**, and the proposal is
  recorded whether or not it was accepted — the same shape as a citation.
- The assumptions surface: a page listing every assumption a run rests on, editable before the
  valuation runs.

**Tests.** An unconfirmed assumption cannot enter a calculation that reaches a report; an
amended assumption keeps the original proposal on the record; a scenario is a diff rather than a
copy, so a base-case change propagates.

**Acceptance.** No figure in a valuation traces to a literal; every assumption in a rendered
report names a person or a proposal.

**Non-goals.** The models themselves.

---

## Task 25 — Macro with vintages: ALFRED, and the UK equivalents

**Objective.** Point-in-time macro. The version of a series **as it stood on the as-of date**,
not as it stands now.

**Build.**
- `aer/sources/macro/fred.py` — the ALFRED vintage endpoint rather than the FRED current one.
  This is the whole point: GDP and CPI are revised for years, and a backtest using today's
  revised series is using numbers nobody had. `docs/PLAN.md` §1.5 calls ALFRED "the correct PIT
  source for macro" and it is the reason this adapter is not simply "FRED".
- `macro_series` and `macro_observations` tables, migration 0016, with the vintage date on the
  observation.
- `aer/sources/macro/boe.py` and `aer/sources/macro/ons.py` — UK rates and CPI, subject to the
  licence determination. Both under the fetch layer's allowlist and rate limits like every other
  source.
- Risk-free rate resolution: a documented series per currency, retrieved at the as-of vintage.

**Tests.** A series revised after the as-of date returns the earlier vintage; a request for a
vintage that does not exist is refused rather than silently falling back to current; cassettes,
no network.

**Acceptance.** Two runs with different as-of dates over the same series get different values,
and both are traceable to a vintage.

**Non-goals.** Macro *interpretation*, which is a Phase 4 agent.

---

## Task 26 — Cost of capital

**Objective.** A WACC every input of which is a fact, a vintage or a confirmed assumption.

**Build.**
- `aer/calc/wacc.py` — CAPM cost of equity, after-tax cost of debt, market-value weights.
- Inputs, each with its origin stated: risk-free from task 25's vintage; equity risk premium as
  a confirmed assumption; **beta as a confirmed assumption**, with a computed override arriving
  in task 29 if the price feed exists; cost of debt from interest expense over average debt,
  with a documented override; the tax rate from the effective rate or a confirmed assumption.
- The capital structure: book weights when no market capitalisation is available, and the
  substitution **stated on the calculation** rather than hidden.

**Tests.** Golden WACC cases against hand-worked answers; `hypothesis` — WACC is monotonically
increasing in beta and in the risk-free rate, and lies between the cost of debt and the cost of
equity; a missing input refuses rather than defaulting.

**Acceptance.** Every WACC input resolves to a fact, a vintage or a confirmed assumption. No
default values anywhere in the module.

---

## Task 27 — The discounted cash flow

**Objective.** The phase's centrepiece, and the thing most likely to be quietly wrong.

**Build.**
- `aer/calc/dcf.py` — driver-based FCFF: revenue growth, margin, tax, capital intensity and
  working-capital drivers, each a confirmed assumption, projected over an explicit forecast
  period.
- **Both terminal values, always shown side by side.** Gordon growth and exit multiple.
  `docs/PLAN.md` requires both because terminal value is usually most of the answer and the two
  methods disagree — presenting one is presenting a choice as a fact.
- The **terminal-value share** as a reported output, because a DCF whose terminal value is 85%
  of enterprise value is a statement about the assumptions rather than about the business.
- Bridge to equity value: enterprise value − net debt ± non-operating items, per share.
- The scenario engine over task 24's scenarios, and the sensitivity grid.

**Tests.** The `hypothesis` invariants `docs/PLAN.md` names — EV − net debt = equity value;
enterprise value monotonically decreasing in WACC; bear ≤ base ≤ bull; scaling every cash flow
by *k* scales EV by *k*. Golden cases within 0.01%. A negative or zero terminal-growth-minus-WACC
denominator refuses rather than producing a vast number.

**Acceptance.** All golden DCFs within 0.01%; every output row has complete input lineage to a
fact or a confirmed assumption; the terminal-value share appears on every result.

---

## Task 28 — Sector enforcement: the block, not the footnote

**Objective.** Make `sector_profiles` do something.

**Build.**
- Sector classification: a model proposal confirmed at the `SECTOR_SPECIALIST` gate, never
  applied unconfirmed.
- Enforcement in code: a blocked model raises a typed refusal naming the profile, the warning
  and what is offered instead. A bank cannot produce an FCFF DCF, and the refusal happens
  before the arithmetic rather than beside it.
- Required-metric reporting: a profile's `required_metrics` that the run could not compute are
  disclosed rather than omitted.

**Tests.** A bank ticker refuses a DCF and says why; a REIT refuses and offers P/FFO; an
unclassified company runs the standard model; the seeded warnings reach the report.

**Acceptance.** A bank ticker cannot produce a DCF by any route, asserted at the calculation
layer rather than at the page.

---

## Task 29 — Prices and corporate actions *(conditional on the subscription)*

**Objective.** The market side, point-in-time clamped.

**Build.**
- `securities`, `price_bars`, `corporate_actions` tables, migration 0017.
- `aer/sources/eodhd.py` — EOD bars, splits and dividends. Under the existing fetch layer:
  allowlist, token bucket, circuit breaker, hashed artefacts.
- **The PIT clamp is in the adapter, not in the caller.** A request for bars never returns a bar
  dated after the as-of date, and a test asserts it on the adapter rather than downstream.
- Split and dividend adjustment as a recorded calculation, with the raw series retained. The
  licence note matters here and is recorded on every source document: EODHD permits internal
  commercial use on a paid plan, and **redistribution of the raw series requires a separate
  add-on** — so derived figures may be published and the series may not.
- Market capitalisation, and beta as a computed override for task 26.

**Tests.** Cassettes; a bar after the as-of date is absent; an unadjusted and adjusted series
differ by exactly the recorded actions; a missing subscription fails with a message naming the
setting rather than silently returning nothing.

**Acceptance.** A price series is reproducible from the archived response and the recorded
adjustments alone.

**If the subscription is declined:** this task and task 30 are dropped, recorded in an ADR the
way task 18 was, and the valuation surface ships with the comps section stating plainly that no
market data source is configured. The DCF, ratios, earnings quality and WACC are unaffected.

---

## Task 30 — Comparables and historical multiples *(needs task 29)*

**Objective.** The relative view, with the peer set a human agreed to.

**Build.**
- Peer-set proposal with a rationale per peer, and the `PEER_SET` gate:
  `POST /api/runs/{id}/gates/PEER_SET/decide`. **A peer set nobody confirmed is a comparison
  nobody can defend** — a badly chosen peer moves a multiple more than most modelling choices.
- `aer/calc/comps.py` — EV/EBITDA, EV/Sales, P/E, P/TBV, P/FFO, each computed on the same basis
  for every peer, with the basis recorded.
- Historical-multiple bands for the subject company: current against its own history, which is
  often the more honest comparison.

**Tests.** A peer with a different fiscal year end is aligned or excluded with a reason; a
multiple with a negative denominator is reported as not meaningful rather than as a number;
the gate refuses to proceed on an unconfirmed peer set.

**Acceptance.** Every multiple names its basis and its date; no comps table is produced without a
confirmed peer set.

---

## Task 31 — The valuation surface

**Objective.** The phase's user-visible outcome.

**Build.** A valuation page: the DCF output with every input traceable in one click to the fact
or assumption behind it, both terminal values with the terminal-value share, the sensitivity
grid, the comps table where prices exist, and **a bold specialist-sector banner** where a
profile blocked or warned. Server-rendered, no script of its own, in the pattern task 20
established.

**Tests.** Every figure on the page links to its calculation; a blocked sector shows the banner
and no DCF; the page works with JavaScript off; Playwright covers the drill-down from a valuation
figure to its assumptions.

**Acceptance.** From the valuation page a reader reaches any input's origin in two clicks — the
same standard Phase 2 set for evidence.

---

## Task 32 — Golden calculations, and the gate extended

**Objective.** Hold the arithmetic to the same standard the evidence is held to.

**Build.**
- `fx_calc_golden` — 30 known-answer calculations across the suite, per `docs/PLAN.md` §2.10.
- Two metrics added to the blocking gate: **numerical consistency** (re-running the calc DAG
  from stored inputs reproduces every output within 0.5%) and **assumption completeness** (no
  calculation reaching a report has an unconfirmed or absent input).
- The replay harness: a run's numbers reproduced from its archived artefacts alone.

**Tests.** A deliberately introduced regression in either new metric fails CI, checked by
introducing one — the practice used throughout Phases 1 and 2.

**Acceptance.** All 30 golden calculations within 0.01%; the gate now has eight blocking metrics
and a regression in any of them is red.

**Closes Phase 3.**

---

## Deliberately not in Phase 3

Report styling, charts, PDF output and Obsidian (Phase 5). Parallel agents, the skill-file
engine, the red-team and the validators (Phase 4). Sector-specialist *models* — bank excess
return, REIT NAV, biotech rNPV — which `docs/PLAN.md` §2.3 defers explicitly while requiring the
warnings and blocks now. Intraday data, options, credit. Anything in the plan's "do not build
yet" list for this phase.
