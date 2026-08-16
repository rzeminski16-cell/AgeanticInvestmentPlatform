# 0054 — A reference numeral is provenance, not a figure

Date: 2026-08-16
Status: Accepted

## Context

`docs/PLAN.md` §2.12 and `core/section_output.py` enforce the numeral rule: every numeral
in a section's content must appear in a numeric claim that names a stored fact or a
recorded calculation. The rule was deliberately strict about what a numeral *denotes* — a
year and a percentage were treated alike, because "a numeral the platform cannot trace is
a numeral the report cannot carry, whatever it denotes".

A live large-cap run showed the cost of that strictness. `section_writer.draft_refused`
fired repeatedly over numerals that denote no quantity at all: the year `2026`, Apple's
CIK `0000320193`, item and exhibit numbers such as `2.02`, `99.1` and `9.01`. The retry
loop recovered every time, so no section was lost — the cost was retries at Opus prices
and prose that writes around dates and filing references rather than naming them.

The tension was already resolved once, one level down. `NUMERAL_EXEMPT_KEYS` exempts id
fields from the scan because a UUID's digit groups would otherwise surface "numerals" no
claim could ever cover — provenance tripping the rule that exists to protect provenance.
A filing reference in prose is provenance too. The counter-argument is real: by regular
expression alone, "2025" in prose is indistinguishable from the figure in "revenue of
2,025 million". Because the change moves the boundary of invariant 3, it was put to the
operator as a decision, not applied as a patch. The operator chose the exemption.

## Decision

**A numeral inside a recognisable date or document-reference span is excused from the
numeral rule. The exemption is by span, never by value.**

`without_document_references` erases recognised reference spans from content text before
the numeral scan runs. The recognised forms are each anchored by context a quantity does
not have:

- calendar dates in either order, with an optional day — "15 March 2026", "March 15,
  2026", "March 2026" — and ISO dates, whose shape is its own anchor;
- fiscal markers — "Q3 2025", "H1 2026", "FY2026", "fiscal year 2026", "the fourth
  quarter of 2024";
- a year in temporal company — "in 2026", "since 2024", "mid-2025", "between 2019 and
  2024", a year range, "the 2026 fiscal year". The anchor list deliberately excludes
  "of", "to" and "for", each of which reads naturally in front of a quantity;
- filing references, where the label is the anchor — "Item 2.02", "Items 2.02 and 9.01",
  "Exhibit 99.1", "Form 4", "Note 12";
- a labelled CIK, and an accession number, whose 10-2-6 digit shape is its own label.

**Span, not value, is what keeps the rule safe.** Erasing the matched characters — rather
than allowlisting the numbers found inside them — means "Revenue grew in 2026 to $2,026
million" loses only the date: the money still surfaces `2026` from `$2,026` and still
needs lineage. A value-based exemption would have waved that figure through, which is the
false negative the strict rule existed to prevent.

**The erasure applies to content only, never to the claims that provide cover.** It can
therefore only narrow what the scan flags: every draft that passed before this ADR still
passes, and no new refusal can be created by it.

**A bare unanchored year still needs lineage.** "2026 saw a change of auditor" trips the
rule exactly as before, because nothing in the span distinguishes it from a quantity. The
writers' prompts now say so with the remedy attached: anchor every year to a month, a
quarter or a temporal word. The remedy for a refused year is anchoring, not a claim.

## Consequences

**Retry spend on reference numerals stops.** The dominant refusals from the live run —
anchored years, labelled item and exhibit numbers, the labelled CIK — no longer burn
drafting attempts, and the writer no longer has a reason to avoid naming dates and
filings in prose.

**A residual false-positive class remains, on purpose.** Unanchored years and unlabelled
reference numbers still trip the rule, and the prompts tell the writer how to avoid that
rather than the platform guessing. Loosening further — a broad year exemption — was
offered to the operator and declined as unsafe: year-shaped quantities exist.

**A residual false-negative class is accepted, bounded by anchor context.** A quantity
phrased inside an anchor ("during 2026 outages…") could in principle ride a span. The
anchors were chosen so such phrasings are unnatural, and every claim a section makes
still passes citation verification independently of this rule.

**The adversarial corpus keeps its verdicts.** The corpus probe's content loses "in 2022"
to the erasure but still carries `34` and `198,270` uncovered, so the §2.12 check it
scores still refuses — asserted by the corpus itself, which runs in CI.

**The pattern is enforced by mutation-verified tests.** An eraser that erases nothing, an
eraser applied to the claims as well, and a broadened bare-year exemption each fail a
named test in `TestAReferenceIsNotAFigure`.
