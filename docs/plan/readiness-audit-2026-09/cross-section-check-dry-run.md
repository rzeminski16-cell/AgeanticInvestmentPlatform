# What the cross-section check would have raised on the committed corpus

**17 September 2026 · Phase 3.2 · ADR 0125 · £0**

ADR 0125 says a check whose first live firing is also its first measurement is a check nobody
can size. This is the measurement, taken before the code could refuse anything, over the run
records committed in this folder. The script is
[`cross-section-check-dry-run.py`](cross-section-check-dry-run.py), beside this note and run
with `uv run python docs/plan/readiness-audit-2026-09/cross-section-check-dry-run.py` from the
repository root. It is not part of the suite; the regressions it produced are, in
`tests/test_consistency.py` and `tests/test_figure_names.py`.

## The answer

**One contradiction, and it is real.** AstraZeneca #2's balance-sheet section states

> "Interest cover is not established by the figures available, and no view is offered on it
> here; the leverage read rests on balance sheet multiples alone…"

while the same report prints interest cover of **8.11×** in three tables, each footnoted to
the same recorded calculation — `[^57]`, `[^90]` and `[^112]`, all reading
`interest cover = operating income / interest expense = 8.1128 … for FY2025
(aer.calc.ratios:interest_cover)`. That is the instance the readiness audit's
`contradiction-and-self-accusation` names, found by code, for nothing, at the validate step.

Two details of that sentence are worth keeping, because both are rules the scan needed. It
carries a clause boundary — *"…available, **and** no view is offered…"* — and the denial and
the figure it denies sit in the first clause, which is what the clause rule is for. And the
same report footnotes an FY2021 interest cover of 0.8123 at `[^143]`; that figure is the
period mismatch which fed eight false red-team challenges, and the period bound keeps it out
of this.

Everything else the scan turned up names a figure the report never published, so the
published-only index the code actually uses does not hold it and the check never reaches it.

## How the two halves were measured

The exports omit prose and the reports omit the ledger, so each pass was read from the
artefact that carries it. Both readings are deliberately **looser** than the code, so both
counts are upper bounds:

| | the code reads | the dry run read | so the count is |
|---|---|---|---|
| Calculations | the figures the report publishes | every row in the ledger | an upper bound |
| Denials | each section's structured content | the rendered Markdown, prose lines only | an upper bound |

## Pass 2 — published calculations

| Run | Calculations | Groups holding two values (whole ledger) | Of those, cited in the report |
|---|---|---|---|
| MSFT #1, quick | 858 | 10 | 0 |
| MSFT #2 | 857 | 10 | 0 |
| MSFT #2, re-run | 850 | 10 | 0 |
| AZN #2 | 831 | 10 | 0 |
| AZN, re-run | 835 | 10 | 0 |
| M&T | 643 | 23 | 0 |
| M&T, re-run | 888 | 32 | 0 |

**Zero on every run**, and the right column is why: not one of the clashing names —
`discount_factor`, `gordon_terminal_value`, `exit_multiple_terminal_value`, `equity_charge`,
`closing_book_value`, `explicit_residual_value`, `net_income_from_roe` — is cited anywhere in
any of the five committed reports. They are forecast and grid series: a run strikes a discount
factor per projected year and a terminal value per case, and it publishes the answer rather
than the ladder.

The groups that remain in the left column are an artefact of what the export carries. The
export has no `parameters`, and `parameters` is exactly what separates ten forecast years of
one discount factor. The code keys on them; the dry run could not.

### Two defects this measurement found before the code could ship them

**`Calculation.name` is the name of the function, not of the figure.** `days_outstanding` is
struck three times a period — days sales, days inventory, days payable — and on the MSFT #2
record those three read 115.2, 90.6 and 3.9 days for FY2025. A key of name and period would
have called a ratio suite that is working a contradiction, three times a year, on every run.
Sixteen such groups sit in one record's ledger.

**A discount factor takes its year as a parameter, not an input.** Ten forecast years are ten
rows sharing one name, one absent period and identical inputs.

Both are fixed by keying on the question a calculation answers — its identity in
`aer.calc.engine` minus its output — which is the ledger's own definition of when two
calculations are the same figure. The first version of this pass had neither field in its key
and would have flooded gate 2 on every run.

## Pass 3 — negative assertions

Five candidates across the five reports, from about 60,000 words:

| Run | Figure named | Clause | Published? | Verdict |
|---|---|---|---|---|
| AZN #2 | interest cover | "Interest cover is not established by the figures available" | **yes**, footnoted | **true positive** |
| MSFT #2 | interest cover | "no interest-cover figure is recorded here and we assert none" | no | out of the index |
| MSFT #2 | capital expenditure | "quarterly capital expenditure cadence within fiscal 2026 is not available" | no | out of the index; denies a cadence, not the figure |
| MSFT #2, re-run | cash conversion | "A three-year cash conversion bridge … cannot be set out on disclosure" | no | out of the index; denies a bridge, not the figure |
| MSFT #2, re-run | operating income | "the evidence available does not include disclosure of equity-method interests … segment operating income" | no | out of the index; a long clause matching a name it is not about |

M&T's two reports produced none.

### Three refinements this measurement forced

The first run of the scan produced **50, 34, 54, 27 and 16** candidates on the same five
reports. Every one of the three changes below came from reading those lists, and each is a
deliberate narrowing:

1. **A denial belongs to its clause, not to its sentence.** MSFT #2 contains "…short-term
   investments of $55.9 billion sit against those borrowings, and no netted leverage figure is
   recorded here", which names one figure and denies a different one. Read whole it reports a
   contradiction that is not there. Enumeration commas are *not* boundaries, because MSFT #1's
   denial is one clause holding six figure names.
2. **"present" is not a word about the record.** "No interest-cover figure is present" is a
   denial and "we do not present the $3.21tn equity value as a downside anchor" is an author
   declining to lead with a figure it goes on to print, and one word carries both.
3. **"without" is not a negator.** AZN #2's "One external reference point is available without
   leaving primary disclosure" read as a denial of the cost of capital the same sentence
   quotes. A sentence that genuinely needs it carries "cannot" as well.

## What this does and does not license

It licenses landing the check **advisory**, which is what ADR 0125 decides: on the evidence
here it would have added one row to one gate-2 page across seven runs, and that row was worth
adding.

It does not license promoting it to a refusal. That still needs the two consecutive clean live
runs ADR 0125 names, for a reason this exercise demonstrates rather than argues: the scan's
first honest measurement was off by a factor of twenty, and the corrections came from reading
real prose and a real ledger, not from thinking harder about the rule.
