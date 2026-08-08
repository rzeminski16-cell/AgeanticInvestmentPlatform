# Gap analysis — what is missing, as of 2026-08-08

Written after the first end-to-end live runs, from the code rather than from the plan. The
phase specifications in `docs/PLAN.md` remain the authority on *scope*; this document is an
honest account of the distance between that scope and what a run actually does today.

Two lists, as asked: what is missing behind the surface, and what is missing in front of it.

---

## The finding that dominates both lists

**A large, tested deterministic layer exists that the live workflow never calls.**

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

The services are reachable from the API and the web — `services/valuation.py`,
`services/scenarios.py`, `services/assumptions.py` all have routes — but **no workflow step
runs them**. This is a wiring gap, not a missing-feature gap, and it is the highest-value
work remaining in the system. Until it closes, most of this platform is code that no
report has ever touched.

---

## List A — backend, safety, security, optimisation, operations

### Correctness and invariants

| # | Gap | Notes |
|---|---|---|
| A1 | **The workflow does not invoke the calculation suite** | See above. Nothing else here is close in value. |
| A2 | **Unmetered model spend** | A reply rejected while the SDK accumulates the stream carries no usage figure, so no `costs` row is written: the tokens are spent and the budget cap never sees them. Breaks the "cost is metered in code" invariant. `MAX_UNREADABLE_REPLIES` bounds the blast radius; the hole itself is open. |
| A3 | **The companyfacts quarantine** | Every run's only source is quarantined `no_publication_date`. The document is a rolling aggregate with no single publication date; the honest fix is the latest `filed` across the facts it carries, recorded as such. |
| A4 | **One source per run** | SEC full-text search and issuer-IR discovery (task 16) and Companies House (task 17) are built and are not called. `_acquire` fetches company facts and stops — its docstring says so deliberately, and that deliberate choice has outlived its purpose. |

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
| B2 | **Valuation, DCF, scenarios and sensitivities never appear in a run** | A1. |
| B3 | **Comparables never appear** | A1 and A20. |
| B4 | **A run reads one filing** | A4. No 10-K text, no IR pages, no announcements — so the recent-developments worker has almost nothing to find, and reports leads rather than findings. |
| B5 | **Charts have nothing to plot** | `services/exhibits.py` is wired into the report; with one source and one calculation there is no series to draw. |

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

1. **A1**, before anything else on either list. It converts a large body of tested code
   into something a report can stand on, and it makes B2, B3 and B5 disappear at once.
2. **A3 and A4** next: they are what the analysis in (1) will be reading. A DCF over one
   quarantined aggregate is arithmetic without evidence.
3. **A2 and A14** together — both are about what a run costs, and both are cheap beside
   what they save.
4. **B1**, which is small and has been asked for repeatedly.
5. The rest of Phase 6 in the order `docs/PLAN.md` gives.
