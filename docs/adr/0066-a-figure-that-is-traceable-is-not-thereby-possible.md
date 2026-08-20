# ADR 0066 — A figure that is traceable is not thereby possible

**Status.** Accepted
**Date.** 2026-08-20
**Extends.** Invariant 3 ("no figure reaches a report unless it is a stored fact or a
recorded calculation") gains a second condition on one surface. Decided on the operator's
direction after the MTB run.

## Context

The MTB run of 2026-08-20 published a front page reading Revenue Q2 FY2026 $442m, Net
income $818m, Net margin FY2025 172.1%. Income exceeding revenue is arithmetically
impossible on a consolidated statement, and the report's own footnotes carried the
contradiction across four fiscal years of recorded margins between 1.31 and 1.85.

Every guard held while it happened. The $442m was a stored fact with a hashed source —
the revenue concept had resolved to a partial caption of the filer's income statement
(gap A62), so the *tag* was faithfully extracted and the *label* was wrong. The 172.1%
was a recorded calculation carrying its formula, inputs and code version. All 91
citations verified. `numerical_consistency` passed at zero, because it checks that a
figure replays consistently, not that it is possible. The red team found the defect
unaided, argued it from the platform's own recorded values, and scored it 5/5 — but a
red-team challenge is advisory prose on the approval page, and the number rendered
anyway.

Invariant 3 guarantees traceability. This run proved traceability and sanity are
different properties, and that the platform owned no check for the second.

## Decision

**Deterministic code asks whether the headline figures are possible, withholds the
surface they would have shared, and escalates — it never decides which figure is wrong.**

Three parts, all in code, no model call anywhere:

1. **The relations live in the correctness core.** `aer/calc/plausibility.py` holds a
   closed set of relations that cannot hold together: net income above revenue on a
   consolidated statement, a net margin above one, asset turnover below one percent on a
   billion-plus balance sheet. Pure, `mypy --strict`, property-tested. The set is
   deliberately small and each relation is an impossibility or a floor set below any
   real business — this is not a heuristic anomaly detector, and it must never grow into
   one without revisiting this ADR.

2. **A run-time metric, so the failure reaches every existing surface.**
   `figure_plausibility` joins the run-time evaluation set at threshold zero: one row
   per run, its failures naming each impossible relation with the period and the values.
   Through the existing machinery a failure lands in the validation table, the approval
   page, the `evaluations.check_failed` log line (gap A60's work), and the
   `VALIDATION_FAILURE` escalation banner — so approving over an impossible figure is a
   recorded decision made with the banner showing.

3. **The at-a-glance block withholds itself, stating why.** The front page re-checks the
   relations over exactly the rows it is about to render and refuses the whole block
   when any fail — the same posture as ADR 0061's mixing refusal: the reader is told,
   rather than shown a lie they cannot detect. The whole block rather than the implicated
   rows, because the check cannot know which leg of an impossible relation is wrong:
   income above revenue means one of the two is mislabelled, and withholding only the
   margin would leave the mislabelled figure standing as the front page's anchor.

## What this deliberately does not do

- **It does not correct the figure.** The ordinary cause is a concept-mapping defect
  (A62's fix is separate), and code guessing which caption the filer meant would be a
  new number from nowhere — the exact thing invariant 3 exists to prevent.
- **It does not block the run.** A margin above one is representable in the world — a
  one-off gain below the revenue line can produce it. The platform's answer to a
  representable-but-extraordinary state is a gate a person crosses knowingly, not a
  refusal that would make the rare legitimate case unpublishable.
- **It does not scan prose.** The model's commentary is already bound to recorded
  figures through claims; this check runs over the recorded figures themselves.

## Consequences

The front page can now be absent for a reason a previous version would have rendered
through. That is the intended trade: a blank at-a-glance block with a stated reason
costs a reader a table; a 172.1% net margin costs the report its credibility and the
operator their trust in every other number. The metric row means the next MTB-shaped
run fails its validation table before approval instead of after publication.
