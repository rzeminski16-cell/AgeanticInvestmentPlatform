# ADR 0095 — An escalated challenge is briefed, never decided

**Status.** Accepted
**Date.** 2026-08-31
**Admits a role.** Required by ADR 0035 — a new agent role requires a record.
**Extends.** ADR 0087, whose test for an authored half this reuses unchanged.
**Required by.** The operator's reading of a live run's review page.

## Context

Gate 3 shows every red-team challenge the run could not resolve, each as an objection, its
basis, a tally of the evidence it cites, and two buttons: keep the draft's position, or
accept the challenge. The operator settles it by choosing a side and writing why.

**They are choosing between two paragraphs of argument with nothing to compare them by.**
Reported after a live run, in the operator's own words: the resolutions want to be "shorter,
easier to understand", to say "the impact and assumption of the choices", and to suggest
"which one to side with".

The first two of those are plainly missing information. An objection states what is wrong
with the draft; it does not state what the report *becomes* if you agree with it, nor what
you have to believe for either side to hold. Both are readable from the two positions, and
neither is written down anywhere.

The third is the one that needed deciding.

## The objection to a suggestion

The platform escalates a thesis conflict **because no rule could choose between the two
positions**. Having done so, offering a preferred side looks like the platform answering the
question it just declared unanswerable — and worse, like a model's opinion arriving at the
one surface where the operator's own judgement is the product.

Three things make it admissible anyway.

**It is comparison, and comparison is the model's half.** The one rule gives deterministic
code every number and every fact, and gives the model planning, interpretation, comparison,
adversarial challenge and writing. The red team's challenge is itself a model's argument; a
reading of two arguments is the same kind of work, on the same side of the line. What would
cross the line is a *figure* or a *fact* in the brief, and the schema has no field for
either.

**"No rule could choose" is not "nothing may be said".** The ladder's rules are
deterministic tests over positions with units and tiers — the same filer, the same period,
a better source. A thesis conflict escalates because those tests do not apply to arguments,
not because the arguments are equally good. Refusing to say anything at all is a different
claim from the one the escalation makes.

**It is beaten by the operator every time.** A lean changes no row. It cannot settle, it
cannot prefill the rationale the operator must write, and the settle controls are unchanged
by its presence.

## Decision

**A new role, `challenge_brief`, writes one brief per unsettled challenge, once, over the
frozen draft — and the brief is advisory in the same structural sense a verdict is.**

Per challenge, six short fields and nothing else:

- what **keeping** the draft's position takes as true, and what follows for the report;
- what **accepting** the challenge takes as true, and what follows for the report;
- the side it **leans** to, from a two-value vocabulary;
- one sentence of **why**.

### ADR 0087's frozen test, unchanged

Written after the revise step, over a subject that has stopped changing, stored as the
step's own output. The same test, for the same reason: an interpretation of something still
moving is stale the moment it is stored.

### It joins no payload and moves no hash

Stored on the step, **never on `disagreements.detail`**. That column is inside the gate-2
payload, so a brief written there would be inside the approval hash — which would make a
model's opinion part of what the operator approves, and would invalidate an approval every
time the interpretation was rewritten. The lean is beside the decision, never inside it.

### It is never evidence, and never leaves the review page

No claim may name it, no citation may resolve to it, and it reaches **no rendered report**.
The report is the shareable artefact; the appendix carries every challenge and its recorded
resolution, and a machine's lean on a conflict a person settled has no place in it. The type
that carries a brief to a page has no field for a source or a figure, in the shape ADR 0074
names and ADR 0087 reuses.

### It never settles anything

The lean is rendered as a lean, beside the two controls, and the controls are what they
were. `settle_by_hand` still refuses an empty rationale — the rule that a decision
overriding a rule must say why is untouched, and the operator's reason is theirs.

## Consequences

### Accepted costs

- **One more model call per run**, on the cheap end of the router, bounded by the number of
  unsettled challenges. It writes a cost row and carries a step estimate like every call.
- **A run that fails at this step loses its briefs and nothing else.** Every failure short
  of a budget refusal degrades to `written: False`, and the page renders as it did before
  this record: statement, basis, evidence, controls.
- **A brief can be wrong**, in the way any interpretation can be wrong. It is labelled
  interpretation, it sits beside the two positions it is reading, and the operator settles.

### What this buys

The choice at gate 3 stops being a comparison of two paragraphs and becomes a comparison of
two consequences. That is the decision the operator is actually making, written down.

### What this does not decide

**Whether a lean should ever appear on a source conflict.** Those escalate for a different
reason — two credible sources, no rule between them — and the answer there is more likely a
better date or a better source than a better argument. Out of scope, and a successor's
question.

**Whether the lean is any good.** No test catches an interpretation that reads well against
a rich fixture and thinly against a starved run. Reading briefs against a deliberately thin
run is in the by-hand sheet, next to the verdict's own entry, for the same reason.
