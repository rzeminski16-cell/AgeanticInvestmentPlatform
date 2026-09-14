# ADR 0118 — The section about segments may read segments

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
**Date.** 2026-09-14
**Amends.** ADR 0058 (a dimensioned fact is a different observation, and never competes with
the aggregate). The identity rule, the uniqueness index, the statement assembler's exclusion
and the cross-section consistency check are all untouched. One of the six readers ADR 0058
named gains a per-section exception.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F8, and the readiness audit's
reading of the Segment Analysis section on the AstraZeneca and Microsoft runs.

## Context

ADR 0058 decided that the dimension is part of a fact's identity, and that dimensioned facts
are excluded wherever a single value per concept-period is assumed. It named six readers:
statement assembly, the worker-visible fact search, **section evidence packs**, the red team's
fact listing, the revenue-history chart and the whole-history growth rate. The reasoning was
exact and is still right — *a segment must never win a period from the aggregate*, and every
ratio downstream would otherwise divide a fraction of the company by the whole of it.

The exclusion is implemented once, at `services/facts.py:88`:

```python
statement = select(FinancialFact).where(
    FinancialFact.company_id == company_id, FinancialFact.dimension_axis.is_(None)
)
```

`visible_facts` is what builds every section's evidence pack. So the **Segment Analysis**
section — the section whose entire subject is the breakdown — is handed an evidence pack with
every segment row removed, and then writes, truthfully:

> *no segment-level dollar figures were available to cite here*

Meanwhile the store holds 626 dimensioned facts across the three audit subjects, extracted by
the platform's own iXBRL sweep, mapped to canonical concepts, hashed to their source
documents:

| Subject | Axis | Rows | What it actually is |
|---|---|---|---|
| AZN | `ifrs-full:GeographicalAreasAxis` | 96 | Revenue by geography: US $23,970m, UK $4,359m, Germany $2,890m, France $1,408m … for FY2025 |
| MSFT | `us-gaap:StatementBusinessSegmentsAxis` | 45 | The three reportable segments |
| MSFT | `srt:ProductOrServiceAxis` | 42 | Revenue by product line |
| MTB | `us-gaap:StatementBusinessSegmentsAxis` | 14 | The bank's business lines |

**This is the platform's own extraction, correctly stored under ADR 0058's identity rule,
and barred from the one section that exists to read it.** A correction to something said
earlier in this work: the gap was diagnosed as an extraction failure needing a new iXBRL
parser. It is not. The parser ran, and on AstraZeneca's second run it saw 3,637 dimensioned
facts and wrote the 224 single-axis ones. The failure is a `WHERE` clause.

## Decision

**The evidence pack's dimension filter becomes a per-section property, defaulting to
excluded. The section about segments sets it to included, and nothing else does.**

- `visible_facts` takes a `dimensions` parameter — `EXCLUDE` (the default, and what every
  existing caller gets) or `INCLUDE_SINGLE_AXIS`.
- Exactly one section definition sets it: the segment section. Whether a second ever does is
  a decision with its own evidence, not a door left open.
- **Single-axis only.** A fact tagged on two axes at once — a product within a geography —
  is excluded even under the carve-out. It is not wrong; it is a cell in a cross-tab, and a
  writer handed cells without the table will state one as a total. ADR 0058's sweep already
  persists only single-axis facts, so this is stating the boundary rather than adding a
  filter.
- **The aggregate travels with the breakdown.** A carved-in pack carries the consolidated line
  for the same concept and period alongside the segments, always. The failure ADR 0058 guards
  against is a slice presented as the whole, and the cheapest structural defence is that the
  whole is right there next to it.
- **The payload says what it is.** Every dimensioned row reaching a writer carries its axis and
  its member as part of the row, rendered as *"Revenue · Geographical areas · United States"*
  rather than as *"Revenue"*. ADR 0058's worry was that "a row whose payload does not say
  'this is one segment's slice' must never reach a surface that would present it as the
  company's line". This is that condition, met.

### What does not change

The other five readers ADR 0058 named keep the exclusion, unconditionally: statement assembly,
the worker-visible fact search, the red team's fact listing, the revenue-history chart and the
whole-history growth rate. Every one of them assumes a single value per concept-period, and
that assumption is exactly as true as it was.

The identity rule, the uniqueness index including both dimension columns, and the
cross-section consistency check's grouping by dimension are untouched.

## What is given up, named rather than discovered

**One section can now state a figure that is not the company's.** That is the whole point and
it is also the risk. It is bounded by three things — one section, single-axis only, and the
aggregate present in the same pack — and checked by a fourth: the cited-figure agreement
metric reads the same rows, so a claim naming a segment fact is checked against that segment
fact and not against the consolidated line.

**The blanket rule was easier to reason about.** "No dimensioned fact ever reaches a writer"
is a sentence anybody can hold in their head; "no dimensioned fact reaches a writer except in
the segment section, single-axis, with the aggregate beside it" is four clauses. The cost of
the simpler rule is a section that cannot do its job, and that price has now been paid on
every run the platform has ever made.

## Consequences

**The Segment Analysis section stops being an apology.** The acceptance test is the stored
runs: re-rendering AZN #2 from its own record must produce revenue by geography with figures,
and must not contain the sentence about no segment-level figures being available.

**The segment chart gets its values.** The exhibit already renders; its bars are unlabelled
because the values were never in the pack.

**A per-section property is the right shape for the next one of these.** The audit found the
same pattern four times over — the platform holds more than it shows — and each instance was a
filter written once for a good reason and applied everywhere. Making this one a property
rather than a special case is the cheap part; keeping it set on exactly one section is the
discipline.

## Alternatives considered

**Lift the filter globally and rely on the payload labels.** One line, and it re-opens exactly
what ADR 0058 closed: the statement assembler's winner-selection would let one segment's
revenue win a period from the consolidated line, and every ratio downstream would divide a
fraction by the whole. ADR 0058 was written after that happened.

**Give the segment section its own query rather than a parameter.** It avoids touching a
shared function. It also means two places build evidence packs, and the second one will drift
from the first — the point-in-time filter, the licence checks and the tier caps all live in
`visible_facts` and would have to be duplicated or forgotten.

**Compute the breakdown as calculations rather than passing facts.** Elegant — segment revenue
as a recorded calculation with the aggregate as its check — and it is arithmetic the filing
already did. The platform would be re-deriving a figure the filer tagged, introducing a
rounding argument, and it still needs the dimensioned facts to reach the calculation.

**Wait for a general evidence-policy redesign.** The section has been broken on every run since
the extractor landed. A carve-out with a named boundary now is better than a correct
architecture later, and the boundary is narrow enough to be rewritten when the redesign comes.
