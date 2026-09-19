# Phase 5 — the gate before the measurement round, as measured

The nine rows of [`11-testing-strategy.md` §5](../V1.0_Alpha/11-testing-strategy.md) are the
conditions the £21 round may not start without. This file is where each one's *measurement*
lives, because "green" without a number is the thing the rows exist to stop.

**Append, never rewrite.** A row's entry records what was run, when, at which commit, and
what came back. A later run that says something different gets its own dated line underneath
rather than replacing the first: the interesting case is a row that was met and stopped being
met, and an overwritten record cannot show it.

The pre-registration these confirmations guard is
[`phase-5-pre-registration.json`](phase-5-pre-registration.json), hashed into §5 row 8 before
any of this was measured.

---

## Row 1 — both suite processes green, with counts

**Default suite, 19 September 2026 at `e551dc7`** — `pytest --ignore=tests/e2e -q`, alone on
the machine, in declared order (`-p no:randomly`):

> **1 failed, 3937 passed, 2 deselected in 872.62s**

The one failure was `test_point_in_time_is_gone.py::test_the_enforcement_has_no_name_left_in_the_code`,
firing on `audit/judges/blinding.py` — committed minutes earlier, and correctly caught. Fixed
at `96acdd6` by an allowlist entry with its reason; the row is not claimed met on this run.

**Two runs before it are recorded here because they are the more useful lesson.** A run
started at 09:46 reported four failures; a run started at 10:18 reported nineteen failures
and errors in its first 7%, at roughly fifteen times the normal pace. Two of the first four
were real (assertions pinning a tier code and a calculation key, fixed at `4d0c276`); every
other failure in both runs was **contention**, because a second database-heavy suite was
running against the same Postgres server and `delete_all`'s ten-second statement timeout was
expiring in fixture setup. That is CI run 476's failure mode, which `tests/e2e/conftest.py`
already documents, reproduced locally by running two processes on one server. *One pytest
process per database* is in `CLAUDE.md`; one Postgres server under two heavy suites is the
part it does not say, and the cost of missing it was reading nineteen bogus failures as real.

**Browser suite, 19 September 2026 at `4d0c276`** — `pytest tests/e2e -q`: **3 failed, 230
passed in 817.22s**. All three were journey rows that seed a section definition or a skill,
failing on a unique-key violation and then on a review page naming two probes; the cause was
`tests/e2e/conftest.py` restating half of `db_cleanup`'s predicate, fixed at `568ed6d`.

**Browser suite, 19 September 2026 at `f123ad5`** — the confirming run, alone on the machine:

> **233 passed in 817.07s**

The default process's confirming run is still owed; until it lands the row is half met.

## Row 2 — the evaluation gate

**Met, 19 September 2026 at `f123ad5`.** `pytest tests/test_evaluation_gate.py
tests/test_eval_metrics.py tests/test_eval_replay.py tests/test_calc_golden.py`, which is what
`just eval` runs:

> **226 passed in 62.72s**

At the post-F1 blocking count: ADR 0113 took the two temporal metrics out of `BLOCKING` with
the rule they measured, leaving ten.

## Row 3 — three shuffled seeds, one of them fresh

Seed 937541 is recorded from 16 September, at an older commit — and the shuffle is over *file
order*, so a seed only reproduces an ordering at the commit it was recorded on. Two further
seeds are owed, on the current head.

## Row 4 — lint, types, hooks, and CI actually green

`ruff check`, `ruff format --check` and `mypy` are clean at every commit in this session's
sequence, across 464 source files. CI's own verdict on the head commit is still owed: runs
504 and after were cancelled by subsequent pushes, and run 503 (`0a771c1`) is the last
completed green one.

## Row 5 — the journey harness green on every inventory row

**Met, 19 September 2026 at `46c5727`.** The last eight unconstructed rows were the §2.4
escalation triggers at the final gate.

| half | measured |
|---|---|
| shape (`python -m audit.smoke --journey`) | 50 rows, 50 green, 0 red, 0 unconstructed, `not_as_recorded: []` |
| browser (`pytest tests/e2e/test_journey.py`) | 13 final-gate rows passed after `568ed6d`; the other 37 passed in the full browser run |

What the eight rows found is in [§5's own section](../V1.0_Alpha/11-testing-strategy.md) and
ROADMAP §3.19 items 29–31: no defect in the platform's reasoning, and a vocabulary leak on
every surface the state touches — including `tier T1_REGULATORY` in a footnote of the
*published report*.

## Row 6 — every stored run with an approved report re-rendered

**Met, 19 September 2026 at `84b8fa5`**, on the row as restated by the operator. M&T Bank
reproduces at 888 calculations / 59 citations / 16 artefacts / 61 model calls; Microsoft at
850 / 75 / 12 / 59; zero citation failures on either. 496 artefacts intact, 30 audit events
holding. The renderer changed after that measurement (`46c5727` reworded the tier in every
footnote), and `replay_run` re-derives calculations and re-reads artefacts by hash rather
than re-rendering, so the proof stands — but the row's own instruction is to re-run all three
commands before the round rather than trust a table, and that still applies.

## Row 7 — the blinding dry run, with the identity-guess rate at chance

**Offline half met, 19 September 2026 at `e551dc7`**: `tests/test_blinding.py` and
`tests/test_judge_rubric.py`, **27 passed**.

**The finding, which is the point of the row.** September's judges were told they were
reading blind and were not. Counted over the recorded texts:

| tell | platform | console |
|---|---|---|
| Markdown footnote markers `[^12]` | 316 | 0 |
| bracketed sources `[S12]` | 0 | 451 |
| numbered section headings | 0 | 18 |
| the run's header block | 1 | 0 |
| the standing disclaimer | 2 | 0 |
| the author's first person | 0 | 39 |
| the assistant's own working | 0 | 1 |

Every one separates the two sides completely, and a judge needed one of them. All three
console notes open with the assistant's narration — *"I'll research this thoroughly before
writing."* — and two run it straight into the title with no newline. **ISSUE 2's September
number carries that caveat whatever this round finds.**

After `audit.judges.blinding.neutralise`, no tell fires on any of the six documents, and none
loses a fifth of its length. The identity guess itself is billable, stays `live_llm`, and is
owed; so is the redaction round for F13.

## Row 8 — the pre-registration committed and hashed

**Met, 19 September 2026 at `84b8fa5`.** `phase-5-pre-registration.json`, hashing to
`70c532bfd67c640f72ceff8895341e8eaded4921ae89a3b54d8ea45685c8c047`, recorded in §5 row 8 and
asserted by `tests/test_pre_registration.py`.

## Row 9 — `aer preflight` ready, and `test-live` passing

**Met, 19 September 2026 at `0a771c1`.** Every preflight row green but the worker, which is
correct until a run needs one; `pytest -m live_llm` passed, 2 tests, 7750 deselected. The
corpus database needed `alembic upgrade head` first — it was at 0080 and missing three of
this session's columns, which preflight named.

---

## Spend

£27.92 of the £100 monthly cap at `0a771c1`. Everything in this file since was £0.
