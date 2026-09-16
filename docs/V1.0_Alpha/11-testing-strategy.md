# Testing strategy for V1.0

*What proves each feature, what asserts each invariant after the invariants change, how the
judgement layer is tested without spending a penny, and what must be green before a
measurement round is allowed to spend one. Compiled 14 September 2026.*

`docs/developers/testing.md` describes the suite that exists. This describes the suite V1.0
needs, and is normative where the two disagree about V1.0 work.

---

## 1. The three rules that do not change

**Tests run with no network and no model spend.** External HTTP is replayed from stored
fixtures through `respx`; model calls go through a fake provider. Everything below obeys
this, including the judgement layer, including the refresh, including the journey harness.
A suite that spends money is a suite nobody runs, and a suite nobody runs is not evidence.

**The full suite is two processes.** `pytest --ignore=tests/e2e`, then `pytest tests/e2e`.
Playwright's synchronous API leaves a running loop on the main thread that wedges every
pytest-asyncio test after it. The journey harness V1.0 adds (§3.1) runs inside the browser
process, for the same reason — it drives a browser, so it inherits the constraint rather than
earning a third command.

**One pytest process per database.** The suite empties tables between tests. Two runs sharing
`aer_test` delete each other's rows and fail in whichever was mid-test, as *"no user exists"*
moments after a fixture committed one. Every concurrent run gets its own
`AER_TEST_DATABASE_URL`. This is written here because V1.0's delivery plan runs work in
parallel sessions and this is the failure it will hit first.

---

## 2. The baseline, honestly stated

As measured on 11 September 2026, during the readiness audit:

| Layer | Count | Time | What it buys |
|---|---|---|---|
| Default suite | 6,865 passed, 2 deselected | 33 min | Everything but the browser |
| Browser suite | 183 passed | 8 min | What an in-process HTTP client structurally cannot see |
| Evaluation gate (`just eval`) | 241 | inside both | The ten blocking metrics |
| `mypy --strict` on `core/` and `calc/` | 410 files clean | 1 min | The correctness core |

And two things the audit found wrong with it, both of which V1.0 must fix before it can trust
any of the above:

- **The suite was order-dependent, and the audit fixed it in the same pass.** `just
  test-shuffled 20260811` gave 28 failed and 7 errors against 6,830 passed, across
  `test_db_schema.py`, `test_decisions.py` and `test_thesis_monitor.py` — which were the
  *victims*: the leak was rows committed by earlier modules, and the fix (F-03) empties every
  table at fixture setup rather than teardown. The shuffle is over file order, so a seed only
  reproduces an ordering at the commit it was recorded on. What remained on 16 September was
  three latent hazards and the verification: a test-class filter in
  `test_contract_schema.py`, the unordered `select(User)` reads in the drivers, and a nightly
  shuffled CI job with a fresh seed on its summary line, which is what verifies the widened
  runner from now on. All three are done, and the first fresh seed on the widened runner —
  `just test-shuffled 937541`, 16 September — gave 6,982 passed, 2 deselected, 0 failed, in
  26 min 31 s.
- **CI was red on every run from 2026-09-09.** The cause recorded at the time was `ruff
  format --check` on Markdown snippets; that was never fixed by a commit and stopped failing
  by itself, because ruff formats Markdown only in preview mode. By 14 September the job was
  red for two other reasons nobody had read: an unsorted import (`ruff check`, I001) and a
  test pinning a document the folder tidy had moved, which aborted collection of the whole
  default suite. **The suite is only evidence if somebody reads its output**, and a
  permanently red job teaches everyone to stop reading. Fixed 16 September, with a guard test
  (`tests/test_pinned_paths.py`) for every documentation path a test pins.

**Both were Phase 1 work, and both came before the journey harness**, because the harness is
an instrument and an instrument that reports differently depending on the order it ran in
measures nothing.

---

## 3. Five new instruments

V1.0 needs five things the suite does not have. Each is named here with where it lives, what
it asserts, and how it fails.

### 3.1 The journey harness — the instrument for ISSUE 1

**Lives in** `tests/e2e/journey.py` (the browser half) and `audit/smoke.py` (the shape half).

**The claim it makes measurable.** *Every state a run can stop in offers a labelled way
forward, in the interface, without the terminal.* The audit destroyed £14.41 on two runs that
had no such way forward, and the loss was not that the platform failed — it was that the
platform stopped somewhere the operator could not leave.

**The inventory comes from code, never by hand.** A row is generated for every member of the
cross product that can actually occur:

- every non-terminal `JobStatus`;
- every gate kind in the approvals model, in each of its three dispositions — pending,
  decided, stale;
- every pause reason the engine can write (`_record_node_failure`, seal drift, budget scope
  `per_run` and `monthly`, unverified citations, a fired look-ahead trigger — the last of
  which F1 deletes, and the harness must then lose the row rather than keep a dead one);
- every `AerError` code that reaches a page.

A state nobody can reach is a defect in the harness or a dead branch in the engine, and the
harness says which by refusing to skip: an unreachable row fails with *"no path constructed"*
rather than passing quietly.

**Three assertions per row.**

1. **A forward control exists**, is enabled, and its label names what it does in the
   operator's vocabulary. *Continue*, *Approve and carry on*, *Raise the cap to £12 and
   resume*, *Start again from the plan* — never *Retry*, never a bare identifier.
2. **The visible text is clean.** Zero UUIDs (`[0-9a-f]{8}-[0-9a-f]{4}-`), zero shell
   commands (`just `, `uv run`, `aer `, a leading `$`), and none of the 135 code identifiers
   the audit counted on gate pages. This is the vocabulary ratchet, asserted rather than
   hoped for.
3. **Pressing it moves.** The job's state after the click is not the state before it. A
   button that re-renders the same dead end is the defect, not the fix.

**It starts RED, deliberately.** Every inventory row lands first as `xfail(strict=True)` with
the audit finding that generated it in the reason string. A fix flips one row to a pass, and
`strict=True` means a fix that does not actually work fails the build instead of quietly
passing as an expected failure. The harness is green when the inventory is empty of xfails.

**The split, and why.** Shape assertions — the forward control, the vocabulary, the state
transition — run against the fake scene, cost nothing and run on every commit. Anything
needing a real filing (a gate page rendered over 496 unmapped concepts, a bank's sector gate)
runs against a **stored run** restored from the corpus in §3.4. Neither half calls a model.

**As built, 16 September 2026.** The inventory is `tests/journey_inventory.py`: 52 rows, read
from code — the five non-terminal job statuses; the seven gates a run can raise, read from the
web layer's step map (so BUDGET and THESIS, which no run opens, generate no row) in four
dispositions, pending, rejected, stale because the page moved, stale because the seal drifted;
the budget guard's two scopes; every `TriggerKind` at the final gate plus unverified citations;
every error code the engine can record on a failed step; and the three error classes the runs
pages catch on the problem page. Two product-side enums made that readable
(`aer.workflow.pauses.PauseReason`, `BudgetScope`), and the same module names the three gates
whose approval is verified against the live payload rather than a sealed hash
(`LIVE_PAYLOAD_GATES`): for those, seal drift is a state the engine cannot produce, the
inventory generates no row, and a test pins the set to the workflow's own calls. The builders
and the three assertions are written once, in `tests/journey_harness.py`, against a
`Surface` protocol; the browser half supplies a Playwright page (`tests/e2e/journey.py`,
collected by `tests/e2e/test_journey.py`) and the shape half supplies the parsed HTML of the
application running in-process (`audit/journey.py`, `python -m audit.smoke --journey`, a step
of the CI test job). The vocabulary is read from code too: shell commands from the justfile
and the CLI's registered commands, identifiers from the metric names, step keys, trigger
kinds, error codes and status values.

The first measurement, on every row the fake scene can construct: **32 red, 20 not yet
constructible, 0 green.** The console prints the workflow's step keys on every run and tells
the operator to type `just worker` on every unfinished one (`aer reseal` after seal drift), so
the vocabulary assertion fails everywhere; the rows that also lack a way forward are the
rejected gates (nothing offers to start again), the stale gates (nothing offers to decide
again), both budget ceilings (named in a sentence whose only control is a bare *settings*
link), the failed step with a remedy (stated, with nothing leading to the settings it names),
the queued run and the problem page. The forward controls that exist do work: continuing a
stranded, paused or failed run moves it, raising a per-run ceiling and continuing moves it,
and every pending gate's link leads to a page whose approval moves it. By assertion: *text*
red on 31 rows, *control* red on 17, *press* measured on the 15 rows with a control and red on
none. `STILL_RED` records each red row **by assertion** with the measured defect,
`UNCONSTRUCTED` the 20 unbuildable rows with the reason. Per assertion rather than per row
because the fixes land in that order — Phase 1.2 puts controls on the rejected and stale
gates while the console still speaks in step keys, and a record that could only say "red"
would not register it. The browser half marks each red row `xfail(strict=True)` on the one
exception it may raise, and both halves fail a row whose measured red set differs from the
recorded one in either direction, so a fix that works has to move the record as well as the
page, and a regression on an assertion the record calls green fails the build at once.

The first fix moved it the same day. Phase 1.2 (ADR 0123) put a control on the rejected and
stale gates, and the `control` assertion left the record on all twelve of those rows in both
halves — *text* still red on each, as the ratchet is Phase 1.4's — while the same change made
the conflict problem page constructible: 33 red rows and 19 not yet constructible, with
*control* red on six (the queued run, the two budget ceilings, the failed step's remedy and
the two problem pages) and *press* measured on 27 and red on none.

### 3.2 The invariant assertions

Three of the eight invariants change in V1.0 and two gain new obligations. An invariant that
is enforced only by a sentence in `CLAUDE.md` is enforced by nothing, so each row below names
the test.

| Invariant | V1.0 change | What asserts it |
|---|---|---|
| **1** Every fact traces to a hashed artefact | Gains the **UK path**, where a fact is this platform's own parse rather than a registry's aggregation (F19) | `just verify-artefacts` over the corpus, plus a golden test that `extract_ixbrl` over a stored accounts document produces byte-identical facts twice. A parsing error is a wrong number with a perfect audit trail, so this is the boundary tested hardest |
| **2** Code confirms a citation | Unchanged | `tests/test_citations.py`; the boundary scanner at `:74` |
| **3** No figure without a fact, a calculation or an attestation | Gains **supersession**: a superseded report's figures must not reach a current surface | A partial unique index (migration M2) plus a service test that a second current report for one company raises, and a render test that a superseded report renders only at its own address |
| **4** Point-in-time enforced at acquisition | **Removed** (F1, ADR 0113) | A structural absence test: a text scan over `src/` and `docs/` for `point.in.time`, `look_ahead`, `temporal_compliance` and `as_of`, allowing only the ADR that records the removal and the retrieval-timestamp code F11 depends on. A deletion nobody asserts is a deletion that grows back |
| **5** Units carried; a mismatch raises | Gains the **derived bank revenue** (ADR 0114) | A property test that the derivation raises on a currency or scale mismatch between net interest income and non-interest income, and that it is refused entirely for a filer the sector gate has not confirmed as a bank |
| **6** Cost metered and capped | Gains the **refresh** and the **scheduler** | The fake provider counts calls; a refresh of a stored run must make no more than the mechanism document's ceiling, and the scheduler's daily pass must make **zero**. "Never spends" is a test, not a promise |
| **7** Skill files additive-only | Unchanged | The attack corpus in `tests/skill_corpus.py`, every member of which must be refused with a readable reason |
| **8** Untrusted content is data | Gains the **evidence boundary** (F16, ADR 0119) | The injection corpus in `tests/injection_fixtures.py`, extended: an instruction-shaped excerpt is printed into a report, that report is fed to a later run's planner through the prior-research path, and the later run's plan must be byte-identical to the plan produced without it |

That last test is the shape the whole strategy wants: **not "the wrapper is present" but "the
outcome is unchanged"**. A test that asserts the label exists proves the label exists.

### 3.3 The judgement layer, tested without a live run

F9 through F14 build the half of the product that has never executed. None of it needs a
model to be tested, because none of it does arithmetic in a prompt.

**The scene.** `tests/scene_fixtures.py` gains a *held position* scene: a stored run, a thesis
of four premises with thresholds, a decision that points at it, a fill, and ninety days of
synthetic end-of-day prices. Everything downstream is a pure function of that scene.

- **Premise resolution** (ADR 0102's `metric · comparator · threshold · unit`) is
  deterministic, so it is a table test over the four unresolvable cases the mechanisms
  document names, plus a `hypothesis` property: resolution never silently substitutes a
  different metric, and a restated figure produces a *changed* finding rather than a
  retrospectively altered one.
- **Findings** are generated by code from facts and prices. The test asserts which findings
  fire, in what order, and that a dismissal is recorded with its reason rather than deleting
  the row.
- **The price-move finding** runs on the synthetic series: a ±2% day, a sign change, and a
  threshold crossing each produce exactly one finding, and a series with none produces none.
  This is where the audit's *"the system holds more than it shows"* is finally asserted the
  other way round.
- **The monitor's cadence** is tested against a frozen clock, never `now()`. Monthly means
  monthly, and the test that proves it is the one that does not read a real clock.
- **The review's four combinations** — right for the right reasons, right anyway, wrong for
  good reasons, wrong for bad reasons — are a closed set, so the test is exhaustive. A form
  that lets the two questions be conflated fails it.

**Where the model is involved** — the reviewer's narrative, the adversary's case — the fake
provider returns a fixed reply and the test asserts what the platform *does with it*: that
the number came from the calculation record and not from the reply, and that a reply
containing a figure the record refutes is dropped before drafting (F2's seeded false
challenge).

### 3.4 The replay corpus — the £0 regression estate

**What exists**, read from the database on 14 September 2026: 6 jobs, of which **5 carry a
complete eighteen-section draft** and 3 are approved and immutable; 3,417 calculations; 46,744
financial facts; 85 source documents; 832 artefact files over 139 MB.

This is the delivery plan's entire offline economy and every *"re-render the five stored runs
and assert the change"* criterion depends on it. It is therefore treated as an asset with its
own obligations:

- **It is backed up before any migration**, and the backup is verified by
  `just verify-backup` rather than assumed. Invariant 1's guarantee currently rests on a
  restore path that has never been exercised; Phase 1 exercises it once and records it.
- **Every document-affecting change ships with a replay proof.** `aer replay-draft` reads the
  archived replies back under today's rules; `aer replay-run` re-derives what the run
  produced from what the run wrote down. Both spend nothing. A prompt change that cannot be
  proved by replay buys one `aer rehearse-section` at about 30p — not a full run.
- **Re-rendering all five is a CI job**, not a manual step, with the outcome asserted:
  zero contradictions, zero withheld figures the record can supply, and — after F8 — the
  multiples, segment figures and implied range that the runs already computed and threw away.
- **The corpus is never edited to make a test pass.** A test that needs different data needs a
  fixture, not a rewritten run. The runs are evidence of what the platform did in September,
  and evidence that gets tidied is not evidence.

### 3.5 The measurement harness — and the thing it must not be

ISSUE 2's target is a number: *at least 3 of 6 comparisons do not choose the console, and at
least 2 of 6 choose the platform*. September's panel exists only as its output. A repeat of it
would be a different instrument wearing the same name, so **the panel becomes code before it
is used again**, and the code is tested offline against September's recorded reads.

`audit/judges/` gains:

- **The rubric, lifted verbatim** from `judges/reads.json`, asserted by a test that the key
  sets diff empty against the recorded reads. A rubric that has drifted by one key is a new
  instrument.
- **Identically-shaped A/B paths.** Both documents are rendered through the same neutral
  template. The blinding test runs over September's texts and asserts that the platform-isms
  are gone — section ordering, footnote markers, the header block, the word *"as at"*, the
  design system's own vocabulary.
- **A seeded assignment**, so which document is A is reproducible and balanced across the
  round.
- **A recorded identity-guess hit rate.** Each judge is asked which document came from which
  system. At chance, the blinding held; materially above chance, the comparison is reported
  with that caveat attached and never without it.
- **The redaction round** for F13's attribution problem: the same runs judged again with the
  authored half of the view removed, so *"the view moved the verdict"* and *"the operator's
  own words moved the verdict"* are separable. Extra judge reads, no extra live run.

Every one of these tests runs offline. The judge reads themselves are marked `live_llm` and
are excluded from the default suite, as every billable call already is.

**Pre-registration is part of the harness.** The readings in Phase 5's kill-gate table are
written to a file, committed, and hashed **before** the round runs; the scorer reads that file
rather than an argument. A gate whose thresholds can be edited after the result is not a gate.

---

## 4. What proves each feature is done

Offline unless the row says otherwise. This is the same *Done when* as
[`04-feature-specifications.md`](04-feature-specifications.md), restated as the test that
carries it.

| # | Feature | The test |
|---|---|---|
| F1 | Point-in-time removal | The structural absence scan (§3.2); all five stored runs still replay; the blocking metric set shrinks by one and `just eval` is green at the new count |
| F2 | The adversary argues the opposite case | A seeded false challenge — a figure the calculation record refutes — is dropped before drafting; no run's disagreements section contains an unresolved item; the appendix is rewritten after a settle rather than at `validate` |
| F3 | The closing section | Every figure in it is a recorded calculation with a footnote; `RESERVED_OUTPUT_FIELDS` is still refused; the section is absent, not empty, when the book is empty |
| F4 | The refresh | A refresh of a stored run re-drafts between 3 and 6 of 18 sections, leaves the rest byte-identical, names every material move in the change summary, and makes no more model calls than the ceiling. Materiality is a table test: ≥2% relative, a sign change, and a premise-threshold crossing are always material |
| F5 | The workbook | The four tests in [`09-the-workbook.md`](09-the-workbook.md) §7, of which the load-bearing one is the recompute: change `Revenue_growth` by a point, recalculate in LibreOffice headless, and assert the per-share result equals the platform's own sensitivity grid |
| F6 | Ask | A fixed question corpus with expected tiers; a `hypothesis` property that the resolver **errs upward only** and never resolves below the true tier; the post-hoc citation check catches a mis-resolution; a question outside the record is refused with a price rather than guessed |
| F7 | Primary-source depth | The dimensioned-facts carve-out reaches the segment section and no other; the 20-F path produces product-level revenue; the checklist rows for segment revenue and guidance move from *absent* to *present-sourced* |
| F8 | Print what exists | Re-rendering the five stored runs produces multiples, segment figures, a market capitalisation and an implied range, **with no sentence denying any of them**. The denial is the regression to guard: a document that both prints a figure and says it is unavailable is worse than one that withholds it |
| F9 | The thesis | The held-position scene (§3.3); a premise cannot be saved without a metric, a comparator, a threshold and a unit; a withdrawn premise supersedes rather than deletes |
| F10 | Decisions | A decision points at a thesis version, not a thesis; `pass` is a decision and is recorded; a trade points back at the decision that authorised it |
| F11 | The monitor | Deterministic resolution over the scene; the four unresolvable cases refuse with their own reason; a restatement produces a new finding |
| F12 | Risk and the pre-trade check | The same shock produces the same figure on the risk page and in the closing section — one assertion over two surfaces, which is the only way that claim can be trusted |
| F13 | The stated view | A run with a valuation never prints *"no view reached"*; a run the operator declines to judge prints the composed half alone; **no rating string is ever model-written**, asserted by a scan of the model's reply against `RESERVED_OUTPUT_FIELDS` |
| F14 | Review and analytics | The four combinations, exhaustively; the review reads the retrieval timestamps F1 kept, and a test asserts it cannot read anything published after the decision |
| F15 | Scheduling | The daily pass against a frozen clock makes zero model calls; an idempotency key means a double fire does one thing; a missed run appears on the health page rather than silently |
| F16 | The evidence boundary | The extended injection corpus (§3.2, invariant 8) |
| F17 | Auth and sharing | Deferred for V1.0 (open question 5). When it lands: two accounts with separate books, and a shared pack that opens with no account and resolves every footnote |
| F18 | Model portability | A full run on a second provider with the same gates and the same verification results; the cost table prices both |
| F19 | The UK path | **The offline refusal test inverts.** `audit/smoke.py` proves today that a domestic LSE ticker is refused at `acquire`; it must prove that the same ticker resolves against Companies House, and the refusal case is rewritten around a company in neither registry. Plus: a UK bank's SIC 2007 code fires the sector gate; a sterling valuation with no confirmed gilt yield **refuses** rather than borrowing the US rate; and `fetch_facts` over a stored accounts document produces the same facts twice |

---

## 5. The gate before a measurement round

A measurement round costs about £21 (Phase 5) or £31.50 (Phase 7) and a day or two of the
operator's attention, and its output is a verdict that decides whether the plan continues. It
is therefore the most expensive thing in V1.0 to get wrong, and the cheapest to protect. **All
of this must be true before the round is allowed to spend:**

1. Both suite processes green, with counts recorded in the round's own notes.
2. `just eval` green, at the post-F1 blocking count.
3. `just test-shuffled` green on three seeds, one of them fresh. Not "green on the seed we
   fixed".
4. `just lint`, `just typecheck` and `just hooks` leaving the tree unchanged — and the CI job
   that runs them **actually green**, not red-and-ignored.
5. The journey harness green on every inventory row, with zero remaining xfails.
6. All five stored runs re-rendered, with zero contradictions.
7. The blinding dry run over September's texts passing, with the identity-guess hit rate at
   chance.
8. The pre-registration file committed and hashed.
9. `aer preflight` reading *Ready to run*, and `just test-live` passing — the fraction of a
   penny that proves the key, the router and the ledger work before £21 depends on them.

A round that starts with any row unmet produces a number nobody can defend, and the number is
the only reason to run it.

---

## 6. What this strategy does not test, and what that costs

Said plainly, because an untested area that nobody has named is an untested area everybody
assumes is covered.

- **Filing diversity.** Four companies once F19 lands — one US large cap, one 20-F filer, one
  bank, one domestic UK filer. A fifth sector meets its first unmapped concepts in production.
  Mitigated by the unmapped-concept gate being an operator surface rather than a failure, which
  is a design choice, not a test.
- **The real Excel.** The workbook is written by `openpyxl` and recalculated by LibreOffice.
  Microsoft Excel is never in the loop. The formulas are a documented subset for that reason,
  and the residual risk is a rendering difference, not an arithmetic one.
- **Load.** Single operator, single worker. Nothing measures concurrency because nothing
  needs to until F17 lands, and F17 is deferred.
- **The browser matrix.** Chromium only.
- **Windows.** The operator's own acceptance pass found Windows-specific problems; this
  session's environment is Linux and cannot reproduce them. They are carried forward as
  untested, not as fixed.
- **The judges themselves.** Three model judges and the operator's own read. Disagreement is
  reported, never averaged. A panel that agrees with itself is not a measurement, and the
  identity-guess hit rate is the only guard against a panel that is agreeing about the wrong
  thing.

---

## 7. The sequence

Testing work is not a phase; it is the first item inside each phase. But three things must
happen before anything else, and they are Phase 1's opening:

1. **Fix the order dependence.** Until this is done, no other result means anything.
2. **Fix the red CI job** and keep it green. A build nobody reads is a build that is not run.
3. **Build the journey harness red**, from the inventory, before the first dead end is fixed —
   so that every fix afterwards has a number it moves.

Everything in §3 and §4 follows the feature it proves, in the same session, in the order the
delivery plan sets. A feature whose test arrives a session later is a feature that shipped
untested, whatever the commit history eventually looks like.
