# ADR 0135 — The report argues both sides and takes neither

- **Status:** Accepted (25 September 2026)
- **Date:** 2026-09-25
- **Decided by:** the operator, 25 September 2026. Asked where a stated view should be entered
  for the adversary to argue against: *"the report should not be taking sides, it should be
  objective, similarly to the red team. The user has to make up their own view in the later
  thesis/decisioning steps."* Then, of the shapes that leaves: both cases in the report, the
  case for and the case against side by side, each point carrying a figure code struck, and
  the report taking neither side.
- **Implements:** `docs/V1.0_Alpha/04-feature-specifications.md` F2, which the operator
  brought back into V1.0 the same day, in the shape above.
- **Amends:** ADR 0115 decisions 1, 2 and 5; ADR 0117, whose authored half is withdrawn;
  ADR 0132's masthead line, which waited for a view the operator will now never state in a
  report.
- **Applies:** invariant 3, unchanged and strictly: every figure the two cases print is a
  recorded calculation, and the input each one moves is itself a recorded calculation or a
  confirmed assumption. Nothing the model writes is a number. ADR 0130 §3 (a recompute moves
  one input, in every forecast year it is confirmed for); ADR 0133 (the strike is the value
  step's own assembly).

## Context

The verdict round's judges gave one reason for every comparison: the platform's stated view
is broken and contradicts itself. The MSFT report shows the machinery that produced it. Its
masthead printed a *"Non-binding view"*. Its Executive Summary carried a *Thesis* field the
contract describes as *"the central view, in two or three sentences"*. Its Investment Thesis
section carried a *Thesis Statement* (*"the central view and why it is held"*), *Supporting
Pillars* and *What Would Change The View*. The platform said it stated no view, and asked the
model for one twice.

ADR 0115 armed an adversary against a view the operator would author at the final gate
(ADR 0117's authored half), so F2 waited on that. The operator has now answered: the report
states no view at all. A view is a thesis (ADR 0102) and a decision (F10), written by the
operator after reading the report. The report's job is to set out, as strongly as the
evidence allows, what each side would have to believe.

## Decision

### 1. The report takes no side

**No section asks for a view.**
- The executive summary's *Thesis* field becomes *Summary*: what the evidence shows, and no
  view on the shares.
- The investment thesis section becomes **The Case For and the Case Against** (§2), at the
  same position. It keeps its key, because a key is an identity rather than a label: every
  report drafted before this pins the version that wrote it, and a refresh compares sections
  by key.
- Both are new versions of their definitions (migration 0090). A report already rendered
  re-renders exactly as it was.

**ADR 0117's authored half is withdrawn.** The operator's view lives in the thesis and the
decision, never in the report. The composed half stands: *"What the valuation gives"* is
arithmetic, and states no side. `reports.rating` stays unwritten, now by decision rather than
pending one.

**The masthead says so:** *"Non-binding view: none — this report takes no side"*, where
ADR 0132 left *"none stated"* waiting for the operator.

### 2. Both cases, argued from the evidence, side by side

The section holds three things:
- one or two sentences naming the question the two cases answer differently;
- the case for owning the shares;
- the case against.

Each case has two to four points, and the two cases are within one point of each other in
number: a case with four points against one with two has taken a side by weight. Each point
has a lead-in, the argument, and the evidence it rests on. It is checked like every other
sentence in the report.

**It is written in the draft by the report's writer, under an advocate's brief for each
side, not by the red team beside the draft.**
- Written in the draft, every check the report passes applies to it: citations re-read by
  hash, cited figures against their calculations, the numeral rule, the denial and
  consistency passes.
- The red team then challenges the two cases like every other section.
- Written beside the draft, as F2 first specified, the cases would have escaped the checks,
  and the one role that challenges claims could not challenge its own.

### 3. A point's figure is struck by code, from a lever the record supplies

A point may name one **lever** from a list code builds for the run. Each lever moves one input
of the report's discounted cash flow to a value that is already on the record:

| Input | Levers |
|---|---|
| The six drivers the proposals derive: revenue growth, operating margin, capital expenditure, depreciation and net working capital as shares of revenue, and the effective tax rate | its lowest, highest and latest observation in the filings, as the same ratio the confirmed assumption averages |
| Perpetual growth | the rate the exit multiple implies; the risk-free rate |
| The exit multiple | the multiple the perpetuity growth implies |

- **What the model writes.** The lever's key, and nothing more. It writes no figure.
- **What code does.**
  - It strikes the base case with that one input moved, in every forecast year for a driver.
    It uses the value step's own assembly, as the calculator does.
  - It records the lever's own value as a calculation over the filed lines it came from.
  - It prints three rows: the lever's value, and the value per share by each terminal
    method. Each row is footnoted to its calculation.
- **What the list leaves out.**
  - A lever equal to the confirmed value, which would move nothing.
  - A lever outside the gate's plausible range.
  - Perpetual growth at or above the discount rate.

**Why a list rather than a stated value.** ADR 0115 decision 2 had the model state the input
(*"terminal growth is unsupportable above 1%"*) for code to strike. That input would have been
the only number in a report that nobody filed, computed or confirmed, and the numeral rule
refuses it. The list gives each argument a number already on the record. The argument decides
which number and says why; the record says what it is.

**A lever's valuation is a perturbation of the base case, never a state of it.**
- It is recorded under the case `argued`, and `argued` joins `sensitivity` wherever a reader
  wants the base case or a scenario: the index, the composed block, the valuation page, the
  refresh's diff and the consistency pass.
- The rows beneath its answer are set aside by lineage (ROADMAP §3.19 item 77).

**When the strikes run.** Levers are struck once every section is drafted, and again when a
revision or a redraft rewrites the section. No section's evidence moves while the draft is
being written.

**When there is no list.** A run valued by a bank's residual income, or one that reached no
valuation, has no list. The writer is told why, and the cases carry no figures.

### 4. The adversary challenges, and takes no side either

The red team's brief is changed:
- The draft states no view: it argues both sides.
- The figures are checked by code, and auditing arithmetic is not its job.
- Its job is to find where claims rest on less than they assert, in either case or anywhere
  else.

Its challenges, the revise loop, the briefs (ADR 0095) and the settle are unchanged.

### 5. A refresh carries a section only into the version that wrote it

A refresh carries a section into the version of its definition that wrote it, and into no
other. Before this it carried by key alone. A report drafted under the thesis contract,
carried into a cases contract, would have rendered as a heading with nothing under it. Now a
section whose definition has moved on is drafted afresh.

A carried cases section keeps its argument, and its levers are struck again on the refresh's
own base case. The argument is prose and is carried; the figures are records and are struck
again.

## What ADR 0115 keeps, and what it loses

- **Decision 1** keeps its first half: the arithmetic is code's. The side to argue is
  withdrawn. Both sides are argued, in the body, by the writer.
- **Decision 2** stands in substance, with the list in place of a stated input.
- **Decision 3** is as corrected on 17 September. Its replacement rule is still unbuilt.
- **Decision 4** keeps the half that landed: the appendix is written after the settle and says
  what happened. Its other half is still unbuilt: approval refused while a challenge is open,
  and a *carried* state. That half changes every approval, the driver's and the fake scene's
  included. The re-measurement will say whether open challenges still read as a defect once
  the report stops attacking its own thesis, and it is built or withdrawn on that.
- **Decision 5** is amended: both cases, named, in the body.

## What is given up, named rather than discovered

- **A lever beyond the record.** A case that margins fall below anything the filings show is
  argued in words. Its figure is the record's floor, and the label says so.
- **A bank's figures.** The residual income model's levers are not in the list, so M&T's
  cases are words only.
- **A conclusion.** A reader who wants a one-word answer from the report gets two cases and a
  question. The answer is the operator's, in a thesis.
- **Rows.** Each lever adds one valuation to the ledger: up to eight a run, each with its full
  lineage, as each grid cell already does.

## Alternatives considered

- **The red team writes both cases after the draft.** This was F2's first shape. It was
  rejected for the reasons in §2.
- **Two advocates, one per side.** That is twice the evidence pack for the same argument. The
  balance it would buy is enforced structurally here, by the point counts.
- **A value the model states** (ADR 0115 decision 2 as written). Rejected for invariant 3, as
  above.
- **The operator's authored view** (ADR 0117). Withdrawn by the operator.

## Acceptance

- **The report itself.** A valued run's report carries the case for and the case against,
  within one point of each other in number. Each lever named is struck, printed beside its
  case and footnoted. No section's contract asks for a view, and the masthead says the report
  takes no side.
- **The ledger.** Every argued valuation carries the case `argued`, and no reader of the base
  case or a scenario reads one.
- **The model's reply.** It carries no figure for a lever, and a lever that is not on the
  run's list is refused like any other refusal, with the list in the retry.
- **The re-measurement.** Its three fresh runs are the first real reports drafted this way.

## Amended 2026-09-25 — a lever says which way it moves the value

The re-measurement's first run, MSFT, priced five levers. Four moved the value the way their
case argued. The fifth did not. The case against named *depreciation at its heaviest share of
revenue*, arguing that the build's depreciation would compress the margin. The strike raised
the value by both methods, to $253.99 and $439.64 against the base case's $222.34 and
$411.23. That is right for the model the report prints: it holds the operating margin as its
own driver and adds depreciation back, so more depreciation is more cash flow and a higher
EBITDA to capitalise. The writer could not see that. Nothing told it which way a lever moves a
valuation, and it guessed from the words. Printed beside the case against, the platform's own
figures argued the case for.

The model still does no arithmetic. **Code strikes every lever on the list once when the
list is built**, on a ledger it throws away, with the same arithmetic the priced table
records. It then tells the writer in words whether the lever raises the value per share,
lowers it, or moves the two methods apart. A method the lever leaves where it was counts for
neither side: a terminal lever moves one method only. The check then refuses:
- a point of the case for that names a lever which lowers the value;
- a point of the case against that names one which raises it.

Each refusal says why, and the writer's retry carries the list. A lever that moves the two
methods apart argues neither case, so either may name it. A lever the arithmetic refuses is
no longer offered, because it would price nothing beside its point. Pricing never prints a
lever beside the case it argues against. A refresh can carry a drafted argument onto a base
case where the lever moves the other way, and then the point stands in words.

On MSFT's own record the list reads as the report should have been told: depreciation at
its heaviest raises the value, and the operating margin at its lowest lowers it.
