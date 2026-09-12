# Hand verification of the code audit's findings (2026-09-12)

Fixed so far (each with a test): #0 CAGR annual, #3 render re-entrant, #4 gate funnel,
#7 IFRS aliases, #14 cap column, #15/#38 Fable priced, #18 grid case, #23 derived
plausibility, #24 RI range checks, #30 wave failure visible, #33 cancel a waiting run,
#34 execute declines RUNNING, #35 API hash pre-check, #37 dated model ids, #42 red-team
retry. Live second pair of order-dependent tests under seed 20260909
(test_section_spine red-team refill; test_planner_salvage) — both pass alone; residual.

Verdicts: CONFIRMED (read in the code, and where noted seen live), CONFIRMED-CONDITIONAL
(true under a stated condition), PARTLY (true but narrower than claimed), REFUTED,
UNVERIFIED (not read). Fix: DONE / PLANNED / OPERATOR (a decision, not a bug).

## Blocking

- #0 CAGR takes interim rows (vs1:2938). CONFIRMED. `financial_facts` holds Q1–Q3 rows for
  MSFT and Q2 rows for AZN; the query has no FY filter. MSFT #1 and AZN escaped because
  the FY row is the latest period_end; a September run on a June-quarter 10-Q filer
  (MTB) would take a three-month figure as `end`. Fix: PLANNED (annual rows only).
- #1 Lone long-term debt valued as debt-free (valuation_run:277). CONFIRMED-CONDITIONAL:
  `_line(latest, "total_debt", required=False)` substitutes a sourced zero; `total_debt`
  derives only from both halves. Whether a filer trips it depends on tagging. Fix:
  OPERATOR — refuse the valuation naming the missing half, or ask for it at the
  assumptions gate; both change what a run does for such a filer.
- #2 Fact-backed numeric claims never checked against the fact's value (evidence:834,
  evaluations:578). CONFIRMED: `covered_figures` lends the claim's *statement* as cover
  and `_cited_figures` joins on `calculation_id` only. The live runs show 0 contradictions
  on 169/168 checkable figures, so the writer did not exploit it; the guard is absent.
  Fix: PLANNED (a numeric claim naming a fact must state a numeral that reads as it).
- #3 A failed step's flushed rows are committed (engine:832). CONFIRMED: the ordinary
  branch commits the session as-is; `_render` flushes the Report before the PDF. Fix:
  PLANNED (roll back before recording the failure).
- #4 Peer/theme gate hashes the step, the page hashes the set (vs1:3482). CONFIRMED:
  `_STEP_OUTPUT_GATES` uses `peer_gate_payload(produced)`; the page and
  `comps.payload_for_job` append operator additions. Fix: PLANNED (one funnel).
- #5 Stale approval is a dead end (vs1:2183, approvals:176). CONFIRMED: "decide on what it
  shows now" while `_refuse_if_already_decided` refuses a second decision. Fix: OPERATOR
  (a superseding decision at a stale gate is an approvals-model change: ADR needed).

## Budget

- #13 Sibling spend invisible to the per-call guard (base:449). CONFIRMED structurally;
  exposure bounded at (N−1) × one call's worst case. Fix: OPERATOR (committing cost rows
  mid-step amends ADR 0016's publication rule).
- #14 raise_cap unseen mid-session (base:437). PARTLY: seen at the next session boundary
  (each fanned-out section and each node opens a fresh session), not the next call. Fix:
  PLANNED (read the column, as the engine's guard does).
- #15/#38 Unknown model priced at Opus 5 rates (costs:147). CONFIRMED. Fix: PLANNED
  (price Fable 5 explicitly; the unknown fallback overstates).
- #37 Haiku priced at Opus rates because the API echoes the dated id (costs:163).
  CONFIRMED LIVE: MSFT #1's `costs` rows for `claude-haiku-4-5-20251001` are at
  $5/$25 per MTok (£0.28 charged for what Haiku's $1/$5 makes ~£0.06). Fix: PLANNED.
- #39 A stream that fails after acceptance writes no cost row (anthropic:240). CONFIRMED
  structurally. Fix: PLANNED if cheap (read the snapshot's usage on failure).
- #31 Cost rows lost when a step dies mid-flight (base:882). CONFIRMED structurally
  (flush only; the step commits at its end). Consistent with AZN's `revise` attempt 0,
  which ran for a minute before the container stopped and left no cost row; not provable
  without the vendor's bill. Fix: OPERATOR (same ADR question as #13).
- #34 Double enqueue; `execute` accepts any status (pages:185, runs:202). CONFIRMED
  structurally. Fix: PLANNED (`execute` declines a RUNNING job).
- #40 Fan-out: a later failure rolls back the paid first attempt (vs1:3127). CONFIRMED
  structurally. Fix: PLANNED (commit what was paid before re-raising).

## Citations, gates, engine, retry, sections, acquisition

- #25 cited_figure_agreement measured at `validate` only; `revise` does not re-run it
  (vs1:1674). CONFIRMED structurally. Fix: PLANNED (re-evaluate after revise).
- #26 Readings discard the scale word, so a 1000× error passes (figures:69). CONFIRMED by
  the design of `reads_as`; the audit's own matcher shares it and is tightened to compare
  the scaled value when a scale is stated. Fix: OPERATOR (the reading ladder is ADR 0060).
- #27 Days / pp / bp figures erased as counts (section_output:318). PLAUSIBLE, not read.
- #28/#36 `override_citation` has no caller in `src/` (grep). CONFIRMED: an unverified
  citation at the final gate has no recovery on any surface. Fix: PLANNED (a form on the
  claims page) or OPERATOR (decide whether overrides should exist at all).
- #29 A claim naming a fact from the wrong period passes (evidence:549). CONFIRMED by
  construction (validation carries id and value only). Fix: OPERATOR (period attribution
  from prose is a judgement; the audit's matcher does it heuristically).
- #30 Wave failure dropped when a branch pauses (engine:545). UNVERIFIED (not read).
- #33 Cancelling a paused run never lands (cancellation:56). UNVERIFIED (not read).
- #35 The JSON API records any 64-character hash (api runs:378). CONFIRMED: no pre-check
  against the live payload, unlike the web route. Fix: PLANNED.
- #42 A schema-rejected or truncated red-team reply fails the run (red_team:196).
  CONFIRMED LIVE on AZN: `red_team` attempt 0 FAILED at 21:36:50 after 3 minutes; the
  driver's resume re-ran it as attempt 1, which succeeded (£0.30). Fix: PLANNED (retry
  once inside the step, as the writer does).
- #41, #43, #44 retry ladders. PLAUSIBLE, not read.
- #47–#52 evidence packs. PLAUSIBLE, not read; #48 (T5 news never reaches a built-in
  section) is checked against MSFT #1's citations by tier below.
- #7 IFRS aliases name elements the taxonomy does not have (concepts:472). CONFIRMED LIVE
  on AZN: `PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`,
  `AdjustmentsForSharebasedPayments`, `IncomeTaxesPaidRefund`, `WeightedAverageShares`,
  `LongtermBorrowings`, `CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings`,
  `DividendsPaidOrdinaryShares` all sit in the run's 322 unmapped tags; the mapped set has
  no capital expenditure, no share count, no long- or short-term debt. Fix: PLANNED
  (correct the aliases; test against the archived AZN companyfacts).
- #8 NCI tie-break (pit:174). PLAUSIBLE; AZN maps both `noncontrolling_interest_income`
  and `net_income`; checked when AZN is scored.
- #9 Three-month and year-to-date collide (facts schema:180). PLAUSIBLE, not read.
- #10 An LSE request spends on plan and critique before dying at acquire. CONFIRMED
  (F-04's offline proof shows the same sequence on the fake scene).

## Calculations

- #18 Grid cells recorded as case="base" (dcf:1351). CONFIRMED. Fix: PLANNED.
- #19 A Gordon-refused grid corner fails the run (valuation_run:531). CONFIRMED
  structurally; only bites when base spread < 1.5 %. Fix: PLANNED (record the cell as
  refused, as the bank grid does).
- #20 Scenarios keep the base WACC while reporting rate overrides (valuation:322).
  CONFIRMED. Fix: OPERATOR (refuse rate overrides in scenarios, or recompute).
- #21 Working capital includes cash and short-term debt (valuation_run:421). CONFIRMED;
  a modelling definition. Fix: OPERATOR.
- #22 Bank book value includes preferred and NCI (residual_income_run:579). CONFIRMED;
  check MTB's tags. Fix: OPERATOR/PLANNED depending on MTB.
- #23 A derived driver outside its band fails the assumptions step (assumption_proposals:227).
  CONFIRMED. Fix: PLANNED.
- #24 Residual-income kernel has no range checks (residual_income:301). CONFIRMED. Fix:
  PLANNED.
