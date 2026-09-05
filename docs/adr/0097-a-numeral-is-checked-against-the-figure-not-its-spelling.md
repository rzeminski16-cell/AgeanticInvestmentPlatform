# ADR 0097 — A numeral is checked against the figure, not against its spelling

**Status.** Accepted
**Date.** 2026-09-01
**Extends.** ADR 0054 (a recognisable reference is not a figure), ADR 0057 (code may narrow
a billed reply) and ADR 0060 (a product name is not a figure) — the three previous
narrowings of the §2.12 numeral rule. This one narrows nothing: it corrects the comparison.
**Required by.** Roadmap §2.1, diagnosed from the MSFT run's exported record.

## Context

The §2.12 rule is one of the platform's load-bearing guarantees: a numeral in a section's
content must be one the section's own numeric claims account for, or the draft is refused.
It is what stops a drafter inventing a figure between the evidence and the prose.

It compared **digit strings**. A numeral was covered if its canonical spelling appeared in
some numeric claim's statement.

EDGAR facts are stored absolute — `FinancialFact.scale` is `0` throughout the extraction
layer — so Microsoft's FY2025 revenue is stored as `331839000000`. No writer produces that
string, and this platform's own renderer does not either:

- `render/display.py` renders money **in millions**, so the figure a reader sees in a table
  is `331,839`;
- `HouseStyle.prose_money = "auto"` renders large money in **billions** in prose, so the
  sentence this platform itself prints is "$331.8 billion";
- a dimensionless ratio is rendered as a **percentage**, so a stored `0.4676` is `46.8%`.

Every one of those is the same figure said the way this platform says it, and every one of
them was an unsourced numeral. **Two of the eight sections the MSFT run lost died there**, on
the same three facts read at two different scales:

- *Historical Financial Analysis*, refused at `draft`, over `331,839`, `182,935` and
  `115,948` in its figure rows — revenue, operating cash flow and capital expenditure, in
  the millions a table renders in.
- *Scenarios & Sensitivities*, which drafted cleanly with 21 recorded claims and was then
  refused at `revise`, over `$331.8 billion`, `$182.9 billion`, `$115.9 billion` and
  `$35.6 billion` — the same quantities, in the billions prose renders in.

A third, *Capital Allocation*, took seven numeral refusals across its two draft attempts
before dying on a different rule.

*Scenarios & Sensitivities* is the one that names the cause exactly. In its flagged field,
`46.8%` and `67.9%` **passed** while `$331.8 billion` in the same clause did not: a ratio is
quoted the same way by a claim and by prose, and a large money figure is not. The rule was
measuring agreement of spelling and calling it agreement of lineage.

The comparison the platform needed already existed. `cited_figure_agreement`, in the
evaluation layer, asks the neighbouring question — whether a sentence quoting a calculation
quotes the right number — and has always answered it with a fixed set of readings (×1, ×100,
×0.001, ×0.000001, ×0.000000001) judged at the precision the draft itself chose. The numeral
rule reimplemented the question and got a different answer.

## Decision

**Cover is a value, not a string.** A numeric claim names exactly one figure (ADR 0096); the
rule now carries that figure's **stored value** alongside the claim's statement, and a
numeral is covered if it reads as one of them under `aer.core.figures.READINGS`.

**One definition of "the same figure said differently."** `READINGS` and `reads_as` move out
of `eval/runtime.py` into `aer.core.figures`, and both callers import them. Two
implementations of this question is how the platform got two answers.

**Precision is the draft's own.** "46.8" asserts one decimal place and "46.76" asserts two;
each is judged against what it actually asserts, and both are true of a stored `0.4676`
while "46.9" is neither. A relative tolerance cannot draw that line — loose enough to accept
a rounding of a small ratio, it would accept half the errors the rule exists to catch.

**Cover comes from a claim that stands up, and from a figure the pack holds.**
`covered_figures` builds both halves in one place: numeric claims with no malformed reason
(ADR 0096), and the stored values of the ids they name, looked up in the assembled evidence.
A claim about to be dropped lends nothing, and an id this run never assembled lends nothing.

**The rule and the salvage read the same cover.** `validate_draft` and `_salvaged` both call
`covered_figures`. They had drifted apart once already — `_erased` carries the note — and a
salvage that keeps a sentence the validator then refuses is a declined salvage and a lost
section.

### What is not weakened

**A different figure is still refused.** Only the named figure's own readings pass: "$412.6
billion" over a stored `331839000000` is flagged exactly as before. **A numeral with no
claim behind it is still refused** — an empty figure list covers nothing, so a section that
cites nothing gains nothing here. The readings are the presentations this platform actually
produces, not any factor that makes two numbers meet: nothing else is admitted, and adding
one is an amendment to this record.

## Consequences

### Accepted costs

- **A five-reading ladder is a five-fold widening of what one figure covers.** A stored
  `331839000000` now also covers `331839`, `331.839` and `33183900000000`. In practice the
  collisions are between scales of the same quantity, which is the point; the residual risk
  is a section quoting a *different* figure that happens to be one of these readings of a
  figure it did cite, which the excerpt-level citation check and `cited_figure_agreement`
  still see.
- **A figure row can now be covered by a claim rather than by naming its own id.** The row
  convention (a `value` beside a `financial_fact_id` or `calculation_id`) is untouched and
  is still the only cover for a row no claim speaks to; what changes is that a row printing
  a figure the section *has* claimed and cited no longer needs to repeat the id. The lineage
  is the same lineage either way.
- **The rule now depends on the evidence pack**, not on the draft alone. A figure a claim
  names but the pack does not hold lends no cover, so a section whose pack was truncated
  past its own cited fact refuses a numeral it would otherwise pass. That is the correct
  failure — the lineage genuinely is not there — and it is visible in the refusal.

### What this buys

The two sections the MSFT run lost to spelling, and the standing tax on every future run.
A drafter writing prose about a large company writes billions, which is what the platform's
own house style prints, and the rule refused it every time.

### What it does not decide

**Whether the house style should reach the writers' instructions at all.** `HouseStyle`'s
docstring says it applies "in the writers' style instructions"; it reaches only the render
layer, so a section is drafted in whatever scale the model chooses and re-rendered in the
platform's. This record makes that mismatch harmless rather than fatal. Closing it — telling
the writer the scale the report will print in — is a separate change and wants the live
contract suite, because it moves a cached prompt block.

## Amended 2026-09-05 — the sign is part of the figure

The first live run of the confirmation runbook lost `cash_flow_analysis` and
`capital_allocation` to numerals right in every digit and refused for their sign. The
scanner captured digits alone, so "-139,500" became `139500`; the comparison, correctly
signed, then found no positive figure beside the stored `-139,500,000,000`. The agreement
metric read the same way, and reported "51.8 days" of a negative cycle and "0.065" of a
negative accruals ratio as disagreements it could not tell from wrong numbers.

**Decided.** One scanner, in `aer.core.figures`, read by the numeral rule and the agreement
metric alike. It carries a sign written as a mark glued to the digits — the hyphen, the
minus sign (U+2212), the en dash (U+2013) — or as the word "negative" or "minus" before
them. It does not read a dash between two figures as a sign, nor accounting parentheses,
because "(67.9 percent)" in prose is a parenthesis and refusing it would cost more sections
than it would catch tables. It also reads a unit glued to the digits — "0.09x", "3.5×",
"$331,839m", "12bn" — from a closed set, so the word-boundary guard that keeps "FY22Q4" from
shedding a "22" still means what it says.

**The two questions differ in one stated way.** The numeral rule asks whether a numeral has
*lineage*; an unsigned numeral reads as the magnitude of a negative figure the claims name,
because "a negative cycle of 51.8 days" over a stored `-51.79` is that figure said as people
say it. The agreement metric asks whether the sentence quotes the *right number*; there the
sign counts, and "0.065" over a stored `-0.0649` is a dropped sign, reported. `reads_as`
carries the switch, so the difference is one parameter rather than two definitions.

**What this changes for a figure the old numeral scanner never saw.** "0.09x" and
"$331,839m" were invisible to the numeral rule — the trailing letter failed its word guard —
and so passed it unexamined, while the metric read them. They are now visible to both, and
a multiple or a sum written with its unit needs the same lineage as one written without.
