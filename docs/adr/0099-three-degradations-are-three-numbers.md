# ADR 0099 — Three degradations are three numbers, not one

**Status.** Accepted
**Date.** 2026-09-01
**Amends.** §2.12's rule that "findings under an insufficiency banner are marked
low-confidence". That rule is kept exactly; what changes is what else was quietly borrowing
its number.
**Required by.** Roadmap §2.2, settled from the MSFT run's exported record.

## Context

A section's confidence is the model's own declared figure — 0.5 when it declares none —
held under a cap when the platform judged the section degraded. `degraded` was one boolean
over three unrelated facts:

1. **An evidence shortfall.** The section's cited evidence did not meet its policy floor:
   too few distinct sources, or none of them primary. This is a statement about the
   evidence, and 0.3 is §2.12's answer to it.
2. **Unsourced material removed.** The salvage (ADR 0057) deleted sentences whose figures
   no claim could resolve, or set aside claims that did not stand up (ADR 0096). What
   remains passed the *full* revalidation, so the section is sound — but the drafting
   produced material with no lineage, and that is worth knowing.
3. **A length trim.** The draft ran past its word budget and lost its trailing sentences.
   Every sentence that survived passed exactly the validation the whole draft passed.

**On the MSFT run of 2026-08-31, all five surviving degraded sections reported exactly
0.30, and not one of them was case 1.** Four — Executive Summary, Earnings Quality, Cash
Flow Analysis and Growth Outlook — were capped for case 3 and nothing else. The fifth,
Valuation & DCF, was case 2.

So a complete, fully cited section that ran long read to a person exactly like one whose
evidence had fallen short. 0.30 is a strong statement about reliability and three of the
five had done nothing to earn it, while the one that had something worth flagging was
indistinguishable from them. That is the failure mode §2.2 named in advance: a confidence
score that is always low is as useless as one that is always high.

The stated reasons already told these apart, in the reader's own words — `EDIT_NOTES`
exists precisely so an edit is not mistaken for a shortfall (gap R2). Only the number
flattened them.

## Decision

**Each degradation carries its own ceiling, and the lowest applicable one wins.**
`confidence_ceiling(insufficient_evidence=..., edits=...)` returns it, or `None`.

| Degradation | Ceiling | Why |
|---|---|---|
| Evidence shortfall | **0.3** | §2.12's number, unchanged. It is about the evidence. |
| Unsourced material removed | **0.5** | The section is sound; the drafting was not. Held to the platform's neutral prior — no better than a section that declared nothing. |
| Length trim | **none** | Nothing about the section's correctness changed. |

**A length trim never moves the number.** It is still recorded, still disclosed to the
reader in `LENGTH_EDIT_NOTE`'s own words, and still counts the section as edited everywhere
that matters — the appendix, the coverage notice, the run record. What it stops doing is
telling a reader to trust prose there is no reason to trust less.

**The ceilings live beside the notes they key off**, in `core/section_output`, because that
module already owns `EDIT_NOTES` and the classifier that routes an edit to the appendix. An
edit added to one and not the other is the drift this placement prevents.

### What is not weakened

**§2.12's rule is untouched.** A section under an insufficiency banner is still capped at
0.3, and a section that is both short of evidence and edited still takes the 0.3 — the
lowest ceiling wins, so no combination can raise a section above what its worst degradation
allows. **Nothing stops being recorded.** All three still write their sentence onto the row
and their entry into the run record; this changes one number, not what is disclosed.

## Consequences

### Accepted costs

- **0.5 for case 2 is a judgement, not a measurement.** There is no calibration set behind
  it; it is the platform's own prior for "declared nothing", used as a ceiling because a
  section whose unsupported material had to be removed should not out-rank one that
  declared no confidence at all. A ceiling that is honest about being a convention is
  better than one number standing in for three.
- **A trimmed section now reports a higher number than it used to.** That is the point, and
  the disclosure that always accompanied it is unchanged.
- **Reports from before this record are not re-scored.** `report_sections.confidence` is
  stored, and a run's numbers are the numbers it was approved with.

### What this buys

The score starts distinguishing what it exists to distinguish. On the diagnosed run that is
four sections of five reading as far weaker than they were, and the one that genuinely
warranted a flag becoming visible among them.
