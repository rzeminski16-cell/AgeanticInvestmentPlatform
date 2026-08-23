# Phase 3 — task sequence (tasks 22–32)

Continues from `docs/archive/phase-2-plan.md`. The phase specification — objective, deliverables,
acceptance criteria — is `docs/archive/PLAN.md` → Stage 3 → Phase 3, and it remains the authority.
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
  contract**, which is the reason it is the recommendation in `docs/archive/PLAN.md` §1.4) against a
  stated budget of ≤£100/month that also has to cover model spend. Deliberately sequenced
  last, so that a decision to skip it costs two tasks and not the phase.
- **A licence determination for the Bank of England IADB and the ONS API** — needed by tasks
  22 and 25 respectively. Both are expected to be Open Government Licence, which permits
  commercial use with attribution, but *expected* is what task 18 also started as. The
  determination is written down before an adapter is built, and the same standing constraint
  applies: nothing that breaches terms of use, and nothing whose commercial-use rights this
  project does not hold.

**yfinance remains disqualified** and nothing in this phase changes that. `docs/archive/PLAN.md` §1.4
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
  UK-FRC aliases. The long tail is explicitly not chased — `docs/archive/PLAN.md` names that as the
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
- `aer/calc/quality.py` — the earnings-quality set `docs/archive/PLAN.md` names: accruals ratio,
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
- Three signals `docs/archive/PLAN.md` names are **not derivable** from a 62-concept vocabulary —
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

**Outcome (2026-08-05).** Done. Five tables in migration 0015, not two.

- `scenarios` needs `scenario_overrides` to *be* a diff, and `sensitivities` needs
  `sensitivity_cells` so a cell can carry the calculation behind it. Both children exist so
  that "what does this case change?" and "what produced this number?" are queries rather than
  JSON scans.
- `assumption_proposals` is the fifth and was not in the plan's list. Without it an amendment
  overwrites the proposal, and "an amended assumption keeps the original on the record"
  cannot hold — the requirement implies the table.
- `as_quantity` refuses an unconfirmed assumption, so the enforcement is at the point the
  number would be used rather than at a review step somebody can skip. `confirm` takes a
  `User`; there is no agent-shaped argument that could be passed instead.
- Proposals carry an explicit `sequence`. Postgres `now()` is transaction-start time, so a
  propose-then-amend in one transaction writes rows with identical timestamps and the history
  a reviewer reads would be in planner order. This is the second time that trap has come up
  (see task 20 on claim ordering).
- One defect found: `NUMERIC(38,12)` returns twelve decimal places, so an assumption amended
  and read from memory hashes differently from the same row read back — and confirming what
  the page showed would have been refused for a reason nobody could see. The plan gate hit
  exactly this in task 10; the fix is the same refresh-before-hash.
- Verified by sabotage: 32 deliberate breakages, all caught. Two initially escaped and both
  were real test gaps: a history-ordering test that could not tell `sequence` from
  `created_at` because rows written together share a timestamp *and* come back in insertion
  order, and a model/migration disagreement that only the drift check can see.

---

## Task 25 — Macro with vintages: ALFRED, and the UK equivalents

**Objective.** Point-in-time macro. The version of a series **as it stood on the as-of date**,
not as it stands now.

**Build.**
- `aer/sources/macro/fred.py` — the ALFRED vintage endpoint rather than the FRED current one.
  This is the whole point: GDP and CPI are revised for years, and a backtest using today's
  revised series is using numbers nobody had. `docs/archive/PLAN.md` §1.5 calls ALFRED "the correct PIT
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

**Outcome (2026-08-05).** ALFRED and ONS done; Bank of England still not built.

- The acceptance criterion holds against the database: `TestTwoRunsWithDifferentAsOfDates`.
  Two vintages of US GDP, the same quarter, different values, both traceable.
- **FRED is not one licence, and that changed the design.** Its terms forbid commercial
  redistribution of copyrighted series, and FRED carries both kinds — BLS and BEA figures are
  public-domain federal works, while Case-Shiller, the ICE BofA family and OECD material are
  not. So the adapter takes an allowlisted *key*, never an identifier, and
  `aer/sources/macro/series.py` records the copyright position per series with the refusals
  listed by name. UK CPI comes from the ONS for the same reason: FRED's UK series are
  OECD-sourced.
- ONS is Open Government Licence with a documented API, so no scraping question arises —
  but it is **not an archive**. Its vintage is the release date, which is a weaker claim than
  ALFRED's, and `is_archived` carries the difference so a UK figure never borrows a US one's
  guarantee. A release after the as-of date is refused, which is the one point-in-time check
  the source honestly supports.
- Risk-free resolution is documented per currency. **There is no GBP entry**, and asking for
  one raises rather than substituting the US Treasury yield — that error is the whole rate
  differential and would look entirely ordinary. Closing ADR 0026 is what adds it.
- No live call has been made: this environment denies outbound HTTPS to every host, including
  `api.stlouisfed.org`. Tested against hand-written cassettes; the first real call is on the
  operator's machine.
- Verified by sabotage: 30 breakages, 27 caught first time. All three escapes were real gaps —
  no quarterly ONS coverage, and two schema constraints no test exercised. Two are now tested.
  The third revealed that a model's `CheckConstraint` has no runtime effect (the test schema
  comes from the migration, and Alembic's autogenerate does not compare CHECK constraints at
  all), so the migration is the enforcement and the model's copy is documentation.
- A pre-existing guard earned its keep: `test_every_provider_has_at_least_one_entry` caught
  `Provider.ONS` being added without a tiering entry, which would have made every ONS document
  tier 6 and therefore uncitable.

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

**Outcome (2026-08-05).** Done, and the interesting part was not the arithmetic.

- `aer/calc/wacc.py`: ten traced calculations — `rate_from_percent`, `cost_of_equity`,
  `average_debt`, `cost_of_debt`, `after_tax_cost_of_debt`, `effective_tax_rate`,
  `equity_weight`, `debt_weight`, `wacc` and `wacc_all_equity` — plus a `cost_of_capital`
  orchestrator returning every component with its own provenance.
- **The acceptance criterion is tested structurally, not by inspection.** A test walks every
  traced function's signature and asserts no parameter has a default *and* every parameter is
  keyword-only, so beta and the equity risk premium cannot be swapped positionally. That
  needed one enabling change in `aer/calc/engine.py`: `@traced` now sets `__wrapped__`, without
  which `inspect.signature` reports the wrapper's `(*args, **kwargs)` and the test would have
  passed vacuously.
- **The unit system has a blind spot, and this is where it opens.** A Treasury yield is `4.36`
  meaning 4.36% with unit `pure`; beta times an ERP is `0.055` with unit `pure`. Both are
  genuinely dimensionless, so adding them yields a 441.5% cost of equity that nothing in
  `aer/calc/units.py` can catch. Closed in three layers — `MacroSeries.quoted_in_percent`
  records the convention, `rate_from_percent` is the single traced conversion (called from
  `services.macro.as_rate`, the one place the flag is read), and every rate guard refuses a
  figure outside ±100% naming the conversion as the likely cause. Written up as ADR 0027,
  because the same shape returns for basis points, rebased indices and pence-per-share.
- **The "or a confirmed assumption" routes carry no flag.** The plan's documented override for
  the cost of debt, and the choice between an effective and a statutory tax rate, are both
  expressed by *which* quantity is passed: an assumption arrives with `SourceKind.ASSUMPTION`
  and a computed rate with `SourceKind.CALCULATION`. A `used_override=True` field would be a
  second, forgeable copy of something the ledger already records.
- **Zero debt is a capital structure, not a missing input.** `cost_of_capital` routes to
  `wacc_all_equity` rather than weighting an invented cost of debt at zero, and refuses the
  contradiction in both directions — debt with no cost of debt, and a cost of debt with no debt.
- No size or country premium. Both fold into the ERP assumption, which then has to justify
  itself; four numbers whose sum nobody stated are harder to review than one somebody did.
- Verified by sabotage: 40 breakages, 38 caught first time. One escape was real — a tax charge
  and a pre-tax profit in different currencies divide to a plausible `0.20` carrying a
  `USD/GBP` unit, and no test checked the guard. Now tested, with a second test one layer down
  asserting a currency-pair rate never reaches the weighted average. The other miss was a
  malformed sabotage pattern, not a gap.

---

## Task 27 — The discounted cash flow

**Objective.** The phase's centrepiece, and the thing most likely to be quietly wrong.

**Build.**
- `aer/calc/dcf.py` — driver-based FCFF: revenue growth, margin, tax, capital intensity and
  working-capital drivers, each a confirmed assumption, projected over an explicit forecast
  period.
- **Both terminal values, always shown side by side.** Gordon growth and exit multiple.
  `docs/archive/PLAN.md` requires both because terminal value is usually most of the answer and the two
  methods disagree — presenting one is presenting a choice as a fact.
- The **terminal-value share** as a reported output, because a DCF whose terminal value is 85%
  of enterprise value is a statement about the assumptions rather than about the business.
- Bridge to equity value: enterprise value − net debt ± non-operating items, per share.
- The scenario engine over task 24's scenarios, and the sensitivity grid.

**Tests.** The `hypothesis` invariants `docs/archive/PLAN.md` names — EV − net debt = equity value;
enterprise value monotonically decreasing in WACC; bear ≤ base ≤ bull; scaling every cash flow
by *k* scales EV by *k*. Golden cases within 0.01%. A negative or zero terminal-growth-minus-WACC
denominator refuses rather than producing a vast number.

**Acceptance.** All golden DCFs within 0.01%; every output row has complete input lineage to a
fact or a confirmed assumption; the terminal-value share appears on every result.

**Outcome (2026-08-05).** Done. `aer/calc/dcf.py` and `aer/services/valuation.py`.

- Seventeen traced calculations. A three-year forecast records 47 of them; every line of every
  year is its own entry, so "what was year four's capex?" is a query rather than a re-run.
- **The golden case was worked on paper first and every exact figure matched.** Revenue, EBIT,
  NOPAT, depreciation, capex, working capital and its movement, EBITDA and free cash flow are
  asserted exactly; the discounted aggregates to 0.01%. The hand arithmetic agreed with the
  implementation to every digit checked, including the Gordon terminal value of 1,835.8164 and
  the 5.83x it implies.
- **Both terminal values, each reporting the other's implied parameter.** That cross-check
  turned out to be the most useful thing in the module: the worked example's 2% perpetual
  growth implies a 5.8x exit multiple, and its 10x exit multiple implies 5.19% perpetual
  growth. The two per-share answers differ by 81%, and the result says so in words rather than
  leaving a reader to notice.
- **Enterprise value is not monotone in revenue growth**, and the property suite says so
  explicitly rather than asserting something false. Where capital intensity exceeds the
  operating margin, growth consumes more cash than it produces and destroys value — which is
  correct, and is why "bear ≤ base ≤ bull" is stated over margin, the discount rate and
  terminal growth instead. The invariants that do hold are all tested: value falls as the
  discount rate rises, rises with margin, rises with terminal growth, scales linearly with the
  level of the cash flows, and EV − net debt + adjustments = equity value.
- **Zero-debt and negative-equity cases are handled rather than printed.** A negative equity
  value carries a caveat saying the per-share figure below zero is arithmetic and not a price.
- The grid is eighty-one complete valuations at most, each with stored lineage — ADR 0028,
  which also records why interpolating between corners is wrong in the direction that flatters
  the valuation.
- Verified by sabotage: 53 breakages, 48 caught first time. **Three of the five escapes were
  real test defects**, and all three were the same defect: a test that could pass while the
  code under it did nothing. The non-operating bridge items summed to nil, so dropping them
  changed no answer. The grid's monotonicity check was non-strict, so a grid that repeated the
  base case in all nine cells passed it — twice over, once for each axis. Fixed by asserting
  strict ordering along both axes, that no two cells hold the same figure, and that adjustments
  move the equity value. The other two escapes were sabotage cases that were algebraic no-ops.

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

**Outcome (2026-08-05).** Done. The acceptance criterion's phrase *by any route* decided the
design, and ADR 0029 records why every obvious implementation fails it.

- **The block is a type.** `ValuationMandate` is permission to run one model on one company,
  validated in `__post_init__` — so a mandate for `dcf_fcff` on a bank does not exist to be
  passed around. `project`, `discounted_cash_flow` and `sensitivity_grid` take one as a
  required keyword argument with no default. A bank does not produce a DCF that is then
  suppressed at the page; the permission cannot be constructed.
- Four ways round it are closed and each is tested: the factory, the constructor,
  `dataclasses.replace` (which re-runs `__post_init__`), and mutation (the dataclass is
  frozen). A guard living only in the factory would have missed three of them.
- **An unconfirmed specialist classification stops the run rather than falling through to
  "unclassified".** This is the subtle half: unclassified is the *permissive* state, so
  falling through would be permissively wrong and would be reached by forgetting rather than
  by deciding. `confirmed_classification` raises; the workflow gate is what makes it
  reachable only when nothing specialist was proposed.
- The refusal names the profile, the seeded warnings verbatim and what is offered instead —
  so a REIT's refusal literally contains "P/FFO", which is the plan's own wording for what
  the operator should be handed. "Blocked" and "not implemented" read differently, because
  they are different statements and conflating them misleads in both directions.
- Required metrics are disclosed both ways: what the run produced and what it owed and did
  not. A list of what a report has says nothing about what it was supposed to have.
- The sector block renders **immediately after the header**, before any analysis. A sector
  warning at the foot of a report is a footnote, which is the thing this task exists to
  prevent.
- **The classifier is a floor.** The proposal comes from the filer's SIC code — free,
  deterministic and available before any model call. `Company.sic` is populated by the
  adapters that parse it (Companies House, the SEC *submissions* endpoint); this slice
  acquires *companyfacts*, which carries none, so a run through that path classifies nothing
  and takes the standard model. Safe, and honestly limited: the block is exercised by runs
  that resolve a SIC and not yet by the slice. Phase 4's classifier agent replaces the
  proposal and nothing else.
- **The first sabotage run was worthless and said so by being perfect.** 38 of 38 "caught",
  because adding a required `mandate` argument had broken `tests/test_dcf.py` — every case
  failed for that reason rather than for the sabotage. `mypy` had found the two production
  call sites immediately; the untyped tests it does not check were invisible. Fixed and
  re-run properly: **33 of 38, five escapes.**
- Three of those five were real, and each produced a change rather than a tolerance. Two
  guards were the same check written twice, so deleting either changed nothing observable —
  now genuinely different, because a *forecast* is the raw material of both free-cash-flow
  models while `discounted_cash_flow` is specifically the firm one. The longest-prefix rule
  in the SIC lookup was unobservable against the seed, since no two profiles overlap by
  prefix — now tested against a constructed pair, with a second test asserting the seed's
  non-overlap so the day it stops holding somebody is told. And nothing rendered a *whole*
  report, so moving the sector block to the foot passed: every test called `_sector_block`
  directly, and position is the entire claim. The other two escapes were redundant code that
  no test could distinguish, and it was deleted.
- Final: 38 of 38 caught, on a suite that was green to begin with.

---

## Task 29 — Prices and corporate actions *(conditional on the subscription)*

**Objective.** The market side, point-in-time clamped.

**Build.**
- `securities`, `price_bars`, `corporate_actions` tables, migration **0018** (0017 went to the
  retention split below).
- `aer/sources/eodhd.py` — EOD bars, splits and dividends. Under the existing fetch layer:
  allowlist, token bucket, circuit breaker, hashed artefacts.
- **The PIT clamp is in the adapter, not in the caller.** A request for bars never returns a bar
  dated after the as-of date, and a test asserts it on the adapter rather than downstream.
- Split and dividend adjustment as a recorded calculation, with the raw series retained. The
  licence note matters here and is recorded on every source document. ~~EODHD permits internal
  commercial use on a paid plan, and redistribution of the raw series requires a separate
  add-on — so derived figures may be published and the series may not.~~ **This was wrong and
  is corrected below and in ADR 0030: the terms grant no derived-data safe harbour, so nothing
  price-derived leaves the machine.**
- Market capitalisation, and beta as a computed override for task 26.

**Tests.** Cassettes; a bar after the as-of date is absent; an unadjusted and adjusted series
differ by exactly the recorded actions; a missing subscription fails with a message naming the
setting rather than silently returning nothing.

**Acceptance.** A price series is reproducible from the archived response and the recorded
adjustments alone.

**If the subscription is declined:** this task and task 30 are dropped, recorded in an ADR the
way task 18 was, and the valuation surface ships with the comps section stating plainly that no
market data source is configured. The DCF, ratios, earnings quality and WACC are unaffected.

**Delivered (2026-08-05), under ADR 0030 route 2.** `aer/calc/prices.py` (adjustment,
returns, market capitalisation, beta), `aer/sources/eodhd/` (parsers, the weighted-call
ledger, the client) and `aer/services/prices.py`. Migration 0018 and ADR 0032 carry the
schema; `docs/data-sources/eodhd.md` carries the licence and rate-limit position. Two
credential leaks were found and fixed on the way — see ADR 0033.

**Held before that (2026-08-05). The terms were read and three of the four answers changed
the design.** See ADR 0030.

- **The €19.99 *All World* plan is a personal-use plan.** Commercial use is a separate
  product, *Internal Use*, at **$399/month** — roughly 4× the whole ≤£100/month ceiling before
  any model spend. This is the same failure that disqualified yfinance, and it inverts §1.4's
  ranking: at the commercial tier EODHD is not the cheapest option, it is about eight times
  Tiingo's explicit commercial plan.
- **There is no derived-data safe harbour.** The terms prohibit *displaying* information in
  "original or **repackaged** form" and define nothing as derived. The assumption written into
  `aer/fetch/policy.py` — "derived figures may be published, raw series may not" — was
  unsupported and **has been corrected**; it would otherwise have been stamped on every price
  document as though somebody had determined it.
- **The one-month post-termination deletion clause contradicts the artefact store**, which has
  no delete path by design (ADR 0008). EODHD did not create this: `docs/archive/PLAN.md` risk T16
  already calls for a retention policy nobody has built. A licensed source is the first one
  that makes it load-bearing. The answer is to split the deletable payload from the
  undeletable provenance, and it comes *before* the adapter rather than after.
- **The rate limits are settled and generous**: 1,000 requests/minute, 100,000 *weighted*
  calls/day. `requests_per_second` is now set from the published ceiling with headroom instead
  of guessed. The weighted daily allowance needs a second limiter the fetch layer does not
  have, which is adapter work.

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

**Delivered (2026-08-05), internal-only under ADR 0030 route 2.** `aer/calc/comps.py` (the five
multiples, peer alignment, medians and percentile bands) and `aer/services/comps.py` (the
`PEER_SET` gate, the deterministic SIC-group proposal, the table). The peer-review page is
`/runs/{id}/peers`; the gate route was already generic.

**Nothing price-derived reaches a shareable surface.** A rendered report gets a `WithheldComps`
— an object with no field that could hold a figure — and the Markdown renderer's signature
accepts only that type. ADR 0034 records why that is a type rather than a flag.

Two things the tests found: `MAX_PERIOD_DRIFT_DAYS` was 92, which permitted exactly the
March-against-December comparison its own rationale said it excluded (now 45); and the calc
engine refused the first shape of the statistics, correctly, because a list of bare `Decimal`s
is a number that cannot say where it came from.

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

**Delivered (2026-08-05).** `/runs/{id}/valuation` and `/calculations/{id}`, both
server-rendered with no script of their own, plus `aer/services/valuation_view.py`.

**Read back from the run's ledger, never recomputed.** A page that re-ran the valuation would
show today's answer beside yesterday's report and both would look authoritative. Where a
figure is absent the page says the run did not produce it.

Building the surface exposed a provenance gap in task 27's work: `enterprise_value`,
`equity_value`, `terminal_value_share` and `value_per_share` each run **twice** per valuation,
once per terminal method, and the ledger held two rows with the same name, different answers
and nothing saying why. `method` is now a recorded parameter on all four — which the page
needed, and which a reader reading the calculations table needed already.

---

## Task 32 — Golden calculations, and the gate extended

**Objective.** Hold the arithmetic to the same standard the evidence is held to.

**Build.**
- `fx_calc_golden` — 30 known-answer calculations across the suite, per `docs/archive/PLAN.md` §2.10.
- Two metrics added to the blocking gate: **numerical consistency** (re-running the calc DAG
  from stored inputs reproduces every output within 0.5%) and **assumption completeness** (no
  calculation reaching a report has an unconfirmed or absent input).
- The replay harness: a run's numbers reproduced from its archived artefacts alone.

**Tests.** A deliberately introduced regression in either new metric fails CI, checked by
introducing one — the practice used throughout Phases 1 and 2.

**Acceptance.** All 30 golden calculations within 0.01%; the gate now has eight blocking metrics
and a regression in any of them is red.

**Delivered (2026-08-06).** `aer/eval/replay.py` (the harness), the two metrics in
`aer/eval/metrics.py`, `tests/fixtures/calc/golden.json` (thirty hand-computed answers in the
stored-record shape, one per calculation, all ten calc modules represented and the coverage
asserted), and the deliberate regressions in `tests/test_eval_replay.py`: a corrupted stored
output, a stored function name the code no longer has, and a re-proposed assumption each turn
their metric red.

**A golden case is a stored row, not a test function.** The corpus is written in exactly the
shape the `calculations` table persists and replayed through the same harness that replays a
live run — so the thirty answers prove the harness's reconstruction (sequence inputs
reassembled, JSONB-flattened enums coerced back) at the same time as the arithmetic.

Two design points worth keeping: the registry of traced functions is **derived** from the calc
modules' own `calculation_name` attributes, refusing duplicates, because a hand-kept mapping
would silently fall behind; and a record that cannot be re-run scores **infinite** drift rather
than being skipped, because a metric that measures only the records that still work passes on
the strength of what it did not check.

The sabotage pass ran seventeen mutations; sixteen are caught (one — the rebuilt inputs
inventing their own sources — escaped first time and now has a test pinning the documented
property). The seventeenth, dropping `sequence` from the replay query's ORDER BY, is the same
defensive-only tie-break task 31 examined: rows in one transaction share `created_at` and
return in insertion order in practice, and the labels carry the stored sequence regardless, so
no deterministic test can distinguish the mutation. Left untested deliberately, as there.

**Closes Phase 3.**

---

## Deliberately not in Phase 3

Report styling, charts, PDF output and Obsidian (Phase 5). Parallel agents, the skill-file
engine, the red-team and the validators (Phase 4). Sector-specialist *models* — bank excess
return, REIT NAV, biotech rNPV — which `docs/archive/PLAN.md` §2.3 defers explicitly while requiring the
warnings and blocks now. Intraday data, options, credit. Anything in the plan's "do not build
yet" list for this phase.
