# ADR 0117 — The report states a view in two halves, and the model writes neither

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
**Date.** 2026-09-14
**Extends.** ADR 0087 (a verdict has two halves: one composed, one authored), whose split this
applies to the report itself rather than to a page. ADR 0102 (a thesis is premises, and a
premise is the judgement) supplies the record the authored half is stored in.
**Does not touch.** `RESERVED_OUTPUT_FIELDS` (`core/schemas/skill.py:73`). No model may
declare a rating, a recommendation, a target price, a fair value or a conviction, and this
ADR adds nothing to what a model may write.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F13, and open question 4,
answered by the operator on 14 September 2026.

## Context

`reports.rating` and `reports.confidence` are columns. `rating` is assigned `None` in exactly
one place and written nowhere. So every report the platform has ever produced prints *"no
view reached"* — not because no view was reached, but because nothing was ever wired to reach
one.

The audit's judges were unanimous on what that costs. Nine blind comparisons, zero chose the
platform, and the most-cited reason was that the console's answer said what it thought and the
platform's did not. A document that assembles eighteen sections of evidence, computes a
discounted cash flow, prices a comparables set, runs three scenarios and then declines to
conclude is not being careful. It is being unfinished.

But the audit also produced the counter-evidence, and it is why this ADR splits rather than
simply fills the column. **AstraZeneca's second run stated a view, argued it, and moved no
judge's verdict.** Stating a conclusion is necessary and demonstrably not sufficient. If both
halves ship at once and the verdict moves, nothing can attribute it.

## Decision

**The report states a view in two halves. Code composes one; the operator authors the other;
the model writes neither.**

### The composed half — deterministic, and it ships first

Assembled in Python from rows the run already holds:

- the base-case valuation range, with the method that produced it named;
- the scenario spread — bear and bull, as figures;
- once a price exists, the implied upside or downside against it;
- `what_would_change_the_view`: the inputs whose movement flips the sign, read from the
  sensitivity grid.

**No adjectives.** No "attractive", no "compelling", no "cautious". It is a range, a method,
a distance from the price, and the levers — every one of them a recorded calculation with a
footnote. It cannot go stale, because it is composed on render from the report's own frozen
record, which is ADR 0087's test applied unchanged.

It is also, on its own, more than the report says today, and it costs no model call.

### The authored half — the operator's, recorded as a judgement

One or two sentences of the operator's own view, entered at the final gate, stored as an
ADR 0102 judgement, and subject to that ADR's rules rather than to a new set:

- **The basis cannot be blank.** The same rule `settle_by_hand` and the thesis withdrawal
  already enforce.
- **At least one falsifier, tied to a stored identifier.** A view with nothing that would
  defeat it is not a view; it is a mood. The falsifier is a premise in ADR 0102's shape —
  metric, comparator, threshold, unit — so the monitor can watch it the moment the report
  ships. This is the seam between the research tool and the judgement layer, and it is the
  reason the view is worth stating at all.
- **It carries the operator's name and the time it was written.** A judgement without a
  holder and a time is the thing ADR 0074 refuses.

### The model writes neither, and writes around both

The section writer receives both halves as inputs and writes the prose that introduces them,
exactly as it already writes prose around a figure it did not produce. A reply that declares
any reserved field is refused, unchanged. **A rating string is never model-written**, and the
acceptance test is a scan of the model's own reply rather than a scan of the rendered page —
because a page can be clean while the reply that produced it was not, and the reply is where
the rule has to hold.

### They ship in that order, deliberately

The composed half lands and is judged alone. Only then does the authored half land, and the
round that judges it also judges the **same runs with the authored half redacted**. Same
runs, a few extra judge reads, no extra live spend — and the two questions *"does stating a
view move the verdict?"* and *"does the operator's own voice move the verdict?"* stay
separable.

### The conclusion is generic; only the facts are personal

The closing section (F3) reads the operator's own book and states what a position would do to
it. The stated view does not. It is a view about a company, held by the named operator, with
its basis and its falsifier — not a recommendation addressed to a reader.

That is a product decision and it is also the safe shape: what makes a conclusion regulated
advice is tailoring it to a specific person's circumstances, not stating it. **Personalised
facts, generic conclusions.** The closing section computes consequences for a book and issues
no instruction; the view states a judgement about a business and is addressed to nobody. Every
user-facing surface still carries the disclaimer, unchanged.

## What is given up, named rather than discovered

**A report cannot conclude without the operator.** A run the operator declines to judge prints
the composed half alone, and that is the whole document's conclusion. There is no automatic
view, no fallback rating, and no "the model's read, for what it is worth". For a product whose
argument is that judgement is the operator's, that is the feature; for a reader who wanted a
one-word answer from a machine, it is a refusal.

**`reports.confidence` stays unwritten for now.** Whether the authored view needs a stated
confidence, and whether that is the thing F14's calibration eventually measures, is open
question 9 and is decided with F13 rather than after it. A confidence column filled with a
number nobody defined is worse than an empty one.

**Two shipping events instead of one**, and the attribution they buy costs a phase boundary.

## Consequences

**A run with a valuation never prints *"no view reached"* again.** That string survives only
for a run that produced no valuation at all, where it is true.

**The final gate gains a field**, and it is optional. Approving without authoring a view is a
legitimate outcome that produces a legitimate report.

**The monitor gets its first premise for free.** A view's falsifier is a premise, and a
premise is what the monitor watches — so the seam between "research finished" and "thesis
being watched" closes at the moment the operator states a view, rather than in a separate
sitting they may never have.

**ADR 0115's adversary has a target.** It argues against the stated view; without one it
argues that the evidence supports none. That dependency is why F13 precedes F2.

## Alternatives considered

**Let the model state the view.** It would be good at it, it would be fast, and it is the one
thing this platform exists not to do. It also puts a rating into a document with no holder, no
basis and nothing to check — which is precisely what `RESERVED_OUTPUT_FIELDS` was written to
prevent, and reversing that on the report while keeping it on sections would be incoherent.

**Ship only the composed half and stop.** Defensible, cheap, and it may be where this ends if
the measurement says the authored half moves nothing. It is not where it starts, because the
composed half alone is a range and a distance, and the judges' complaint was about the absence
of a position rather than the absence of a number.

**Ship both at once.** What the operator originally wanted, and it makes the measurement
unable to attribute what moved — which is the failure mode the entire delivery plan is built
to avoid. Resolved by the redaction round: both ship, one round judges them apart.

**Store the view on the report rather than as a judgement.** A column is simpler. It also has
no holder, no time, no basis and no falsifier, and the moment the operator revises the view
there is nothing to supersede. ADR 0102's record already holds all five.
