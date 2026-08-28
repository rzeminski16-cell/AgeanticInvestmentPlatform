# The remaining work — one plan

*Written 2026-08-28, on trunk `0d1e39f`. [`ROADMAP.md`](ROADMAP.md) stays the authority on
scope and its order is the operator's; this document lays everything still open end to end,
with what each item concretely needs, what blocks it, and who can move it. Where this and the
roadmap disagree, the roadmap wins; the ADRs outrank both. Every code claim below was
re-verified against the tree on the date above, not carried over from an older document.*

---

## Where the tree stands, verified today

Three roadmap items closed today: §2.3 and §3.15 together as ADR 0090 (pause, step and resume
are the same job), §3.13 as ADR 0091 (the critique-and-revise loop), §3.14 as ADR 0092 (the
web-search tool). The interface overhaul (§3.12) is **five tranches into ten** — further than
the roadmap's own §3.12 entry says, which still reads "not built" from 2026-08-25.

**And the trunk is red.** Tranche 5 merged code-complete with **thirty-three browser tests
failing** — stale text assertions against ten rewritten templates, plus one genuine design
question — and the in-process suite has not been run in full since tranche 5 landed. The
failure list the [handover](interface-overhaul-handover.md) promised to append was never
appended; it has to be reproduced from a clean run. The ramp ratchet stands at **1,594** of
the original 1,837.

The state of every open item, as the code actually has it:

| Item | What the tree says |
|---|---|
| §2.1 five sections fail to draft | Untouched. Blocked on data only the operator holds — `scripts/list-runs.sql` and `scripts/export-run-diagnosis.sql` are written and waiting to be run against the operator's database |
| §2.2 section confidence | The 0.30 is `min(chosen, 0.3)` on the degraded path (`sections/evidence.py:992`) — a **cap, not a floor**. An undeclared confidence defaults to 0.5. Three sections at exactly 0.30 means three degraded packs, which is §2.1's territory |
| §2.4 report document layout | Untouched. The surface is `src/aer/render/` — WeasyPrint's print stylesheet, deliberately outside the overhaul |
| §2.6 splits | `corporate_actions` holds `split_ratio`; only the price series consumes it (`calc/prices.py`). `TransactionKind` has six members and no split; nothing anywhere turns a split into a holdings change |
| §2.7 R18 | The dangerous mapping does **not** exist — no `ShareBasedCompensation…RiskFreeRate` tag maps anywhere. But `core/concepts.py` has no never-map mechanism either: an absent tag is indistinguishable from a not-yet-mapped one, so nothing stops the mapping being added in good faith later |
| §2.8 A55 | The gate machinery is built (`_unmapped_rows`, ranked by share of revenue); the curation itself is untouched and is judgement, not code |
| §3.1 third door | `record_acquisition` still requires a `ResearchRequest` (keyword-only, `services/acquisition.py:63`). `work_orders` has no `kind` column — it does carry `tool` and `subject_kind`. `add_listing` exists nowhere but the roadmap. The form refuses an unknown ticker with prose and has no path to create a `Security` |
| §3.3 migration step 4 | **25** `session.get(ResearchRequest, …)` call sites across 11 files — the roadmap's ~20 has grown; `web/pages.py` alone holds 11 |
| §3.4 bank-model sensitivity | The gap is stated twice in code (`residual_income_run.py:21-26` and the `_NO_SCENARIOS_CAVEAT` at line 85). The DCF reference is **two 5×5 grids** (WACC × terminal growth, WACC × exit multiple, `valuation_run.py`, `SENSITIVITY_POINTS = 5`) — the roadmap's "81-cell grid" is the 9×9 ceiling nothing constructs |
| §3.5 conviction guard | `RESERVED_OUTPUT_FIELDS` has six members; `conviction` is not among them |
| A5 / A7 / A8 | Confirmed all three: `get_current_user` returns the first `users` row (`api/deps.py:94`); the only rate limiter is the outbound fetch bucket; the only compose file is local dev with ports bound to `127.0.0.1`. An `is_production` flag exists and gates only `/docs` |

---

## The order

Six phases. The roadmap's order is priority, not serialisation: 1A waits on the operator, so
1B and 0 run meanwhile without skipping ahead — §3.1 is second on the operator's own list, and
phase 0 restores a baseline rather than building anything.

| Phase | What | Roadmap items | Gated on |
|---|---|---|---|
| **0** | Back to green | the overhaul's red suite | Nothing. First |
| **1A** | The drafting failure, diagnosed then fixed | §2.1, §2.2 | `run-diagnosis.json` from the operator |
| **1B** | The portfolio's third door | §3.1 | An ADR amending 0072 — writable now |
| **2** | The overhaul, resumed | §3.12 tranches 6–9, closing §2.5 | Phase 0 green; tranche 8 wants §3.1 landed |
| **3** | The document and the data fixes | §2.4, §2.6, §2.7, §2.8 | Independent of phase 2 — disjoint surfaces |
| **4** | Portfolio depth | §3.2, §3.3, §3.4 | §3.2 wants tranche 8's layout |
| **5** | The judgement layer | §3.5 → §3.11 | Strict dependency order; §3.3 before §3.6 |
| **6** | Before it leaves one machine | A5, A7, A8 as one gate | Only when leaving one machine is actually intended |

---

## Phase 0 — back to green

Nothing that touches a template can be trusted until this is done, and neither can anything
else: a red suite cannot tell a new failure from an old one.

1. **Reproduce the thirty-three.** The handover's failure list was never captured; run
   `pytest tests/e2e` and hold the output. (In a remote session that means a local PostgreSQL
   and Redis first, as tranche 0 did — there is no Docker daemon there, and without a database
   the default suite silently skips 1,849 tests.)
2. **Fix the stale assertions** to tranche 5's intended wording — checking each new wording is
   *intended*, not merely what renders.
3. **The one real design question:** the seven planned tools sit behind a closed disclosure on
   the front door (`index.html:121`), which contradicts that page's stated job — the shape of
   the product visible on arrival. The handover's proposed correction is right and cheap: the
   `disclosure` macro already takes `open` (`_ui/controls.html:174`). Ship it open.
4. **Run both suites in full**, two processes as always (`pytest --ignore=tests/e2e`, then
   `pytest tests/e2e`), and update the handover: failure list discharged, baseline restated.

**Exit:** both suites green on trunk, recorded in the handover. The tranche-5 lesson becomes
standing practice from here: a tranche that touches templates is finished when the browser
suite has been *seen* green, before the commit — the twenty minutes is part of the work.

---

## Phase 1A — the drafting failure (§2.1, §2.2)

Top of the roadmap, and blocked on data rather than effort: the diagnosis is pinned to the
2026-08-24 MSFT run, whose rows live on the operator's machine.

1. **Operator:** two read-only commands, in `scripts/README.md` — list the runs, export the
   failed one to `run-diagnosis.json`, read the file before sending it. This is the single
   highest-leverage operator action open.
2. **Read the export back** — per failed section: attempts, evidence tally by kind, refusal
   reason in the producer's own words, stop reasons and token counts per model call, pack
   size. The instrumentation (§4.6) records all of it.
3. **What the code already admits, to test against the data** rather than assume:
   - `validate_draft` checks only the 1.25× word ceiling — **there is no minimum**, so a thin
     draft passes unchallenged and a starved pack becomes a thin section or a refusal, never a
     retry (`sections/evidence.py:852-860`).
   - A truncation retry raises the token ceiling to 32,768 **and halves the word budget**
     (`writing.py:136-153`) — one plausible mechanism for "a retry that swings past the
     target", in either direction.
   - `MAX_GENERATION_ATTEMPTS = 2`: one retry, ever.
4. **Scope the fix only after the read-back** — the repository's own rule. The likely shape is
   evidence-pack assembly (why five packs starved) plus retry targeting (what a second attempt
   is told), in `sections/` — but the export decides.
5. **§2.2 rides on the same file.** Structurally settled today: 0.30 is the degraded-path cap,
   0.5 the undeclared default. The data answers which degradation fired; the remaining
   judgement is whether a cap that flattens every degraded section to one number communicates,
   or whether the reason belongs on the surface instead. Read first, change second.
6. **Confirmation is one live run** — model spend, operator-approved — which also exercises
   the new critique-and-revise loop (ADR 0091) against the same sections for the first time.

---

## Phase 1B — the portfolio's third door (§3.1)

Decided 2026-08-25: a work order roots the book's own acquisitions. Nothing here waits on
anyone. Four steps, in order:

1. **The ADR amending 0072** — a run and a book acquisition distinguishable in the table
   rather than by inference. The material fact the ADR works with: `work_orders` already
   carries `tool` (default `"research"`) and `subject_kind` (default `"company"`), so the
   decision is whether those two are the distinguisher or a new `kind` column is — not whether
   one is needed.
2. **`record_acquisition` reads point-in-time off the work order** instead of requiring a
   `ResearchRequest` (`services/acquisition.py:63`). A book acquisition is inherently not
   point-in-time — today's close is the point.
3. **`add_listing(session, ticker, exchange, client)`** — resolve the symbol, fetch a short
   window of bars, refuse with the vendor's reason where it returns none, record the artefact,
   the security and the bars under the book's work order. Invariant 1 intact by construction;
   `price_bars.source_document_id` keeps meaning what ADR 0031 says it means.
4. **The form's third door** — `_resolve_security` (`web/portfolio/pages.py:308`) stops
   refusing the unknown with prose and verifies it once, at first sight: dealable, or refused
   with why. The dual-listing refusal and the empty-box-is-cash rule stand unchanged.

**Land before tranche 8**, which rewrites this form — the plan already counts on it.

---

## Phase 2 — the overhaul, tranches 6–9 (§3.12, closing §2.5)

Resume exactly where the [handover](interface-overhaul-handover.md) says, on a green base.

- **Tranche 6 — console and the seven gates.** The largest tranche and the largest redesign,
  and the one piece of new model plumbing left: the `verdict` role and its step under ADR
  0087 — registered, tool-less, closed schema, routed and capped, writing the review gate's
  authored half once when the draft freezes. Building it needs no model spend; the fake
  provider covers the tests. It touches the workflow engine, which is why phase 0 precedes it.
  Do not flatten the gates into one another; the assumptions gate's three-forms-per-row must
  not nest inside the decision form.
- **Tranche 7 — evidence and reports.** Verdict-first evidence surfaces, the excerpt at the
  visual centre, the deterministic sensitivity figure with table equivalence, replay's typed
  verdict. This is also where a handler builds a `RenderedFigure` from a `LineageNode` — the
  one gap tranche 1 left open by design.
- **Tranche 8 — portfolio, skills, knowledge.** Carries §3.1's third door rather than
  reinventing it. **Leave room for §3.2 and ship no placeholder chart.**
- **Tranche 9 — removal and hardening.** The ratchet reaches zero and becomes a hard
  assertion; the legacy aliases go; the by-hand pass — keyboard, 320px, 200% zoom, both
  schemes, scripting off — happens here and nothing substitutes for it. Closing this closes
  **both §2.5 and §3.12**.

Two loose ends ride along: the **Firefox half of D7** (one run on a machine with Firefox —
the operator's, most likely; the risk is structurally low but unverified), and the two
order-dependent test failures the testing plan records and deliberately leaves alone.

---

## Phase 3 — the document and the data fixes (§2.4, §2.6, §2.7, §2.8)

Independent of phase 2 — `src/aer/render/` and the extraction layer are surfaces the overhaul
deliberately does not touch — so these interleave wherever there is capacity.

**§2.4 — the report document.** Rework the disagreement appendix as prose blocks per
disagreement; fix the at-a-glance tables so label and value stop rendering as stacked blocks;
then check every section's print layout against a real run's document, not a fixture. Pairs
naturally with commercial check 5 — validating WeasyPrint's native dependencies on the target
Windows machine — since both want the operator generating a real PDF where they actually run.

**§2.7 — R18.** Small and self-contained, and the exploration sharpened it: the wrong mapping
does not exist, and neither does anything that would stop it existing. Add a never-map table
to `core/concepts.py` with the reason held beside each entry, pin the
`ShareBasedCompensation…RiskFreeRate` family there, and make `canonical_concept` (or its
tests) refuse the entry ever appearing in an alias table. A denied tag should also read as
*denied* at the unmapped-concepts gate, not as not-yet-mapped — that distinction is the whole
point of the mechanism.

**§2.6 — a split arrives as a transaction.** `TransactionKind` gains a split kind — a schema
decision with a shape question (a ratio is not a quantity), so a short ADR, the more so given
a currency-exchange kind was explicitly decided against until its row shape was safe. Then a
derivation: from a `corporate_actions` split row to a transaction on each affected holding,
quantity multiplied, **cost pool unchanged** (ADR 0085 — a split is not a purchase), never a
bare quantity that changed with nothing behind it.

**§2.8 — A55 concept-map curation.** The mechanism exists; the work is judgement over
accounting semantics. Preparable without the operator: a worksheet from the gate's own ranked
rows across the filings already held — tag, label, largest figure, share — with a proposed
mapping or a proposed never-map per row. The operator decides; a session encodes the decisions
into the alias tables. Batched sessions, not one heroic pass.

**§2.9** stays what it is: a standard enforced in review whenever a new refusal path grows a
placeholder, not a work item.

---

## Phase 4 — portfolio depth (§3.2, §3.3, §3.4)

**§3.2 — return and exposure.** After tranche 8's layout exists to hold it. Time-weighted and
money-weighted return over a value series walked from transactions and price history — flows
are flows, never gains — and concentration by holding, sector, currency and listing country
that reports what it knows and names what it does not. Derived on the way to the page under
ADR 0083, nothing stored, every figure carrying the weakest grade beneath it.

**§3.3 — work-order migration, step 4.** First make the 25 `session.get(ResearchRequest, …)`
call sites optional mandate reads — 11 of them in `web/pages.py` — because a monitor run will
have no request; then the drop revision (`jobs.request_id`, `approvals.request_id`,
`source_documents.request_id`, the duplicated columns). Staged so the downgrade stays lossless
while the columns still hold the data, exactly as ADR 0072 planned. **This is a prerequisite
for §3.6**, not just tidying.

**§3.4 — sensitivity for the bank model.** Mirror the DCF's real shape — two 5×5 grids from
`SENSITIVITY_POINTS`, not the mythical 81 cells — over the residual-income model's own axes
(cost of equity against the terminal assumption being its natural pair). Property-based tests
as `calc/` demands; retire `_NO_SCENARIOS_CAVEAT` only when the grid genuinely exists; correct
the roadmap's 81-cell line while closing the item.

---

## Phase 5 — the judgement layer (§3.5–§3.11)

The order is forced by dependency and the roadmap already says so. What each first item needs:

1. **§3.5 judgements and theses** (ADRs 0074, 0079 — already decided, so this is build, not
   decision). A thesis storable without becoming evidence; and `RESERVED_OUTPUT_FIELDS` gains
   `conviction` with its attack file — six members today, and this is the seventh.
2. **§3.6 the thesis monitor** (ADRs 0078, 0079) — raises questions, answers none. Needs §3.5,
   and needs §3.3's optional mandate reads, because a monitor run has no research request.
3. **§3.7 decisions and the trade journal** — written before the outcome is known.
4. **§3.8 post-trade review and analytics** (ADR 0081) — scored against process, never P&L.
5. **§3.9 portfolio risk** (ADR 0080) — commented, not scored; its rate prerequisite is met.
6. **§3.10 watchlist** — wants the standing budget and the two clocks (ADR 0075) designed
   first; a standing budget is a new thing, not one run's cap renamed.
7. **§3.11 the methodology library** — three versioned, pinned, composed `SkillKind`s. Quietly
   more valuable since today: ADR 0091 made an operator-authored methodology skill **the only
   route by which a recorded lesson reaches a future run**, so the library is now the missing
   half of the critique loop's memory, not just a nice-to-have.

Do not fold any of these forward. Each is small enough to mis-build by starting it inside its
predecessor.

---

## Phase 6 — before it leaves one machine (A5, A7, A8)

One gate, three parts, unchanged and confirmed: first-row authentication, no inbound rate
limiting, no production deployment story. **Deliberately unscheduled** — none of it is needed
for a personal tool on a laptop, all of it is needed before anything else, and shipping any
one alone buys nothing. The trigger is intent to run anywhere that is not the operator's
machine, and the moment that intent exists this becomes the whole of the next phase. Note
multi-user deployment stays decided against; A5 is single-operator authentication, not a user
system.

---

## What only the operator can move

In leverage order. Everything else in this plan is a session's work.

1. **Export the run diagnosis** — two read-only `psql` commands from `scripts/README.md`,
   then hand over `run-diagnosis.json` (read it first). Unblocks §2.1 and §2.2, the top of
   the roadmap.
2. **Approve one confirmation run's spend** when the §2.1 fix lands.
3. **The peer-discovery decision** (§4.15's remnant): `propose_peers` currently buys a
   reasoned peer list that can contribute no figure. Skip it when no price client is
   configured, or amend ADR 0059 and acquire peer data so comps compute — the second
   multiplies the data subscription across the peer set. Either is fine; it should be chosen,
   not inherited.
4. **Concept-map curation sittings** (§2.8) over the prepared worksheet.
5. **Commercial checks** (roadmap list): EODHD's licence terms **in writing** (operator);
   WeasyPrint's native dependencies on the target Windows machine (operator, alongside §2.4).
   The Companies House rate limit and Langfuse's current self-host licence are verifiable
   against primary sources by a session with web access — delegable on request.
6. **One Firefox run** for D7's second engine, and **tranche 9's manual pass** when it comes.

---

## What this plan deliberately does not contain

The decided-against list in the roadmap stands untouched — trade execution, an optimiser,
multi-user deployment, a positions table, the Bank of England adapter and the rest. Nothing
here reopens any of it, and nothing here needs to.

---

**See also:** [ROADMAP](ROADMAP.md) · [the overhaul plan](interface-overhaul.md) ·
[the handover](interface-overhaul-handover.md) · [the decision records](../adr/)
