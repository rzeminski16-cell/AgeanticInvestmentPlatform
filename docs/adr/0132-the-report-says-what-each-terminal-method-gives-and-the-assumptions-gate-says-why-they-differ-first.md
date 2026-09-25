# ADR 0132 — The report says what each terminal method gives, and the assumptions gate says why they differ first

- **Status:** Accepted (25 September 2026)
- **Date:** 2026-09-25
- **Amends:** ADR 0117 (the report states a view in two halves), its composed half only. The
  authored half, the model's exclusion from both, and `RESERVED_OUTPUT_FIELDS` are untouched.
- **Applies:** ADR 0038's successor rule in the specification (both terminal methods shown,
  never averaged), ADR 0125 (the document is checked against itself)
- **Decided by:** the operator, 25 September 2026, choosing the recommended option of three

## Context

The verdict round read `abandon`, and every judge gave the same first reason: the document
contradicts itself about what it thinks. On MSFT, one document said three incompatible things.

| Where | What it said |
|---|---|
| The masthead | *"Non-binding view: $227.43 to $442.01 a share"* |
| The valuation section | The two figures *"should not be read as the ends of a range"* |
| The approval table | *"Non-binding view: no view reached"* |

AZN's masthead printed $173.82 to $304.95 above an executive summary saying its discount rate
was *"unusably low"* and *"we do not build a valuation on it"*.

The two figures are not a range. They are what two terminal methods give, and they disagree
for a reason the run already computes: each method implies a value for the other's
assumption. On MSFT, the confirmed 14× exit multiple implies cash flow growing 6.5% a year
for ever at a 9.7% discount rate, and the confirmed 3% perpetuity growth implies an exit
multiple of 6.4×. Two assumptions about the same years, confirmed together, that cannot both
hold. The run records both implied figures, prints them deep in the valuation section, and
checks nothing when they are confirmed.

The operator was given three choices: leave it, take the figures off the masthead, or say
what they are and catch the cause earlier. They chose the third.

## Decision

### 1. The masthead says what each method gives, and never calls it a view or a range

The header line reads **"What each method gives: $227.43 (perpetuity growth) and $442.01 (exit
multiple) a share"**. The words *view* and *range* leave the composed half. *"Non-binding
view"* is reserved for the authored half — the operator's own, when F13 lets them state it —
and where no view has been stated the line says so in those words. It does not say *"no view
reached"*, which read as the document failing to conclude rather than as the operator not
having written one.

### 2. The block beneath says why the two differ, with the figures that show it

The composed block's heading becomes **"What the valuation gives"**, its first group
**"By terminal method"**. A new group, **"Why the two methods differ"**, prints the two implied
figures the base case already struck: the perpetual growth the exit multiple implies, and the
exit multiple the perpetuity growth implies. Each is a recorded calculation with its
footnote, as every figure in the block is. The group is omitted when the run struck neither.
Composing still means assembling, and the block still does no arithmetic.

### 3. The assumptions gate shows the same disagreement before it is confirmed

When the gate's proposed terminal growth and exit multiple, run through the valuation the
value step would run, give per-share figures further apart than the band at which the result
already says so (`METHOD_DISAGREEMENT`, a quarter), the gate prints the two implied figures
beside the two assumptions. For example: *"a 14× exit multiple implies perpetual growth of 6.5%
at this discount rate; perpetuity growth of 3% implies an exit multiple of 6.4×"*. It says
that confirming both keeps the report's two figures that far apart. It refuses nothing. Keeping
them apart knowingly is a legitimate judgement, and the operator's to make.

**The preview is computed and never recorded.** It is the same assembly (`base_case_inputs`)
and the same arithmetic (`discounted_cash_flow`) the value step uses. It runs on a ledger that
is thrown away, over the values as proposed. That is the one place a proposed, unconfirmed
value enters arithmetic, and it is allowed because the arithmetic's only output is a sentence
on the gate that asks for the confirmation. `as_quantity`'s refusal holds for everything that
is recorded. Nothing the preview computes reaches a report, a ledger or a footnote. The model
calculator, the operator's what-if page, is the same preview over confirmed values with the
operator's changes, and runs through the same function.

## What is given up

**The masthead no longer reads as a conclusion.** A reader skimming for a price target finds
two figures and a reason instead of a band. That is the claim the verdict narrowed the product
to, stated where it is read first.

**A gate sentence that can be ignored.** The disagreement is shown, not enforced. An operator
who confirms both anyway gets the report they chose, with the reason printed in its block.

## Consequences

- `aer.render.view` changes its titles and gains the implied-figures group. The Markdown and
  HTML header lines and the report page change their wording. Golden fixtures move with them.
- A new preview function serves the gate and the calculator. The gate's page gains the
  sentence and its figures when the band is crossed.
- ADR 0117's consequence *"a run with a valuation never prints 'no view reached' again"* holds,
  and more widely: no run prints it.

## As built, 25 September 2026

- **One assembly, extracted rather than copied.** The value step's reads — the analysis, the
  mandate, the market capitalisation and the close — became
  `vertical_slice_v1.valuation_basis`, which the step now calls and
  `aer.services.preview.basis_for_run` calls over the run's recorded step outputs. The preview
  is `aer.services.preview.strike`: `base_case_inputs`, then `discounted_cash_flow` with the case
  label `preview`. It takes no session, which is how "never recorded" is enforced rather than
  promised. A test strikes the preview, confirms the rows unchanged, runs the value step, and
  finds every figure equal at the ledger's twelve decimal places.
- **When the gate stays silent.** It says nothing when the preview cannot be struck: an input
  not yet proposed, a figure the filings lack, a bank's residual income (one terminal
  assumption, not two), or a price step that has not finished. Without the price, the preview
  would weigh equity at book and show a discount rate the report will not use. Once the gate
  is decided the page shows no preview. A decision the rows have moved under is open again,
  and shows it.
- **A dead precedent removed.** `assumption_gate.provisional_discount_rate` computed a cost of
  equity from three proposed values "to show an operator roughly where the bounds will fall".
  It had no caller. It is deleted, so the preview is the one place an unconfirmed value
  enters arithmetic, as §3 says.
- **The label decides the notation.** Both implied figures are dimensionless.
  `aer.render.display` reads a pure number by its label, checking percentage words first. So
  the multiple's row is labelled *"Exit multiple the perpetuity method implies"*: a label
  containing "growth" printed 6.4 as 640%.
- **The same words on every surface that printed the old ones.**
  - The prior-comparison section's valuation row now names each figure by its method:
    *"265 (perpetuity growth) and 241.5 (exit multiple) USD/share"*. It used to say *"241.5
    to 265"* in the same document that said the two were not a range.
  - Its view and confidence rows said *"Recorded at this run's approval"* on every run,
    whether or not anything ever was. They now say that neither was stated when the section
    was written.
  - The company page, the prior-report digest, the Obsidian export and the report page all
    say *"none stated"*.
