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

**3 sessions · ~£7 · one live run**

**Approved by the operator on 14 September 2026.** 0.1 and 0.4 are approved as written; 0.3's
three facts are all now settled (see [`06-open-questions.md`](06-open-questions.md)); and 0.5
is added — the QUICK-mode run, which the operator agreed to on the same day.

The cheapest thing in this plan is also the one that decides the rest of it. Eighteen expensive
judge reads produced a pile of complaints and no ordering over them.

| # | What | Why it is first |
|---|---|---|
| 0.1 | **DONE, 14 September 2026, £3.96** — [`12-the-ranked-backlog.md`](12-the-ranked-backlog.md). Re-ran the **18 existing reads over the 18 existing documents** with two added rubric keys: *"what one change to this document would move you off 'no'?"* and *"what would you need to see to prefer it?"* | Judge tokens only — no live run, no code, no new documents. It converts complaints into a ranked backlog before ~40 sessions are committed on an ordering inferred from post-hoc prose |
| 0.2 | **DONE, 16 September 2026, £0** — [`../plan/readiness-audit-2026-09/source-map.md`](../plan/readiness-audit-2026-09/source-map.md). Every decisive figure the console read from a primary source is inside an accession the platform opened (Microsoft), a filing type it already fetches (AstraZeneca's results 6-Ks, which the five-most-recent rule skipped for September notices) or its own fact store; the patent-expiry PDF neither side read is an issuer document. Three small acquisition changes and one new form (the proxy) fall out of it, all inside F7 | It confirms §2's finding 2 from the record rather than from memory, and keeps the tier-ceiling decision out of this plan |
| 0.3 | **Settle three facts in writing**: do the five audit run databases and the artefact store still exist (every £0 re-render in this plan depends on it); what does the executed EODHD agreement permit in an *exported* artefact; and what the pre-registered targets and the abandonment criterion are (§7) | Four plans costed their whole offline economy on stored rows nobody checked. If the answer is no, one seeding run is bought now and said so |
| 0.4 | **The operator reads two documents blind, themselves** — audit §4.4 checks 1 and 2, files already committed | Three model judges are standing in for one real decision-maker who has not yet read them |
| 0.5 | **Run `AnalysisMode.QUICK` once and read it** (~£4, one live run) | It exists, drops nine of the eighteen sections, scales budgets to 0.6, and **has never been run in the platform's life**. It answers the largest unasked question in this plan — whether eighteen sections is the right spine for one private investor — for the price of half a report. Open question 7, approved |

**0.3 is already settled**, and its three answers are recorded rather than assumed: the run data
exists (5 complete drafts, 3 approved reports, 832 artefacts); the price subscription is treated
as permitting publication of derived figures, with ADR 0034's withheld type as the fallback if
that turns out to be wrong; and the abandonment criterion in §9 is signed off.

**Exit:** a ranked backlog, a source map, a committed pre-registration with targets and an
abandonment criterion, the operator's own read, and a decision on the spine.

### What 0.1 came back with, and what it changes

Three results, in [`12-the-ranked-backlog.md`](12-the-ranked-backlog.md):

1. **Self-contradiction outranks everything** — weight 9.42, named first by **seven of nine**
   judges, and twelve of thirteen mentions tagged *small*. It confirms this plan's ordering
   (Phase 3 is already self-contradiction, Phase 4 already print-what-exists) and changes their
   weight: they are not tranches A and B of ISSUE 2, they are most of it.
2. **`states_a_view` was not named once** across nine platform reads. F13 has not disappeared —
   it has been **absorbed**: what the judges ask for is one reconciled base case with its method
   named and a price beside it, which is F13's composed half arriving as the resolution of a
   contradiction. **Phase 6's view work is re-scoped to the composed half**, and F13's cost and
   prominence fall. ADR 0117 stands as written; it already ships the halves in that order.
3. **Eight of nine say *partly*** if every change they named were made; one says yes. That
   number belongs in the Phase 5 pre-registration: the judges are saying in advance that
   document changes alone move them from *no* to *partly*, which is movement and is not
   preference. A round producing exactly that is the middle row of §9's table, not the top one.

And from the comparator's side: the nine baseline reads rank **provenance first**, asking for
figures sourced to the 10-K rather than to Yahoo, btw.media and a Substack. That is the thing
the platform already does best and the console structurally cannot — the durable advantage,
confirmed from the other side rather than asserted from ours, and the argument for F8 printing
the verified excerpt behind F16's boundary.

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
| 1.1 | **DONE, red, 16 September 2026, £0** — the dead-end inventory and a journey harness that starts RED. The inventory is generated from code (`tests/journey_inventory.py`, 52 rows); the builders and the three assertions are written once (`tests/journey_harness.py`) and driven twice — in a browser (`tests/e2e/journey.py`, `tests/e2e/test_journey.py`) and in-process on the fake scene (`audit/journey.py`, `python -m audit.smoke --journey`, a step of the CI test job). Measured on landing: 32 rows red, 20 not yet constructible, 0 green (33 and 19 after 1.2, which made one more problem page constructible); `STILL_RED` records each red row by assertion, `UNCONSTRUCTED` the rest with the reason, and both halves fail when a row's verdict moves in either direction. Before it: CI green again (1.0) and the order dependence verified on a fresh seed, 6,982 passed (1.1a) | The instrument for ISSUE 1. Split deliberately: shape assertions (a forward control on every stopped state, zero UUIDs, zero shell commands in visible text) run on the fake scene; anything needing real filings runs on a stored run. Still to construct on the fake scene: the two gates the scene never raises (sector, unmapped concepts), the nine escalation triggers, an unverifiable citation, and two of the three problem pages |
| 1.2 | **DONE, 16 September 2026, £0** — [ADR 0123](../adr/0123-a-decision-at-a-gate-is-superseded-and-a-rejection-ends-the-run.md), Accepted with the code the same day. A decision the page moved under is decided again and the new decision supersedes it (`approvals.supersedes_id`, migration 0074; `Decision.AMENDED` written for the first time); a rejection ends the run through the cancellation service and the console offers *Start a new run*; re-seal is a control that re-seals the waiting gate and continues; re-measure is a control that runs `validate` and everything after it again as the next attempt. Proved by `tests/test_gate_remedies.py` and by the harness: the `control` assertion left the record on all twelve rejected and stale gate rows | In that dependency order — re-seal enables re-measure. What remains of these rows is the vocabulary, which is 1.4's |
| 1.3 | **DONE, 16 September 2026, £0** — [ADR 0116](../adr/0116-a-report-is-superseded-never-replaced-and-exactly-one-is-current.md) lands: `reports.superseded_by`, `superseded_at`, `supersession_reason` with the partial unique index `reports_one_current_per_company` (migration 0075, which refuses to start on a company that already has two approved reports, naming it). Approving a second report on a company supersedes the first at the render step, with the reason recorded; the operator withdraws a wrong report from its page with a mandatory reason; `start_run` may run a request again once its report is withdrawn or superseded; a thesis points only at a current report; the report page carries the superseded or withdrawn band and the library the state. `tests/test_report_supersession.py` | `decisions.py` and `theses.py` already implement supersede-and-withdraw-with-a-reason; this copies that shape. It closes "which report is current", which four plans assumed and none owned. The `refreshed` reason waits for F4 |
| 1.4 | **DONE, 16 September 2026, £0** — the vocabulary ratchet, finished and asserted: the harness's `text` assertion is green on every constructible row, and so are the other two — 33 rows green, 19 unconstructed, `STILL_RED` empty. The console names steps and errors in words and links to the worker's status in settings instead of saying `just worker`; every pause message speaks its gate (`GateKind.spoken`) and none names `aer reseal`; the gate pages print section titles, provider names, proposer roles and assumption names (`GateKind.spoken`, `provider_words`, `proposer_words`, `concept_name` over `ASSUMPTION_WORDS`); the budget ceilings and the failed step's remedy link to the setting they name; the problem page leads back to the run. The report itself moved with it, because the review page prints the draft: the validators table and the coverage notice name metrics in words (`Metric.spoken`), the calculation footnote drops the function reference (the formula and the code version stay; the drill-down keeps the reference), and a valuation refusal names the risk-free rate, the beta and the equity risk premium rather than their keys. The goldens were re-recorded and every change in them is one of those four | The instrument decided the scope: the plan-gate rows were red on a *test fixture's* prose, and the final-gate rows on the document's own content, so both were fixed rather than exempted. Identifiers on pages the harness cannot yet construct (the sector and unmapped-concepts gates, a fired trigger's evidence lines) are asserted only once those rows are |
| 1.5 | **DONE, 16 September 2026, £0** — the unmapped-concepts gate shows at most twenty rows on its first screen, the payload's own ranking (largest share of the reference line first), and folds everything past them behind one collapsed row that says what it holds — how many at or above the 5% floor, how many below, how many with no figure to size. The verdict names both sides of the floor for the whole filing before the operator scrolls, and the share column names the line it is against (*Share of revenue*), which the extract step now records for new runs without moving any earlier run's sealed hash. Nothing is dropped: the fold is a disclosure over the same hashed rows, and the filter reaches into it and opens it on a match. `figures.unmapped_queue` is the cut, pure and tested; `tests/test_unmapped_gate.py` proves the page on a thirty-extension filing | "Skip them" is the gate's own approval — nothing on this page maps a tag; mapping is the curation worksheet's sitting (§2.8). The other long gate pages already fold their walls: the captured statement lines sit behind a disclosure, and the sector page's rows are per statement |
| 1.6 | **DONE, 16 September 2026, £0, in two halves.** **The risk-free rate.** `MacroClient` is built in the runtime bundle and handed to the executor with the other services; a new `acquire_macro` step, straight after `acquire`, fetches the filings' currency's documented series at the run's own as-of vintage, records it in the vintage store, converts the published percentage through the one traced conversion (persisted as a calculation the report can walk), and refuses a reading older than fourteen days — an earlier run's row is not this run's rate. The assumptions gate proposes it as a derived assumption under *a published series*, with the instrument, date, vintage and publisher in the justification, never confirmed in advance; every way of having no rate (no client, no key, no documented series for the currency, nothing at the vintage, a stale reading, a failed fetch) is a sentence in the step's record and the gate's own reason for asking. Per run, keyed by vintage: two runs sharing an as-of date write one set of rows, which settles the diagnosis's open question the cheap and correct way. `tests/test_macro_acquisition.py` (the service, and the FRED fixture replayed) and `tests/test_assumption_gate.py` (a driven run). **The equity risk premium** — a judgement no series carries — is the standing operator assumption of [ADR 0124](../adr/0124-a-standing-operator-assumption-is-a-stored-proposal-confirmed-at-every-gate.md), Accepted with the code the same day: a settings override (`standing_equity_risk_premium`, with its justification beside it, on the settings page and by environment variable) that is refused without a reason or outside the plausible band the gate applies, recorded in the audit trail with who set it and when, and proposed into every run that needs it under *you* (`operator:standing`) with the date and author appended to the justification — never confirmed in advance, never a macro observation, never an attestation. With none set the gate asks as before, and its reason now says where a standing one can be set. `tests/test_configuration.py` (the setting: the pair saved, cleared, refused; a value from the environment) and `tests/test_assumption_gate.py` (a driven run with a series and a standing premium reaches the gate with only the beta outstanding) | ADR 0082 already decided the acquisition shape, so no new record for the risk-free half; ADR 0124 is for the standing value only, and names the two wrong homes — the observations table and an attestation — so the next implementer does not reach for either. Sterling still refuses at `risk_free_series_for("GBP")` (ADR 0026), and the refusal now reaches the gate as its sentence |
| 1.7 | **DONE, 16 September 2026, £0** — `excluded_sources` is enforced in code at every place a run reads. A pure matcher (`core/exclusions.py`: a domain excludes itself and everything beneath it, on label boundaries, never by string suffix) answers one question for four callers. At acquisition, a document from an excluded domain — by the URL asked for or the one that answered after redirects — is quarantined with its own reason, `excluded_by_operator`, reported ahead of every other reason and recorded in the audit trail with the domain that bit; the service reads the list off the mandate row the work order shares an id with, so no acquisition path can forget it, and the sources page prints the reason in words. The research workers' `fetch_known_url` refuses a URL on an excluded domain before the archive is consulted and before the fetch layer is reached, naming the exclusion; `search_sources` never lists such a document; the web-search and full-text listings withhold hits on excluded domains and say how many; and the validator refuses a finding citing one even though its id resolves. The planner's prompt line stays as guidance; the enforcement no longer depends on it. `tests/test_exclusions.py` (the matcher), `tests/test_source_documents.py` (the rule and the record), `tests/test_research_workers.py` and `tests/test_web_search.py` (the executors) | Invariant 8, done the way the invariant says: enforced in code, so injected text cannot cause a fetch the operator forbade. Kept, not discarded, like every quarantine — "we fetched this and refused it because you said so" is the record that shows the exclusion was honoured — and the ADR 0111 override path applies to it like any other refusal, on the record |
| 1.8 | **DONE, 16 September 2026, £0** — the recovery path exercised once by hand on a full run, and then kept exercised. By hand: a fresh database and store, the migrations, a seeded operator, and the fake scene (Microsoft through the whole workflow against the suite's fake provider and stub filing client — the objects `audit.smoke` uses) driven inline to an approved report; then `aer backup` (schema 0075, 56 artefacts, 2.4 MiB, verified as written), `aer verify-backup` (56 artefacts and the dump match the manifest), `aer restore --yes` into a *second* database and a *second* store (56 artefacts verified on the way in), and the three questions `testing-by-hand.md` §17 asks of the restored side: `aer verify-artefacts` — 55 checked, all intact; `aer verify-audit` — 7 events, the chain intact; `aer replay-run` — 5 calculations, 15 citations, 3 artefacts and 27 model calls all still hold. The same three commands on the source gave the same answers, which is what a restore is for. Kept: `tests/test_backup.py::TestARunSurvivesTheRoundTrip` drives the same scene, backs up, restores into a different database and store, and asserts the artefacts hash, the chain links, the run reproduces with the source's own calculation count, and the report is still immutable — on every CI run, where `pg_dump` is on the runner | Half a session, as estimated. What the exercise did not cover, and says so: a backup of the *real* corpus, which does not exist here (Phase 1½ takes it the day the runs are re-seeded), and a restore across a schema revision, which the manifest records so a mismatch can be noticed and nothing yet refuses |

**Exit:** every inventory row has a browser-proved forward control; the harness is green on rows
that started red.

**Where the exit stands, 16 September 2026.** The list above is done, 1.0 to 1.8. The harness
is green on all three assertions for every row it can construct — 33 of 52, every one of which
started red — and `STILL_RED` is empty. The other 19 rows are not proved and are not claimed:
they are the harness's own backlog (`UNCONSTRUCTED` in `tests/journey_inventory.py`, each with
the fixture it waits for — the sector and unmapped-concepts gates the fake scene never raises,
the nine escalation triggers, an unverifiable citation, a bare `AerError` problem page), and
each becomes a proof the day its fixture exists. Phase 1½ begins with that understood.

**Phase 1½, first half — F1, DONE, 17 September 2026, £0.** ADR 0113 landed in the order
it asked for: the structural-absence scan first (`tests/test_point_in_time_is_gone.py`,
red), then the five enforcement layers — `sources/sec/pit.py` replaced by
`sources/sec/selection.py` (the latest filing's word on each period, the rest recorded as
superseded), the per-adapter bounding in the SEC and Companies House clients, the
quarantine's date rule, the claim-time comparison and the direct SQL filters in the
workflow — then the mode (migration 0076 drops `work_orders.point_in_time`; the request form
keeps only the undated-sources pair), the two metrics (`BLOCKING` ten → nine, `RUN_TIME`
eleven → nine, both with the comment rewritten), the trigger (§2.4's third row, and the
journey inventory is 51 rows: 33 green, 18 unconstructed, both halves agreeing), the three
prompt clauses (planner version 5, plan critic version 2, the writer's user turn), the
renderer's header line and coverage sentence with the three goldens re-recorded, the tests
(the corpus of dated documents is now `tests/publication_date_fixtures.py`, scored on the
extractor alone) and the behaviour documents. Three pieces the spec did not name went with
it — the validator's date-adjudication assist, a period bound in peer discovery, the
full-text search's split of hits at the date — and ADR 0113 records them. **Not done, and
said so:** ADR 0113's *every archived run still replays*, which needs the corpus. The second
half of the phase — keys, the QUICK run, the three re-seeded runs, `aer backup`, that
replay — is unchanged and waits on the operator's keys (D2).

## 6. Phase 2 — A bank can be researched

**ISSUE 1, blocking · 7–9 sessions · £8 live · 1 ADR**

**F19, the UK path, lands in Phase 4a** — a new half-phase between Phases 4 and 5, added on the
operator's decision of 14 September. It is not folded in here even though it touches the same
sector-classification code, because this phase is *blocking* and that one is not: a bank cannot
produce a report at all today, and a UK filer produces no report because it was never in scope.
Mixing a blocking fix with a scope addition is how a blocking fix slips.

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

## 8a. Phase 4a — A London listing can be researched

**Scope addition · 8–11 sessions · £8 live · 1 ADR (0121) · added 14 September 2026**

The product documentation has claimed "UK or US" since the first plan and a domestic London
listing cannot be researched at all. The operator's decision is to build the path rather than
narrow the sentence. Specified as **F19**; argued in **ADR 0121**.

| # | What | Notes |
|---|---|---|
| 4a.1 | **Dispatch `acquire` on the resolved registry** rather than on `sec_client` by name; record which registry answered on the request | A subject resolving in both registries is refused with both choices named — ADR 0093's dual-listing rule, applied to research |
| 4a.2 | **`CompaniesHouseClient.fetch_facts`** — accounts filings newest first, fetch and hash each, `extract_ixbrl` over each, union the facts, **four filings deep by default** | **The only piece that is not wiring.** Companies House publishes no companyfacts equivalent, so every UK fact is this platform's own parse of a document rather than a registry's aggregation |
| 4a.3 | **Seed UK SIC 2007 into the sector profiles**, with `companies.sic_scheme` to say which scheme a code belongs to | Skipping this reproduces M&T's 172.1% on the first UK bank: the code would match nothing, the gate would not fire, and a bank would take the standard model |
| 4a.4 | **The gilt yield as an operator-owned assumption**, sourced and confirmed at the gate; `risk_free_series_for` keeps refusing rather than substituting | An automated GBP series is open question 19 and a commercial check, not a design task |
| 4a.5 | **Invert the offline refusal test** and correct the product documentation's claim in the same change | The claim may not run ahead of the code, in either direction |

**Why it is affordable at all**: the Companies House client is complete with 32 tests, the fetch
policy allowlists both hosts, the rate limit was verified on 2026-09-04, the credential is wired,
the offline iXBRL extractor was built for UK filings, and `companies.company_number` already
exists with a constraint written for a CIK-less company. **No migration on `companies` is
needed for the identifier** — only `sic_scheme` is new.

**Exit:** a domestic London filer reaches an approved, rendered report with every figure traced
to its own accounts documents; a UK bank fires the sector gate; a sterling valuation carries a
sourced gilt yield or refuses.

**Risk, stated:** this is the first subject whose facts the platform extracted itself. A parsing
error here is a wrong number with a perfect audit trail — which is the failure `plausibility.py`
was written for after the last one. It gets the hardest tests in the plan.

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

## 10a. Phase 6b — The knowledge map learns what you decided

**Scope addition · 5–7 sessions · £0 live · 1 ADR (0122) · added 14 September 2026**

The same pattern as the judgement layer, found the same way: **already built, barely read.**
`../archive/knowledge-graph.md` says in its own words that the layer it planned is built — seven
node kinds, six edge kinds, an Obsidian projection, an in-app graph view, growing on its own as
the platform is used. It reads back into two places and knows nothing about what the operator
decided. Specified as **F20**; argued in **ADR 0122**.

| # | What | Notes |
|---|---|---|
| 6b.1 | **Four node kinds and five edges** — thesis, premise, decision, verdict — under the rule the map already runs on: only confirmed state produces an edge | A decision points at a **thesis version**, not a thesis. *What did I believe when I bought this?* is a different question from *what do I believe now* |
| 6b.2 | **The monitor traverses it**: a finding on one premise surfaces against every other position sharing its metric *and* a theme or sector | The connective tissue exists (ADR 0065); nothing traverses it on a finding. Deliberately narrow first cut — two holdings sharing "revenue growth" share almost nothing |
| 6b.3 | **Ask tier 1 reads it** — *"which of my holdings depend on the same premise?"* becomes free rather than research | The map **is** the record tier 1 answers from |
| 6b.4 | **Materiality becomes partly positional**: a section feeding a premise you hold is material at a smaller move than one feeding nothing you own | Sharpens F4 without changing its thresholds |
| 6b.5 | **The methodology library measures methods**, as `calc/outcomes.py` already measures assumptions | The difference between a library of methods and one that knows which worked |
| 6b.6 | **Prove it is evidence for nothing** — a seeded attempt to cite a premise is refused | A premise is an attestation (ADR 0073), so its lineage reaches no shareable surface; the vault stays one-directional |

**Why here.** It is last in the dependency order and cannot move earlier: there is nothing to
record until F9, F10 and F14 exist. It is placed after Phase 6 and before the verdict round so
that what it changes is in the documents the round judges.

**Exit:** a finding on one holding's premise surfaces against another that shares it; Ask answers
a cross-position question from the record for nothing; a seeded premise citation is refused; and
nothing is backfilled — the map starts empty on the judgement side and fills with use.

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
| 0 — ask first | 3 | ~7 | 4 (two documents read, one QUICK report) |
| 1 — the journey | 12–16 | 0 | 2 |
| 2 — the bank | 7–9 | 8 | 2 |
| 3 — self-contradiction | 10–12 | 0 | 1 |
| 4 — print what exists | 11–13 | 0 | 1 |
| 4a — the UK path | 8–11 | 8 | 2 |
| 5 — first signal | 2 | 21 | 8 |
| 6 — view and argument | 12–16 | 0 | 2 |
| 6b — the knowledge map | 5–7 | 0 | 1 |
| 7 — verdict and roadmap | 4 | 31.50 | 16 |
| **Total** | **74–94** | **~£80** | **~39** |

£36.68 of the audit's £100 remains, so the programme needs about £43 more, and Phase 5's gate
stands between the operator and most of it. The rise from ~£64 is Phase 4a (the UK path, £8) and
Phase 0's added QUICK run (£4). At four sessions a week this is three to four months;
the stop points are Phase 2 (a bank works), Phase 4 (the document stops lying about itself) and
Phase 5 (the evidence to continue or to narrow). **Phase 4a and Phase 6b are the two that can be
dropped without disturbing the sequence** — one adds a market and one adds connective tissue,
neither fixes a defect, and Phase 4a sits before the gate precisely so that dropping it is cheap.
Phase 6 loses two sessions because 0.1 showed the authored half of the view is asked for by
nobody, which pays for most of Phase 6b.

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
