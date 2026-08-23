# 0052 — A step with no estimate is a step with no cap

Date: 2026-08-14
Status: Accepted

## Context

Invariant 6: cost is metered and capped **in code**, and "caps that only warn are caps that
do not work". ADR 0051 closed the monthly half of that — a ceiling that was stored and never
compared. This is the other half, and it hid in a subtler place.

`BudgetGuard.check` is correct. The problem is what reaches it. Both call sites in
`aer.workflow.engine` are written:

```python
if self._budget is not None and step.estimated_cost_gbp > 0:
    await self._budget.check(...)
```

So `estimated_cost_gbp` is not advisory. It is the switch that decides whether a step is
looked at. A step that declares no estimate is not treated as cheap; it is not treated at
all.

Every spending step in `vertical_slice_v1` declared one — the planner, the five research
workers, the assumption proposals, the validator, the red team — except `draft`. Which is
one Opus call per model-written section, nineteen sections, and **£5.17 on the first full
live run**: by a wide margin the most expensive thing the workflow does, and the only
spending step the guard never saw.

Two consequences, both live for the whole life of the engine:

1. **The cap did not bound a run.** The default per-run ceiling was £2.50. The measured run
   went well past eight pounds and was never paused, because the item that took it there was
   invisible.
2. **The projected cost shown at the plan gate understated the run by its largest term.** The
   operator approving a run was shown a figure with the drafting left out.

Neither is a bug in the guard. Both are the guard being asked about the wrong set of steps.

It was found by asking what a *different* change made worse. Raising `report_writer`'s
`max_output_tokens` (five sections had come back with `stop_reason: max_tokens` and no draft)
raises the worst-case cost of the drafting step — which is when it became worth checking what
bounded that step, and the answer was nothing.

## Decision

**Every step that can call a model declares an estimate, and the rule is tested as a
decision rather than inferred as a measurement.** `DRAFT_ESTIMATE_GBP` is £6.00 — above the
measured £5.17, generous on the same principle as every other estimate in that module, since
an estimate that understates lets a run through a guard that should have paused it.

The test does not try to detect which steps spend. It asserts that every step is either
guarded or explicitly named as deterministic, so a step added later lands in neither list and
fails with its own name. Detecting spend statically would be a cleverer test that answers a
question nobody should be guessing at: whether a step calls a model is something its author
knows.

**The default per-run budget moves from £2.50 to £12.00.** Not a loosening — the opposite.
£2.50 was chosen before there was a run to measure, and it never stopped anything, because
the step that would have breached it was unguarded. With the guard now able to see that step,
£2.50 would stop *every* run at drafting: the same wrong number, failing loudly instead of
silently. £12.00 admits a measured run with headroom and leaves the monthly ceiling as the
thing that bounds the total. It is a default, `AER_PER_RUN_BUDGET_GBP` overrides it, and the
plan gate shows the projected cost before anything is spent.

The test fixtures read that default from `Settings` rather than restating it. A fixture that
budgets more generously than production can never notice a step growing past the ceiling real
runs are held to — which is precisely how this went unseen: the fixtures said £2.50 and so
did production, but neither was ever asked about the draft step.

## Consequences

**The guard is still consulted only before a step, never inside one.** The drafting step
makes nineteen model calls with no check between them, so the ceiling bounds when the draft
may *start*, not what it may spend once running. That is a real remaining gap and it is
recorded as one rather than quietly implied to be closed. Per-call budgeting is a larger
change than the one that found this, and it needs its own decision about what a partial
report is worth.

**Estimates now have to be maintained.** An estimate that drifts far below reality
re-creates this defect in a milder form. The mitigation is that the estimates are deliberately
generous and the metered figures are on the `/costs` page, so drift is visible to anyone who
looks — but nothing enforces it, and pretending otherwise would be the same mistake this ADR
is about.

**One stale 2.50 is deliberately left alone.** `research_requests.max_cost_gbp` carries a
`server_default` of 2.50 from migration 0001. No path reaches it: the create schema declares
`max_cost_gbp` required with no default, `_apply` always assigns it, and the form is
populated from settings — so the column default is unreachable rather than wrong-in-effect.
A migration to change an unreachable constant is more risk than the inconsistency it removes,
and recording that judgement here is better than a silent mismatch nobody can explain later.

**`docs/archive/PLAN.md` §Stage 2 acceptance criterion 7 now disagrees with reality.** It says "Run
cost ≤ £2.50, wall-clock ≤ 60 min for a large-cap US company"; the measured run was over £8
and took 48 minutes. PLAN.md is the authority on scope, so it is **not** amended here — the
criterion is either a target the platform must be made to meet (a real conversation about
model routing and section count) or a figure that predated measurement, and that is the
operator's call, not a decision to take in an ADR about the budget guard.

**A run that would exceed the cap now stops before drafting rather than after.** That is the
intended behaviour and it is also a visible change: an operator whose ceiling is set below
roughly £7.50 will see `budget_exceeded` at the `draft` step where previously they saw a
finished report and a larger bill. The run console names which ceiling was hit (ADR 0051), so
the fix is one setting away.
