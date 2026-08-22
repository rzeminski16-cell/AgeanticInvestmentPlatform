# ADR 0067 — A proxy may be proposed only if it names itself as one

**Status.** Accepted
**Date.** 2026-08-22
**Extends.** ADR 0046 (assumptions are proposed with a justification and confirmed by a
person) to a proposal that is deliberately *not* the quantity it stands for. Decided on
the operator's direction after the CHRW run and the R13 fixes that followed it.

## Context

The cost of debt is interest expense over average debt: two filed lines, arithmetic with
a provenance, and until this month it was derived or it was nothing. The live CHRW run
found the third state. C.H. Robinson tags no interest expense at all — the charge sits
inside a net "interest and other" caption, which is ordinary presentation and not an
anomaly — so `_cost_of_debt` refused, and the refusal discarded a forecast for which the
operator had confirmed all eleven other assumptions.

Report-quality R13 closed the structural half of that: `cost_of_debt` became suppliable
by hand, conditionally required, and named on the gate before approval rather than
discovered from a finished report. That leaves the operator with an empty box against a
number they may have no independent source for.

There is a figure in the filings that speaks to it. CHRW tags `InterestPaid` — the cash
outflow from the cash-flow statement — and cash interest over average debt is *a* rate the
borrowings cost. It is not the cost of debt. It differs from the charge to profit by
payment timing and by any interest capitalised into an asset rather than expensed, and it
can sit either side of the true rate.

So the question is whether the platform may put that number in front of a person. The
argument against is the one this repository makes everywhere: a figure that is nearly the
thing it is labelled as is worse than an absent one, because the absence is visible. The
argument for is that the alternative is not "no number" but "a number the operator picked
under pressure with nothing behind it", and an empty box with a hard deadline is where
guesses come from.

## Decision

**A derivation that does not compute the quantity it is proposed for may be offered as a
proposal, and only as a proposal, if it states its own basis and the direction of its
error in the justification a reviewer reads.**

Four conditions, all enforced in code:

1. **It is proposed, never confirmed.** `aer.services.assumptions.propose` writes an
   unconfirmed row whatever its caller says, and `as_quantity` refuses one, so the
   valuation cannot rest on the proxy until a person has agreed it may. Confirming is the
   act that adopts the substitution, and it is the operator's.

2. **The justification names the substitution and its direction.** Not "cash interest
   paid / average debt" as a formula, but the sentence a reviewer needs: that this is a
   cash-basis proxy rather than the accrual cost of debt, that this filer tags no interest
   expense so the accrual figure does not exist, that cash interest differs from the charge
   to profit by timing and by capitalisation, and that the figure can therefore sit either
   side of the true rate. A proxy whose justification reads like a derivation is the
   failure this ADR exists to prevent.

3. **It is offered only where the real derivation is impossible.** `cost_of_debt_required`
   already states that condition — debt on the latest balance sheet and no interest expense
   under any concept this platform maps — and the proposal is made only for those runs. A
   filer whose interest expense the valuation can read gets no proxy and no extra row, and
   the derivation from filed lines continues to outrank any assumption.

4. **It declines rather than overclaims.** No cash figure filed, no debt to divide by, a
   negative charge, or a rate outside the plausible band produces a sentence saying so,
   which the gate shows in place of a value. The band is checked in the derivation rather
   than left to `propose`, which raises on an implausible value: a proxy that killed the
   gate assembly for one odd filer would be a convenience that cost a run.

## Consequences

**A CHRW-shaped run now reaches the gate with a number against every input.** The operator
confirms, amends or rejects it on the record; the report's method note shows the row's
provenance as a proposal from `aer.services.assumption_proposals` confirmed at the gate,
which is the truth about where it came from.

**The platform now proposes one figure that is not what its name says.** That is a real
cost, and the containment is the four conditions above — particularly the second, which is
prose and therefore the one most likely to erode. It is pinned by a test asserting the
justification names the cash basis, not merely that a justification exists.

**This ADR does not license proxies generally.** It licenses this one, under these
conditions. Another quantity wanting the same treatment is another decision: the test is
whether the substitution can be stated in one sentence a reviewer can act on, and whether
the real derivation is genuinely unavailable rather than merely inconvenient.

**What was rejected.** Silently substituting cash interest inside `_cost_of_debt` — which
would have produced a valuation with no visible substitution at all, exactly the confidently
wrong number the platform is built to refuse. Also rejected: leaving the box empty, which
the operator considered and declined, on the ground that an unanchored guess is worse than
a labelled anchor somebody has to agree to.
