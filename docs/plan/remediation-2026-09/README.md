# The remediation diagnosis — the evidence behind the plan

The plan is [`../remediation-2026-09.md`](../remediation-2026-09.md). This folder is what it
was written from, kept for the same reason the audit's results folder is kept: a plan whose
reasoning cannot be re-read is an opinion.

| File | What it holds |
|---|---|
| `diagnosis-workflow.js` | The orchestration that produced it — fourteen subsystem readers over the audit record and the code, one adversarial refuter each, then four independently framed plans, three judges and two completeness critics. Thirty-seven agents |
| `diagnosis.json` | 165 findings that survived refutation (of 168 raised), each with its root cause at file:line, its options, its regression test, its acceptance criterion and the refuter's verdict — plus the three that were refuted and 125 gaps the refuters named |
| `plans-and-judgements.json` | The four competing plans, the three judges' scores and rankings, and the two critics' findings — including the assumptions they showed the record does not support |

Three of those findings changed the plan's shape and are argued in §2 of the plan: a stated
view is necessary-sounding and unproven; the console's winning material was never T5, so this
is an acquisition problem rather than an evidence-policy one; and the effective independent
sample for the current product is one document read by three judges.
