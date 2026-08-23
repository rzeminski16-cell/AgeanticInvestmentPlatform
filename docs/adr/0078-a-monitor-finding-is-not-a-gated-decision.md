# ADR 0078 — A monitor finding is not a gated decision

**Status.** Accepted
**Date.** 2026-08-22
**Required by.** `docs/archive/investment-os.md` §13 question 2, and by ADRs 0079, 0080 and 0081 —
three roles that produce output continuously and, without this record, have nowhere to put
it that is not an approval.
**Extends.** ADR 0046, which named review fatigue as a failure mode while arguing for a role
narrow enough to avoid it. This record meets the same failure mode at the scale where it
actually bites.

## Context

Gates are this platform's central control, and they work for a specific and fragile reason.

`GateKind` (`src/aer/core/enums.py:123`) holds eight values, and the number a report
actually meets is smaller than that in both directions. `vertical_slice_v1` gives seven of
them a workflow step; `BUDGET` has none, and no gate page renders one, so it sits in
`GATE_ORDER` as a kind nothing currently reaches. Of the seven that are declared,
`approvals._CONDITIONAL` exempts five — unmapped concepts, peer set, sector specialist,
theme set and assumptions all fire only on the runs that need them — leaving **`PLAN` and
`FINAL` as the only gates every run passes.** So a report is decided at two gates at the low
end and seven at the high. Each shows a payload a person can read in a minute, and
`payload_hash` seals exactly what was on the screen, because "storing only the decision
would leave 'approved' meaning nothing in particular six months later". At that count
reading the payload is the natural thing to do rather than the diligent thing to do.

ADR 0046 named the property that makes this work: *"review fatigue over a long list is a
real failure mode, where review of two is not."*

The platform already knows the difference between something worth telling a person and
something worth asking one. `aer/core/escalation.py` holds the ten trigger conditions from
`docs/archive/PLAN.md` §2.4 — low source coverage, potential look-ahead, validation failure and
seven more — and **not one of them opens a gate.** They change what the final gate *says*:
"the run already pauses at gate 2 unconditionally … so what a fired trigger changes is what
that pause *says*". Ten conditions, one decision. That ratio is the design, and it was
arrived at for a workflow that runs a few times a week.

A nightly thesis monitor across forty watchlist names has no such ratio available. It
generates gate-shaped output at roughly forty a day, produced at 03:00 with nobody in front
of it, and what meets the operator in the morning is a list of forty items each asking to be
approved. A handful of readable gates per report is a control. Forty a day is a rubber
stamp, and the difference is not one of degree.

**A person facing that list clicks approve, and mechanically clicking approve is strictly
worse than not gating at all.** Not equivalent — worse. With no gate, the record says a
monitor observed something and nobody has yet looked. With a gate, the record says a named
person was shown a specific payload, read it, and agreed: an `approvals` row carrying an
actor, a timestamp, a `payload_hash` and a chained audit event witnessing the decision. That
is the strongest evidentiary claim this platform is capable of making, and it would be
false.

It would also be false **invisibly**. A rubber-stamped row is byte-for-byte
indistinguishable from one produced by genuine reading, so nothing downstream can discount
it, and the post-trade surfaces that later ask "what was the operator shown, and what did
they decide" would read forty reflexes as forty judgements. A degraded gate does not merely
fail to add evidence; it manufactures false evidence of human judgement, and it does so
where nothing can detect the difference.

The damage does not stay in the monitor, either. Gates are one mechanism shared across every
tool. An operator taught by a nightly queue that *approve* is the button you press to clear
a list carries that habit to the final gate on a report.

## Decision

**Tiered. Only a `contradicted` status opens a gate. Everything else accumulates as a
finding, with no approval semantics of any kind.**

The thesis monitor's status enum is closed at `{unchanged, weakened, strengthened,
contradicted, unobservable}` (ADR 0079). Four of those describe evidence moving. One
describes a premise the position rests on having failed, and that is the single transition
with a consequence somebody must own: holding a position whose stated thesis has been
contradicted is a decision, and so is not holding it. It happens rarely, which is precisely
what makes it affordable as a gate and precisely what makes it deserve one.

**That gate has a schema prerequisite, and it is not this record's to satisfy.**
`approvals.request_id` is `NOT NULL` with a foreign key to `research_requests`
(`src/aer/db/models/approval.py:37-39`), so a monitor run — which has no research request —
cannot write an approval row at all; ADR 0072 repoints the column to `work_orders`, and the
one outcome tiered here as a genuine human judgement is unrecordable until it does.

**The other four are findings, and a finding is a question raised.** It changes nothing on
its own. It has no `approvals` row, no `payload_hash`, no decision column and no actor who
agreed to anything, and **every surface that renders one must label it a finding rather than
a decision.** The labelling is part of the control, not presentation polish: a queue that
looks like an inbox of approvals will be worked like one, and the whole point of this record
is to stop that shape existing.

A finding is not a lighter kind of approval. It is a different record making a different
claim — *the platform noticed this*, not *a person agreed to this* — and that distinction is
worth a separate table rather than a nullable column on the existing one.

**The tier is assigned by code at the point the status is written**, not chosen at display
time and not requested by the model. The monitor's output contract has no field in which to
ask for a gate, exactly as it has no field for a rating or an action. A role that could
escalate itself would be making a judgement about how much of the operator's attention it
deserves, which is a judgement about the operator rather than about the evidence.

## Attention items are resolvable, and never by the condition lifting

**An attention item is closed by an explicit act that records a reason. It is never closed
by the underlying condition quietly going away.**

The temptation is obvious. A monitor finds a margin premise weakened, re-runs a week later
and finds it unchanged; the item vanishes and the queue is tidier. Reject that on two
grounds.

**"I saw this and chose to do nothing" is decision data.** It is among the most valuable
rows the platform can hold. ADR 0081 scores the process rather than the outcome, and the
process is exactly what the operator was shown, when, and what they did about it. An
auto-clearing item leaves no answer to "was this visible before the position lost money" —
worse, it leaves a misleading one, because the evidence that the question was ever raised
has been tidied away by the same mechanism that raised it. A finding the operator dismissed
with a stated reason and a finding that evaporated on its own are opposite facts about the
operator and identical rows in a queue that empties itself.

**And a queue that empties itself teaches the operator that ignoring it works.** That is the
mirror image of the fatigue problem above, arriving at the same destination — an unread
surface — by the opposite route. If the cost of ignoring an item is that it goes away,
ignoring is the rational policy, and the queue is decoration inside a month.

The repository has argued this before, on a smaller surface.
`services/disagreements.py::settle_by_hand` refuses to settle a conflict that was never
escalated, refuses an outcome that is not a choice between the two positions, and refuses an
empty rationale: "a decision that overrides a rule without saying why is the least reviewable
row in the table". All three refusals carry over.

**The resolution is an appended record following the `audit_events` shape — actor, event
type, payload, chained hash — not a flag flipped on the item.** `settle_by_hand` mutates in
place, which is fair for a row belonging to a run that ends; a standing queue over a
permanent book does not end. ADR 0031 made this argument for erasure, and it holds here: a
row rewritten in place records the current state and nothing about how it got there. An item
reopened, a resolution the operator later regrets, and a condition that recurs are three
different histories that one boolean cannot tell apart.

## The audit chain must reach this

Tiering findings against gates is only worth doing if a finding, its resolution and the
decision that followed are chained and can be found together afterwards, and
`audit_events` correlates by `job_id` and `request_id` and by nothing else — a vocabulary
built when every consequential record was a research record. ADR 0072 adds the generic
subject correlation that makes a portfolio record reachable, as a prerequisite of this
decision rather than a consequence of it. **The motivation is this record's:** a trade
entry, a position correction and a thesis edit landing outside the chain would leave the
most consequential records in the system the least tamper-evident, sitting beside a
discarded draft request that is — an exact inversion of the design as it stands.

## Cost changes shape

`BudgetExceededError` is documented as a control-flow signal: "the orchestrator pauses the
run and waits for a human decision rather than failing it" (`src/aer/errors.py:126`). The
engine implements precisely that — `JobStatus.BUDGET_EXCEEDED` is a pause in every one of
its four handlers — and the agent's own refusal ends "the run is paused for a decision.
Raise the cap on this request to continue."

That is correct at a console. At 03:00 on an unattended monitor it is a run that halts,
holds its state, tells nobody and waits for somebody who is asleep. The monthly ceiling
raises the same error and pauses the same way, and no per-request cap change releases it.

**An unattended run that breaches its cap must stop and leave a finding. It must never pause
waiting for nobody.** Pausing is a request for input; a request for input addressed to
nobody is a run discovered in the morning having achieved neither the work nor the decision.
Stopping with a finding achieves the second, which was the only one available.

The deeper change is shape rather than amount. A report is a bounded expense: one run, one
cap, a person who chose to start it. Continuous monitoring is a standing subscription —
forty names a night, indefinitely, authorised once — in which the per-run cap governs a sum
too small to be a control and the monthly cap is the only real one. ADR 0052's lesson, that
a step with no estimate is a step with no cap, was learned on a workflow somebody was
watching. It applies with more force to a step nobody watches, because the loop that would
otherwise catch a bad estimate — an operator seeing an implausible figure on a run console —
is not there.

## Consequences

**The attention queue becomes the platform's single cross-tool integration surface**, and
therefore needs one severity vocabulary shared by every tool that contributes to it. A
monitor finding, a risk observation, a stale mark and a failed nightly pull have to be
comparable at a glance or the queue is five queues in a shared frame. One vocabulary,
defined once, is the cost of that; it is also what lets the queue be sorted by something
more useful than recency.

**Gates stay rare, so gates stay meaningful.** The count per report is unchanged, the
monitor adds one gate kind that fires seldom, and an `approvals` row keeps meaning what it
has always meant. That is bought with a real cost: a `weakened` finding nobody reads is a
finding nobody read, and the platform will have recorded exactly that. Recording it honestly
is the point — the alternative on offer was a row asserting that somebody read it.
