# ADR 0115 — The adversary argues the other side, and every challenge is resolved before the document freezes

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
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

Before any challenge reaches the writer, every figure it names is checked against the
calculation record and the fact store — the same readings `cited_figure_agreement` uses. A
challenge asserting a value the record contradicts is **dropped, with its reason recorded**,
and never drafted.

This is the direct fix for the eight false AstraZeneca challenges, and it is deterministic
rather than a better prompt, because a better prompt is a hope and this is a test.

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
