# The remediation plan — closing the two gaps the readiness audit found

*Written 2026-09-13, after the readiness audit of 2026-09-12
([`readiness-audit-2026-09.md`](../plan/readiness-audit-2026-09.md)). The operator's question was
direct: the system is not user-ready in many ways, and it is not better than the Claude
console — what is the plan to fix both?*

*This is a proposal, not scope. [`ROADMAP.md`](../plan/ROADMAP.md) remains the authority: nothing here
is real work until it carries a §2 or §3 number, and §9 below says which. Every claim traces
to a file:line, an audit section or `judges/reads.json`; a diagnosis pass of fourteen
subsystem readers, each adversarially refuted, produced 168 findings of which 165 survived,
and the ones that changed the plan are named.*

---

## 1. The two issues are one issue

**The platform computes, stores, hashes and verifies far more than it ever shows anybody.**

That is not a slogan; it is what the code says, in six places that were each verified:

| The platform already has | Where it stops |
|---|---|
| Microsoft's own P/E of **27.43×** and EV/EBITDA of **18.93×** — computed, traced and replayed on every run | `render/document.py:436` types the comps parameter `WithheldComps \| None`, so no multiple can pass through it. The docstring says why: a licence position the operator **reversed on 2026-08-09** (`fetch/policy.py:192`, `derived_figures_publishable=True`) |
| **626 dimensioned facts** across the three subjects, mapped and stored — including AstraZeneca's revenue by geography (US $23,970m, UK $4,359m, Germany $2,890m for FY2025) and Microsoft's 45 business-segment and 42 product-or-service rows | `services/facts.py:88` bars every dimensioned row from every section's evidence pack, so the Segment Analysis writer truthfully writes "no segment-level dollar figures were available to cite here" |
| A WACC, a terminal value and a value per share on every valuing run | `sections/evidence.py:580` orders calculations `period_end DESC NULLS LAST` and takes 40; `calc/engine.py:165` records those three with `period = None`, so they sort last and are cut at the database. MSFT #1's Executive Summary says "no valuation output, no discount rate" eleven lines under a front page printing "WACC 9.3%, value per share $485.29" |
| 259 verified excerpts, re-read by hash to confirm each citation | None is printed in the exported document — which is the file the judges scored, and the dimension the platform exists for |
| A margin-decomposition bridge, unit- and mutation-tested (`calc/bridge.py`) | Zero production callers. Decomposition is the one thing the console won on, and the writers are correctly forbidden to derive it themselves |
| A conclusion to reach | `vertical_slice_v1.py:3765` assigns `report.rating = None`. Nothing in the codebase ever writes it, so "no view reached" is printed unconditionally — a hardcoded `None`, not a judgement |

So: **"not user-ready" is the last mile on the interface; "not better than the console" is the
last mile on the document.** Same disease, two organs. That reframing is what makes this
tractable: most of the work is connecting, correcting and printing things that already exist
and are already tested, not building new capability.

## 2. What the evidence says — including what it refutes

The audit's own figures were re-read from `judges/reads.json` rather than from its summary.

**The defeat is bigger than reported, and narrower in cause.** The file holds **18 reads and
9 blind comparisons**, not the six §4.1 tabulates (it counts the first round and adds AZN #2's
three in the following paragraph). Across nine comparisons and six dimensions, the 54 votes
are **console 51, equal 3, platform 0** — and all three draws are on verifiability. Not one
comparison split in the platform's favour on any dimension.

Four things that plan changed, because the record contradicts the obvious reading:

1. **A stated view is necessary-sounding and unproven.** AZN #2 scored `view_stated` true 3
   of 3 and `argued` true 3 of 3 — and `would_act_on_it` still answered "no" 3 of 3, with all
   three comparisons still choosing the console. The audit says it plainly: *"It changed no
   verdict."* And the view the judges actually scored was the console's `UNDERWEIGHT | Target
   price $405 | Last close $493.16 | expected total return −17.1%` — a rating, a target and a
   return, four of the six strings in `RESERVED_OUTPUT_FIELDS`, which a model may never write
   here. **What the platform is missing is not a model's opinion. It is a price anchor and an
   implied upside, both of which are code-computable.**
2. **This is an acquisition problem, not an evidence-policy problem.** The console's winning
   material was not exotic: MSFT's segment table came from **Exhibit 99.1 inside accession
   `0001193125-26-380280`** — the very 8-K folder the platform downloaded the primary document
   from — and AstraZeneca's patent-cliff answer came from an AR extract on a 6-K, the FY2025
   20-F and the H1 press release. **None of it is T5.** `Filing.url()` builds only the primary
   document, so the exhibit is never fetched. That retires the tier-ceiling decision, an ADR
   fight against ADR 0092 and ADR 0111, and a nineteenth section — from the first pass
   entirely.
3. **Peer multiples matter less than the audit implies.** The console has none either: its own
   "still to look up" lists peer multiples on both subjects. The expensive item (acquire eight
   peers' filings) is deferred; the cheap one — print the subject's own multiples, which every
   run already computed — is not.
4. **The effective sample is one document.** Three of the nine comparisons judge AZN #1, whose
   valuation was withheld by F-12, a bug since fixed; the platform would not ship that document
   today. For the current product the independent evidence is **one document (AZN #2) read by
   three near-identical judges**. That is thin, and §7 below spends £3 fixing it before it
   spends £60 acting on it.

**And a correction to something I said earlier in this session:** I told the operator the
segment gap was an extraction failure — that SEC companyfacts carries no dimensional facts, so
the filing's own iXBRL would have to be parsed. That is wrong. The platform already reads,
maps and stores dimensioned iXBRL (AZN #2's sweep saw 3,637 dimensioned facts and wrote the
224 single-axis ones; MSFT #1's PDF carries a segment chart citing the 10-K). The failure is one `WHERE dimension_axis IS NULL` at
`services/facts.py:88`. The fix is a carve-out in the evidence selector, not a new parser —
days of work smaller, and it changes the plan.

## 3. The rules this plan works under

Four standing rules, each adopted because a judge or a critic showed what goes wrong without it.

- **Replay-first.** A document change ships with an offline proof: re-render the five stored
  audit runs from their own rows and assert the change. Live runs are for what only a live run
  can show. This is what keeps the programme inside its budget.
- **Advisory before blocking.** Every new check this plan adds — cross-section agreement, an
  unsettled-challenge refusal, an uncited-answer refusal — lands as a *report* at gate 2 first
  and becomes blocking only after a full run passes it clean. Three new ways to strand a run,
  in a programme whose own measurement depends on runs completing, is how a plan eats itself.
- **Every blocking check gets ADR 0018's shape**: the operator may override **one instance**
  with a mandatory written reason, recorded and rendered on the document. Never a bulk override.
- **Operator hours are a budget line.** `worker.py:245` runs one job at a time by design, and a
  measurement round is a day of the operator's attention, not an afternoon. The running order
  prices sessions, pounds **and** operator hours.

---

## 4. Phase 0 — Ask before you build

**2 sessions · ~£3 (judge tokens only) · nothing live**

The cheapest thing in this plan is also the one that decides the rest of it. Eighteen expensive
judge reads produced a pile of complaints and no ordering over them.

| # | What | Why it is first |
|---|---|---|
| 0.1 | Re-run the **18 existing reads over the 18 existing documents** with two added rubric keys: *"what one change to this document would move you off 'no'?"* and *"what would you need to see to prefer it?"* | Judge tokens only — no live run, no code, no new documents. It converts complaints into a ranked backlog before ~40 sessions are committed on an ordering inferred from post-hoc prose |
| 0.2 | **Map both console notes source by source.** For each figure a judge called decisive, record the document it came from and whether that accession is already in the platform's submissions index | One sitting, £0. It is what established §2's finding 2, and it is what keeps the tier-ceiling decision out of this plan |
| 0.3 | **Settle three facts in writing**: do the five audit run databases and the artefact store still exist (every £0 re-render in this plan depends on it); what does the executed EODHD agreement permit in an *exported* artefact; and what the pre-registered targets and the abandonment criterion are (§7) | Four plans costed their whole offline economy on stored rows nobody checked. If the answer is no, one seeding run is bought now and said so |
| 0.4 | **The operator reads two documents blind, themselves** — audit §4.4 checks 1 and 2, files already committed | Three model judges are standing in for one real decision-maker who has not yet read them |

**Exit:** a ranked backlog, a source map, a committed pre-registration with targets and an
abandonment criterion, and the operator's own read.

## 5. Phase 1 — The half that needs no judge and no money

**ISSUE 1 · 12–16 sessions · £0 live · 3 ADRs**

Everything here is provable on the fake scene offline. Two of three judges said it should come
first for exactly that reason: it is half the operator's request and it costs nothing but
sessions. Two runs — MSFT #2 at £6.80 and M&T at £7.61 — were **destroyed by the absence of a
button**: £14.41 of a £100 budget, because a rejected gate leaves the job non-terminal
(`services/runs.py:165`), the eleven evaluation rows are written once inside `validate`, and
the engine skips any step whose row says SUCCEEDED (`workflow/engine.py:518`).

| # | What | Notes |
|---|---|---|
| 1.1 | **The dead-end inventory, and a journey harness that starts RED** (`tests/e2e/journey.py` plus shape assertions in `audit/smoke.py`) | The instrument for ISSUE 1. Split deliberately: shape assertions (a forward control on every stopped state, zero UUIDs, zero shell commands in visible text) run on the fake scene; anything needing real filings runs on a stored run |
| 1.2 | **A superseding decision at a stale gate** (F-16), **re-measure a refused check** (F-22), **re-seal**, and the decided-gate console state | In that dependency order — re-seal enables re-measure. ADR-shaped: it changes the approvals model |
| 1.3 | **`superseded_by` on reports**, with a recorded reason, and a withdraw/correct path | `decisions.py` and `theses.py` already implement supersede-and-withdraw-with-a-reason; this copies that shape. It closes "which report is current", which four plans assumed and none owned |
| 1.4 | **The vocabulary ratchet, finished**: zero UUIDs and zero shell commands in user-facing text; the console stops telling the operator to watch `just worker`; `aer reseal` leaves the pause messages | 135 distinct code identifiers currently reach gate pages |
| 1.5 | **Cut the gate pages**: unmapped concepts to a first screen under 20 rows with both counts named (496 / 312 / 852 rows today; 7,911 words on MSFT, 13,592 on the bank) | This is also the operator-hours fix that makes measurement rounds affordable |
| 1.6 | **Wire the macro stack** so the risk-free rate and ERP stop being hand-typed every run | Built, licence-cleared, and has no caller in `src/` |
| 1.7 | **Enforce `excluded_sources` in code** | Today it is a request-form field whose only consumer is a line in a prompt (`agents/planner.py:277`). A broken promise on the one page where the operator says what the run may not read — and invariant 8's territory |
| 1.8 | **Exercise backup and restore once**, and record it | Half a session. Invariant 1's guarantee currently rests on a store whose recovery path has never been run |

**Exit:** every inventory row has a browser-proved forward control; the harness is green on rows
that started red.

## 6. Phase 2 — A bank can be researched

**ISSUE 1, blocking · 7–9 sessions · £8 live · 1 ADR**

All three judges flagged the same thing: the one blocking finding the audit left open must not
be an optional branch. An entire subject class produces no report at all.

The diagnosis corrected the audit's own sizing, and it matters: **seven of the eleven impossible
relations are quarterly, and none is reachable from `aer.calc`.** The plausibility guard, the
front page, the revenue chart and the writers' evidence pack all read stored **facts**
(`services/evaluations.py:612`, `render/glance.py:264`, `services/exhibits.py:198`,
`sections/evidence.py:135`). A derived revenue is a calculation, not a fact — so "one function
in `aer.calc`" clears 4 of 11 findings and gate 2 still refuses. Fix it **at the parse and fact
layer**, where a sector-confirmed rule can reach every consumer.

Also here: the turnover half of the guard has **never fired** — `evaluations.py:612` queries the
concept `total_assets` where the canonical concept is `assets` (`core/concepts.py:110`). Fixing
it *adds* findings to M&T before it removes them, which is why it lands before the live run.

**Exit:** one live M&T run reaches an approved, rendered report; `figure_plausibility` 0 of 0
including the two findings the fixed guard now adds.

## 7. Phase 3 — The document stops contradicting itself

**ISSUE 2, tranche A · 10–12 sessions · £0 live · 1 ADR**

The single most-named defect across all nine comparisons, and the cheapest to start.

| # | What | Effort |
|---|---|---|
| 3.1 | **Port F-27's calculation index into `gather_evidence`** — order by name then newest period, one row per `(name, case)`, skip sensitivity cells, carry the period, bound the pool. The query is already written, at `services/red_team.py:343-374` | **1 session, no ADR, no spend.** The keystone: it is what lets the Executive Summary see the valuation it currently denies exists |
| 3.2 | **Cross-section consistency** over the assembled draft: no figure name+period carrying two values; no sentence asserting a figure is unavailable that another section prints | Advisory at gate 2 first, then blocking |
| 3.3 | **The adversary stops lying, and the appendix stops lying about the adversary.** Re-check every challenge against the calculation record before publication; rewrite the appendix after settlement | Today every challenge prints "Escalated for human decision" whatever happened, and six of AZN #2's eight rested on a void premise — while `cited_figure_agreement` (81 figures, 0 failures) sat in the table above refuting them |

**Exit:** re-render all five stored runs from their own records — zero contradictions, zero
undecided accusations published.

## 8. Phase 4 — Print what the run already computed

**ISSUE 2, tranche B · 11–13 sessions · £0 live · 2 ADRs**

| # | What | Notes |
|---|---|---|
| 4.1 | **Fix the price layer** (`services/price_acquisition.py`, the bars count that loses the whole layer on a warm database) → a market capitalisation and an enterprise value reach the page | AZN #2 and M&T lost their entire price layer to this |
| 4.2 | **Prefer market equity in the WACC** | Today `calc/wacc.py:162` prints "Book equity was used as the equity weight because no market capitalisation was available", and Recorded Caveats tells the reader every valuation discounted at it is too high. The sceptic judge called it "an act of self-demolition" |
| 4.3 | **Carry the subject's own multiples into the document** — §17 prints 27.4× and 18.9× with footnotes resolving to the recorded calculations | The licence determination already permits it (`fetch/policy.py:192`); only the render signature forbids it |
| 4.4 | **An implied upside/downside** against the price, and the composed base-case range in the header instead of "no view reached" | One render branch. `assemble_document` already takes `rating: str \| None` |
| 4.5 | **Segment revenue reaches the segment section**: carve dimensioned facts into the evidence pack (an ADR 0058 amendment), print values on the chart bars, stop the exhibit vanishing on a second run of the same company, and carry exhibits into the Markdown edition | The 626 stored rows finally reach a writer |
| 4.6 | **Name the eight operator-confirmed peers** in §17 and the industry section | An hour. It is the only change that moves "competitive position with named competitors" off *absent* |
| 4.7 | **Acquire the exhibits inside accessions already opened** (EX-99 / 6-K press releases) | Where the console's winning material came from. An acquisition fix, not an evidence-policy fight |
| 4.8 | **Print the verified excerpt** in the exported document — after deciding the boundary | Invariant 8: an excerpt leaving the trusted zone can re-enter the next run's planner prompt. Decide what an excerpt is outside the boundary *before* printing it |

**Exit:** re-render MSFT #1 and AZN #2 from stored rows — multiples, a market capitalisation, an
implied range, segment figures and resolvable footnotes, with no sentence denying any of them.

## 9. Phase 5 — The first measured signal, and the kill gate

**2 live runs · £21 · 1 day of operator time**

One AZN run and one MSFT run on the fixed tree, re-judged blind by the coded panel against the
September notes **plus one fresh baseline on the current best route** (~£10). A comparator that
is three constants in `audit/baseline/anthropic_baseline.py` improves for free while this plan
runs; measuring against September's console in December measures nothing.

**Pre-registered readings, written in Phase 0 before any of this is built:**

| Result | What it means | What happens |
|---|---|---|
| ≥ 2 of 6 comparisons no longer choose the console, **or** any judge's stated reason changes category | The thesis holds | Continue to Phase 6 |
| Dimensions move but no verdict does | Necessary, not sufficient — as AZN #2 already showed once | Continue, but the view work is re-scoped to the composed half only |
| **Nothing moves and no stated reason changes category** | The document is not where the gap is | **Stop.** Narrow the product's claim to what §4.3 already scores "yes" — an evidence base and a checking instrument — and spend the remaining sessions on ISSUE 1 alone |

That last row is the abandonment criterion, and it is the line that makes every other gate real.

## 10. Phase 6 — The view, the argument, the export

**ISSUE 2, tranche C · 14–18 sessions · £0 live · 3 ADRs · conditional on Phase 5**

- **The composed half of the view first, judged alone**: the base-case range, the method, the
  implied upside, and `what_would_change_the_view` as a required field. Only then the operator's
  own stated view, stored as an ADR 0102 judgement — never a model's rating. Shipping both at
  once makes the round unable to attribute what moved.
- **Wire `calc/bridge.py`** for contribution-to-growth and margin decomposition. This is the one
  thing the console won on that the platform is currently forbidden to do — not by rule, but by
  the absence of a deterministic producer.
- **Widen the 40-row keyhole per section.** All 48 section executions across three logged runs
  reported `evidence_truncated: true`.
- **Read `thesis_monitor.py` before wiring the view into it** — one session. Half the product
  downstream of a stated view is undiagnosed.

## 11. Phase 7 — The verdict round, and the roadmap

**3 live runs · £31.50 · 2 days of operator time**

MSFT ×2 (the second the next UTC day, so it doubles as the refresh case and the variance check
at no extra cost) and AZN ×1; September's notes reused plus the fresh baseline; the full coded
panel; the confirmed assumption set fixed in the pre-registration so the round measures the
product and not the operator's typing.

Then: **write all of it into the roadmap.** For these two issues the roadmap is empty — every
§2 and §3 item is done except concept-map curation, report readability, the one-machine gate and
two commercial checks, and none of those four would change a judge's verdict or remove a dead
end. The audit's 28 findings and §8's sixteen decisions appear only in a preamble paragraph. So
this plan jumps no queue; it must be *written into* §2 and §3 with numbers, and the two stale
"next" pointers (`ROADMAP.md:120` and `:391`, both naming closed items) corrected.

---

## 12. The targets, stated before the work

| | Today | Fixed when |
|---|---|---|
| **ISSUE 1** | 3 of 6 runs reached an approved report; £14.41 lost to two dead ends; the terminal is load-bearing | The journey harness reports **zero** stopped states without a labelled forward control, **zero** UUIDs and **zero** shell commands in visible text; and three of three runs in the measurement window — one a bank — reach an approved rendered report with no terminal and no rescue |
| **ISSUE 2** | 0 of 9 comparisons; 54 dimension votes console 51 / equal 3 / platform 0; `would_act_on_it` no 9/9 | **At least 3 of 6 comparisons do not choose the console**, and at least 2 of 6 choose the platform, with the fresh baseline in the set — or the abandonment criterion fires and the claim narrows |

Both instruments are code before either is used: the judging panel lifted verbatim from
`judges/reads.json` so the key sets diff empty, neutral identically-shaped A/B paths, a seeded
assignment and a recorded identity-guess hit rate. September's panel exists only as its output;
a repeat of it would be a different instrument wearing the same name.

## 13. What this plan does not do

- **No tier-ceiling change, no nineteenth section, no T5 evidence fight.** §2's finding 2.
- **No peer acquisition** in the first pass. The console has no peer multiples either.
- **No UK domestic path.** It is a source adapter, a company-number column that fails a check
  constraint today, a SIC scheme that shares no prefix with the US codes the sector profiles
  match on, and an ADR. It is a roadmap item; the honest interim is to stop offering "LSE" in
  the form and refuse at request time naming the 20-F route.
- **No speed claim.** The real machine time is 26.5–36.8 minutes (the audit's 31–42 sums a
  five-wide research wave five times over). The cheap win — the evidence base and the valuation
  are complete at 7.7–11.3 minutes and there are already two unrendered preview surfaces — is in
  Phase 1; a ten-minute answer is not promised.
- **No claim that the valuation is *good*.** The audit established it is traceable, not that it
  is defensible, and this plan makes it more prominent. One offline session reviews the terminal
  spread across the five stored runs before the view work lands.

## 14. Cost, effort and what the operator must decide

| Phase | Sessions | Live £ | Operator hours |
|---|---|---|---|
| 0 — ask first | 2 | ~3 | 3 (two documents read) |
| 1 — the journey | 12–16 | 0 | 2 |
| 2 — the bank | 7–9 | 8 | 2 |
| 3 — self-contradiction | 10–12 | 0 | 1 |
| 4 — print what exists | 11–13 | 0 | 1 |
| 5 — first signal | 2 | 21 | 8 |
| 6 — view and argument | 14–18 | 0 | 2 |
| 7 — verdict and roadmap | 4 | 31.50 | 16 |
| **Total** | **62–76** | **~£64** | **~35** |

£36.68 of the audit's £100 remains, so the programme needs about £27 more, and Phase 5's gate
stands between the operator and most of it. At four sessions a week this is three to four months;
the stop points are Phase 2 (a bank works), Phase 4 (the document stops lying about itself) and
Phase 5 (the evidence to continue or to narrow).

**Decisions only the operator can take**, in the order they are needed:

1. **Does the audit's run data still exist?** Everything costed at £0 assumes it does.
2. **What does the EODHD agreement permit in an exported file?** `fetch/policy.py` and
   `render/document.py` currently disagree, and three items in Phase 4 are designed on the side
   that loses. Name a default so work continues if the answer takes weeks.
3. **Whose view is it?** The recommendation is: the composed half is the code's, the stated half
   is the operator's, and the model writes neither.
4. **A bank's revenue** — derive, withhold or leave (audit §8 item 2). The recommendation is
   derive, at the fact layer, with the ADR.
5. **Is the eighteen-section spine right for one private investor?** `AnalysisMode.QUICK` exists,
   drops nine sections, scales budgets to 0.6, and has never been run. **£4 answers the largest
   unasked question in this plan**, and it is not in any phase above because it is the operator's
   call whether to ask it.

---

*The diagnosis behind this plan — 165 verified findings across fourteen areas, four competing
plans, three judges and two critics — is reproducible from the workflow script committed with
this document's commit. Where this plan and the audit disagree, the disagreement is named and
the evidence is cited; where it and the roadmap disagree, the roadmap wins until §9 is done.*
