# Running a report

What happens between pressing *start* and holding a finished document, and what each
approval commits you to.

---

## The shape of a run

A **run** is the unit of work: one research request in, one cited report out. It executes
in the background worker, is recorded step by step, and survives a crash — restart the
worker and it resumes from the last completed step rather than from the beginning.

```
plan → [GATE] → acquire → classify → [gate] → peers → [gate] → themes → [gate]
     → prices → extract → [gate] → calculate → research (×5, parallel) + comps
     → assumptions → [gate] → value → draft → validate → red team → [GATE] → render
```

Seven places can stop and wait for you. **`[GATE]` — the plan and the final review —
always does.** The other five appear only when the company makes them necessary.

## Watching it

The console at `/runs/{id}` is built for the case that matters: **a step that calls a model
changes nothing for minutes.** It shows every step the workflow declares — not only those
that have started — a pulsing marker and a ticking clock on the one running, and a "server
last checked at…" line driven by a heartbeat on the event stream.

Between them those two things distinguish a healthy run mid-thought from a dead worker,
without either pretending to more certainty than it has. Without JavaScript the page falls
back to a meta refresh and still works.

## Gate 1 — the plan *(always)*

**`/runs/{id}/plan`.** The first thing that happens is that the platform spends about
£0.15 proposing what it intends to do: the sections it will write, the sources it will use,
what it expects to cost, how long it expects to take, and the risks it can already see.

Then it stops.

**Approving this commits you to the money.** Everything expensive is downstream — the
drafting step alone is typically the largest single cost in a run. Read the source list: if
it does not name the filings you would have reached for, that is the cheapest moment to
find out.

## The conditional gates

These appear only when the run needs a judgement it will not make for you.

**Sector specialist.** Raised when the company's accounting differs enough that the
standard treatment would be wrong. A bank has no classified balance sheet, so current
assets and current liabilities are not thin — they are *undefined*, and asking for them
would produce a report describing a filer as disclosing poorly for keeping its accounts
exactly as it must. Confirm the classification and the model changes with it: a confirmed
bank is valued on residual income over its book value, and the discounted cash flow is
refused rather than footnoted.

**Peer set.** Comparable companies are only as good as the peers, and a model proposing
peers is proposing an opinion. It suggests; the registry resolves them to real securities;
you confirm. No comparables table is computed until you have.

**Theme set.** A bounded slate of research themes, proposed by a model and confirmed by
you before any of them becomes an edge in the knowledge graph. A failed call proposes
nothing rather than guessing.

**Unmapped concepts.** Raised when a filing uses tags the concept map does not know. The
gate names the lines that would be lost, so a silent gap in a financial statement becomes a
decision instead of an absence.

### Assumptions — the one gate that approves work not yet done

**Raised whenever a valuation model applies.** A run whose sector mandate blocks every
model never reaches a forecast, so it does not stop to approve one — a gate is only a
control if clearing it achieves something.

This is the only gate that asks you to accept inputs rather than outputs. A model proposes
only the numbers **no filing can answer** — a terminal growth rate, an equity risk premium —
each with its justification, and never a number that could have been read off a statement. A
discounted cash flow's gate asks for about nine; a bank's asks for three, because its model
reads three, and it is asked for no revenue path or exit multiple it will never use.

Approve these and the valuation runs on them. Every figure downstream carries them as
recorded inputs, so a report can always answer "what was this resting on?"

If a required input is missing — a beta, a risk-free rate — the gate stops and you supply
it there, then resume. It used to proceed instead, which left a run pausable but not
resumable.

## Gate 2 — the final review *(always)*

**`/runs/{id}/review`.** The drafted report, exactly as it will be stored, alongside:

- **The validation results.** Citation accuracy, temporal compliance, numerical
  consistency, source coverage and completeness — deterministic checks, each a number
  against a threshold. The model's own validator *advises* and cannot overrule them.
- **The red team's bear case.** A separate pass, working from its own context rather than
  the drafter's, attacking the thesis it was handed.
- **Every claim**, at `/runs/{id}/claims`, with whether its evidence verified.
- **Every source**, at `/runs/{id}/sources`, including the ones the run refused and why.

Approve, and the document is rendered and frozen. Reject, and nothing is.

## What an approval actually is

Both universal gates show you a payload **and a hash of exactly that payload**, and your
approval carries that hash back. If what the run produced changed between the page being
served and the button being pressed, the hashes differ and the workflow refuses to
continue — an approval of something else is not an approval of this. The page and the run
build that payload from the *same function*, so "what was shown" and "what was approved"
cannot drift apart.

Approving never executes anything inline. It records the decision, commits, and enqueues.
A gate approval that ran the remaining steps inside the web request would hold your browser
open for the length of a research run and abandon it if you closed the tab.

## Money

Every model call is priced in pounds at the provider boundary and checked against two
ceilings: the run's own cap and the month's. The engine **refuses to start** a step whose
projected cost would break either. A cap that only warned would not be a cap.

Actual spend is metered per call into a `costs` table and never recomputed from estimates.
`/costs` shows it per role, with the prompt-cache hit rate.

One consequence worth knowing: a step with no cost estimate is invisible to the guard.
That is a recorded lesson rather than a theoretical risk — it is why every step now
declares one.

## Point-in-time

If the request is in point-in-time mode, nothing published after the as-of date may support
a claim. This is enforced **when a source is acquired**, not filtered afterwards, and
checked a second time on the latest date before the report is rendered.

It is a *selection* over the whole record rather than a filter on it, which matters for
restatements: you see what a reader could have seen then, not today's numbers with the
recent ones removed.

## When a run stops

- **Cancelling** is a decision, recorded like any other. Work already done is kept.
- **A crash** loses nothing: steps are recorded as they complete, and the worker resumes.
- **A terminal failure with no report** can be superseded, which re-runs the plan step on
  the same work order. If you fixed a skill in between, the pin set is compared against the
  enabled skills' current versions, so a re-plan picks up your fix rather than silently
  reusing the version you just replaced.

---

**Next:** [reading a report](reading-a-report.md) · [troubleshooting](troubleshooting.md)
