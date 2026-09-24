# Phase 7 — the gate before the verdict round, as measured

The nine rows of [`11-testing-strategy.md` §5](../V1.0_Alpha/11-testing-strategy.md) again,
this time before the £31.50 round the delivery plan's §11 describes. Phase 5's measurements
are in [`phase-5-gate.md`](phase-5-gate.md) and stay there; they were taken at `36fa4e3`, and
everything from Phase 6, 6a, 6b and the round's own tooling has landed since, so none of them
is evidence about this head. Every row is measured again here, at the commit the round runs
on.

**Append, never rewrite**, on the rule the Phase 5 file set: a row's entry records what was
run, when, at which commit and what came back, and a later run that says something different
gets its own dated line underneath.

The pre-registration these rows guard is
[`phase-7-pre-registration.json`](phase-7-pre-registration.json), committed and hashed at
`ce16d0d` before any of this was measured and before any of the round's spend.

**The round's head is `d0f815e`**, two commits past the one the gate started on, and both
moves were the gate finding something. The first measurement, at `ce16d0d`, found a defect in
the round's own tooling (row 1); the fix, `e693d87`, restarted the gate. Row 4 then found that
`just hooks` rewrote eighty-five recorded files and failed its secrets scan; the fix,
`d0f815e`, touches `.pre-commit-config.yaml` and `.secrets.baseline` and nothing else — no
test and no module under `src/` or `audit/` reads either file (searched, not assumed), so what
rows 1, 2, 5, 6 and 9 measured at `e693d87` is what `d0f815e` would measure. Rows 3 and 4 are measured at
`d0f815e` itself, and CI ran every suite again there. No code changes after it.

---

## Row 1 — both suite processes green, with counts

**At `ce16d0d`, 24 September 2026 — not met, and the reason is the gate working.** Both
processes started at 08:11 UTC.

| process | measured |
|---|---|
| browser (`pytest tests/e2e -q`) | **234 passed in 989.12s (16m 29s)** |
| default (`pytest --ignore=tests/e2e -q`) | **1 failed** in the first 1%, stopped by hand at 80% with no second failure |

The failure was `test_every_priced_model_is_a_deliberate_decision_about_effort`. Phase 7.0
added Claude Opus 5.5 to the price table, so the fresh baseline could be metered, and did not
add it to the provider's table of models that accept an effort setting. That test is the one
place pricing a model and deciding its request shape are held together, and it held: a route
to Opus 5.5 would otherwise have run at the vendor's default effort without anyone deciding
it. The targeted tests run before the commit had covered the price table and its readers,
not the provider's request shape.

Fixed at `e693d87`: Opus 5.5 joins both capability tables, effort and the dynamic-filtering
search variant, because it has Opus 5's feature set with effort as its only thinking control.
The search table had no guard of the same kind and now has one. The run was stopped at 80%
rather than finished because the restart re-runs every test it had left, on the tree the
round will use.

**At `e693d87`, 24 September 2026** — both processes started at 08:50 UTC on a still tree,
each on its own database:

| process | measured |
|---|---|
| browser (`pytest tests/e2e -q`) | **234 passed in 954.84s (15m 54s)** |
| default (`pytest --ignore=tests/e2e -q`) | **8,054 passed, 2 deselected in 2876.41s (47m 56s)** |

**Met.** 8,054 against the 7,604 Phase 5 measured at `36fa4e3`: 450 tests added since. The
default process now takes about eight minutes longer than it did on 19 September.

## Row 2 — the evaluation gate

**Met, 24 September 2026 at `e693d87`.** `pytest tests/test_evaluation_gate.py
tests/test_eval_metrics.py tests/test_eval_replay.py tests/test_calc_golden.py`, which is what
`just eval` runs: **235 passed in 67.24s**, against Phase 5's 226. The same 235 at `ce16d0d`
earlier the same morning.

## Row 3 — three shuffled seeds, one of them fresh

**At `d0f815e`, 24 September 2026**, one after another on one database, nothing else running
against it. Phase 5's two seeds again, because a seed names a file order only at the commit it
is run on and these name new orders here, and one never used before:

| seed | measured |
|---|---|
| 784552 | **8,054 passed, 2 deselected in 2847.82s (47m 27s)** |
| 385699 | **8,054 passed, 2 deselected in 2836.86s (47m 16s)** |
| **62868**, fresh | **8,054 passed, 2 deselected in 2970.59s (49m 30s)** |

**Met.** Three orders, three times the declared-order count, and nothing between them: no test
in this tree depends on the order it runs in.

## Row 4 — lint, types, hooks, and CI actually green

**At `e693d87`, 24 September 2026 — not met, and never measured before.** `just hooks`
(`pre-commit run --all-files`), run at 09:39 UTC after both suites had passed:

| hook | result |
|---|---|
| ruff, ruff format, mypy, check-json, check-yaml, check-toml, the rest | passed |
| end-of-file-fixer, trailing-whitespace | **rewrote 85 committed files** |
| detect-secrets | **failed: 708 strings not in the baseline** |

**Nothing had run the hooks over the whole tree.** CI's lint job runs `ruff check`,
`ruff format --check` and `mypy` — not the hooks — and this container had no git hook
installed, so no commit made in it ran them either. Phase 5's record of this row measures
CI's lint job and says nothing of the hooks; the row's words, *"`just hooks` leaving the tree
unchanged"*, were not what was measured then.

The eighty-five were all recorded output: run exports, captured pages, judge reads, scores
and a design export, some from the readiness audit and the rest from this session's corpus
runs and Phase 5's round. The hook config's own header already excludes committed output from
the whitespace hooks, on the ground that *"rewriting them means the file no longer matches
what produced it"*; the recorded runs were missing from that list, and a document that cites a
recorded run cites its bytes. The files were restored untouched, and the exclusion now names
them, leaving the hand-written notes beside them checked.

The 708, and two more in test fixtures the hook skips, were **read one by one before any went
into the baseline**: SHA-256 digests of requests, responses, payloads and artefacts; the gate
forms' hidden seal hash; commit ids; seven XBRL concept names long enough to look random;
public Companies House fixture data; and the deliberately fake credential in
`tests/test_exclusions.py`. **No secret.** They went into `.secrets.baseline` the way the
readiness audit's own evidence did on 12 September — plugins and filters unchanged, so nothing
new is exempt and a secret in a later recorded run is still caught.

**Met at `d0f815e`, 24 September 2026.** `just hooks`: all fourteen hooks passed, and the tree
was unchanged. The git hook is now installed, so every commit from here runs them.

**CI run 549 on `d0f815e`, all three jobs green:**

| job | measured |
|---|---|
| Lint and types | `ruff check`, `ruff format --check`, `mypy` — 0.6 minutes |
| Tests and the evaluation gate | the default suite, then `just eval`, then the journey harness's shape half — 53.3 minutes |
| Browser tests | `pytest tests/e2e` — 15.7 minutes |

It confirms rows 1, 2 and 5 on the round's head itself, on a machine that is not this one.

## Row 5 — the journey harness green on every inventory row

**Met, 24 September 2026 at `e693d87`.**

| half | measured |
|---|---|
| shape (`python -m audit.smoke --journey`) | **50 rows, 50 green, 0 red, 0 unconstructed, 0 errors, `not_as_recorded: []`** — and the same at `ce16d0d` |
| browser (`pytest tests/e2e/test_journey.py`) | the fifty rows pass inside row 1's browser process, 234 passed |

The same fifty rows as Phase 5, generated from code rather than listed by hand.

## Row 6 — every stored run with an approved report re-rendered

**Met, 24 September 2026 at `e693d87`**, on the row as the operator restated it on 19
September: `aer replay-run` over every approved report the corpus holds, then
`aer verify-artefacts` and `aer verify-audit`. Four approved reports now, where Phase 5 had
two — the round's own two joined the corpus on 19 September. Run three times today: at
`ce16d0d` before the migration below and after it, and at `e693d87`. Every figure was the
same all three times.

| run | measured |
|---|---|
| M&T Bank, 17 September (`2840e674`) | reproduces: **888 calculations, 59 citations, 16 artefacts, 61 model calls** |
| Microsoft, 17 September (`960a85b8`) | reproduces: **850 calculations, 75 citations, 12 artefacts, 59 model calls** |
| AstraZeneca, 19 September, Phase 5 (`2dd8fae4`) | reproduces: **837 calculations, 43 citations, 13 artefacts, 63 model calls** |
| Microsoft, 19 September, Phase 5 (`10a60223`) | reproduces: **860 calculations, 53 citations, 20 artefacts, 57 model calls** |
| `aer verify-artefacts` | **772 checked, 772 intact** |
| `aer verify-audit` | **47 events checked, the chain is intact** |

M&T and the 17 September Microsoft run reproduce at exactly the figures Phase 5 recorded for
them, after everything that has landed in the code that re-derives them since.

**The dev database was five migrations behind**, at 0082 against the code's 0087. The corpus
had not been opened since Phase 5, and Phase 6a's five features each brought a migration: the
price-move alert, the closing section, Ask's questions, the refresh and the review's deferral.
`aer preflight` named it. Backed up first
(`aer backup --to var/backup-2026-09-24`: schema 0082, 897 artefacts, 166.1 MiB, verified),
then `alembic upgrade head`, then all four replays run again on the migrated database: every
figure identical. A migration that had moved a stored run's numbers would have shown here,
and the round would have attributed the difference to itself.

## Row 7 — the blinding, with the identity-guess rate measured and recorded

**Met as restated, at `e693d87`.** The offline half — `tests/test_blinding.py` and
`tests/test_judge_panel.py` — runs inside row 1's default process, and the panel's Phase 7
design is held there too: its pairings against this pre-registration's, the per-set scoring,
and the reading's numeric half.

The live rate stays Phase 5's until this round measures its own: **1.0, over fifteen reads,
every one stated as certain.** The pre-registration fixes what that means for this round —
*"The Phase 5 rate attaches to all nine comparisons unless the round's own identity guess,
last in the spend order, measures a lower one."* No work since Phase 5 set out to lower it,
and what the judges named first then — the validator's scoreboard, the red-team log and a
single structured evidence base — is in the documents by design.

## Row 8 — the pre-registration committed and hashed

**Met, 24 September 2026 at `e693d87`**, where the file is unchanged from `ce16d0d`, the
commit that landed it. `phase-7-pre-registration.json`, hashing to
`dd391c6f23260e95ee6ec92532e6a5169b482f58908e24f2342f25f5556a6d96`, recorded in §5 row 8 and
asserted by `tests/test_pre_registration.py` — which also checks this file's pairings against
the panel's, its typed assumptions against `audit/subjects.py`, and its spend order against the
£31.50.

**Phase 5's lesson applied before, not after:** the runner reads nothing from the
pre-registration, so the test compares the two. Where they disagreed on 19 September the code
would have won silently; here the test fails first.

## Row 9 — `aer preflight` ready, and `test-live` passing

**Met, 24 September 2026 at `e693d87`.** Every preflight row passes but two: the worker, which
is correct until a run needs one (the audit driver starts its own), and the wire contract,
which preflight hands to `test-live` by name. `pytest -m live_llm`: **2 passed, 8,288
deselected in 9.29s** — and at `ce16d0d`, 2 passed in 9.98s. Re-run on the new head because
the fix is in the provider module the live test exercises.

| preflight row | reading |
|---|---|
| schema | the database schema matches the models (after the migration above) |
| run cap | the per-run ceiling is £20.00 |
| monthly room | £42.59 of the month's £100.00 spent; £57.41 remains |
| worker | none reporting — the driver starts its own |
| wire contract | skipped by preflight, answered by `test-live` above |

---

## Where the nine stand — 24 September 2026, at `d0f815e`

| # | Row | State |
|---|---|---|
| 1 | Both suite processes green, with counts | **Met** — 8,054 and 234, after the first measurement found a defect in the round's tooling |
| 2 | The evaluation gate | **Met** — 235, and green again in CI 549 |
| 3 | Three shuffled seeds, one fresh | **Met** — 784552, 385699 and the fresh 62868, all 8,054 |
| 4 | Lint, types, hooks, CI actually green | **Met** — after the hooks were run over the whole tree for the first time, and CI 549 |
| 5 | The journey harness on every inventory row | **Met** — 50/50, both halves |
| 6 | Every stored run with an approved report re-rendered | **Met as restated** — four runs, every figure identical three times over |
| 7 | The blinding, rate measured and recorded | **Met as restated** — Phase 5's 1.0 carries until this round measures its own |
| 8 | The pre-registration committed and hashed | **Met** |
| 9 | `aer preflight` ready, `test-live` passing | **Met** |

**Two rows found something, and both findings moved the head before a penny of the round was
spent.** Row 1 found the provider deciding nothing about a model the price table had just
learned. Row 4 found that one of its own four checks had never been run as written. Neither
is a defect in what the round measures — no role routes to Opus 5.5, and the hooks guard the
repository rather than the product — but both are the gate doing the one thing it is for:
the round starts on a tree whose every row was measured, not assumed.

## Spend

**£0.00 of the round's £31.50 at the end of the gate.** `test-live` ran twice, two calls
each time, for a fraction of a penny; the test calls the provider directly rather than through
the router, so it writes no cost row and is on neither ledger.

The audit ledger — the round's £100 ceiling across platform runs, baselines and judge reads —
stands at **£48.92**: platform £42.59, judge reads £6.33 (Phase 5's identity guess £4.27 and
six comparisons £2.06), baselines £0.00. Recorded here because `audit/out/ledger.json` is
git-ignored and lives only as long as this container does.
