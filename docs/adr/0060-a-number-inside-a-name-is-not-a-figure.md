# 0060 — A number inside a name is not a figure

Date: 2026-08-18
Status: accepted

Amends ADR 0054 (which excused dates and document references) and ADR 0057 (which excused
plain counts and made the numeral rule non-fatal). The invariant is unchanged: no figure
reaches a report without lineage. What changes is what counts as a figure.

## Context

A live report lost five sections to the digits `365`. The executive summary, the business
overview, the historical financial analysis and the catalysts were each refused with
"contains the numeral(s) 365 which no numeric claim resolves to a stored fact or recorded
calculation" — in prose that never asserted a quantity at all. The subject was Microsoft,
and the sentences were about Microsoft 365.

This is the same failure ADR 0054 and ADR 0057 each addressed once before, in a different
corner: provenance tripping the rule that exists to protect provenance. A date is not a
quantity; a count of a list's own items is not a quantity; and the number in a product's
name is not a quantity either. In all three cases the numeral rule was reading characters
that denote nothing measurable and demanding a stored fact for them.

The carve-outs are not free, and this one is the least mechanical of the three. A date has
a shape. A plain count has a bound. A product name has neither — "Microsoft 365" and
"Revenue 365" are the same characters in the same arrangement, and only the word in front
distinguishes them.

## Decision

**The word that owns the number decides.** The pair is matched, not the number alone, and
the head word must pass three tests before its number is excused:

1. **It carries a capital.** Names do; ordinary prose words do not. "the segment reached
   365" keeps its figure.
2. **It is capitalised *mid-sentence*.** Every sentence capitalises its first word, so
   that capital says nothing about whether the word is a name. The first draft of this
   rule trusted it and excused "Shipped 240 units" — a real quantity, caught by an
   existing test written for ADR 0057's count rule. The suite was right and the rule was
   wrong.
3. **It is not a word the platform knows as a line item.** That denylist is *derived from*
   `aer.core.concepts.CANONICAL_CONCEPTS`, split into words, plus the short list of prose
   finance terms no filer tags (`EBITDA`, `margin`, `sales`). So "Revenue 365", "EBITDA
   1234", "Cash 500" and "Goodwill 365" all remain figures, and the set grows when the
   concept vocabulary grows rather than drifting behind it.

**The number must look like a name's number too.** At most four bare digits, no thousands
separator, no decimal, no per-cent sign, and not followed by a measure word — "Azure 12
million" is a measurement whatever precedes it. A product name carries none of those.

**One-way, like the erasers beside it.** Applied to content before the scan and never to
the claims that provide cover, so it can only narrow what gets flagged and any draft that
passed before still passes.

## Consequences

- The prose a reader actually wants — "seats on Microsoft 365 grew", "adoption of Windows
  11 accelerated" — stops costing a section.
- **The accepted trade, stated plainly:** a product name that *opens* a sentence keeps its
  figure, so "Microsoft 365 seats grew." is still refused where "Seats on Microsoft 365
  grew." is not. That is the conservative direction — a wrongly flagged sentence costs a
  sentence, a wrongly excused one costs the invariant — and it is the price of not
  trusting a capital that grammar would have produced anyway.
- The narrower risk that remains: a genuinely unsourced figure written mid-sentence
  directly after a capitalised non-financial word. "the Together 365 programme" is the
  shape of it. That row of the ledger is accepted for the same reason ADR 0057 accepted
  its own: the alternative was billed, fully cited sections dying over their own subject's
  product names, five at a time.
- The stakes of a *wrong flag* are lower than they were when ADR 0054 was written, because
  ADR 0057's salvage means a wrongly flagged sentence now costs a sentence rather than a
  section. That asymmetry is why this rule is written conservatively — two guards and four
  bounds — rather than as a broad "numbers near capitals are names".
- The three erasers are now composed in one place (`_erased`), because the numeral scan
  and the salvage that removes offending sentences must agree exactly about what counts as
  a figure, and they had two independent copies of the composition the moment there were
  three erasers instead of two.
- **A single bare year is still a figure**, unchanged from ADR 0054. The live run also
  refused a bare "2026" in the governance section, and excusing it is a separate decision
  about the year rules rather than about names; it is not taken here.
