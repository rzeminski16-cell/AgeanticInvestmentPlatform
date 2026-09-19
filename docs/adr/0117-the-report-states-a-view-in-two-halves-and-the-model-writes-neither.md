# ADR 0117 — The report states a view in two halves, and the model writes neither

**Status.** **Accepted in part, 2026-09-18** — the composed half has landed (Phase 4.4).
The authored half, the redaction round and `RESERVED_OUTPUT_FIELDS`' extension to the
report remain Proposed, and ship only after the composed half has been judged alone, which
is what the two-shipping-events decision below is for.
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


## What landed, 2026-09-18 — the composed half

`aer/render/view.py` composes the block; `assemble_document` places it first, before the
at-a-glance numbers, because a position a reader meets after eighteen sections of evidence
is a position they meet last. Every figure in it is a row the run struck, with a marker
that resolves to the arithmetic.

**Composed had to be given a sharper meaning than this ADR gave it.** "Composed on render"
reads as though the renderer may do the arithmetic, and it may not: an implied upside is a
*number*, and a number in this platform is a traced calculation recorded by the step that
computes it. A renderer subtracting a price from a value would produce a figure no ledger
row accounts for and no footnote could point at. So `calc.basic.implied_upside` is struck
in the valuation step and the block assembles it. Composing is assembling; the word now
says so wherever it appears here.

**The distance is struck for both terminal methods and never their average.** ADR 0038
carries the two terminal assumptions separately and says their disagreement is itself the
finding; one tidy percentage would have discarded it at the last step.

**The scenario spread is supported and empty**, on every run so far. A scenario is the
operator's to define and none has defined one — the `scenarios` table is empty across the
whole stored corpus — so "bear and bull, as figures" describes a category the block fills
when a run has one and omits when it does not, exactly as the front page omits an empty
category. Worth knowing before the composed half is judged: on today's corpus the reader
sees a range, a distance and the levers, and no spread.

**"What would change the view" is the sensitivity grid's own swing**, a lowest and a
highest stored cell per grid with the two assumptions named in words. The first draft
reported one row per axis and the extreme of a grid is the same cell whichever axis names
it, so three levers came out reading $32.47, $32.47 and $32.25 under three different
headings — a label describing nothing. The pair belongs to the grid.

**The header line changed and `reports.rating` did not.** A run with a valuation prints its
base-case range; *"no view reached"* survives for a run with none, where it is true. The
rating column stays unwritten, as this ADR requires: a rating belongs to the authored half,
stored as an ADR 0102 judgement with a holder, a time, a basis and a falsifier, and a
column has none of those.

**One display defect this block found**, and it is not the block's: a dimensionless figure
takes its reading from words in its label, so the two upside rows — "perpetuity growth" and
"exit multiple" — rendered as `0.7%` and `-0.01x`, the same kind of figure in two notations
with neither chosen. "Upside" is now a percentage word and the rows are labelled with it.

## A correction, 2026-09-19 — the two-methods rule is not ADR 0038's

The section above says *"ADR 0038 carries the two terminal assumptions separately and says
their disagreement is itself the finding"*. **ADR 0038 says nothing of the kind.** It is
*Validator assists advise; deterministic verdicts cannot be overruled* — the `validator`
role's ADR, about citation and date advisories. The number is simply wrong.

The rule is real and its home is the specification: `docs/archive/PLAN.md`, Phase 3's
deliverables — *"driver-based FCFF DCF, terminal value (Gordon + exit multiple, both
shown)"*. No ADR was ever written for it, because it was never a decision anyone had to
argue; it is in `aer.calc.dcf`'s module docstring as the module's own rule and is enforced
there.

Recorded rather than edited, on this repository's standing rule that a decision record keeps
its words. The citation had already been copied into `aer.services.valuation_run`, and was
about to be copied a third time into `aer.render.glance`; both now cite the specification and
point here. Found while fixing roadmap §3.19 item 38, which is the same rule being undone one
layer further along — the front page printed one of the two methods as though it were the
answer.
