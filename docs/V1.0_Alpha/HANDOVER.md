# Starting the work — what a fresh session needs

*Written 14 September 2026, at the end of the design phase. Everything in this folder is
decided; nothing is built. This page is what a session that has never seen the conversation
needs in order to pick up cleanly.*

---

## 1. Read these three, in this order

1. [`README.md`](README.md) — the phase, the read order, the ten ADRs.
2. [`12-the-ranked-backlog.md`](12-the-ranked-backlog.md) — **read before the delivery plan.**
   It is the only measured document in the folder, it reorders the weight of the plan, and it
   is what stops Phase 6 being over-built.
3. [`05-delivery-plan.md`](05-delivery-plan.md) — nine phases, 74–94 sessions, ~£80 live, and
   the kill gate at Phase 5.

Then `CLAUDE.md` at the repository root, which outranks everything above on conventions, and
[`../plan/README.md`](../plan/README.md) for the order of authority.

## 2. What is settled, so you do not re-litigate it

| | |
|---|---|
| **Point-in-time** | Removed. ADR 0113. Invariant 4 goes when that ADR is accepted, not before — `CLAUDE.md` still says eight and should |
| **A bank's revenue** | Derived at the fact layer, only for a confirmed bank. ADR 0114 |
| **The price subscription** | Assume publication is permitted. If it is not, the figure is withheld through ADR 0034's type — never quietly printed, never silently dropped |
| **UK or US** | **Both.** Build the Companies House path. ADR 0121, F19, Phase 4a |
| **The gilt yield** | An operator-confirmed assumption. `risk_free_series_for` keeps refusing rather than substituting |
| **UK acquisition depth** | Four accounts filings by default |
| **The UK test subject** | **Tesco** — already the subject of the audit's refusal proof, so the inverted test reads as a direct before-and-after |
| **Accounts and sharing** | Deferred out of V1.0. ADR 0120 is drafted anyway, and its three constraints on work happening now are accepted |
| **The abandonment criterion** | Signed off. Phase 5 can fail, and that is the point |

## 3. What is left of Phase 0

**0.1 is done** — £3.96, 14 September, results in
[`12-the-ranked-backlog.md`](12-the-ranked-backlog.md), raw JSON committed to
[`../plan/readiness-audit-2026-09/judges/`](../plan/readiness-audit-2026-09/judges/).

**0.5, the QUICK-mode run, is not.** It is approved, costed at about £4, and has never been run
in the platform's life. It drops nine of the eighteen sections and scales budgets to 0.6, and it
answers the largest unasked question in the plan: whether eighteen sections is the right spine
for one private investor.

It needs the services up (§4) and then one command:

```bash
uv run python -m audit.driver.run msft1 --mode quick --cap 6.00 --screenshots
```

The driver clears every gate under `audit/driver/policy.py`, supplies the operator-owned
assumptions from `audit/subjects.py` with their sources, and writes every readout to
`audit/out/msft1/`. **Copy anything worth keeping into
`docs/plan/readiness-audit-2026-09/`** — `audit/out/` is git-ignored, and this container is
ephemeral.

**0.2 is done** — 16 September, £0, from committed records only:
[`../plan/readiness-audit-2026-09/source-map.md`](../plan/readiness-audit-2026-09/source-map.md).
Every decisive figure the console read from a primary source sits inside an accession the
platform opened, a filing type it fetches, or its own fact store; the gap is exhibits, the
five-most-recent rule for current reports, and the unconstructed issuer adapter — not evidence
policy. Still outstanding: **0.4** (the operator reads two documents blind, themselves — three
hours, no session can do it for them).

## 4. Bringing the environment up

The container restarts lose PostgreSQL and Redis. `.env` is git-ignored and carries the three
keys; never echo it, and `just config` must show every secret masked.

```bash
pg_ctlcluster 16 main start
redis-server /etc/redis/redis.conf --daemonize yes
uv sync --all-groups
uv run alembic upgrade head
uv run aer preflight            # the `worker` row fails until the worker is up — expected
uv run just test-live           # a fraction of a penny, before anything else spends
```

Then the worker, in its own process: `uv run arq aer.worker.WorkerSettings`.

**Two rules that will bite you otherwise:**

- **One pytest process per database.** The suite empties tables between tests, so two
  concurrent runs sharing `aer_test` delete each other's rows and fail nowhere near the cause.
  Give each run its own `AER_TEST_DATABASE_URL`, exported in the shell rather than in `.env` —
  it is read at import, before `hermetic_environment` strips every `AER_*` variable.
- **The full suite is two processes**: `pytest --ignore=tests/e2e`, then `pytest tests/e2e`.
  Playwright's sync API leaves a running loop that wedges every pytest-asyncio test after it.

## 5. Phase 1's first three things, in order

Everything here is provable offline and costs nothing but sessions.

1. **Verify the order dependence is fixed, and keep it fixed.** The audit's own fix landed
   with the audit (F-03: every table emptied at fixture setup through `tests/db_cleanup.py`;
   the shuffled runner widened to every directory), and the three modules the first draft of
   this page named as leaking were victims, not leakers. The shuffle is over *file* order, so
   `just test-shuffled 20260811` reproduces nothing at a later commit. What was left, done 16
   September: a test-class filter in
   `tests/test_contract_schema.py`, where a test agent claiming the real `validator` role could
   win the walk; every unordered `select(User)` in a driver replaced by the run's owner or an
   assertion that exactly one user exists (`tests/workflow_fixtures.py`); and a nightly
   shuffled job (`.github/workflows/nightly-shuffled.yml`) with a fresh seed on its summary
   line — which is the verification, every night, on the widened runner. The first fresh
   seed, `just test-shuffled 937541` on 16 September: 6,982 passed, 2 deselected, 0 failed.
2. **Fix the red CI job and keep it green.** It was red for two reasons neither this page nor
   the testing strategy named: `ruff check` failed on one unsorted import
   (`tests/test_assumption_outcomes.py`), and `tests/test_type_scale.py` pinned
   `docs/redesign/01-design-system.md` after the 14 September tidy moved it to
   `docs/design-system.md`, which aborted collection of the whole default suite — so the suite
   had not run in CI since run 453. The `ruff format --check` failure recorded on 2026-09-09
   was never fixed by a commit; ruff's Markdown formatting is preview-gated, so the snippets
   left scope by themselves. Both fixed 16 September, with `tests/test_pinned_paths.py` so a
   moved document fails one test rather than the whole suite.
3. **Build the journey harness red**, from the inventory, *before* the first dead end is fixed —
   so every fix afterwards has a number it moves. **Done 16 September**, and red:
   [`11-testing-strategy.md`](11-testing-strategy.md) §3.1 records what was built and what it
   measured — 52 rows generated from code, 32 red, 20 not yet constructible on the fake scene,
   none green. Two lists in `tests/journey_inventory.py` are the record: `STILL_RED` names
   each red row by which of the three assertions fails and why (*text* on 31, *control* on
   17, *press* on none of the 15 measured), `UNCONSTRUCTED` each unbuildable row with the
   reason, and both halves (`just test-journey`) fail a row whose measured red set differs
   from the record in either direction. So a fix in Phase 1.2–1.4 is done when it removes
   its assertion from the row's record — the row itself, once the record is empty — and the
   harness stays green; a fix that leaves the record untouched fails the build as "not as
   recorded". Every pause the workflow raises now records its `PauseReason` beside
   its gate (`tests/test_pauses.py` pins the raise sites to the enum), and the builders read
   that rather than the pause's prose. What the harness still owes is harness backlog rather
   than platform backlog: the twenty unconstructed rows each need a fixture — a scene that
   raises the sector or unmapped-concepts gate, a section brain that plants an unverifiable
   citation, a fixture per escalation trigger, and a forged stale form for the conflict page.

Then the rest of Phase 1 in [`05-delivery-plan.md`](05-delivery-plan.md) §5 — of which 1.2
is done (16 September, [ADR 0123](../adr/0123-a-decision-at-a-gate-is-superseded-and-a-rejection-ends-the-run.md)):
the twelve rejected and stale gate rows lost their `control` assertion from the record the
same day, which is the harness doing what §3 above says it is for. 1.3 is done too (ADR
0116, migration 0075: a report is superseded, never replaced, and exactly one is current).
1.4, the vocabulary ratchet, emptied `STILL_RED` the same day: 33 rows green on all three
assertions, 19 still unconstructed, both halves agreeing. 1.5 cut the unmapped-concepts
gate to twenty ranked rows with the rest behind one fold and both counts named. 1.6 wired
the macro stack: the risk-free rate is fetched at the run's own vintage, converted through
the traced calculation and proposed at the gate under *a published series*; the equity risk
premium is a standing operator assumption (ADR 0124, Accepted the same day), set once in
settings with its justification and proposed into every run for the operator to confirm.
1.7 enforced `excluded_sources` in code: a document from an excluded domain is quarantined
at acquisition with its own reason, a worker's fetch of one is refused before the archive
or the network is reached, a listing withholds hits on one and says how many, and the
validator refuses a finding citing one. 1.8 exercised the recovery path on a full fake-scene
run — backup, verify, restore into a second database and store, then the artefacts, the
audit chain and the run's replay all holding on the restored side — and a test now keeps it
exercised. That closes Phase 1's list. Its exit criterion holds on every row the harness can
construct (33 of 52); the 19 unconstructed rows are the harness's own backlog, named in
`tests/journey_inventory.py`, and each becomes a proof the day its fixture exists. Next is
Phase 1½: F1 under ADR 0113 (the structural-absence scan first), and the corpus the day the
keys arrive.

## 6. Standing constraints on any session doing this work

- **Branch.** Develop, commit and push on the branch the session is told to use. Never push
  elsewhere without being asked.
- **An ADR before the code**, wherever a feature says it needs one. All ten are drafted and
  marked **Proposed** — which is the one state in which an ADR can still be argued with. An ADR
  becomes Accepted when the change it argues lands.
- **Never move a calculation into a prompt.** It is the rule everything else follows from.
- **Replay-first.** A document change ships with an offline proof: re-render the five stored
  runs and assert the change. A prompt change that cannot be proved by replay buys one
  `aer rehearse-section` at about 30p — not a full run.
- **Do not fold a later item's work into an earlier one.** The dependency graph in
  [`04-feature-specifications.md`](04-feature-specifications.md) is real, and F1 goes first and
  alone because it touches the drafting prompts.
- **If a prerequisite is missing or an architectural choice is unclear, stop and ask.** A wrong
  foundational choice is expensive to undo here.

## 7. The three things this plan has already got wrong

Recorded because the pattern matters more than the instances: **every one was a plan document
confidently describing code nobody had read.**

1. *"3,637 segment facts, mapped and stored."* The sweep **saw** 3,637 and **wrote** 224; the
   store holds 626 across three subjects.
2. *"A company-number column that fails a check constraint today."* It does not. The constraint
   is `cik IS NOT NULL OR company_number IS NOT NULL`, written for exactly a CIK-less UK
   company.
3. *"The segment gap is an extraction failure needing a new iXBRL parser."* The parser ran. The
   failure is one `WHERE dimension_axis IS NULL` at `services/facts.py:88`.

**Read the code before costing the work.** Twice now that has made a feature dramatically
cheaper than the plan said, and once it made a win fifteen times smaller.
