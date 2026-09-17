# Phase 0.5 — the QUICK-mode run, and what it answered

*17 September 2026. `uv run python -m audit.driver.run msft1 --mode quick --cap 6.00
--screenshots`. £4.95, thirty-one minutes, 48 model calls. The record is in
[`msft1-quick/`](msft1-quick/); the screenshots were dropped and the pages kept as HTML,
which is what a png of a page shows plus its text.*

QUICK mode had never been run in the platform's life. It was approved, costed at about £4,
and it exists to answer the largest unasked question in the delivery plan: **whether
eighteen sections is the right spine for one private investor.**

## The answer to that question

**Half the sections cost two thirds of the money.**

| | 14 September, standard | 17 September, quick |
|---|---|---|
| Sections written | 18 | 9 |
| Spend | £7.48 | £4.95 |
| Model calls | 57 | 48 |
| Output tokens | 250,163 | 168,378 |

Dropping nine sections saved £2.53. It did not halve anything, because **more than half the
run is not section writing**: the plan and its critic, the five research workers, the
adversary and the revise pass cost £2.32 between them and do not vary with how many
sections follow. Of the £4.95, drafting and revising were £2.77.

So QUICK is not the cheap mode the name suggests, and the eighteen-section spine is not
where the money is. A shorter report is worth having for the reader's sake — nine sections
is a note somebody will actually finish — but it is not a cost lever. The cost lever is
output tokens: £3.10 of the £4.95, at 168,378 of them, written almost entirely by the
report writer on the largest model.

## What it produced

Nine of nine sections generated, none pending, none failed:

executive summary, investment thesis, business overview, historical financial analysis,
balance sheet and liquidity, cash flow analysis, valuation, key risks, and the validation
and disagreements appendix.

Fourteen citations, fourteen verified. The run replays: 858 calculations, 14 citations, 15
artefacts and 48 model calls all reproduce from their own records.

## What it did not produce, and why that is the finding

**The run stopped at the final gate and no report was approved.** Both reasons were defects
in the checking layer rather than in the document, and both are now fixed with tests.

**1. The agreement metric had no reading for a trillion.**
`cited_figure_agreement` scored 2 against a threshold of 0:

```
valuation_dcf/equity_value#269 cites `equity_value` = 3114740780819.794301897679 USD and states 3.11
valuation_dcf/enterprise_value#267 cites `enterprise_value` = 3134099780819.794301897679 USD and states 3.13
```

The drafter wrote "$3.11 trillion" and the figure is right. `aer.core.figures.READINGS`
admitted a figure said in units, per cent, thousands, millions or billions, and stopped
there — its own comment says "a drafter writing longhand says billions". Microsoft's equity
is $3.1tn, and so is every other company worth writing about at this size. A blocking metric
that fails every large-cap valuation is a metric somebody switches off, so the reading is
admitted for exactly the reason billions is: it is how the sentence says the number. A wrong
figure in trillions is still caught, which the tests hold.

**2. The driver's policy called a complete report incomplete.**
`MIN_SECTIONS_GENERATED` was a fixed 17, which is all but one of the eighteen a standard run
writes. QUICK writes nine. Nine of nine tripped a floor written for a different mode, and
the stop reason read "too many sections lost" about a report that had lost nothing. The
tolerance was right and the floor was the standard spine written down a second time;
`aer.services.acceptance` had the relative form all along, and its own sections check passed
this run. The policy now counts against the sections the run has.

Neither defect could have been found without running the mode. Both are the readiness
audit's own pattern, recorded in the handover's §7: a document confidently describing code
nobody had read.

## The budget stop, which worked and is worth knowing about

The run paused at the drafting step with `BUDGET_EXCEEDED` against its £6 cap, and the
driver raised the cap to £12 under its policy. **Actual spend at that moment was £1.87.**
The pause was on a projection, not on money spent, and the projection for nine remaining
sections was more than three times what they cost.

That is the cap doing its job in the safe direction. It is also worth calibrating: a £6 cap
stops a run that finishes at £4.95, so an operator setting a cap near the expected cost will
be interrupted by arithmetic rather than by spending.

## What is not in this record

No approved report, so no `report.md` or `report.pdf`. The draft is in the review page
(`msft1-quick/screens/review.html`, 10,043 words) and in the run export. The run is left at
`AWAITING_APPROVAL` in the database rather than approved by a script: approving a report is
the operator's decision, and the two defects above are the reason the policy declined.
