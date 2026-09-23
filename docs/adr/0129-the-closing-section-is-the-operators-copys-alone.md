# ADR 0129 — The closing section is shown in full on the operator's copy and withheld from what leaves

- **Status:** Accepted
- **Date:** 2026-09-23
- **Applies:** ADR 0073 (the attested grade propagates as a type), ADR 0104 (a decision's size
  is a sentence), ADR 0117 (personalised facts, generic conclusions), ADR 0120 §3 (no attested
  figure leaves in a pack)
- **Builds:** F3, *The closing section reads the operator's own book*
  (`docs/V1.0_Alpha/04-feature-specifications.md`), page specification §8.4

## Context

F3 asks the report to end with a section that takes the operator's own book and the request's
context fields — a planned weight, a horizon, a purpose — and states what the position would do
to the book that already exists: the weight after, the top-five concentration before and after,
the sector's share before and after, and the stated horizon against the model's own payback.
Every figure a recorded calculation with a footnote; the model writing prose around figures it
did not produce; a run commissioned without a planned weight rendering the section absent rather
than empty. ADR 0117 places it: *"the closing section computes consequences for a book and
issues no instruction"*.

Building it against the code found four things the specification does not say, and one
question it cannot answer alone.

**The figures rest on the operator's book, and a book is attested.** A holding typed into the
portfolio form is an attestation at the *attested* grade (ADR 0073); a custodian statement's is
*documented*. The weights, the concentration and the cash are computed over those rows, so
every closing-section figure carries the grade of the weakest entry beneath it — and ADR 0073's
promise is that *a lineage containing any attested node cannot reach a shareable rendering*,
enforced by a type with no field for the figure. `aer.render.document` says of itself that *a
rendered report is the shareable artefact — it gets exported, attached and sent*. Read together,
F3 asks for figures in the one document ADR 0073 forbids them from, for any operator who types
their trades — which is every operator today, since the portfolio form writes at the attested
grade always.

ADR 0073 anticipated the answer without naming the surface. Its disclosure text reads: *"It is
shown in full on the operator's own copy, where the grade is stated beside it."* ADR 0120 §3
names the other end: *"the closing section's consequences for the operator's book … are absent
from a pack, by type rather than by filter."* What neither says is which rendering of a report
is the operator's own copy and which is the one that leaves. The code already carries the
distinction for the comparables block: `aer.calc.comps.Audience` is `INTERNAL` or `SHAREABLE`,
the render step assembles the stored HTML, PDF and Markdown for `SHAREABLE`, and the valuation
page reads `INTERNAL`.

**A decision's size is a sentence, and this section computes with a number.** ADR 0104 refused
a numeric intended size on the decision: *"the day something multiplied it by a net asset value
the position would be sized by a view"*. F12's pre-trade check therefore states the book as it
stands and computes no *after* (§3.19.43). F3's planned weight is a number the operator types on
the request form, before any research exists, beside the current and maximum weights that have
stood there since the form was built — and the section multiplies it by nothing.

**Nothing computes a payback.** The specification's example — *"this discounted cash flow's
payback lands in year six"* — names a figure no function produces, and the explicit forecast is
five years, so the example's answer lies beyond what the model forecasts.

**"The sector moved too" is not what the book knows.** The book's exposure bands group holdings
by the filer's own classification, so the sector's share before and after is computable exactly
as the specification asks; that part stands.

## Decision

**1. The report has two audiences, and the closing section is the one place they differ.**
`assemble_document` takes an `Audience`, defaulting to `SHAREABLE`. The render step, the
Markdown export and the presentation-integrity evaluation assemble the shareable copy. The
report reader page and the final-gate preview — the operator's own screens — pass `INTERNAL`.
Same walk, same numbering, same content rows; the closing section is the only section whose
content the audience transforms.

**2. On the operator's copy the section is shown in full, with the grade stated beside it.**
Every figure carries its footnote to a stored calculation whose lineage is the whole book walk
as at the run's date. An evidence note names the typed entries the figures rest on, or says that
every entry is documented and the section crosses unchanged.

**3. On the shareable copy an attested lineage is the disclosure and nothing else.** Not the
rows, and not the model's commentary — prose that quotes a withheld figure is the figure in
another notation. The transform returns a content object with one field, ADR 0073's own
`Attested.as_sentence()`, and the contract renders it under *Withheld from this copy*. A
documented lineage crosses in full: the containment is the grade's, not the section's.

**4. The section computes fractions of the book, and never a money amount for the position.**
Cash after, concentration after and the sector's share after are each a fraction; the planned
weight enters the ledger as an assumption in the request's own relation
(`SourceTable.RESEARCH_REQUESTS`, on the terms ADR 0106 set for a scenario shock) and is
multiplied by nothing. ADR 0104's line — a decision's size is a sentence — stands unamended: the
decision form still carries no number, and no calculation reads one from it.

**5. The book figures are struck once and composed from the ledger after that.** The section's
augmenter values the operator's book in a ledger of its own the first time it is asked — in the
draft step — persists it under the run, and every later call reads the rows back. A preview, a
replayed draft and the render step compose the same block from the same record; nothing is
struck twice, and the whole book walk is in the lineage a footnote resolves to.

**6. The horizon is set against the explicit forecast's recovery, and a payback the forecast
does not reach is stated as such.** Two company figures are struck in the value step wherever
the run holds a market capitalisation: how much of today's enterprise value the explicit
forecast's discounted free cash flow recovers, and the first year the cumulative recovery reaches
it. Only the explicit years count — the terminal value is what the market pays for beyond them,
and folding it in would make every payback the last forecast year. A payback beyond the forecast
is refused by the arithmetic and stated by the section as *beyond*, with the recovery beside it.

**7. Absent, not empty, is the row's own predicate.** The section definition's applicability is
`{"has_planned_weight": [true]}`, a property of the request; a run commissioned without a
planned weight has no `report_sections` row for it. A run with one and no book on record renders
the section's one honest sentence and no figure — the standalone path, with no writer call.

**8. The model's commentary is refused if it instructs.** Advisory phrasing — *you should*,
*recommend*, *advise*, *consider buying*, a rating, a target — is a refusal with the phrase
named; *the trade would increase concentration* is a consequence and stays. The writer is told
which figures the block carries and that the decision is the operator's.

## What was rejected

**Two assemblies of one report.** A separate composer for the operator's copy would be the
parallel rendering `aer.render.document` was built to prevent. One walk with an audience
parameter is the comps block's own shape, extended to the second thing that differs by reader.

**Withholding the figures on the operator's own copy too.** Absurd for the reason
`aer.services.portfolio.Figure` gives: withholding a person's own holdings from that person.

**Keeping the commentary on the shareable copy.** A sentence reading *"the trade takes the
five largest to 44% of the book"* discloses the withheld figure; the model cannot be asked to
write around numbers it was shown, and a scan for numerals would miss *"nearly half"*.

**A section-level flag for the audience.** ADR 0073's argument against flags applies: the
transform returns an object with no rows in it, and a template handed it cannot print one.

**Storing the planned weight as a confirmed assumption row.** It would appear at the
assumptions gate as a valuation input, which it is not; the request's own relation is where a
number typed on the request lives, as a scenario shock lives in its scenario's.

## Consequences

The report reader and the final-gate preview now show something the stored HTML does not,
for any operator whose book is typed. That is the deliberate consequence of ADR 0073 and it is
stated on the page: the evidence note says the figures are shown to you in full and withheld
from any copy that leaves this machine. An operator who wants the section in a shared copy has
one honest path — document the trades — and no flag.

`assemble_document` is called from five places; four assemble the shareable copy by default
and two operator screens pass `INTERNAL`. A new caller that should show the operator's copy and
forgets to say so shows the shareable one, which is the safe direction to fail in.

The F3 specification is corrected where this ADR departs from it: the payback is against the
explicit forecast; the section carries a grade; `RESERVED_OUTPUT_FIELDS` holds thirteen names,
not six; and the horizon field already existed. The legal note in F3 stands and is repeated
here: advising oneself is not a regulated activity, advising another person on a specific
investment is, and **before this ships to any user who is not its author, take advice**. The
consequences-not-instructions shape and the withheld shareable copy are what keep this on the
right side of that line for the operator's own use; they are not a substitute for the advice.
