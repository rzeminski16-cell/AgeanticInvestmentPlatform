# ADR 0123 — A decision at a gate is superseded, never re-asserted; a rejection ends the run; the remedies for a stale gate are controls

**Status.** Proposed — V1.0_Alpha, Phase 1.2. Accepted when the change it argues lands.
**Date.** 2026-09-16
**Extends.** ADR 0090 (a resumed run is the same job, and the audit trail says so): `status`
is where the run is now, history lives where history already lives, and continuing is a
recorded decision, never a rewrite. ADR 0102 and ADR 0104, whose supersede-with-a-pointer
shape this copies for approvals as ADR 0116 copies it for reports.
**Amends.** The approvals service's third rule — *approving twice is refused* — which is
narrowed, not removed: a second decision at a gate is refused unless it supersedes one that
has gone stale, and says which.
**Does not touch.** Invariant 2 (the model may propose a citation; only code confirms one),
the hash discipline (an approval records a digest of exactly what was displayed, and the
workflow compares it before continuing), or ADR 0018's per-instance override, which is a
separate control on a separate surface (readiness audit F-21, Phase 1.4).
**Required by.** `docs/V1.0_Alpha/05-delivery-plan.md` Phase 1.2; readiness audit findings
F-16 (a stale approval is a dead end) and F-22 (nothing re-measures a failed check on a
finished run); roadmap §2.11; and the journey harness, whose rows `gate.*.rejected`,
`gate.*.stale_page_moved` and `gate.*.stale_seal_drift` are red on their forward control
(`tests/journey_inventory.py`, measured 16 September 2026).

## Context

An approval gate compares three digests. The **approval** carries the hash of what the
review page computed when the operator decided. The **seal** is the hash the run's own
sealing step wrote — `critique_plan` for the plan, `revise` for the report. The **page** is
the hash of the payload as it renders now. When all three agree the run continues; when
they do not, `_require_approval` says which two agree and pauses the run, and since this
session it writes the case into the pause's record as a `PauseReason`:

| Case | What agrees | What the pause says | What the operator can do today |
|---|---|---|---|
| `gate_stale_page_moved` | seal and page; the approval is of older content | *"Open the review page again and decide on what it shows now."* | Nothing. A second decision at a decided gate is refused: *"changing one needs a new run, not a second approval of the old"* |
| `gate_stale_seal_drift` | approval and page; the seal is the odd one out | *"The seal is re-derived … by `aer reseal <job-id>`"* | Open a terminal |
| `gate_rejected` | — | *"The gate was rejected. The run stops here."* | Nothing. The job stays `AWAITING_APPROVAL`, so it can be neither resumed (the gate pauses again) nor started afresh (`start_run` supersedes only a terminal run) |

The first row is the dead end the readiness audit named F-16, and the third is how it cost
money: MSFT #2 and M&T reached states the interface could not leave, £14.41 of a £100 budget.
The second row is not a dead end for a person with a terminal and is one for the product
the delivery plan describes, whose console tells the operator to type a command.

A fourth case sits beside them. The final gate shows the run's eleven checks with their
results; a check that failed names its findings. Nothing re-measures it. The evaluation rows
are written once, inside `validate`, and the engine never re-executes a step whose row says
`SUCCEEDED` — which is right for crash recovery and wrong here, because the only way to see a
corrected figure re-checked is to pay for the whole run again. That is F-22.

The vocabulary already has the word this needs. `Decision.AMENDED` has been a member of the
enum, with its own label in the web vocabulary, since the approvals model was written, and
nothing has ever written it. The approvals module's docstring says why: a change of mind
*"needs its own vocabulary before it needs an implementation."* This is the vocabulary.

## Decision

### 1. A decision is superseded, never re-asserted

`approvals` gains `supersedes_id`: a nullable self-reference with `ON DELETE RESTRICT`, a
uniqueness constraint (a decision is superseded at most once) and a check that a row does not
supersede itself — the shape `judgements`, `attestations` and `assumption_proposals` already
carry. Rows are still never updated or deleted.

A second decision at a gate is accepted on exactly two conditions, both checked by the
service and neither by the page:

- the recorded decision's `payload_hash` differs from the hash of the gate's payload as it
  renders now — the page has moved under the decision; and
- the new row names the decision it supersedes, and that decision is the gate's current one.

Everything else the third rule refused stays refused: a second decision over an unchanged
payload is re-assertion, a decision out of gate order is out of order, and a decision at a
gate whose run has passed it is a decision about nothing.

**`Decision.AMENDED` is the value a superseding decision writes when it approves what the
page shows now.** A superseding rejection writes `REJECTED`. So the audit chain reads
*approved (hash A) — amended (hash B, supersedes the first)* rather than *approved, approved*,
and a reader can tell an approval from an approval taken after the content changed under one.
The workflow passes a gate on `APPROVED` or `AMENDED`; the set is named once
(`PASSING_DECISIONS`) and every reader of "is this gate approved?" reads it there.

**The current decision at a gate is the newest row no other row supersedes.** One service
function answers it, and the six places that today select an approval by gate — the gate
check, the out-of-order check, the confirmed-slate readers for peers, themes and the sector,
the re-seal service — read it through that function rather than through their own query.

Why not rewrite the stale row's decision to `AMENDED` and be done: because that is a rewrite
of a decision record, and ADR 0090 settled that the record of what happened is never
rewritten to make the present tidy. The stale approval was a real decision over real content;
it stays, and the row that supersedes it says so.

### 2. A rejection ends the run

Recording `REJECTED` at a run gate requests the run's cancellation, with the rejection as the
reason — the gate, and the operator's note if one was written. The cancellation service's own
rule then applies: a run waiting at a gate has nothing executing it, so it is `CANCELLED` at
once; a run that is moving stops at its next boundary. The job is terminal, and a request
whose run produced no report may be started again, which is what `start_run` has done after a
failure since the cancellation fix and now does after a rejection.

No new job status. *Rejected* is a decision and *cancelled* is what it did to the run, and
the cancellation row carries the reason; a `REJECTED` status on the job would duplicate the
approval row and force a fourth terminal state through every exhaustive match in the
codebase. The gate button has said *"Reject and stop this run"* all along. Now it does.

The workflow's own branch — *"the gate was rejected; the run stops here"* — stays, as the
defensive stop for a rejection that reached the gate by a path that did not cancel. It should
never be reached from the product.

### 3. Re-seal is a control

`POST /runs/{job_id}/reseal` calls `reseal_final_gate` — the same function the terminal
command calls, with the same audit event — and then does what the command tells the operator
to do next: when the recorded approval matches the moved seal, the same request records a
resume (ADR 0090) and re-enqueues the run; when it does not, the console says the approval was
of older content and offers to decide again, which is now possible under part 1. The pause
message stops naming the command. The command stays, for a terminal.

### 4. Re-measure is a control

`POST /runs/{job_id}/remeasure` invalidates the `validate` step and every step after it that
has run, appends `run.remeasure_requested` to the audit chain with the reason, records a
resume and re-enqueues the run. Invalidating a step means what the engine's own retry path
means: the row stays, its status stops being `SUCCEEDED`, and the next execution runs it as
attempt *n+1* — so the record keeps every attempt, and "this check was measured twice" is a
column rather than an inference.

The steps from `validate` onward were already required to be re-entrant, because crash
recovery re-executes any of them: the evaluation writer deletes and rewrites its rows, the
red team's challenges are fingerprinted so a second pass finds rather than duplicates them,
the revise pass replaces its notes, the render step reuses its report row. The seal moves
with the re-run of `revise`, so a decision recorded over the refused check becomes stale in
the ordinary way and is superseded under part 1.

Refused while the run is executing (a worker may be about to write the very rows) and after
it has finished: a finished report is not re-measured, it is superseded with a reason under
ADR 0116. What re-measuring costs is the steps from `validate` onward, never the whole run.

### 5. The console names the case and offers its control

For a run stopped at a gate the console says which of the cases above it is in, read from
the pause's `PauseReason`, and offers exactly the control that case needs, labelled in the
operator's vocabulary:

| Reason | The console offers |
|---|---|
| `gate_waiting` | The gate's page, as now |
| `gate_rejected` | *Start a new run* (the run is terminal; the request page's own control, moved to where the operator is) |
| `gate_stale_page_moved` | *Decide again* — the gate's page, which shows the decision it will supersede |
| `gate_stale_seal_drift` | *Re-seal and continue* |
| a failed check at the final gate | *Re-measure the checks* |

A terminal run that produced no report offers *Start a new run* whatever ended it. The
step keys, error codes and the remaining shell command on the console are Phase 1.4's, not
this decision's; the harness records them separately.

## Consequences

- The three dead ends the audit named at the gates have a way forward that is not the
  terminal, and the fourth — a refused check — has one for the first time. The harness's
  `gate.*.rejected`, `gate.*.stale_page_moved` and `gate.*.stale_seal_drift` rows move their
  `control` assertion out of the record; their `text` assertion stays until Phase 1.4.
- `Decision.AMENDED` is written for the first time, with one meaning. `approval.amended`
  joins `approval.approved` and `approval.rejected` in the audit chain, and the event names
  the decision it supersedes.
- Migration 0074 adds the column and its three constraints. No existing row changes.
- Every test that rejected a gate and asserted the run stayed waiting now asserts that it
  ended, which is what the button always said.
- The audit driver reads a gate's current decision through the service. Its policy still
  stops on a post-approval pause rather than deciding again; whether it should is the
  driver's decision, not this one.
- A second decision is possible only where the page moved. A gate that was approved over the
  content it still shows cannot be approved again, rejected again or "corrected": that is the
  rule the hash exists to enforce, unchanged.
- What this does not do: bulk re-decision; *approve anyway* over an unverified citation
  (F-21, ADR 0018's override, Phase 1.4); superseding a report (ADR 0116); any change to
  which gates fire or to their order.
