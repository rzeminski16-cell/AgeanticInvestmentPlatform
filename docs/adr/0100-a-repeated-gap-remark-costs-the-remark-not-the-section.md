# ADR 0100 — A repeated gap remark costs the remark, not the section

**Status.** Accepted
**Date.** 2026-09-01
**Extends.** ADR 0057 (code may narrow a billed reply rather than discard it) with a fourth
repair, alongside ADR 0096's third. The gap budget itself — gap R4, `MAX_GAP_SENTENCES` —
is unchanged.
**Required by.** Roadmap §2.1, the last of its five diagnosed causes.

## Context

A live report spent a third of its prose describing absent disclosure. Honestly, and
uselessly: the drafting prompt's rule 6 already said "state the gap in one clause and move
on", nothing enforced it, and advisory rules drift. So the budget became a validation rule
(gap R4): one sentence per section may be about what is missing, and the rest must be about
the company.

The budget is right. **The remedy was the one ADR 0057 exists to refuse.** A draft that said
"not disclosed" twice was refused whole, retried, refused again, and the section stored with
no content — losing the other two thirds, which were about the company and fully cited. Two
of the eight sections the MSFT run of 2026-08-31 lost tripped it: Historical Financial
Analysis and Management & Governance, each alongside another cause.

Every neighbouring rule already had its repair. An unsourced numeral loses its sentence
(ADR 0057). A malformed claim loses the claim (ADR 0096). An overrun loses its tail
(ADR 0057). Only the gap budget still cost the whole draft, and it is the one whose
offending sentences are *true*.

## Decision

**Surplus gap remarks are removed; the first one is kept.** `without_surplus_gap_sentences`
walks the content in document order, keeps the first sentence whose subject is the missing
disclosure, and removes the rest. That is precisely what the rule asks the writer to do, so
the salvage carries out the instruction the draft ignored rather than substituting a
different one.

**The first is the first a reader meets**, across the whole content rather than per field:
the allowance is spent by the walk. A section keeps one remark, not one per string it
happens to be split across.

**One predicate, read by the rule and by the eraser.** `_is_gap_sentence` is what
`gap_sentences` counts and what the eraser removes. A rule counting one set and a salvage
removing another hands back a draft the revalidation refuses for the thing it just
repaired — which is the failure `covered_figures` was introduced to prevent for numerals
(ADR 0097).

**It runs after the numeral repair and before the trim.** A gap sentence carrying an
unsourced figure is already gone by then and costs this repair nothing; removing sentences
removes words, so the trim runs last and takes only what is still over.

**The edit does not move the confidence.** Under ADR 0099 this is neither an evidence
shortfall nor removed unsourced material: what went was repetition of something true, and
the prose that remains passed everything. It is recorded like every other edit —
`GAP_EDIT_NOTE`, in the reader's own register, into the appendix and the coverage notice.

### What is not weakened

**The budget is exactly where it was.** One sentence, refused in code, `MAX_GAP_SENTENCES`
unchanged. A section still cannot spend its prose on absence — it simply loses the surplus
prose instead of losing the section. **The phrases decide, as before.** A sentence about the
company survives however hedged its verbs, because it matches none of them. **Full
revalidation still gates the salvage** (ADR 0057): a reduced draft that breaks any other
rule is refused exactly as it would have been.

## Consequences

### Accepted costs

- **A section built wholly of gap remarks still fails**, and should: the salvage declines
  when removal would empty a string, so a field that is nothing but absence fails loudly
  rather than rendering blank.
- **That decline is stricter than it needs to be in one place.** A surplus gap remark
  standing alone as one item in a list of strings empties that item, so the salvage declines
  rather than dropping the item. Dropping it would be defensible and is probably better —
  but `without_unsourced_numeral_sentences` draws the same line, and two erasers with two
  decline policies is worse than one that is conservative. Moving both is a decision of its
  own.
- **A reader sees one gap remark where the writer wrote several.** The remaining one is the
  writer's own opening statement of the limitation, and the edit is disclosed; the gaps that
  matter to the analysis are in the evidence banner and the appendix, which this does not
  touch.

### What this buys

The last of §2.1's five causes. Two sections of the diagnosed run, and the standing tax of
losing a fully cited draft over a repetition an editor would strike.
