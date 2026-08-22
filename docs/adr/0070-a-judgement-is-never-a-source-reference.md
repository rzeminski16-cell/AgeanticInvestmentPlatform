# ADR 0070 — A judgement is never a source reference

**Status.** Proposed
**Date.** 2026-08-22
**Required by.** `docs/investment-os.md` §5, which names this the single most important rule
in the expansion, and by ADRs 0075 and 0077, whose entire output is a view somebody holds.
**Extends.** ADR 0066. Traceable and possible are different properties; traceable and
meaningful are different properties too.

## Context

The new domains are full of non-numeric positions a person holds. A thesis premise —
"pricing power survives the input-cost cycle". A decision to trim on valuation rather than
on evidence. A post-trade classification of *good process, bad outcome*. A conviction. A
confidence of 72% that a premise still holds.

None of these is any of the four record kinds, and the misfits are specific rather than
awkward. Not a **Fact**: nobody filed it, and there are no bytes to hash. Not a
**Calculation**: no formula, no `function_ref`, and re-running nothing reproduces nothing.
Not an **Assumption**: an Assumption is a chosen *number* with a justification, whose
purpose is to feed arithmetic — `as_quantity` exists to turn one into a `Quantity`, and
`uq_assumptions_name_per_request` exists so a valuation holds exactly one of each. And not
an **Attestation**: ADR 0069's fourth kind says what the book says, and nothing about a view
is about the book.

The platform already knows half of this. `ClaimKind.OPINION` has existed since
`core/enums.py:303`, and the check constraint on `claims` has a second half whose comment
says why: without it "an opinion could carry a fact id that nothing checks and readers would
reasonably assume was verified". What is missing is a row that outlives the report.

## Decision

**A fifth record kind, `Judgement`: a named person held this view at this time on this
stated basis.**

Its guarantee is deliberately the weakest of the five, and the weakness *is* the record: it
attests that a view existed, at a knowable time, on a basis somebody wrote down — not that
the view was right, not that anything supports it. That is not a defect to be shored up
later; it is the honest description of what the row contains.

## The rule

**A `Judgement` may never be a `SourceRef`.** Not "should not", and not a lint.

`SourceKind` (`src/aer/calc/units.py:136`) gains no fifth value. `SourceRef` gains no fifth
constructor beside `fact`, `calculation` and `assumption`. The exclusive choice on `claims`
widens to admit an `attestation_id` under ADR 0069 and **has no column for a
`judgement_id`**. The schema cannot express the thing this record forbids, so there is
nothing for a later change to talk its way past.

## A laundered number passes every check this platform has

A confidence of 72% is a number. Whether a number may enter arithmetic is decided in this
codebase by exactly one place: `_as_input` (`src/aer/calc/engine.py:413`) raises
`UnsourcedValueError` when `value.source is None`, and asks nothing further. It does not
inspect `source.kind`. Neither does `_classify` above it. The whole gate between a value and
a recorded calculation is the question *does it have a source at all*.

So the moment a judgement can be a `SourceRef`, a position size is 72% times a risk budget.
`@traced` records the formula, both inputs with their units and sources, and the code
version. The lineage resolver walks it, the provenance viewer renders a complete green tree,
the citation check has nothing to object to because there is no citation to verify, and
`numerical_consistency` replays the figure exactly — because it does replay exactly. The
arithmetic is impeccable and the figure means nothing.

That is a judgement laundered into a number, and it would pass every check this platform
has — because every check this platform has verifies that a number came from somewhere, not
that where it came from could know.

**ADR 0066 is the same lesson one step nearer the surface.** It was learned by publishing a
172.1% net margin with all 91 citations verified and every guard green, and its answer was a
closed set of impossible relations in `aer/calc/plausibility.py`. Nothing of that kind helps
here. A margin above one is impossible; there is no impossible value of a conviction. 0066's
failure was a number that could not be true; this one is a number that cannot be false, so
no plausibility relation will ever catch it. The only place to stop it is the point where it
becomes a source, which is here.

## Display and derivation are different privileges

The tension is real and worth stating rather than waving away: a confidence figure is
useful. Charted over time against what actually happened, it answers the most valuable
question the Decision Analytics screen can ask — *is this operator well calibrated?* — and
that needs the number stored, not discarded.

So a Judgement **may be displayed**: a badge on a position, a column in the decision log,
the basis text in full. It **may be compared with outcomes**, which is the whole point of
ADR 0077's reviewer. It **may be aggregated for the operator's own review**. What it may not
do is enter arithmetic that produces a figure a report or a position size rests on.

The line is not whether arithmetic happens — a calibration curve is arithmetic. It is
whether the output is a figure anything rests on. A calibration statistic is a figure about
the operator, published nowhere and load-bearing for nothing, and it is computed outside the
traced engine for exactly that reason: a `@traced` calculation is this platform's promise
that a number is defensible, and lending that promise to a mean of confidence scores is the
same laundering one level up. The platform has never confused the two privileges before — a
red-team challenge scored 5/5 renders on the approval page and moves no number, and the
validator's assistant writes advisory prose beside a deterministic result.

**The enforcement is the move this repository already makes** — capability as a type with no
field for the forbidden thing. ADR 0034's `WithheldComps` carries no peer name and no
price-derived figure; ADR 0029's `ValuationMandate` has no constructor for a bank; ADR
0069's shareable rendering has no field for an attested figure. Fourth use, so it is a house
pattern rather than a coincidence. A missing column is the only rule that a later prompt, a
later template and a later person under time pressure are all equally unable to argue with.

## Consequences

**Things somebody will want become unrepresentable, and that is the intended cost.**

- **Conviction-weighted position sizing cannot be written down.** The weight would have to
  be a sourced `Quantity`, and a Judgement cannot supply a source.
- **A confidence-adjusted target price cannot be computed.** Nor a conviction-weighted
  expected return, nor a thesis-strength-weighted anything.
- **A model's stated confidence cannot influence a figure by any route**, including the
  indirect ones. `RESERVED_OUTPUT_FIELDS` currently names six fields and `conviction` is not
  among them; the model-facing half of this rule is unfinished until it is.

These are genuine omissions, and the person who wants them back may well be the operator
with a good argument. **A future ADR wanting conviction weighting must argue against this
one** — naming which check would catch a wrong weight, and what makes a conviction score the
kind of thing arithmetic may consume — rather than adding a fifth `SourceKind` value in a
migration and calling it a schema change.
