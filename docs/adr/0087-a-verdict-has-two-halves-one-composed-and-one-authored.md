# ADR 0087 — A verdict has two halves: one composed, one authored

**Status.** Accepted
**Date.** 2026-08-25
**Admits a role.** Required by ADR 0035 — a new agent role requires a record.
**Required by.** Roadmap §3.12 and the design in `docs/redesign/`, whose central move is to
lead every operational page with a plain-language verdict. Decided on the operator's
direction.

## Context

The redesign leads each page with a sentence saying what is true before showing the record
that supports it. Two versions of that sentence were delivered, and they are different
products.

The **specification** proposes: *"2 items need a decision; 3 red-team challenges are
available to read; £6.40 of £8.00 spent."* Two counts and two figures, composable from rows
the run already holds.

The **prototype** renders: *"The draft is complete, traceable, and cautious — but the critic
found one valuation dependency worth reading."* No deterministic composer produces
"cautious", and none decides which of several challenges is the one worth reading. That
sentence requires reading the adversary's objection and judging it.

**The operator chose the second.** This record is what that choice actually costs and how it
is built without breaking anything.

### Three problems the plain reading has

**A stored sentence goes stale.** "2 items need a decision" is true until the operator
settles a disagreement, at which point the page is lying about its own state. Regenerating
on each change costs a model call per interaction.

**Some verdicts are not about one run.** The main menu's verdict aggregates live state
across every open run: *"Two runs are waiting for you. One stopped safely."* There is no
moment at which that is frozen, so there is nothing to write it once *about*. Authoring it
on each page load is a model call per view, which every cost rule here exists to prevent.

**There is nowhere to store a judgement.** ADR 0074 settles that a judgement is never a
source reference, but the record that would hold one is roadmap §3.5 and is not built. The
roadmap is explicit that its dependency order is real and that a later item's work is not to
be folded into an earlier one.

## Decision

**A verdict is two halves, and they are produced by different machinery.**

### The composed half

Counts, states, dates and figures, assembled deterministically in Python from stored rows
on every render. Live by construction, so it cannot go stale. It carries no figure the
record does not already hold, and every figure in it is rendered by the existing house-style
door.

### The authored half

One or two sentences of interpretation, produced by a model, **written once over a subject
that is already frozen**, and stored as the output of the step that wrote it.

**Frozen is the whole test.** A draft that has been written does not change while the
operator reads it; a red-team report that has been returned does not re-argue itself. Those
are safe to interpret once. A cross-run aggregate is never frozen, so **it has no authored
half at all** — the main menu's verdict is composed, permanently, and no amount of wanting
better prose there changes it.

### Where each applies

| Surface | Authored half | Why |
|---|---|---|
| Gate 3, the review | **Yes** | Its subject is the finished draft and the returned challenges. Frozen when the step ends |
| Evidence and calculation pages | **Yes** | Their subject is a completed run's stored record |
| A finished report | **Yes** | Frozen by definition |
| The run console | **No** | Its subject is a run in motion |
| Every other gate | **No** | Composed only, until one of them proves it needs more |
| **The main menu** | **No, and never** | Live aggregate across runs. Composed only |

### The role

`verdict`, registered in `agents/registry.py` like every other:

- **Purpose:** interpret a frozen record for a reader who is about to make a decision. It
  states no figure of its own and asserts no fact.
- **Tools:** none. It is handed what it interprets, as the section writer is (ADR 0042).
- **Output:** a short structured object — the sentence, and a tone key drawn from a closed
  vocabulary. Not free prose into a template.
- **Cost:** routed, metered and capped in pounds like every call (ADRs 0012, 0053), with a
  step estimate so it is not a step with no cap (ADR 0052).

### It is a step output, not a judgement row

Stored the way a drafted section is stored: attached to its run, carrying its role, its
prompt and its cost. **This is deliberately not the judgement record**, which §3.5 will
build for views a *person* holds. Building that table here to hold a model's sentence would
be folding a later item's work into an earlier one, and would put a machine's interpretation
in the table meant for the operator's own.

### And it is never evidence

**No claim may name it. No citation may resolve to it. It reaches no shareable surface as
support for anything.** The one rule already says the model owns interpretation and code
owns fact; this is interpretation, and the distance between the two is the whole platform.
A verdict that could be cited would be a system that laundered a model's opinion into the
evidence chain by way of a nice sentence at the top of a page.

Enforced structurally, in the shape ADR 0074 uses: the type carrying an authored verdict has
no constructor that produces a `SourceRef`, so a caller cannot cite one by mistake.

## Consequences

### Accepted costs

- **One model call per run per authored surface**, on the cheap end of the router. A few
  pence on a run that costs pounds. It writes a cost row like everything else.
- **A run that fails before its verdict step has no authored half**, and the page falls back
  to its composed half alone. That is the correct behaviour and it must not read as a defect:
  the composed half is a complete sentence on its own, always.
- **The authored half can be wrong** in the way any interpretation can be wrong. It is
  labelled as interpretation, it sits beside the composed counts that are not, and it is
  never the only thing on the page.

### What this buys

**The composed half cannot go stale and the authored half cannot be stale**, because it only
ever describes something that stopped changing. The failure mode that made the plain reading
unworkable is designed out rather than monitored.

**The main menu keeps working with no provider configured**, because its verdict was never a
model's. That matters more than it looks: the front door is the page an operator opens when
something is wrong, and it already renders with no database.

### What it does not decide

**Whether the authored half is any good.** A sentence that reads well against a rich fixture
and thinly against a starved run is a real risk, and no test catches it. Reading verdicts
against a deliberately thin run is in the by-hand sheet for that reason.

**Whether other surfaces should gain one later.** The table above is a starting scope, not a
boundary. Widening it needs only the frozen test to be satisfied — and a note in this
record's successor if the answer ever changes for the main menu, which it should not.
