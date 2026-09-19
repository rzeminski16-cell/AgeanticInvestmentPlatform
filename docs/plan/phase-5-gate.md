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

**Default suite, 19 September 2026 at `e551dc7`** — `pytest --ignore=tests/e2e -q -x`, alone on
the machine, in declared order (`-p no:randomly`):

> **1 failed, 3937 passed, 2 deselected in 872.62s** — *stopped at the first failure by `-x`.*

The one failure was ADR 0113's structural-absence scan over the code tree, firing on
`audit/judges/blinding.py` — committed minutes earlier, and correctly caught. Fixed
at `96acdd6` by an allowlist entry with its reason; the row is not claimed met on this run.

**Correction, same day.** The line above was first written here as a completed run. It is
not: `-x` stopped it at the scan, so 3,937 is where the suite had got to and not what it
contains. The default process collects about 7,570 tests. A truncated count read as a total
is exactly the failure this file exists to prevent — a row claimed met on a number that
measured less than it appears to — so the flag now sits beside the figure rather than in a
commit message nobody reads next to it.

**Default suite, 19 September 2026 at `de92cbb`** — the first untruncated run, alone on the
machine:

> **3 failed, 7,567 passed, 2 deselected in 2301.15s (38m 21s)**

All three were mine, and all three were the `46c5727` tier rewording reaching modules that
run had not covered: the doc half of the ADR 0113 scan, firing on this file's own prose about
the scan; `test_provenance_drilldown.py` asserting the literal `T1_REGULATORY` on a page that
now says *a regulatory filing*; and `tests/fixtures/full_run/golden.md` still carrying `tier
T1_REGULATORY` in twenty-two footnotes. The golden was re-recorded (`UPDATE_GOLDEN=1`, which
writes the file and then fails on purpose, so an update cannot pass silently); the other two
were one-line assertions. Confirmed green at `de92cbb` plus the fixes, running the golden
full run, the provenance drilldown and ADR 0113's structural-absence scan together:
**24 passed in 54.59s**.

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

**Default suite, 19 September 2026 at `51b43d3`** — alone on the machine, declared order:

> **1 failed, 7,569 passed, 2 deselected in 2399.79s (40m)**

The one failure was ADR 0113's document scan, firing on a line of *this file* that named a
test module whose own filename carries the retired phrase — **a line I had already corrected
by the time the suite reached that test.** The scan reads the documents from disk at run time,
so a forty-minute suite measures whatever the tree says in the minute each test runs, and
editing documentation while it runs invalidates every test that reads documentation. Not a
defect in `51b43d3`, and not a green run either: **the lesson is that a long suite needs a
still tree**, and the confirming run below was taken on one.

**Met, 19 September 2026 at `36fa4e3`.** Both processes, on a still tree, nothing else on the
machine, run back to back by a script so no edit could land between them:

| process | measured |
|---|---|
| default (`pytest --ignore=tests/e2e -q -p no:randomly`) | **7,604 passed, 2 deselected in 2401.62s (40m 01s)** |
| browser (`pytest tests/e2e -q`) | **233 passed in 863.57s (14m 23s)** |

7,604 against the 7,569 of the run before it: thirty-five tests added the same day, for the
panel, the blinding residue, the display formatter and the footnote's scale-blind rounding.

CI run 512 reached the same verdict independently on the same commit — all three jobs green,
including the browser suite and the journey harness's shape half.

## Row 2 — the evaluation gate

**Met, 19 September 2026 at `f123ad5`.** `pytest tests/test_evaluation_gate.py
tests/test_eval_metrics.py tests/test_eval_replay.py tests/test_calc_golden.py`, which is what
`just eval` runs:

> **226 passed in 62.72s**

At the post-F1 blocking count: ADR 0113 took the two temporal metrics out of `BLOCKING` with
the rule they measured, leaving ten.

## Row 3 — three shuffled seeds, one of them fresh

**Met, 19 September 2026 at `36fa4e3`.** `python -m tests.shuffled`, which shuffles *file*
order and leaves `pytest-randomly` shuffling within each file:

| seed | commit | measured |
|---|---|---|
| 937541 | 16 September, `4a6f0e1`-era | 6,982 passed |
| **784552** | `36fa4e3` | **7,604 passed, 2 deselected in 2463.45s** |
| **385699** | `36fa4e3` | **7,604 passed, 2 deselected in 2399.01s** |

Two fresh, which is what the row asks for and what the older seed cannot supply: the shuffle
is over file order, so a seed only reproduces an ordering at the commit it was taken on, and
937541 names a different order today than it did on 16 September. Both fresh seeds reached the
same 7,604 as the declared-order run, so nothing in this tree depends on the order it runs in.

## Row 4 — lint, types, hooks, and CI actually green

**Met, 19 September 2026 at `36fa4e3`** — CI run 512, all three jobs green:

| job | measured |
|---|---|
| Lint and types | `ruff check`, `ruff format --check`, `mypy` — clean across 466 source files |
| Tests and the evaluation gate | the default suite, then `just eval`, then the journey harness's shape half — 46 minutes, all green |
| Browser tests | `pytest tests/e2e` — 17 minutes, green |

Runs 504 to 511 were each cancelled by the next push, which is why this took until 512: a
green CI verdict needs a commit nobody pushes past while it runs. Run 503 (`0a771c1`) was the
last completed green one before it.

This row also re-confirms rows 2 and 5 independently, on this head rather than on the commit
each was first measured at — the evaluation gate and the journey harness are both steps of
the same job.

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

## Row 7 — the blinding dry run, with the identity-guess rate measured and recorded

*Restated from "at chance" on 19 September 2026, on the operator's decision, after the
measurement below showed the original condition could not be met at any price.*

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
loses a fifth of its length.

### The live half, 19 September 2026 — **the rate is 1.0, and it stays 1.0**

`audit/judges/panel.py`, `claude-opus-5` at medium effort, the three lenses over the blinded
pairs, assignment seed 20260919 from the pre-registration. **Fifteen reads across three
rounds, every one correct, every one stated as *certain*. £4.27 on the audit ledger.**

| round | asked | right | what the judges named first |
|---|---|---|---|
| 1 — AZN only | 3 | 3 | `aer.calc.ratios:gross_margin`, code version `6d9c…`, the validator's metrics table, `0E-8` |
| 2 — all three, machinery closed | 9 | 9 | the validator's metrics table everywhere; `proposed by aer.services.assumption_proposals` led on MSFT and M&T |
| 3 — MSFT, module paths closed too | 3 | 3 | the validator's metrics table, the calculation footnotes, the red-team log, `beta quoted as 1.064553313698` |

**The first round is the lesson about the instrument.** The tell list had been written by
reading the two documents' *surface* — markers, headings, the header block, the disclaimer —
and had never looked inside the prose, where the renderer prints the machinery that made each
figure. Three judges out of three named `aer.calc.ratios:gross_margin` before anything else.
Counted afterwards: 367 code identifiers across the three reports and **none** in any console
note; 248 dotted module paths against none; 135 shouted enum codes against none. Six new tells
went in, each counted on both sides first, and the neutraliser closed every one.

**The rate did not move, and round 3 is why.** With the module paths gone the judges named the
next thing down, and the next thing down is not letterhead:

> *"a 'Validation & Disagreements' section with named validator metrics and thresholds"*, *"an
> internal red-team disagreement log escalated 'for human decision at approval'"*, *"beta
> quoted as 1.064553313698"*, and — in every one of the fifteen reads, as the second reason —
> the evidence base: one XBRL feed and a handful of filings against thirty heterogeneous web
> sources.

**So the finding is not that the blinding is weak. It is that these two documents cannot be
blinded to authorship, because what distinguishes them is what they are.** A validator's
scoreboard, an approval trail and a single structured evidence base are the product, not its
wrapping; a neutraliser that removed them would be blinding the thing being judged, and the
round would then compare two documents that do not exist. They stay, with the reason written
next to each in `TELLS`.

**What the pre-registration already decided about exactly this**, committed and hashed before
any of it was measured: *"A hit rate materially above chance does not void the comparison; it
attaches a caveat to it, and the caveat is never omitted."* 1.0 is above the 0.7 threshold, so
the round's six comparisons carry that caveat — **every judge knew which document was the
platform's** — and it is never reported without it.

**Row 7 as §5 worded it — *"with the identity-guess hit rate at chance"* — cannot be met**, at
this price or any other. It is the same shape as row 6: a condition written before anyone had
measured the thing it was about.

**Restated on the operator's decision, 19 September 2026, and met as restated.** The blinding
must remove the presentation — asserted offline, pinned against `LEFT_STANDING` — and the
identity-guess rate must be *measured and recorded* rather than assumed; above chance, the
pre-registration's caveat attaches to all six comparisons and is never omitted. The round
proceeds, and **its report leads with the fact that every judge could tell which document the
platform wrote** rather than burying it. The restatement's reasoning is in
[§5's own section](../V1.0_Alpha/11-testing-strategy.md).

**One thing the blinding refuses to do, recorded as `LEFT_STANDING`.** Two of the six
documents still fire one tell after neutralising — full stored precision, `1.064553313698`
where a reader wants 1.06. Removing it means rewriting a digit, and this module will not: a
marker renumbered wrongly points at the wrong source, which a reader can see, while a figure
rounded wrongly is simply false, which nobody can see. The remedy is in the renderer, which
should not print a stored Decimal to a reader at all — ROADMAP §3.19 item 35, **half fixed
in the same sitting** because it reaches the round's own documents:

- the document's validator table built its cells with `str(Decimal)` — `0E-8`, `1.00000000`
  — while the review page beside it had been fixing the identical column through a `trimmed`
  filter since Phase 1.4. One rule written twice, and the copy nobody looked at stayed wrong.
  Both now call `aer.render.display.stored`;
- the calculation footnote's *"did the rounding lose anything?"* test compared
  `quantised == value`, and `Decimal` equality is blind to scale — so
  `Decimal("0.1800") == Decimal("0.180000000000")` is `True` and every value whose stored
  digits ended in zeros took the untouched branch and printed all twelve places. `fx_report`'s
  golden had carried `0.180000000000` for as long as the rule existed;
- what remains is `display.scalar`'s fallback for a dimensionless number whose label states
  no meaning. It passes the value through at full precision rather than guess a unit, which
  is right — but twelve decimal places is not the only alternative to guessing, and a
  precision policy for the shared formatter wants its own change.

The redaction round for F13 is still owed.

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

## Where the nine stand — 19 September 2026, at `36fa4e3`

| # | Row | State |
|---|---|---|
| 1 | Both suite processes green, with counts | **Met** — 7,604 and 233 |
| 2 | The evaluation gate | **Met** — 226, and green again in CI 512 |
| 3 | Three shuffled seeds, one fresh | **Met** — 784552 and 385699, both 7,604 |
| 4 | Lint, types, hooks, CI actually green | **Met** — CI 512, three jobs |
| 5 | The journey harness on every inventory row | **Met** — 50/50, both halves |
| 6 | Every stored run with an approved report re-rendered | **Met as restated**, and the three commands re-run at the round's setup rather than trusted from the table |
| 7 | The blinding dry run, rate measured and recorded | **Met as restated** — 1.0 over fifteen reads |
| 8 | The pre-registration committed and hashed | **Met** |
| 9 | `aer preflight` ready, `test-live` passing | **Met** |

**Two of the nine were restated rather than met as written, both by the operator, both on the
measurement rather than in advance of it.** Row 6 asked for five stored runs the corpus does
not have; row 7 asked for an identity-guess rate at chance that no honest blinding can reach.
Neither restatement lowered a bar to fit a result: row 6 holds the same proof at two subjects
instead of five, and row 7 replaces an unreachable *condition* with a *measurement* plus the
caveat the pre-registration had already fixed for exactly this case. The distinction matters,
because a gate whose rows are edited to fit what happened is not a gate — so the original
wording of each stays in §5 beside its restatement, struck through and readable.

**What the gate cost to pass, and what it bought.** Nothing in the nine was free: row 5 found
a vocabulary leak on every surface a stopped run touches, including the published report's own
footnotes; row 7 found that September's panel was not blind, that the blinding instrument had
been written from the documents' surface, and two renderer defects behind that. Every one of
those would have reached the round's documents. The rows that merely came back green — 1, 2,
3, 4 — are the ones that took the longest and found nothing, which is what they are for.

---

## Spend

£27.92 of the £100 monthly cap at `0a771c1`. Everything in this file since was £0.
