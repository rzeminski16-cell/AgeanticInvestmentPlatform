# 0039 — The red team attacks from a separate context it structurally cannot escape

Date: 2026-08-06. Status: accepted.

## Context

A drafting pipeline that checks its own work converges on self-consistent nonsense: every
role shares the same working notes, so every role shares the same blind spots. §2.5's
evaluator row is explicit that adversarial roles "must not share context with the thing
they check", and §2.4's escalation table gates on the red team materially contradicting
the base thesis on a scored dimension. Task 40 builds that adversary. A new agent role
requires an ADR (ADR 0035). This is the `red_team` role's.

## Decision

**Isolation is a property of the input type, not a discipline of the caller.**
`RedTeamInput` has fields for exactly two things: the draft's recorded `claims` rows and
the run's evidence index (facts, calculations, sources — ids, values, tiers, dates). It
has no field for section prose, worker findings, coverage notes or any other artefact of
the drafting context, and `extra="forbid"` refuses anything smuggled under another name.
A caller holding the working notes has nowhere to put them. The service builds the input
from the tables alone and never loads section content, which a test proves by planting a
marker in the drafting context and asserting it cannot reach the composed prompt.

**Challenges are scored and cited, or they do not exist.** Each challenge names a
dimension from a closed vocabulary — closed because "on a scored dimension" is only
meaningful if the platform can group and compare by it — a severity from 1 to 5, and the
evidence it rests on. A challenge citing nothing fails the response schema; one citing an
id the run does not hold is rejected whole, not trimmed, because an argument resting
partly on fabricated evidence is a fabrication with good footnotes. The role holds no
tools: an adversary that could fetch would build its case from material the base thesis
never saw, and the comparison would stop being about the draft.

**Every surviving challenge lands on the task 19 ladder's thesis rung.** Escalated to
gate 2, never auto-resolved, both positions stored and published — a challenge the system
itself could dismiss would be worth nothing. Materiality follows severity: at 4 or above
the challenge materially contradicts the thesis and raises the §2.4 banner (the state
task 41's escalation engine gates on); below it the challenge is still recorded and
published without claiming the thesis is in danger. `thesis_conflict` gained a
`material` argument for exactly this — the escalation and the record never vary, only
the banner. Recording is idempotent on the challenge's own content digest, so a retried
step neither duplicates nor drops what the adversary said.

**One call, on the batch path, and the gate hash moves to the last word.** §1.8 budgets
the bear case on the batch transport; the run's single red-team call travels through
`Agent.run_batch`, with the sync path kept for parity testing. Because challenges join
the gate 2 payload as escalations, the payload hash the final gate verifies is now
computed by the `red_team` step — the last step that can change what the operator will
be shown — rather than by `draft`. A run whose draft recorded no claims skips the
adversary visibly and spends nothing: there is nothing asserted to attack, and the step
says so instead of manufacturing objections to prose it cannot see.

## Consequences

A fixture run with a planted contradiction produces a challenge that materially
contradicts the base thesis on a scored dimension, recorded where gate 2 displays it and
task 41 can trigger on it. The operator sees the bull and bear positions side by side
with the ladder's resolution states reachable — settle by hand, or approve with the
conflict on the record. The cost is one Opus-class call per run with claims, routed,
capped and metered like any role, and halved by the batch transport. The known limit:
the red team sees recorded claims, not prose, so a thesis asserted only in prose and
never as a claim escapes challenge — which is an argument for drafting that records its
claims, not for widening the adversary's context.
