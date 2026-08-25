# ADR 0086 — A cited calculation must be the one the sentence quotes

**Status.** Accepted
**Date.** 2026-08-25
**Extends.** Invariant 3 ("no figure reaches a report unless it is a stored fact, a
recorded calculation or an attestation"). ADR 0066 added the second condition — that a
figure be *possible*. This adds the third — that the sentence use the figure it cites.
Decided on the operator's direction after the MSFT run.

## Context

The MSFT run of 2026-08-24 drafted a balance-sheet section asserting a quick ratio of 0.93
and a current ratio of 1.23. The run's own `quick_ratio` calculations were 1.567 and 1.536,
its `current_ratio` values 1.785 and 1.769. Debt to equity was drafted at 0.09× against
recorded values of 0.299 and 0.229, interest cover at roughly 50.9× against 40.4 and 45.0,
and the cash conversion cycle at −51.8 days against −7.41 and −2.56.

The direction of the error mattered as much as its size. The draft concluded that liquidity
was thin — "a quick ratio below one" — where the recorded figures say it is comfortable. A
reader taking the section at face value would have reached the opposite view of the
company's balance sheet from the one the run's own arithmetic supports.

**Every deterministic check passed.** `numerical_consistency` re-executes stored
calculations from their own records and never reads the prose. `citation_accuracy`
re-reads quoted excerpts from the filings and confirms them, and the filings were quoted
correctly — what was wrong was the number in the sentence beside the quote.
`figure_plausibility` (ADR 0066) asks whether the headline relations are possible, and 0.93
is a perfectly possible quick ratio. `presentation_integrity` scans the rendered document
for defect classes, and a wrong figure is not a presentation defect.

The red team found it, argued it from the platform's own recorded values, and scored it 4/5.
That is the second time an adversarial pass has been the only thing standing between a wrong
number and a reader, and ADR 0066 already recorded why that is not good enough: a red-team
challenge is prose on an approval page, and the number renders anyway.

## Decision

**A drafted claim that names a calculation must state that calculation's figure, checked
deterministically, at threshold zero.**

`cited_figure_agreement` joins the run-time metric set. Four parts:

1. **The rule is structural, not textual.** `claims.calculation_id` already exists and the
   section writer already sets it from the drafter's own proposal — `validate_draft` refuses
   an id the section was not dealt, so the join is sound by construction. The alternative
   considered and rejected was to hunt phrases like "quick ratio of 0.93" in the prose and
   look up a `quick_ratio` row: that needs a ratio vocabulary somebody maintains for ever
   and is wrong the first time a writer phrases one differently.

2. **Agreement is the draft's own precision, not a tolerance.** A figure written to two
   decimal places agrees if the calculation rounds to it at two decimal places. So 0.09 over
   a stored 0.0857 passes — that is exactly what "0.09" claims — while 0.93 over 1.567 fails
   at every precision. A relative tolerance cannot express this: loose enough to accept a
   two-decimal rounding of a small ratio, it would accept a fifth of the errors worth
   catching.

3. **The renderings the platform actually produces are all admitted.** `render.display`
   scales a dimensionless figure by a hundred for a percentage and renders money in
   millions; a drafter writing longhand says billions. Each is the same figure said
   differently. A check that failed on "46.8%" over a stored 0.4676 would be switched off
   within a week and would deserve to be.

4. **A claim resting on a calculation without printing it is not a violation.** Plenty of
   sentences cite a figure they do not quote, and failing those would make the metric fire
   on good prose until somebody turned it off — which is how a blocking check dies.

The threshold is zero and the metric joins `VALIDATION_FAILURE`, so a failure reaches the
validation table, the coverage notice, the check-failed log line and the escalation banner
through machinery that already existed.

## Consequences

A run whose prose disagrees with its ledger now fails a check by name, with the calculation,
the recorded value and the drafted figures in the failure text — an operator can act on it
without opening the ledger.

**It does not decide which is wrong**, and deliberately so. A sentence disagreeing with its
cited calculation means either the writer misread the row or cited the wrong row, and saying
which would be a guess. The same posture ADR 0066 took for an impossible relation.

**It measures only claims that carry a `calculation_id`.** A figure a writer states with no
citation at all is not caught here — that is the source-coverage and citation floors' job,
and a section whose numeric claims cite nothing already fails those. What this closes is the
narrower and more dangerous case: a sentence that *looks* fully accounted for.

**A run with no cited figures records the metric as not exercised**, never as a pass. A gate
that passes when its population disappears has stopped testing anything, and it does so
silently.
