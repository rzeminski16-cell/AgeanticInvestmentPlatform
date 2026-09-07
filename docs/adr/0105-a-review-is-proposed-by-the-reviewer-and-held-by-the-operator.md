# ADR 0105 — A review is proposed by the reviewer and held by the operator

**Status.** Accepted
**Date.** 2026-09-03
**Required by.** Roadmap §3.8, and by ADR 0081, which admitted the `post_trade_reviewer`
role and settled what it may say, and has had no closed position to read.
**Extends.** ADR 0081 (the reviewer scores the process, not the outcome), ADR 0074 (a
judgement is never a source reference), ADR 0102 (a judgement subtype is a value and a
table), ADR 0104 (a decision is written before the outcome, and the trade points back at it).

## Context

ADR 0081 decided the shape of the reviewer's output — a per-premise verdict from a closed
enum, a `process_quality` field free to disagree with the outcome, a platform-filled
`outcome` the model may read and may not restate, and free-text lessons — and said it runs
"once per closed position, over the premises as they were written, the decision as it was
recorded, whatever the monitor said while the position was open, and a deterministic
comparison of expectation against outcome". Theses (§3.5), findings (§3.6) and decisions
(§3.7) now exist. Three things the build had to decide were left open.

**What is a closed position?** ADR 0083 made a position a calculation over transactions,
not a row, so there is nothing to close. What the book has is a security whose held
quantity went from something to nothing on a date.

**Whose judgement is a review?** ADR 0081's Consequences say `process_quality` "is one
person's judgement about their own earlier judgement, held to a Judgement's standard". A
judgement's guarantee is that a *named person* held the view (ADR 0074), and a model is
not a person. Yet the same record has the reviewer *return* `process_quality`.

**What does "the outcome is platform-filled" mean when the book is in three currencies?**
A realised return on a London holding bought in pence and a book kept in pounds is a chain
of conversions before it is a subtraction (ADR 0081 says exactly this), and every figure a
review shows has to be a recorded calculation (invariant 3).

## Decision

### 1. A closed position is an episode of trades

For each security in a book, the trades in force are walked in order (the same walk the
pooled cost uses, ADR 0085). An **episode** runs from the first trade after the holding was
last nil to the trade that brings it back to nil; that trade's date is when the position
closed. A security bought, sold out, and bought again has two episodes and two reviews. A
holding still open has no closed episode and is not reviewed — the only condition ADR 0081
admits the role on is that the position is closed, and this is where it is enforced.

### 2. The outcome is code's, and every figure in it is a recorded calculation

For an episode, deterministic code computes what a review compares expectation against:

- **Cost**: each purchase's consideration and dealing costs, through `acquisition_cost`,
  converted into the book's currency at the trade's own date.
- **Proceeds**: each sale's cash effect and each dividend the security paid inside the
  episode, converted the same way.
- **Realised return**: `(proceeds - cost) / cost`, a new `@traced` function in
  `calc/outcomes.py` beside `assumption_delta`, so the figure carries its formula, its two
  sourced inputs and the code version.
- **Holding period** in days, from the episode's first trade to its last, beside the
  **intended horizon** the decisions stated, so the reviewer sees "sold after 4 months
  against 24 intended" as two figures rather than one adjective.

The decisions under review are the ones the episode's trades carried out (ADR 0104's
link), plus any hold or pass recorded on the same thesis while the position was open. The
premises are the thesis's, as they stand, withdrawn ones marked. The monitor's findings on
that thesis inside the episode are handed over as what the platform said at the time.

The ledger is persisted against the pass's job, and the outcome names the calculation ids,
so a figure on the review page resolves to its formula like any other.

### 3. The reviewer proposes; the operator confirms; the review is the operator's judgement

The `post_trade_reviewer` role runs once per episode, in a pass rooted on its own work order
(`tool="review"`), and its draft — per-premise verdicts, `process_quality` with a basis,
lessons — lands on the pass's job step as output. **Nothing is a judgement yet.** The
operator reads the draft beside the outcome and confirms it, amending any verdict, the
quality, the basis or the lessons, and *that* is the review: the third judgement subtype,
`reviews`, keyed on a judgement whose holder is the operator and whose basis is theirs.

The proposal is kept on the review row, as it arrived, beside what the operator confirmed.
Whether the operator agreed with the reviewer is itself decision data — the calibration
question ADR 0074 permits a stored view to answer — and it is not answerable if the draft
is overwritten.

This is the Suggested → Approved shape the Investment OS plan named as one to reuse:
`assumption_proposals` → `assumptions`, a model's number confirmed by a person before
anything reads it. The reviewer's draft is admitted on the same terms.

**Per-premise verdicts are rows**, `review_verdicts`, each naming the premise and carrying
the premise's statement as it read at review time, so a verdict survives the thesis moving
on. `untested` is available and the prompt says when it applies: a premise about a year not
yet filed when the position closed is unanswered, not held or failed by the price.

### 4. Every statistic on the analytics surface carries its `n`, in the type

`Statistic` has a required `count` and no constructor without one. Below `MINIMUM_SAMPLE`
closed positions (three, the comps table's own floor) a proportion is rendered as a tally
rather than a percentage. ADR 0081's argument is not repeated; the enforcement is the shape.

The four cells — process quality against the sign of the realised return — are the first
table on the page, and the two off-diagonal cells are the ones the page exists to make
reachable.

## What was rejected

**The reviewer writing the review row directly, with itself as the holder.** A judgement's
holder is a named person. A model's opinion stored as a person's would be the laundering
ADR 0074 forbids, one level up: not a figure, but a verdict nobody held.

**Marking an open position to compute an interim outcome.** ADR 0081 admits the role on the
condition that the position is closed, and an outcome over an open position is ADR 0079's
noise with a P&L attached.

**Reviewing a decision rather than an episode.** A position is usually opened by one
decision and closed by another, with adds and trims between. The thing that has an outcome
is the episode; the decisions are what it is scored against.

**A catalyst comparison.** ADR 0081 lists "whether the catalyst arrived by the date the
thesis named"; a thesis has no catalyst field today, and a comparison against a date nobody
wrote would be invented. The horizon comparison is the one the decision journal supports.

## Consequences

Post-trade review and decision analytics are the sixth and seventh working tools. The
review page lists every closed episode with its state — unreviewed, proposed, reviewed —
runs the reviewer from the page, shows the proposal beside the outcome, and confirms it as
the operator's judgement. The analytics page shows the four cells, the verdicts by kind,
the horizon adherence and the reviewer-operator agreement, each with its `n`.

The loop ADR 0079 named stays open by design: a lesson is a judgement, displayed and
compared and never cited, and the path from a lesson to a methodology change stays manual.
