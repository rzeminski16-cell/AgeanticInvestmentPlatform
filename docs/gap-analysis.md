# Gap analysis — what is missing, as of 2026-08-08

*Updated after A1–A4, then A21, B13 and A9, then B1, A19 and A20, then B2 and B3, and most
recently the test-suite health items A16–A18 and the data-lifecycle items A10–A12. Items struck through are done; the reasoning
is kept because a gap and the shape of its fix are worth reading together.*

*Since A16 was closed the whole suite runs in one process again — 4102 unit tests in 16m25s
and 75 browser tests in 2m33s, both green — so the figures quoted below are measured rather
than remembered.*

Written after the first end-to-end live runs, from the code rather than from the plan. The
phase specifications in `docs/PLAN.md` remain the authority on *scope*; this document is an
honest account of the distance between that scope and what a run actually does today.

Two lists, as asked: what is missing behind the surface, and what is missing in front of it.

---

## The finding that dominated both lists — now closed

**A large, tested deterministic layer existed that the live workflow never called.**

`src/aer/calc/` holds `statements.py`, `ratios.py`, `quality.py`, `wacc.py`, `dcf.py`,
`comps.py`, `fx.py`, `prices.py` and `bridge.py` — built through Phase 3, each with unit and
property tests. The production workflow is still `vertical_slice_v1`, and its `calculate`
step computes exactly one number: revenue CAGR.

```
{"job_id": "…", "count": 1, "names": ["cagr"], "event": "calculations.persisted"}
```

Everything else follows from that. The valuation page is empty because no run has ever
produced a discounted cash flow. The comparables page is empty for the same reason. The
balance-sheet, cash-flow, earnings-quality and DCF sections have one figure between them to
write from, so they write almost nothing even when the writer is working.

The services were reachable from the API and the web — `services/valuation.py`,
`services/scenarios.py`, `services/assumptions.py` all have routes — and **no workflow step
ran them**. A wiring gap, not a missing-feature gap, and the highest-value work in the
system at the time it was written.

`aer.services.analysis` closed it for the statements, the ratio suite and the
earnings-quality signals: a two-year scene now persists more than ten calculations where it
persisted one. **The valuation is a different kind of gap and stays open** —
`inputs_from` refuses to run without a confirmed assumption for every driver and scalar,
and says why: a terminal growth rate this platform chose "would be its opinion presented as
the operator's". So a DCF needs an operator working through the assumptions page, or an
agent that proposes assumptions — and that is a new role, which ADR 0035 says needs an ADR
before it needs code.

**That ADR is now written (0046) and two thirds of the answer is built.** Six of the eight
assumptions have a history in the filings the run already holds, so
`aer.services.assumption_proposals` derives them arithmetically with their basis stated.
The two that no filing answers — the perpetual growth rate and the exit multiple — come
from the `assumption_proposal` role, whose output contract has a field for each and no
other fields, whose bounds are checked in code, and whose out-of-band value is *refused*
rather than clamped. What remains for B2 is the gate that shows an operator every proposed
value with its justification, and the workflow step that runs the valuation once they have
confirmed them.

---

## List A — backend, safety, security, optimisation, operations

### Correctness and invariants

| # | Gap | Notes |
|---|---|---|
| ~~A1~~ | ~~**The workflow does not invoke the calculation suite**~~ | **Closed.** `aer.services.analysis` assembles statements, ratios and quality signals for each annual period into one ledger, and `calculate` runs it. One calculation a run became more than ten on a two-year scene. The DCF stays out deliberately: it refuses to run without confirmed assumptions, and an agent that proposed them would be a new role needing an ADR (0035). |
| ~~A2~~ | ~~**Unmetered model spend**~~ | **Closed.** The schema now goes to the wire as a dict, so the SDK sends it and skips the client-side parse: the stream always completes, the usage is always whole, and validation happens where a failure is a value already metered. `SpentButUnusableError` carries the bill and both payloads; `Agent.run` writes the `agent_runs` and `costs` rows before re-raising, with `stop_reason` marking them. |
| ~~A3~~ | ~~**The companyfacts quarantine**~~ | **Closed, and recorded as ADR 0044.** The aggregate takes the date of its newest component — the day it could first have existed — at 0.9 confidence to mark it derived. A current run gets a citable primary source; a historical run still quarantines it, now for a reason that is true. A side effect worth noting: the validator's date-adjudication call has disappeared from every run, because there is nothing left to adjudicate. |
| ~~A21~~ | ~~**A filing is fetched and only lightly read**~~ | **Closed, deterministically.** Forty paragraphs in document order is the cover page, the listing table and the transfer agent's address — all genuinely in the artefact, none of it citable by a research section. Two passes replace it: the document is cut at its statutory item headings (`Item 1`, `Item 1A`, `Item 7`, where the form *obliges* the prose to be), then paragraphs inside those items are scored on the vocabulary a research report uses and the best forty kept, emitted in the filing's own order. No model call, so the selection replays. A filing with no recognisable headings is read whole rather than not at all. |
| ~~A4~~ | ~~**One source per run**~~ | **Closed at the chosen scope.** `aer.services.filings` fetches the latest annual report and the recent current reports from the submissions index, inside the point-in-time window, each dated by its acceptance and excerpted so it can be cited. SEC full-text search, issuer-IR discovery and Companies House remain uncalled — see B13. |

### Security

| # | Gap | Notes |
|---|---|---|
| A5 | **No authentication** | `get_current_user` returns the first row of `users`. Correct for a local single-user tool; blocking for anything reachable from a network. Phase 6 keeps it behind a flag. |
| ~~A6~~ | ~~**The FRED API key is compromised**~~ | **Closed 2026-08-09.** ADR 0033. The operator rotated the key locally; the old one is dead. No code change was ever going to fix this one. |
| A7 | **No inbound rate limiting** | The token bucket protects outbound fetches. The web application has none. |
| A8 | **No production deployment story** | No `docker-compose.prod.yml`, no TLS or reverse-proxy configuration, no deployment guide. |

### Data lifecycle

| # | Gap | Notes |
|---|---|---|
| ~~A9~~ | ~~**No retention policy, no GC, no integrity sweep**~~ | **Closed, with a correction to the original claim.** `services/retention.py` did exist and did have no caller — but what it held was the *licensed purge* path, which answers a publisher's demand to destroy copies and is correctly idle while nothing licensed is in the store. The two sweeps a single-machine platform actually needs each week were simply absent, and are now there: `verify_store` re-reads every artefact and checks it still hashes to its name, and `collect_garbage` clears bytes no source document, report or agent run points at. `aer verify-artefacts` exits non-zero on a bad store so it can be a cron line; `aer gc-artefacts` reports and only deletes with `--delete`. Two traps closed on the way: a purged artefact is *expected* to be absent and is skipped rather than reported as loss, and every reference branch filters its nulls — `x NOT IN (…, NULL)` is never true, so one agent run with no archived response payload would have made the sweep return no orphans at all and look exactly like a clean store. |
| ~~A10~~ | ~~**No backups**~~ | **Closed.** `aer backup --to DIR` writes a `pg_dump` and the artefact store into one directory with a manifest hashing both — both halves or neither, since a database restored beside an empty store is a set of citations into nothing. A backup nobody has read is not a backup, so `aer verify-backup` re-hashes the dump and every archived file against the manifest and touches no database; it can run wherever the backup lives, which is where a restore is first attempted. `aer backup` runs it before reporting success and `aer restore` refuses without it. Credentials go to `pg_dump` through `PGPASSWORD`, never argv, because `ps` is world-readable — two tests hold that. The restore test restores into a scratch database and reads the rows back, so the source cannot be what it is reading. |
| ~~A11~~ | ~~**No audit-chain verification command**~~ | **Closed.** `aer verify-audit` walks the log in id order and stops at the first break, naming the event id so an operator can go and look at the row. Three things it had to get right: the log is paged, and the seam between two pages is the one place a break is invisible — the first record of a page has no predecessor in its own sequence — so `find_chain_break` now takes an anchor and the service carries it across; deleting the *beginning* of the log leaves every survivor self-consistent, so the first row is checked for a `prev_hash` it should not have; and an empty log reports as empty rather than sound, because "the chain is intact" printed against a truncated table is reassuring at the worst possible moment. Sabotage caught the page-seam test not testing the seam — deleting a row shifts every later row up a place. |
| ~~A12~~ | ~~**No run-level replay**~~ | **Closed.** `aer replay-run <job-id>` re-derives a run from its own record on four legs, each able to fail it alone: calculations re-execute and match, citations still find their excerpts in the artefacts, artefacts still read back by hash, and every model call still has both halves of its exchange archived. Nothing is fetched and no model is called — reproduction is a question about the record, not about the world, so it costs nothing and answers the same in a year. The fourth leg is the one that looks optional: an `agent_run` whose archived response has gone leaves prose in the report with no accounting behind it. Sabotage found the artefact leg was only passing because deleting a cited filing also fails the citation check; the test now uses an artefact nothing quotes, so only that leg can be the cause. |

### Observability and cost

| # | Gap | Notes |
|---|---|---|
| A13 | **No Langfuse, no OTel/Grafana** | Structured JSON logs only — which is why debugging a live run means reading worker output by hand. |
| A14 | **No prompt caching** | `cache_control` appears nowhere in the codebase. `Agent.composed_system_prompt` orders the platform contract first *because* caching keys on a stable prefix, and then never asks for the cache. On Opus at high effort this is real money. |
| A15 | **No cost dashboard, no cache-hit measurement** | Phase 6's cost optimisation pass has no baseline to measure against. |

### Test-suite health

| # | Gap | Notes |
|---|---|---|
| ~~A16~~ | ~~**`tests/test_extraction.py` deadlocks in a full-suite run**~~ | **Closed — ADR 0047.** One line of test code, not a threading interaction: the memory-cap test called the child-only `_apply_memory_cap(1 << 30)` in the *pytest* process, and it sets `RLIMIT_AS` soft **and hard**, which an unprivileged process can never raise again. The session was capped at 1 GiB permanently; once its address space passed that (`VmSize: 1225312 kB` by the sandbox tests) every `mmap` failed, so `pthread_create` could not get a stack — `start_new_thread` returned, `/proc` showed `Threads: 1`, and `Thread.start()` blocked for ever. It wedged one statement *before* `_run_child` armed its parse timeout, so a memory limit presented as an unkillable hang. The assertion now runs in a subprocess and also checks the cap took effect. Worth recording that an earlier fix attempt blamed OpenBLAS and looked convincing on a two-file reproduction — it was only keeping that short run under the cap. |
| ~~A17~~ | ~~**DB-touching CLI tests contend for locks**~~ | **Closed.** `tests/db_cleanup.py` empties the database by deleting in reverse `Base.metadata.sorted_tables` order — dependency-sorted parents-first, so reversed is a safe deletion order — instead of taking the table lock a `TRUNCATE` needs. The transactional fixtures and a command that opens its own engine can now both be in flight without deadlocking. One trap found while writing it: the first version also deleted the reference data the migrations seed, and the twelve failures that caused surfaced in `test_section_writer.py`, a file with no visible connection to the change. `SEEDED_BY_MIGRATIONS` names `section_definitions` and `sector_profiles` and leaves them alone. |
| ~~A18~~ | ~~**The fake provider does not enforce schemas**~~ | **Closed, and it immediately earned its keep.** `FakeProvider` now refuses what the API would refuse: an unknown effort level, an unsendable request, and — through `_validated` — a scripted reply the declared contract does not permit, serialised and re-validated exactly as a real reply is. `ScriptedResponse(..., unchecked=True)` is the named, per-response opt-out for the one test that must inject an impossible reply to exercise a consumer's own defences. `tests/schema_guard.py` and `tests/test_fake_fidelity.py` hold it. **The first thing it caught was a production bug**, not a test bug: a skill declaring `{"type": "number"}` gets a float once the reply is validated into the pinned contract, so `8` arrives as `8.0` while the claim carrying its lineage reads "8 years" — and `unsourced_numerals` compared spellings, refusing a properly sourced section. `aer.providers.anthropic` had always validated this way; only the fake had been passing scripted ints through untouched, so no test could see it. `numerals_in` now canonicalises trailing fractional zeros on both sides. |

### Data sources

| # | Gap | Notes |
|---|---|---|
| ~~A19~~ | ~~**Bank of England macro adapter not built**~~ | **Closed differently from how it was framed, and the framing was the error.** The BoE adapter is not "not built" — it is *determined against*: ADR 0026 found the Bank documents a CSV download route for programmatic use and disallows that same route in its own `robots.txt`, and reaching it through the unlisted viewer path would be circumventing a stated restriction. So the euro is the pivot instead. `aer.sources.macro.ecb` reads the ECB's daily reference rates through a documented API with no such conflict, and because every ECB rate has the euro on one side, a GBP/USD rate is a **cross** — `aer.calc.fx.cross` divides two published figures as a *traced calculation*, so a derived rate never looks published. ADR 0045. **The GBP risk-free rate is still missing** and this did not fix it: the UK proxy is a gilt yield, which is BoE data, so `risk_free_series_for("GBP")` still refuses rather than discounting sterling at a US Treasury yield. |
| ~~A20~~ | ~~**EODHD unresolved**~~ | **Resolved: ADR 0030 route 2** — keep the personal subscription, build for internal use only, accept explicitly that a future commercial version needs a different licence. Most of what the route required was already built by the tasks that followed the ADR: the comps table returns a `WithheldComps` with *no rows* to a shareable audience (0034), exportable and internal charts are separate functions with a render-time refusal, the corrected licence note is pinned, and the weighted daily-call ledger reserves before the request. **One thing was missing and it was the load-bearing one:** `purge_provider` had no caller anywhere in `src/`, so the deletion the agreement requires within a month of the subscription ending could be performed only by writing Python at a REPL. `aer purge-licensed` is that caller, and a test over the policy table fails if a second paid feed is ever added without one. The derived-data question was then **settled by operator determination on 2026-08-09** (ADR 0030, amended): figures computed from the data may be published, on the operator's reading of the executed agreement rather than on an inference from the public terms — and the licence note says which. The permission does not extend to the series itself or a chart of it, so the price chart stays internal. The withholding machinery survives the decision that opened it, defaulting closed, so the next paid feed inherits nothing. |

---

## List B — features, from the operator's seat

### Broken or absent in what already exists

| # | Gap | Notes |
|---|---|---|
| ~~B1~~ | ~~**No way to delete or archive a research request**~~ | **Closed, with both verbs.** Archive is one click on the list, destroys nothing and is undone by one more; it is accepted whatever state the request is in, which is the case `delete_request` had to refuse. Remove is a confirmation page listing what would be destroyed by table, what survives, and what the run cost — then deletes the request and everything derived from it, walking the schema's own dependency sort. The audit chain, the spend ledger and the content-addressed artefacts all survive, and a purge is refused when a later run's claims cite evidence this one gathered, which is the common case for a company researched twice. |
| ~~B2~~ | ~~**Valuation, DCF, scenarios and sensitivities never appear in a run**~~ | **Closed.** Six assumptions are derived from the filings with their derivation stated, two are proposed by the ADR 0046 role under bounds that refuse rather than clamp, and the three the discount rate decomposes into are named as outstanding with a reason — or proposed, in beta's case, once prices are acquired. A conditional `ASSUMPTIONS` gate stops the run only when the set is complete, because a gate an operator cannot clear is a run that pauses and never resumes; the create route is what lets them clear it. The `value` step then runs the WACC, the DCF, the scenarios and two sensitivity grids, every figure a recorded calculation. |
| ~~B3~~ | ~~**Comparables never appear**~~ | **Closed at the reachable scope.** `acquire_prices` fetches the subject's series and its market's, stores both as hashed artefacts under the licensed tier, computes a market capitalisation and proposes a beta — which is what makes B2's gate reachable without an operator typing one. The market proxy is a documented decision per exchange and an undocumented exchange is refused, because a London share regressed against the S&P looks exactly like one regressed against the All-Share. `comps` then builds the table. **It is thin, and honestly so:** a peer's multiple needs that peer's filings and prices, this workflow acquires neither, so every confirmed peer is excluded *by name with the reason*. Acquiring peer data is a larger job than B3 and is Task 30's. |
| ~~B4~~ | ~~**A run reads one filing**~~ | **Closed.** A run now reads the annual report and the recent current reports as well as the aggregate. Whether the recent-developments worker does better with them is the next live run's question. |
| ~~B13~~ | ~~**No full-text search**~~ (IR discovery deliberately deferred) | **Half closed, on purpose.** `search_filings_full_text` is now a worker tool: the model supplies the phrase, code supplies the CIK and the as-of bound, and hits come back as metadata — form, date, URL — so reading one still costs a `fetch_known_url` call and the twelve-call budget keeps meaning something. Hits after the as-of date are counted and named rather than dropped, because "found nothing" and "found things you may not read" call for different next moves. **Issuer-IR discovery stays uncalled by choice**: `aer.sources.issuer` holds that no code path learns a domain from a page and then fetches it, and a discovery step is exactly that path with a search engine in the middle. |
| ~~B5~~ | ~~**Charts have nothing to plot**~~ | **Closed by A1**, at least in principle: a run now records a multi-period ledger, so the revenue-and-margin history has a series behind it. The scenario and sensitivity charts stay placeholders until the valuation does. |

### Not built (Phase 6)

| # | Gap | Notes |
|---|---|---|
| B6 | **Settings screen** | Change models, budgets and methodology without editing `.env`. |
| B7 | **Cost dashboard** | Spend per run, per role, per model, against the monthly cap. |
| B8 | **"Reproduce this run" button** | Depends on A12. |
| B9 | **Watchlists and scheduled runs** | APScheduler in the worker. |
| B10 | **Skill export/import with a confirmation diff** | Threat T20, plus a starter library of example custom sections so the feature is not a blank page. |
| B11 | **Provider and model configuration UI** | Overlaps B6. |

### Deliberately out of scope

| # | Item | Notes |
|---|---|---|
| B12 | **Multi-company and portfolio views** | Named here so it stays a decision rather than an oversight. `docs/PLAN.md` keeps the platform to one report at a time. |

---

## Corrections to the task record

`docs/PLAN.md` Stage 4's task list and the working task board disagree with
`docs/phase-3-plan.md`, which records outcomes against each task. The phase plan is right:

* **Task 26 (cost of capital)** — done. `src/aer/calc/wacc.py`.
* **Task 27 (the discounted cash flow)** — done. `src/aer/calc/dcf.py`, `services/valuation.py`.
* **Task 28 (sector enforcement)** — done.
* **Task 25 (macro with vintages)** — ALFRED and ONS done. The Bank of England is not
  outstanding but *refused* (ADR 0026); the euro reference rates replace it as the FX
  source (ADR 0045), and the GBP risk-free rate remains genuinely open.

Tasks 29–32 remain genuinely outstanding.

---

## Suggested order

**A1 to A4, A21, B13, A9, B1, A19, A20, B2 and B3 are done, and the whole test-suite health
section — A16, A17 and A18 — is now closed.** That last one is worth a sentence of its own:
until A16 was fixed the suite could not complete in one process, so every "verified" claim
in this document rested on a subset. It completes now, which is what makes the rest of these
entries checkable.

What that leaves, in the order it is worth doing:

1. **A14 — prompt caching.** The composition is already ordered for it and never asks for
   it. The cheapest remaining saving by a distance, now that A2 means the meter can show
   what it saves.
2. **The valuation path.** Not a line above, because it is a design question rather than a
   gap: a DCF needs confirmed assumptions, so it needs either an operator working through
   the assumptions page or a proposing agent — and the second needs an ADR before it needs
   code.
3. The rest of Phase 6 in the order `docs/PLAN.md` gives. **A10 to A12 are done** —
   backups with a verifier and a working restore, audit-chain verification, and run-level
   replay — which closes the whole data-lifecycle section. What A9 made pressing is now
   answered: the sweep can tell an operator the store has lost a document, and there is
   something to restore it from.
