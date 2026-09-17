# ADR 0125 — The document is checked against itself, and a contradiction is not a source conflict

**Status.** Accepted — 17 September 2026, with the change (Phase 3.2). Written the same day,
before the code. The promotion from advisory to blocking is a **separate** decision this
record specifies and does not take; see *Consequences*.
**Date.** 2026-09-17
**Extends.** ADR 0018's gap C6 check, implemented in `aer.services.consistency`, from
published facts to published calculations and to the draft's own sentences.
**Relates to.** ADR 0115 (the adversary argues the other side, and every challenge is
resolved before the document freezes), which owns the *other* half of the same defect —
what happens to a challenge nobody settles. This record owns what the platform finds without
a model call.
**Required by.** `docs/V1.0_Alpha/05-delivery-plan.md` §7 item 3.2, and the readiness audit's
`contradiction-and-self-accusation`.

## Context

The single most-named defect across all nine judge reads was a report that says two
incompatible things about a load-bearing figure. The instances are not subtle:

- msft1's Executive Summary: *"No discounted cash flow, cost of equity, weighted average cost
  of capital, terminal value, value per share or peer multiple sits on this record"* — thirty
  lines below an At-a-glance table printing a WACC of 9.3% and a value per share of $485.29,
  and above a full discounted cash flow section.
- msft1's Key Risks: operating cash flow *"is not among the figures available here"*, while
  six sections cite $182.9bn.
- azn2's Balance Sheet: *"Interest cover is not established by the figures available"*, while
  Earnings Quality, Growth Outlook and Scenarios all quote 8.11×.

Every one of those is a **denial of a figure the same document prints**. Not one of them is a
source disagreeing with another source; the platform had one answer throughout and wrote a
sentence saying it had none.

The reason nothing caught them is structural. The eighteen sections fan out concurrently and
each depends on the evidence pack and on nothing another section produces, so **no step's
subject is the assembled document**. The eleven validator metrics measure within-section and
per-calculation properties. `aer.services.consistency` does read across sections — it was
written after the red team found exactly this class on a live AAPL report — but it reads
**stored facts only**: it compares `financial_facts` rows grouped by concept, period and
dimension, and it has nothing to say about a calculation or about a sentence.

So the rule exists and its reach is too short. Two published values for one calculated figure
is arithmetic, and arithmetic belongs to code. A sentence denying a figure the document prints
is a comparison between a word and a number, and that belongs to code too.

## Decision

**The consistency check reads everything the report publishes, and a contradiction between two
of the run's own outputs is recorded as a defect in the draft rather than as a difference of
opinion between sources.**

### 1. Published calculations are compared, as published facts already are

One name, one case, one period, one value. The pass runs over exactly the calculations the
report publishes — those a numeric claim names and those a section's figure row carries by
`calculation_id`, the same two channels the fact pass reads — never over the whole ledger,
because two rows nobody printed cannot contradict a reader.

Grouping is by `(name, case, period)`. The case is part of what a figure *is*: a bear-case
value per share and a base-case value per share are two answers to two questions. The period
is the half the fact pass already learned — an annual figure beside a quarterly one is a
labelling problem, not a disagreement — and a sensitivity cell is excluded for the reason
`indexed_calculations` excludes it, because a grid point is not an answer.

### 2. A sentence that denies a figure the document prints is a contradiction

A **negative assertion** is a sentence that denies **and** names a figure this document
publishes. The check is the conjunction, in one sentence, and nothing looser: a sentence
about missing evidence is an ordinary and honest thing for a research report to contain, and
a figure name alone is what every sentence in the document is about.

**A denial is a negator followed by a word about the record, in that order.** The first
implementation reused `aer.core.section_output`'s existing gap predicate — the one that asks
whether a sentence's subject is the disclosure rather than the company — and it failed on the
two sentences this whole record is about. Neither *"No … value per share … sits on this
record"* nor *"operating cash flow is not among the figures available here"* matches any of
that module's nineteen phrases, because that vocabulary was built to *count repeated remarks
about missing evidence* for the length rule, not to find a denial of a named figure. Two
questions, and the convenient predicate answers the other one.

So this has a rule of its own, and the rule is an order rather than a phrase list or a
proximity window. A window is what the obvious implementation reaches for and it cannot work
here: msft1's sentence puts nineteen words of enumeration between its "No" and its "sits", so
any window narrow enough to be safe misses the instance the check exists for. The two lists —
negators, and words about the record — are both short, both stated, and both grow when a live
run shows a phrasing they missed. That is how `section_output`'s own phrases were built, and
it is the honest way to build a list of how people write: this one already carries "sits on
this record", which no amount of thinking produced and one report did.

The sentence *splitter* is still shared, lifted out of `gap_sentences` into
`prose_sentences`. Two splitters would eventually disagree about where "U.S." ends, and that
disagreement already cost a live run half a sentence (gap A65).

Three bounds keep it from firing on prose that is right, and each is a deliberate choice to
**under-report rather than over-report**, because this check is specified to block later:

- **Two words minimum.** A figure is matched by a phrase of at least two words. `revenue`,
  `assets` and `beta` are therefore never matched: a single common noun inside a denial is
  ambiguous in a way that "interest cover", "value per share" and "operating cash flow" are
  not. Every instance the audit found is multi-word.
- **A named period binds.** If the sentence names a period, the denial counts only where the
  document publishes that figure *for that period*. "Free cash flow for FY2021 is not
  disclosed" is not contradicted by an FY2025 free cash flow. A sentence naming no period is
  an unqualified denial and counts against every period.
- **A name with no readable phrase never fires.** The phrase for a figure is its own name with
  the underscores taken out, plus the handful of aliases a reader actually writes
  (`wacc` → "weighted average cost of capital"; `fcf` → "free cash flow"). A name whose
  transform reads as nonsense — `pp_and_e_net` — matches no sentence and is silently absent,
  which is the safe direction for a rule that will one day stop a run.

A section denying a figure **it prints itself** counts, and is named as such. msft1's
Executive Summary is that case, and it is the worse one.

### 3. A contradiction is its own kind, and the ladder is not run

`DisagreementKind.SELF_CONTRADICTION`, with `ResolutionRule.DOCUMENT_CONTRADICTS_ITSELF`, and
the outcome is always **escalated**.

The disagreement ladder in `aer.core.disagreement` decides between two *sources* by tier,
basis and filing date. None of those three exists here: both sides are this run's own output,
produced by the same code from the same evidence on the same day. Running the ladder would
reach rung 6 — same tier, same basis, same date — and escalate, which is the right outcome
reached by an argument that is false at every step, and it would write a rationale saying
*"both are T1_REGULATORY, both as-reported, both filed 2026-09-17"* about two numbers no
regulator ever saw. A rationale is the only part of a disagreement row a reader of the report
actually meets. It may not lie to make a code path shorter.

So this takes the shape `thesis_conflict` already established: a constructor of its own, in
the same pure module, that escalates by construction and states the platform's actual reason.

### 4. A computed position says that it is computed

`Position` gains `computed: bool`. A calculation has no publisher, and
`aer.core.disagreement.position_figure` — which exists precisely because a placeholder was
being rendered as though it informed the reader (gap A68) — would otherwise print
`9.3 percent (T1_REGULATORY)` beside a discount rate this platform worked out itself.
Attributing the platform's own arithmetic to a regulator on the operator's approval page is
the same defect as the thesis placeholder, one field further along. A computed position reads
as its figure and *"this run's own arithmetic"*, and no tier.

### 5. It reports at gate 2. It does not refuse

A recorded contradiction reaches the operator exactly as a red-team challenge does: an
escalated `disagreements` row on the review page and in the report's own appendix. The run
still stops at the final gate, because the final gate always needs a person; what changes is
what the operator is shown before they approve.

**No new escalation trigger, and the credible-source trigger explicitly excludes this kind.**
§2.4's table has eight rows and this is not one of them. `TriggerKind.THESIS_DISAGREEMENT` was
appended to that vocabulary once and removed on 25 August 2026, with the reason recorded in
the enum: a banner that says "three faults" over two faults and one component working
correctly teaches an operator to read the red one as noise, which is the only way a trigger
can fail. The same argument applies here and reaches the same answer. And the trigger's own
sentence — *"Credible sources disagree materially about a figure"* — is false of a
self-contradiction, in which no source disagrees with anything.

**Never as an evaluation metric.** `_validate`'s docstring is *"scores, never a pause"*, and a
run reached AWAITING_APPROVAL in the audit with `presentation_integrity` failing. An eval row
cannot stop anything, so routing a check that is meant to stop something through
`aer.eval.metrics` would be building the refusal in the one place that cannot refuse.

## What is given up, named rather than discovered

**Every denial the two-word rule cannot see.** A sentence denying revenue, assets or beta by
name goes unrecorded. That is a real gap and it is chosen: the alternative is a check that
fires on "no evidence of revenue growth in the fourth quarter" and is switched off within a
week.

**A figure's period is only as good as the sentence's.** The period bound reads `FY2021`,
`Q3 FY2025` and a bare four-digit year. A denial that says "in the prior year" is read as
unqualified and will be recorded against every period, which is a false positive this record
declines to hide. It is the one bound that errs towards reporting, and it is why the check
reports before it blocks.

**The operator gets more to read at gate 2 before they get less.** Until the redraft half of
ADR 0115 lands, a contradiction is surfaced rather than fixed: the platform will say the
document argues with itself and then ask a person to decide what to do about it. That is
strictly better than shipping the contradiction silently, and strictly worse than not having
it. It is the middle state the delivery plan asks for on purpose.

**Every contradiction it records will print "Escalated for human decision at approval" in the
appendix**, because that string is written at `validate` for every escalated row and is not
rewritten after the operator settles. That is ADR 0115's defect, not this one's, and item 3.3
fixes it. Named here so that the first run carrying these rows does not look like a new
failure.

## Consequences

**The promotion to blocking is a separate decision, and its route is fixed now.** When it
comes, it goes through the final gate's own evidence rule — beside
`_refuse_unsupported_evidence`, raising `StepPaused` with its own pause reason — and through
nothing else. The condition is **two consecutive clean live runs**: a new blocking check that
fires on every run would strand the programme's own re-runs, which is the failure mode the
re-measure and supersede controls of ADR 0123 exist to prevent and should not have to.

**When it blocks, it gets ADR 0018's one-instance override with a written reason**, like every
other blocking check. A contradiction a person has looked at and judged harmless must be
passable, or the first false positive makes the platform unusable and the override gets built
in a hurry.

**The acceptance evidence is a count, not an opinion.** The check was run offline against the
committed run records before it could refuse anything, and the number of contradictions it
would have raised on each is written down in
[`../plan/readiness-audit-2026-09/cross-section-check-dry-run.md`](../plan/readiness-audit-2026-09/cross-section-check-dry-run.md).
A check whose first live firing is also its first measurement is a check nobody can size.

**Measured, 17 September 2026: one contradiction across seven run records, and it is real.**
AstraZeneca #2's balance sheet says *"Interest cover is not established by the figures
available"* in a report that computes interest cover, footnotes it, and quotes 8.11× in three
other sections. The calculation pass records nothing on any of the seven, because not one of
the clashing names in any ledger is cited in any report: they are forecast and grid series,
and a run publishes the answer rather than the ladder.

The exercise paid for itself three times over before the code shipped, and each finding is in
the readout with its regression test:

- **`Calculation.name` is the function's name, not the figure's.** `days_outstanding` serves
  days sales, days inventory and days payable, reading 115.2, 90.6 and 3.9 days for one period
  on the MSFT #2 record. A key of name and period would have called a working ratio suite a
  contradiction three times a year on every run.
- **A discount factor takes its year as a parameter, not an input**, so ten forecast years are
  ten rows with one name, one absent period and identical inputs.
- **The first denial scan produced fifty candidates on one report** where the finished one
  produces two. The three narrowings — a denial belongs to its clause, "present" is not a word
  about the record, "without" is not a negator — each came from reading real prose.

Both key defects are fixed by keying the calculation pass on **the question a calculation
answers**: its identity in `aer.calc.engine` minus its output. Reusing the ledger's own
definition of when two calculations are the same figure is what stops this check and the
ledger disagreeing later.

**One migration**, adding `self_contradiction` to `disagreement_kind` and
`document_contradicts_itself` to `resolution_rule`. No table changes: the row shape already
holds two positions, a rationale and an escalation.

## Alternatives considered

**Add `figure_agreement_across_sections` as an evaluation metric.** The delivery plan's own
first option, and it is wrong for one reason that is dispositive: an eval row is a score and
never a pause. It would measure the defect accurately and stop nothing, which is the state
`presentation_integrity` was already in.

**Run the ladder on calculations and accept the rationale.** Shorter by about forty lines. It
puts a sentence in the report's appendix asserting that two of this platform's own numbers
were filed by a regulator on the same day. The appendix is the part of a research report that
makes the rest of it trustworthy; it is the last place to save forty lines.

**Compare every calculation, not only the published ones.** A run records six to eight hundred
calculations and re-strikes many of them legitimately. The report's claim is about what it
publishes, and a contradiction the reader cannot see is not one. The fact pass settled this
question already and this follows it.

**Have a model read the assembled draft for contradictions.** It is what happens today — the
red team finds them, hours and pounds after the rows that disagree were sitting in one
database — and the audit measured the result: of the adversary's arithmetic findings, the
false ones outnumbered the true. Same concept, same period, two values is arithmetic. Never
move a calculation into a prompt.

**Ship it blocking straight away.** The delivery plan's standing rule is advisory before
blocking, and the specific risk is named in the plan: a deterministic figure index over a
14,000-word document with 831 calculations will find genuine near-misses, and gate 2 could
become unreachable. Two clean live runs are cheap insurance against a check that is right in
principle and unusable in practice.
