# ADR 0101 — A bank's grid varies the spread, and a fading driver is refused rather than shifted

**Status.** Accepted
**Date.** 2026-09-02
**Supersedes.** ADR 0070's last consequence — "scenarios and sensitivity grids are not built
for this model" — which was a stated gap rather than a decision to leave one.
**Extends.** ADR 0028 (a grid cell is a complete valuation). Its ceiling, its
calculations-before-cells order and its refusal to interpolate are unchanged and apply here
unaltered.

## Context

`aer.calc.residual_income` values a bank as its filed book value plus the present value of
what its equity earns above the cost of that equity. It ships two terminal treatments and no
grids, and `aer.services.residual_income_run` says so in a caveat on every result:

> No scenarios or sensitivity grids were run for this valuation. The two terminal treatments
> bracket the answer, but nothing here varies the return on equity or the cost of equity a
> step at a time.

The module's own reasoning for the omission was that "a grid whose axes were the return on
equity and the cost of equity would mostly re-describe the spread the model is already
reporting". **That is half right, and the half it gets wrong is the important half.** The two
treatments bracket the *terminal* question — whether competition removes the excess return —
and say nothing at all about the *spread* question, which is what a reader actually asks of a
bank: how much of the premium to book survives if the bank earns fifty basis points less, or
if the market demands fifty more. The cost of equity is not a single lever either. It enters
the equity charge, the discount factors and the perpetuity denominator, and the three do not
move together, so a grid over it is not a restatement of anything the result already prints.

What genuinely stood in the way was the axis rule. `aer.calc.dcf.VARIABLE_FIELDS` admits
three scalars and refuses every driver path, on grounds worth quoting:

> A grid axis has to be one number with an ordering; varying "revenue growth" means varying
> five numbers at once, and a reader looking at the axis label would have no way to know
> which.

The residual-income model has exactly two scalars — the cost of equity and the terminal
growth rate — and under the fade-to-nothing treatment the second one is not read at all. Two
usable scalars, one of which is inert half the time, is not enough for the two grids the
discounted cash flow ships. The return on equity is the missing axis, and it is a driver path.

## Decision

**Scenarios and grids are built, and both terminal treatments run for every case.** ADR 0070
holds that choosing between fading and perpetual growth is a judgement about banking rather
than arithmetic, so presenting one alone would present that judgement as a computed result.
That reasoning does not weaken for a bear case; it applies to it in the same words. Each
scenario therefore reports both treatments, and each may have its perpetuity refused on its
own terms.

**A driver-path axis is permitted only when the confirmed path is flat.** When the return on
equity is one number repeated across the forecast — which is what a bank's gate usually
confirms, because the return is derived from two filed lines rather than argued year by year
— an axis over it *is* one number with an ordering, and the discounted cash flow's objection
does not apply. The grid replaces the whole path with a new flat path at each axis value.
When the confirmed path fades, the grid is refused by name and the report says which
assumption to flatten if the operator wants it.

**Two grids, each under the treatment its second axis means something in.**

| Grid | Axes | Treatment | What it answers |
|---|---|---|---|
| The terminal grid | cost of equity × terminal growth | perpetual growth | How much of the answer is the perpetuity claim |
| The spread grid | cost of equity × return on equity | fade to nothing | What the explicit forecast is worth if the spread is wrong |

Terminal growth is not an input under fade to nothing, so a grid over it there would render
five identical columns — a sensitivity to something that does nothing, which is worse than no
grid because it looks like a result. The spread grid sits under fade precisely because that
treatment makes no perpetuity claim, so what varies is the forecast spread and nothing else.

**A grid that cannot be computed is absent whole, with its reason recorded.** The perpetuity
refuses a terminal growth at or above the cost of equity, and a final year whose residual
income is negative. Both are reachable from a grid corner: the low-cost-of-equity column
narrows the spread, and the high-cost-of-equity column can push the final year below its
charge. A partly filled grid would be worse than none — ADR 0028's whole point is that every
cell is a complete valuation, and a hole is a cell a reader interprets — so the refusal takes
the grid and leaves a sentence saying which corner failed and why. This is the shape
`BankValuationOutcome.perpetual_refusal` already uses for the base case.

**Grid cells are labelled `sensitivity`, not `base`.** The `case` parameter exists so a
scenario chart can be read off the ledger, and `aer.services.exhibits._latest_for_case` takes
the *most recent* row for a case. Grid cells recorded as `base` are twenty-five later rows of
the same name under the same label as the base valuation, and a scenario keyed `base` would
draw its bar from whichever corner of the grid happened to be written last. Labelling the
grid distinctly makes that unrepresentable rather than unlikely.

**`residual_income_value` takes a `case`.** It has not until now, so a bank's scenarios would
have been recorded indistinguishably — which is the exact defect `aer.calc.dcf.enterprise_value`
records having had and fixed. The label reaches the three outcome calculations, the ones a
reader quotes.

## Options rejected

**A parallel shift of a fading path.** "What if the return on equity is fifty basis points
lower throughout" is a perfectly meaningful sensitivity and a genuinely single parameter. It
is rejected on what it renders: `sensitivity_cells.x_value` would hold the shift, so the
heatmap's axis would read `-0.5%`, which a reader scanning a bank's grid takes for a return on
equity of minus a half per cent. A grid whose axis labels can be read as the wrong quantity is
the failure ADR 0028 exists to prevent, arriving through the axis instead of the cells.

**Averaging a fading path to one number and varying that.** Worse than the shift: the base
column would not be the base case, because a valuation on the average of a fade is not the
valuation of the fade. A grid that does not contain the valuation it is a sensitivity of is
not a sensitivity.

**A single grid over the cost of equity and the terminal growth rate.** What was available
without solving the driver-path question, and it is the grid a bank reader needs least — it
varies the terminal claim, which the two treatments already bracket from end to end, while
saying nothing about the spread.

**Clamping a refused corner to nil, or to the fade value.** Both fill the hole with a number
that is not a valuation. The perpetuity's refusals are statements about the bank; a corner
that quietly showed book value instead would turn "this cannot be valued that way" into "this
is worth its book", which is a different claim and a more comfortable one.

## Consequences

* **A bank run now writes about two thousand more `calculations` rows.** Two 5×5 grids of
  complete valuations over a five-year forecast, on ADR 0028's terms. That is the cost of the
  decision that cells are computed rather than interpolated, and it is the same cost the
  discounted cash flow already pays.

* **The caveat changes from an admission to a description.** Where a bank's result said no
  grids were run, it now says which two were, or which one was refused and why.

* **The scenario bridge and the heatmap work for banks.** `_heatmap_input` was already
  model-agnostic — it reads the run's first stored grid. `_scenario_input` was not: it looked
  for `value_per_share` rows discriminated by terminal *method*, and a bank's rows are named
  `residual_income_per_share` and discriminated by *treatment*. It reads both now.

* **The discounted cash flow's grid cells still carry `case="base"`.** Named here rather than
  changed: the same reasoning applies to it, but a run's stored ledger is what the replay
  harness scores, and re-labelling rows that existing runs already hold is a migration
  question rather than a one-line fix. The bank model is built without the defect; removing it
  from the cash-flow model is its own task.
