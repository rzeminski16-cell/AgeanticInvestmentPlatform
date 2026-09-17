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

**0.5 is done** — 17 September, £4.95, the first QUICK run in the platform's life. The
readout is [`../plan/readiness-audit-2026-09/quick-mode-readout.md`](../plan/readiness-audit-2026-09/quick-mode-readout.md)
and the record is beside it in `msft1-quick/`.

Its answer to the spine question: **half the sections cost two thirds of the money**, £4.95
against the standard run's £7.48, because the plan, the critic, the five research workers,
the adversary and the revise pass do not vary with how many sections follow. A shorter
report is worth having for the reader; it is not a cost lever. The lever is output tokens,
£3.10 of the £4.95.

It also found two defects in the checking layer, neither reachable without running the mode,
both now fixed with tests: the agreement metric had no reading for a figure said in
trillions and failed a correct $3.11tn valuation, and the driver's own policy carried the
standard spine as a fixed floor of seventeen sections and called nine of nine "too many
sections lost".

The command, for the next time:

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
`tests/journey_inventory.py`, and each becomes a proof the day its fixture exists.
Phase 1½'s first half is done too (17 September): F1 landed under ADR 0113 — the
structural-absence scan (`tests/test_point_in_time_is_gone.py`) is green over `src/`,
`tests/`, `audit/`, `migrations/` and the behaviour documents; migration 0076 dropped
`work_orders.point_in_time`; the blocking set is eight; the trigger's row left the journey
inventory, which is now 51 rows (33 green, 18 unconstructed); the ADR is Accepted in code
and says what it still waits for. **Phase 1½ closed the same day, 17 September**, when the
keys arrived: preflight ready, the live wiring test passed, the QUICK run (§3), and three
re-seeded commissions — M&T £8.40, AstraZeneca £7.00, Microsoft £7.57. ADR 0113 is
**Accepted outright**: all four archived runs replay, 3,431 calculations and 189 citations
reproducing, with 496 artefacts intact and the audit chain whole over 30 events. `aer
backup` took and verified the corpus at schema 0077, 576 artefacts, 98.8 MiB.

**That backup is in `var/`, which is git-ignored, and this container is ephemeral.** The
committed run exports under `../plan/readiness-audit-2026-09/*-rerun/` survive; the
replayable corpus does not. Copy `var/backup-2026-09-17/` off the machine, or the next
session re-seeds it again at about £23.

Phase 2's offline half is done as well (17 September): the plausibility guard's turnover
relation had never fired, because it asked for a concept named `total_assets` where the
canonical one is `assets` — fixed first, with a test, since it *adds* findings to M&T
before ADR 0114 removes them. Then ADR 0114 itself, Accepted: a confirmed bank's ASC 606
caption is stored as `revenue_from_contracts` before selection, and revenue is derived from
net interest income plus non-interest income as a fact with `basis = derived` carrying its
formula, its inputs by id and the code version (migration 0077, under a check constraint
that makes the pair inseparable). **Phase 2's exit was met the same day**: one live M&T
commission, £8.40, to an approved and immutable report with all nine exercised blocking
metrics passing and `figure_plausibility` 0 of 0. FY2025 revenue is the derived $9,690m and
the margin reads 29.4%, against September's 172.1%. Three years where M&T tagged its own
total-revenue caption were left to stand, and in all three that caption equals this sum to
the dollar. ADR 0114 carries the reading; the record is in
[`../plan/readiness-audit-2026-09/mtb-rerun/`](../plan/readiness-audit-2026-09/mtb-rerun/).

Phase 3 is under way. **3.1 is done** (17 September, £0): the red team's calculation index
moved to `services/calculations.py` and both callers read it, so the adversary and the
sections can no longer disagree about what the run computed. **3.2 is done the same day**,
under **ADR 0125**: `services/consistency.py` now makes three passes rather than one —
published facts down the ladder as before, published calculations, and a scan for a sentence
denying a figure the document prints. A contradiction is its own kind of disagreement,
because both sides are the run's own output and there is no tier to prefer; the ladder is not
run and the rationale says so instead of describing a tier contest that never happened. **It
reports and refuses nothing.** The promotion to blocking is a separate decision the ADR
specifies and does not take: through the final gate's own evidence rule, never through
`eval/metrics.py`, after two consecutive clean live runs.

It was measured offline first, over the committed records, and the readout is in
[`../plan/readiness-audit-2026-09/cross-section-check-dry-run.md`](../plan/readiness-audit-2026-09/cross-section-check-dry-run.md):
**one contradiction across seven runs, and it is real** — AZN #2 denies an interest cover it
computes, footnotes and quotes at 8.11× in three other sections. That measurement found two
defects in the check before it could ship: `Calculation.name` is the *function's* name, so
`days_outstanding` serves days sales, days inventory and days payable at once; and a discount
factor takes its year as a parameter rather than an input. Either would have flooded gate 2
on every run. Both are fixed by keying on the question a calculation answers — its identity in
`aer.calc.engine` minus its output.

**3.3's appendix half is done too** (17 September, £0), under ADR 0115, which is now
**Accepted in part**. The settle path refills the disagreements section before it re-seals,
so the report says what was settled and why instead of printing *"Escalated for human
decision at approval"* over every challenge whatever became of it — the defect all three
AstraZeneca judges marked the document down for. The rule is spoken rather than named, the
winner is its label rather than "position A", and the summary counts the three kinds of
conflict separately instead of calling a red-team challenge a disagreement between sources.

**3.3's re-check half is withdrawn as ADR 0115 specified it, and re-specified there.**
Measuring it against the run it was argued from refuted its premise: all **33 of 33** numerals
in AZN #2's three false challenges are real recorded values. They quote FY2025 *and* FY2021 of
one figure side by side — interest cover 8.11 beside 0.812 — and call the pair a
contradiction, which is the comparison `services/consistency.py` forbids the platform's own
document. Phase 3.1's period-labelled index already closed that at source. The rule worth
building is named in the ADR and waits to be measured first, as ADR 0125's was.

**And 3.2's own check was re-measured against the live corpus, which corrected it.** The
offline dry run read the rendered Markdown against an index of every name in the ledger; the
code reads each section's structured content against the published-figure index. Run for real
against the four re-seeded runs the shipped version recorded **eighteen** denials, not the one
the offline figure implied. Four narrowings took it **18 → 5 → 2 → 1**, each with a regression
test quoting the sentence that produced it — the platform's own record sections are not
scanned, a narrowed denial is a different subject, a clause quoting the figure is using it,
and the negation reaches forwards and stops at the word about the record. The readout carries
all four. It is the argument for ADR 0125's own decision to ship advisory: the first honest
measurement of a prose rule was out by a factor of nine.

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
