# Gap analysis — what is missing, as of 2026-08-08

*Updated after A1–A4 were closed. Items struck through are done; the reasoning is kept
because a gap and the shape of its fix are worth reading together.*

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

---

## List A — backend, safety, security, optimisation, operations

### Correctness and invariants

| # | Gap | Notes |
|---|---|---|
| ~~A1~~ | ~~**The workflow does not invoke the calculation suite**~~ | **Closed.** `aer.services.analysis` assembles statements, ratios and quality signals for each annual period into one ledger, and `calculate` runs it. One calculation a run became more than ten on a two-year scene. The DCF stays out deliberately: it refuses to run without confirmed assumptions, and an agent that proposed them would be a new role needing an ADR (0035). |
| ~~A2~~ | ~~**Unmetered model spend**~~ | **Closed.** The schema now goes to the wire as a dict, so the SDK sends it and skips the client-side parse: the stream always completes, the usage is always whole, and validation happens where a failure is a value already metered. `SpentButUnusableError` carries the bill and both payloads; `Agent.run` writes the `agent_runs` and `costs` rows before re-raising, with `stop_reason` marking them. |
| ~~A3~~ | ~~**The companyfacts quarantine**~~ | **Closed, and recorded as ADR 0044.** The aggregate takes the date of its newest component — the day it could first have existed — at 0.9 confidence to mark it derived. A current run gets a citable primary source; a historical run still quarantines it, now for a reason that is true. A side effect worth noting: the validator's date-adjudication call has disappeared from every run, because there is nothing left to adjudicate. |
| A21 | **A filing is fetched and only lightly read** | Forty paragraphs in document order: enough for a section to cite, and not a reading of the filing. Choosing passages by relevance would get far more out of the same document and the same fetch. |
| ~~A4~~ | ~~**One source per run**~~ | **Closed at the chosen scope.** `aer.services.filings` fetches the latest annual report and the recent current reports from the submissions index, inside the point-in-time window, each dated by its acceptance and excerpted so it can be cited. SEC full-text search, issuer-IR discovery and Companies House remain uncalled — see B13. |

### Security

| # | Gap | Notes |
|---|---|---|
| A5 | **No authentication** | `get_current_user` returns the first row of `users`. Correct for a local single-user tool; blocking for anything reachable from a network. Phase 6 keeps it behind a flag. |
| A6 | **The FRED API key is compromised** | ADR 0033. Needs rotating by the operator; no code change will fix it. |
| A7 | **No inbound rate limiting** | The token bucket protects outbound fetches. The web application has none. |
| A8 | **No production deployment story** | No `docker-compose.prod.yml`, no TLS or reverse-proxy configuration, no deployment guide. |

### Data lifecycle

| # | Gap | Notes |
|---|---|---|
| A9 | **No retention policy, no GC, no integrity sweep** | `services/retention.py` exists; nothing calls it. The artefact store grows without bound. |
| A10 | **No backups** | Of the database or of the artefact store. |
| A11 | **No audit-chain verification command** | The chain is written on every event and nothing ever checks it. |
| A12 | **No run-level replay** | `aer.eval.replay` replays *calculations* inside the evaluation suite. "Reproduce this run" does not exist. |

### Observability and cost

| # | Gap | Notes |
|---|---|---|
| A13 | **No Langfuse, no OTel/Grafana** | Structured JSON logs only — which is why debugging a live run means reading worker output by hand. |
| A14 | **No prompt caching** | `cache_control` appears nowhere in the codebase. `Agent.composed_system_prompt` orders the platform contract first *because* caching keys on a stable prefix, and then never asks for the cache. On Opus at high effort this is real money. |
| A15 | **No cost dashboard, no cache-hit measurement** | Phase 6's cost optimisation pass has no baseline to measure against. |

### Test-suite health

| # | Gap | Notes |
|---|---|---|
| A16 | **`tests/test_extraction.py` deadlocks in a full-suite run** | Pre-existing: the parse sandbox's child and pytest's threads block each other, and the timeout does not fire. It must be run alone, so CI covers it separately from everything else. |
| A17 | **DB-touching CLI tests contend for locks** | The transactional fixtures and a command that opens its own engine and really commits will deadlock on a `TRUNCATE`. Worked around in `reset-research` by deleting in dependency order; the pattern remains fragile. |
| A18 | **The fake provider does not enforce schemas** | Exactly how the empty-report bug survived every test for weeks: a fake answers from a script, so a response schema the API would reject passes silently. `tests/test_contract_schema.py` closes this for output schemas; nothing covers the request shape the same way. |

### Data sources

| # | Gap | Notes |
|---|---|---|
| A19 | **Bank of England macro adapter not built** | ALFRED and ONS are done (task 25 partial). |
| A20 | **EODHD unresolved** | Prices, corporate actions and comparables are conditional on the subscription, and ADR 0030 holds that nothing price-derived may leave the machine even with it. |

---

## List B — features, from the operator's seat

### Broken or absent in what already exists

| # | Gap | Notes |
|---|---|---|
| B1 | **No way to delete or archive a research request** | `aer reset-research` is all-or-nothing. The requests list offers no per-row control. |
| B2 | **Valuation, DCF, scenarios and sensitivities never appear in a run** | Not A1 any more: the analysis runs, and the valuation is blocked on confirmed assumptions by design. See the section above. |
| B3 | **Comparables never appear** | A20, and the same assumption question for the multiples that need a forecast. |
| ~~B4~~ | ~~**A run reads one filing**~~ | **Closed.** A run now reads the annual report and the recent current reports as well as the aggregate. Whether the recent-developments worker does better with them is the next live run's question. |
| B13 | **No full-text search, no issuer IR pages** | Built in task 16 and still not called. The workers can reach IR pages through `fetch_known_url` once a host is established; nothing seeks them out. |
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
* **Task 25 (macro with vintages)** — ALFRED and ONS done; Bank of England outstanding (A19).

Tasks 29–32 remain genuinely outstanding.

---

## Suggested order

**A1 to A4 are done.** What that leaves, in the order it is worth doing:

1. **A14 — prompt caching.** The composition is already ordered for it and never asks for
   it. The cheapest remaining saving by a distance, now that A2 means the meter can show
   what it saves.
2. **B1 — delete or archive a request.** Small, and asked for repeatedly.
3. **The valuation path.** Not a line above, because it is a design question rather than a
   gap: a DCF needs confirmed assumptions, so it needs either an operator working through
   the assumptions page or a proposing agent — and the second needs an ADR before it needs
   code.
4. **A3's neighbour, A21.** The filings are fetched and barely read.
5. **A9 to A12** — retention, backups, audit verification, replay. None urgent on one
   machine; all much harder to add after there is data worth keeping.
6. The rest of Phase 6 in the order `docs/PLAN.md` gives.
