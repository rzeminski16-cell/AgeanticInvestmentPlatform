# 0057 — A count is not a figure, and a clause is not a section

Date: 2026-08-17
Status: accepted. Extends ADR 0054.

## Context

ADR 0054 decided that a reference numeral — a year, a filing label, an accession number —
is provenance, not a figure, and is erased by span before the unsourced-numeral scan.
The first full live run showed the boundary was drawn too tightly, and that the penalty
for crossing it was disproportionate:

- Both sections the report lost died over a single flagged token each: Business Overview
  on the `"13"` of a market count, Catalysts on the `"3"` of its own list. Neither is a
  measurement; no stored fact could ever cover them, because they are the writer counting
  the nouns of its own prose.
- Year *lists* were flagged past their anchor — "in 2014, 2019 and 2024" excused the
  head and flagged the tail — and the fiscal split-year form "2014/15" flagged its own
  second half.
- The rule consumed roughly half the drafting budget in retries, and the retries pushed
  the model towards raw, exactly-resolvable values in prose (`11729000000 USD`), trading
  a false refusal for unreadable text.

## Decision

Three changes, each one-way: they can only narrow what the scan flags, never widen it,
so any draft that passed before still passes.

1. **Year lists, ranges and split years are references.** An anchored year expression
   excuses the whole list it opens; a bare pair or list of year-shaped tokens ("2014 and
   2024") is reference-shaped by itself, as is the split year ("2014/15"). A money amount
   cannot back into these forms — written with separators ("2,014") it does not match
   the year atom — and a *single* bare year is deliberately still a figure.

2. **A plain count is not a figure.** A bare integer under one hundred — no separators,
   no decimals, no currency sign — followed by an ordinary word is counting that word,
   not measuring anything. Followed by a measure word (million, percent, basis, a
   currency) it remains a figure and still needs lineage. The trade: a genuinely
   unsourced small measurement phrased without any measure word ("headcount grew by 9
   people") now passes unflagged. That row of the ledger is accepted; the alternative
   was billed sections dying over their own list lengths.

3. **The numeral rule is no longer fatal on its own.** A draft whose only failure is
   unsourced numerals is salvaged rather than discarded: the offending *sentences* are
   removed — the same code-narrows-model-output move the plan salvage makes (gap A42) —
   and the narrowed draft is revalidated in full. The salvage declines whenever removal
   is not the repair: an unsourced numeral in a JSON number field, a removal that would
   empty a field, or a narrowed draft that still fails any rule. A salvaged section
   records that clauses were removed, in its degradation note and in the log, and its
   confidence is degraded accordingly.

4. **Nor is the word budget** — amended 2026-08-18, after a live run in which nine of
   sixteen sections overran their budget and several were refused for nothing else. A
   complete, fully cited draft discarded for being long is the worst trade in the
   pipeline: the evidence work is done and paid for, and the remedy is an edit. So a
   draft over the ceiling is **trimmed by dropping trailing sentences** — the longest
   field first, so the field that overran pays and a short lead-in is left whole — and
   revalidated in full like any other salvage.

   Three bounds make this an edit rather than a rewrite. It trims to the *ceiling* the
   validator refuses above, never to the stated budget, because prose the rule has no
   quarrel with is not the platform's to remove. It never empties a field: every string
   keeps its first sentence. And it never drops a list item or a field, because shedding
   one is a contract decision rather than an edit — a draft that cannot fit by shedding
   trailing sentences fails exactly as it did before. A shortened section says so in its
   degradation note and is confidence-degraded, on the same channel the numeral salvage
   uses: the platform edited a person's report, and that is not something to do quietly.

## Consequences

- A section can no longer be lost to a count of its own catalysts, and the failure mode
  for a genuinely unsourced figure becomes "that sentence disappears and the section
  says so" rather than "the section disappears".
- Nor is it lost to its own length. The cost is that the reader may meet a section whose
  final sentence was cut by code rather than by its author; the note on the section says
  so, and the alternative was the section not existing at all.
- The trim target and the refusal line are now one function (`word_ceiling`), because two
  copies of the factor would drift into a salvage that shortens a section to a length the
  validator still rejects — an edit for nothing.
- The pressure on the writer to phrase prose in raw resolvable integers is reduced,
  which the display formatter (R1) then no longer has to fight.
- The scan is measurably laxer in the count corner, as recorded above. The presentation
  gate (O3) is expected to watch the rendered output for regressions this could admit.
- The erasure functions remain applied to content only, never to the claims that
  provide cover — the one-way contract ADR 0054 established is unchanged.
