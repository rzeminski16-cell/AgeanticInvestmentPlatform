# Interface overhaul — where this stopped, and what picks it up

**Paused 2026-08-28**, five tranches into ten, on branch `claude/gui-ux-overhaul-spec-6d92jn`.

This is the document to read before resuming. [The plan](interface-overhaul.md) says what each
tranche does and [the testing plan](interface-overhaul-testing.md) says what each must prove;
this says what is actually true of the tree right now.

---

## Read this first: the branch is red

**Thirty-three browser tests fail.** The in-process suite was last green at tranche 4 —
5,888 passed — and has not been run in full since tranche 5 landed.

They fail because tranche 5 rewrote ten templates and moved or reworded text that browser
tests assert on. The first one confirmed is the clearest example and probably the pattern for
most of them: the overview now says **"Start with two things"** on an empty database, where
the test still expects **"Nothing is waiting"**. That is the first-run state working exactly
as tranche 5 intended, and a test that had no way to know. The same class of staleness was
found and fixed in the in-process suite; the browser suite was not re-run before the commit.

**How this happened, because it is the useful part.** Tranche 5 was verified against targeted
subsets — the request suites, the component tests, the palette ratchet — and every one of them
passed. Subsets do not catch a page whose *words* changed, because the tests that read words
live in the browser suite and take twenty minutes. The lesson is not "run more subsets": it is
that a tranche touching templates is not finished until the browser suite has been seen green,
and the twenty minutes has to be spent before the commit rather than after it.

One of the thirty-three is a design question rather than a stale assertion, and it wants an
answer rather than a fix: **the seven planned tools now sit behind a collapsed disclosure on
the front door.** That contradicts that page's stated job — the shape of the product visible
on arrival rather than discovered — and `test_a_planned_tool_is_reachable_and_says_what_it_waits_for`
is right to complain. The likely correction is to ship the disclosure open.

The complete failure list is in the section at the end of this document.

---

## What is done and verified

| Tranche | State | Last full verification |
|---|---|---|
| **0 — Hold** | Done | Both suites baselined; ceiling, axe, swap ids, fifty routes |
| **1 — Vocabulary** | Done | 5,783 in-process · 127 browser |
| **2 — Tokens** | Done | 5,823 · 137 |
| **3 — Macros** | Done | 5,888 · 161 |
| **4 — Shell** | Done | 5,888 · 161 |
| **5 — Overview and requests** | **Code complete, suite red** | 33 browser failures, in-process not run |
| **6 — Console and gates** | Not started | — |
| **7 — Evidence and reports** | Not started | — |
| **8 — Portfolio, skills, knowledge** | Not started | — |
| **9 — Removal and hardening** | Not started | — |

### The debt, measured

The ramp ratchet opened at **1,837** raw Tailwind classes and stands at **1,594** — tranche 5
removed exactly the 243 the plan predicted. What is left, by area:

| Ramps | Where | Tranche |
|---:|---|---|
| 848 | `runs/` | 6 (console and gates), 7 (evidence) |
| 175 | `skills/` | 8 |
| 102 | `reports/` | 7 |
| 85 | `plans/` | 6 |
| 56 | `knowledge` | 8 |
| 18 | `portfolio/` | 8 |
| 310 | everything else | 8, 9 |

---

## Resume here

1. **Fix the thirty-three.** Then run **both** suites and hold the result. Nothing else should
   start until that is green.
2. **Tranche 6** is the largest in the plan: 705 ramps across the console and seven gates, and
   it carries the one piece of new model plumbing left — the `verdict` role and its step under
   [ADR 0087](../adr/0087-a-verdict-has-two-halves-one-composed-and-one-authored.md). Building
   it needs no model spend; the fake provider covers the tests. It does touch the workflow
   engine, so it wants a green base under it.
3. Tranches 7, 8 and 9 follow. Tranche 9 is where the ratchet reaches zero and becomes a hard
   assertion, and where the by-hand pass happens.

---

## What will still be open when every tranche is done

**Roadmap §2.1 and §3.1.** Both were sequenced *ahead* of this overhaul and neither was
started. §2.1 — five sections failing to draft — is blocked on data rather than on effort: its
diagnosis is pinned to the 2026-08-24 MSFT run, and that run's rows live on the operator's
machine. `scripts/list-runs.sql` and `scripts/export-run-diagnosis.sql` exist to get them off
it, read-only, and are still waiting to be run. Only the final confirmation needs model spend.

**The Firefox half of D7.** The correction asks for the wide-width navigation reveal to be
verified in Chromium *and* Firefox. Only Chromium is installed in the environment this was
built in, and it forbids installing another engine. The mitigation is structural rather than
tested: the shell ships `<details open>` and closes it with script, so it never depends on
revealing a *closed* `<details>` from author CSS — which is the behaviour that differs per
engine. The risk is low and it is unverified; one run on a machine with Firefox closes it.

**Two order-dependent test failures**, recorded in the testing plan. Both predate this work.

**Tranche 9's manual pass** — keyboard, 320px, 200% zoom, both schemes, scripting off. No test
substitutes for it.

---

## The thirty-three

_Being captured from a clean run as this was written; appended in the commit that follows._
