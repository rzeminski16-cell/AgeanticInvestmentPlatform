# ADR 0028 — A sensitivity grid is eighty-one valuations, not one valuation and eighty numbers

**Status.** Accepted
**Date.** 2026-08-05
**Extends.** ADR 0011 (traced calculations) and the `sensitivity_cells.calculation_id`
decision recorded in `aer/db/models/sensitivity.py`.

## Context

A sensitivity grid is the single easiest figure in a valuation to fabricate. Nine discount
rates against nine terminal growth rates renders as eighty-one pieces of analysis, and
nothing in the presentation distinguishes:

1. eighty-one complete discounted cash flows;
2. one discounted cash flow and eighty numbers interpolated between the corners;
3. one discounted cash flow and eighty numbers from a first-order approximation around the
   base case;
4. eighty-one numbers a language model produced because a grid was asked for.

The fourth is the one this platform exists to prevent, and it is prevented structurally —
models do not produce figures here. But the second and third are what a reasonable engineer
reaches for when a grid is slow, and they are wrong in a specific and misleading way. A
discounted cash flow is not linear in its discount rate: the perpetuity denominator is
`WACC − g`, so the surface is a hyperbola in exactly the region a grid is drawn to explore.
Interpolating across it understates the convexity, and understating convexity means
understating how bad the bad corner is — which is the corner the grid was drawn for.

Task 27 had to decide what a cell *is* before deciding how many of them there could be.

## Decision

**Every cell is a complete valuation, and its lineage is stored.**

`sensitivity_grid` calls `discounted_cash_flow` once per cell against an input set produced
by `dataclasses.replace`, so each cell carries the full forecast, both terminal values, the
bridge to equity and a per-share figure. `GridCell.calculation_id` is read off the result
quantity's own `SourceRef`, so a cell cannot exist without the calculation that produced it,
and `sensitivity_cells.calculation_id` is not nullable.

**Which means the grid is bounded.** `MAX_AXIS_POINTS = 9`. A nine-by-nine grid over a
five-year forecast is about six thousand `calculations` rows.

**And the order of writing is fixed.** `aer.services.valuation.run_sensitivity` persists the
calculation context *before* it writes the cells. Writing the cells first would either fail
on the foreign key or — worse, if the ids happened to collide with rows written by some other
path — succeed and point the grid at somebody else's arithmetic.

## Options rejected

**Interpolate between computed corners.** Fast, and wrong in the direction that flatters the
valuation, for the reason above. It also breaks invariant 3: an interpolated cell is a figure
in a report that is neither a stored fact nor a recorded calculation.

**Compute every cell but store only its output.** This is the tempting middle: the numbers
would be real, and the row count would fall by two orders of magnitude. Rejected because
"the numbers are real" is exactly the claim a reader cannot check. A grid whose cells store
only values is indistinguishable, in the database and in the report, from a grid whose cells
were invented — which is the whole failure mode this ADR is about. The cost of the guarantee
is rows; the cost of not having it is that the guarantee does not exist.

**Let the grid be unbounded and accept the storage.** Rejected as a matter of honesty about
the cost rather than a matter of storage. An unbounded axis invites a 25-by-25 grid, which is
625 valuations and about 45,000 rows, in a system that produces roughly one report a week and
whose provenance viewer a person has to be able to read.

## Consequences

**A grid is slow, and visibly so.** Eighty-one valuations is eighty-one times the work. This
is the right shape for a system doing one report a week under human approval, and would be
the wrong shape for one serving grids interactively. If that ever changes, the answer is
caching complete valuations by their input hash, not approximating them.

**The row count is real and will grow.** `calculations` is already the largest table by row
count and this makes it decisively so. Retention is a separate question — `docs/archive/PLAN.md`
covers deletion and retention policy — and this ADR does not pretend the rows are free.

**A test asserts the grid is not degenerate.** Not merely that the cells are ordered — a grid
that repeated the base case in every cell satisfies a non-strict ordering, and the first
version of the sabotage suite escaped on exactly that. The tests assert *strict* monotonicity
along both axes and that no two cells hold the same figure.
