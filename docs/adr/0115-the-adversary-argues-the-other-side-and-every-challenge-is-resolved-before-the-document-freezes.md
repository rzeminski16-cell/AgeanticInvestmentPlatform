# ADR 0115 — The adversary argues the other side, and every challenge is resolved before the document freezes

**Status.** **Accepted in part — 17 September 2026 (Phase 3.3).** Decision 4 has landed: the
appendix is assembled after the settle and says what happened. Decisions 1, 2 and 5 wait on
ADR 0117, because there is nothing to argue against until the report states a view. **Decision
3 does not stand as written** and is corrected below, against a measurement of the run it was
argued from. This record becomes Accepted outright when the remaining decisions land.
**Date.** 2026-09-14
**Amends.** ADR 0095 (an escalated challenge is briefed, never decided). The brief, its
advisory status and its exclusion from every rendered report are untouched; what changes is
that a challenge may no longer reach the report **unresolved**.
**Extends.** ADR 0102 (a thesis is premises, and a premise is the judgement) — the adversary
now attacks a stated position rather than a document.
**Depends on.** ADR 0117 (the report states a view in two halves). There is nothing to argue
against until the report states something.
**Required by.** `docs/V1.0_Alpha/04-feature-specifications.md` F2, and the readiness audit's
judge reads of the AstraZeneca run.

## Context

The red team is briefed to find what is wrong with the draft. In practice it spends most of
its budget re-checking sources and arithmetic — work that deterministic code already does,
for nothing, and better. Citation verification re-reads the artefact by hash. Numerical
consistency, figure plausibility and cited-figure agreement are computed from the calculation
record. Paying a frontier model to audit arithmetic spends tokens on the one job it is
worst at.

The audit measured what that costs. The AstraZeneca run published **eight challenges that
were arithmetically false**: the adversary had been handed FY2021 figures carrying no period
labels and accused the draft of contradicting itself on every headline number. The report
shipped the accusations. All three judges reconstructed the truth from the footnotes,
established that the draft was right and the challenge was wrong, and **marked the document
down for it** — twice over, once for the error and once for having published it.

And every challenge in the appendix reads *"Escalated for human decision"*, whatever became
of it, because the appendix is assembled at `validate` and never rewritten after the operator
settles. A reader cannot tell an accepted challenge from a rejected one from an open one.

Two defects, one shape: **the adversary is pointed at the wrong target, and the record of what
happened to its output is written before the thing that happens.**

## Decision

### 1. The adversary is told the arithmetic is already checked, and given a side to argue

The brief states plainly: *the figures are verified by code; auditing arithmetic is not your
job.* It is handed the draft's **conclusion** and the full evidence base, and asked to build
the strongest opposing investment case.

- A long thesis → argue the short.
- A short thesis → argue the long.
- No thesis → argue that the evidence does not support one.

This is squarely the model's half of the one rule. Adversarial challenge is named in it
explicitly, alongside planning, interpretation, comparison and writing. What the model was
doing before — recomputing a margin to see whether the draft got it right — is the other
half, and it was losing.

### 2. Its output is a case with a number attached

Not a list of objections. Per challenge: the claim, the evidence for it by identifier, and
**what it would do to the valuation if true** — which is a deterministic recomputation the
platform performs from the adversary's stated input change, not a figure the adversary
writes. The model says *"terminal growth is unsupportable above 1%"*; code says what the
value per share becomes at 1%.

That keeps the arithmetic where it belongs and makes a challenge comparable: two objections
are two arguments, but £180 and £12 are two numbers.

### 3. A challenge that the record refutes is dropped before drafting, by code

*As first written:* before any challenge reaches the writer, every figure it names is checked
against the calculation record and the fact store — the same readings `cited_figure_agreement`
uses. A challenge asserting a value the record contradicts is **dropped, with its reason
recorded**, and never drafted. *"This is the direct fix for the eight false AstraZeneca
challenges."*

**Corrected, 17 September 2026, and the correction is a measurement.** Every numeral in the
AstraZeneca challenges was run through `reads_as` against that run's own eight hundred
recorded calculations. **Thirty-three of thirty-three are real recorded values.** The record
refuted nothing, so this rule would have dropped none of the eight, and the sentence claiming
otherwise was written from the ADR's own description of the failure rather than from the
rows.

What the challenges actually did is visible once the figures are resolved:

| Challenge | Quotes | Which is | Beside | Which is |
|---|---|---|---|---|
| profitability | 81.9 | gross margin, FY2025 | 0.668 | gross margin, FY2021 |
| balance sheet | 8.11 | interest cover, FY2025 | 0.812 | interest cover, FY2021 |
| valuation | 15.6 | return on invested capital, FY2025 | 0.0823 | return on invested capital, FY2022 |

**Two periods of one figure, called a contradiction.** That is precisely the comparison
`aer.services.consistency` has forbidden the platform's own document since gap C6, and which
ADR 0125 has now extended to calculations: same name, *different* period, not a disagreement.
The adversary was doing by hand what the platform forbids in code, because the evidence pack
handed it one name carrying several periods and no labels.

**That cause is already closed, structurally, and not by this rule.** Phase 3.1 made the
evidence index one row per name at its newest period with the period stated
(`aer.services.calculations.indexed_calculations`), and `_unresolvable_evidence` already
refuses a challenge citing any id outside that index. The adversary can no longer be handed
the FY2021 row, so it can no longer quote it.

**So the drop rule is withdrawn as specified**, for a second reason beyond being aimed at the
wrong target: it is not safely implementable. A legitimate challenge may quote only the
*draft's* figures — "the draft claims 81.9% and no recorded calculation supports it" — and a
rule dropping any challenge whose numerals miss its cited evidence would delete that argument.
Deleting a true objection is a worse failure than publishing a false one, because nobody ever
sees what was deleted.

**What replaces it, when it is built**, is the rule the measurement points at: a challenge
that quotes two values of *one* calculation name at *two* periods and asserts the record
contradicts the draft is comparing periods, not finding an error. That is deterministic, it is
the platform's own existing rule turned on the adversary, and it would have refused all three
challenges above. It is specified here and deliberately **not built in this change**, because
ADR 0125's lesson is that a prose rule is measured against real prose before it is allowed to
delete anything — and the corpus to measure it against is the same seven records this
correction came from.

### 4. Every challenge is resolved before the document is frozen

A challenge reaches a published report in exactly one of three states, and the appendix is
assembled **after** the settle rather than at `validate`:

- **Accepted** — the draft changed, and the appendix says what changed.
- **Rejected** — with the operator's recorded reason. `settle_by_hand` already refuses an
  empty rationale, and that rule is untouched.
- **Carried** — an open question the operator explicitly acknowledged, printed as an open
  question rather than as an unresolved accusation.

There is no fourth state. "Escalated for human decision" is not a resolution; it is a note
that the document was frozen in the middle of a conversation.

### 5. The published section is a bear case or a bull case, named as such, in the body

Not an appendix of objections. The strongest opposing case, written as prose, in the report,
under a heading that says which side it argues. The judges' checklist asks whether a bear
case is present in the text; today the answer is that the appendix contains some.

### What ADR 0095 keeps

Everything structural. `challenge_brief` still writes one brief per unsettled challenge, once,
over a frozen subject; the brief is still stored on the step and never on
`disagreements.detail`, so it stays outside the approval hash; it is still never evidence, no
claim may name it, and **it still reaches no rendered report**. The lean is still a lean
beside two controls, and the operator's reason is still theirs.

What ADR 0095 did not decide — and could not, because the thesis did not exist — is what
happens to a challenge nobody settles. This ADR decides it: the document does not freeze
until somebody has.

## What is given up, named rather than discovered

**A second pair of eyes on the arithmetic.** The adversary occasionally caught a real
inconsistency that the deterministic checks had not framed as a contradiction. That is a
genuine loss, and it is accepted on evidence: across the audit's runs, the adversary's
arithmetic findings were wrong far more often than they were right, and a false accusation in
a published document costs more than a missed one — the judges' reads say so directly.

**The operator cannot approve and walk away from an unsettled challenge.** The final gate now
has one more thing that must be true before a report exists. That is deliberate: the
alternative is a report whose appendix argues with itself, which is what exists today.

**A run can stop on a challenge nobody wants to settle.** The *carried* state is the escape
hatch, and it requires an explicit acknowledgement rather than a default, because a default
would restore exactly the behaviour this removes.

## Consequences

**The red team's budget buys argument instead of re-checking.** Same spend, different work.

**The disagreements renderer and the appendix assembly move** from `validate` to after the
settle. The final gate's payload hash is computed over the settled state, which is the state
the operator is approving — today it is computed over a state that is about to change.

**Landed 17 September 2026, and it needed no new machinery.** The settle route already
refused a settle after a decision and already re-sealed the gate afterwards (ADR 0123); what
it did not do was refill the section in between, so the seal moved to cover an appendix that
still said the conflict was open. One call, in that gap, in that order. The sentences
changed with it: a settled conflict names the side that stands and prints the operator's own
reason without the address recorded beside it, a rule-settled one speaks its rung instead of
printing `later_filing_wins` and *"position A selected"*, and one nobody has settled says it
is open rather than that it is escalated. The summary above them counted everything as a
"disagreement between sources", which was wrong about a red-team challenge and wrong about a
self-contradiction; it now counts the three kinds separately and says how many are open.

**The acceptance test is a seeded false challenge**: a figure the calculation record refutes,
injected, and asserted to be dropped before drafting. Plus the judges' own read — three
judges must name the bear case rather than naming the appendix as a defect.

## Alternatives considered

**Keep the adversary auditing arithmetic and fix the period labels.** The immediate cause of
the eight false challenges was unlabelled FY2021 figures, and labelling them is a small fix.
It leaves a frontier model doing deterministic work, and it leaves the platform one prompt
regression away from the same failure.

**Have the adversary argue the other side *and* audit the arithmetic.** Two jobs, one budget,
and the audit job is the one that produces publishable errors. Pick one.

**Drop the adversary entirely and rely on the deterministic checks.** The checks verify that
the document is internally consistent and correctly sourced. They cannot tell you the thesis
is wrong. The whole argument for this platform over a chat is that somebody is made to argue
the other side; removing the adversary removes it.

**Let the model resolve its own challenges.** It would be fast, and it would be a model
grading its own argument at the one surface where the operator's judgement is the product.
ADR 0095 settled this and it stays settled: briefed, never decided.
