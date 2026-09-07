# The seven gates

*Where a person decides, and where the money is committed. The most important design problem
in this brief.*

---

## At a glance

| | |
|---|---|
| **URLs** | `/runs/{id}/plan` · `/financials` · `/sector` · `/peers` · `/themes` · `/assumptions` · `/review` |
| **Who arrives** | The operator, summoned — from the work list, or from the console's banner |
| **What they came for** | To make one decision, correctly, without reading everything |
| **Templates** | `plans/review.html` (85 raw ramps) · `runs/financials.html` (60) · `sector.html` (33) · `peers.html` (44) · `themes.html` (28) · `assumptions.html` (100) · `review.html` (**226 — the worst page in the product**) |

---

## The job

**Let a person make a consequential decision they are qualified to make, from a page that
gives them what they need and does not bury it in what they do not.**

Every gate is the same underlying transaction: *here is what I propose; approving costs money
and commits you; rejecting stops the run.* What differs is what is being proposed, and how
much of it a reasonable person needs to read.

---

## The shared shape

**Every gate is a URL, not a wizard step.** A gate is a page you can leave, bookmark, come
back to, and open on a second screen. Nothing is held in browser memory. This is deliberate
and it is not negotiable — see [`../01-constraints.md`](../01-constraints.md).

**Every gate ends in the same form:**

| Element | Notes |
|---|---|
| **Notes** | Optional, up to 4,000 characters. *"Why you approved or rejected this. Recorded in the audit trail."* |
| **Approve and continue** | Primary |
| **Reject and stop** | Secondary |
| A hidden **payload hash** | The hash of exactly the structure rendered on the page |
| A line of explanation | *"Approving records a hash of exactly what is shown on this page, so an approval cannot be transferred to a different plan."* |

**The payload hash is the mechanism the whole product rests on, and it deserves better
design than a hidden input and a footnote.** If the thing being approved changed between the
page being served and the button being pressed, the workflow refuses to continue — an approval
of something else is not an approval of *this*. It is why optimistic UI is forbidden here, and
it is a genuinely reassuring guarantee that the interface currently apologises for in 11-pixel
grey text.

**A decided gate shows no form.** Instead: *"This gate was already approved. A decision is not
a state to be re-asserted; changing it needs a new run."*

**The decision is recorded and the run is queued, never executed inline.** A gate approval
that ran the remaining steps inside the request would hold the browser open for the length of
a research run and abandon it if the tab closed. So: POST, redirect to the console, watch.

---

## Gate 1 — the plan · `/runs/{id}/plan` · **always fires**

**The most consequential approval in the product.** About £0.15 has been spent proposing what
the run intends to do. Everything expensive is downstream — the drafting step alone is
typically the largest single cost in a run.

Seven blocks:

| Block | Contents |
|---|---|
| What it intends to do | The plan in prose |
| Sections it will write | The chosen sections |
| The report's built-in sections | The ones that always appear |
| **Sources it intends to use** | The list |
| What it says it may get wrong | The risks the planner can already see |
| Your skills on this run | Which user-authored skills apply, at which version, and — for a methodology, house view or preference — which roles it composes into (ADR 0108) |
| Cost and time | The estimate |

**The advice worth designing around:** *"Read the source list: if it does not name the filings
you would have reached for, that is the cheapest moment to find out."* That single sentence is
what this gate is for, and the source list is currently the fourth of seven blocks.

---

## Conditional gate — confirm the extracted financials · `/runs/{id}/financials`

**The question is: does this gap matter?** Some tags in the filing could not be mapped to a
canonical concept. Should the run proceed without them?

Three tables and one list:

- **Tags with no canonical concept.** Each carries its label, **the largest figure it held in
  this filing**, the period that figure belongs to, and what it is **as a share of the biggest
  mapped line**. Sorted biggest share first, so the row that decides the gate is the first on
  the screen.
- **Tags this platform refuses to map**, each with the reason (roadmap §2.7) — *not* a
  question, and said so on the page. A refusal is a decision already taken; shown in the
  same list as the gaps it reads as work outstanding, which is how a considered refusal
  gets approved away and how the mapping it was refused for arrives later in good faith.
  A filing whose only unmapped tags are refused ones does not stop the run at all.
- **What did map** — closed by default. Because the question is a comparison, and an operator
  asked it over element names alone was being asked to hold the statements in their head.
- **What the extractor complained about.**

*The largest figure, not the latest, and the reasoning is worth keeping:* a tag's most recent
observation can be a quarter, a restatement or a zero, and what is being decided is whether
anything material hangs on the element at all.

Both tables filter as you type — **from markup that is hidden until a script reveals it**, so
scripting off gets a complete table rather than a dead search box.

*This page was rebuilt in 2026-08 from a bare list of taxonomy element names, which could not
answer its own question. It is the best-designed gate in the product and a good model for the
others.*

---

## Conditional gate — the sector · `/runs/{id}/sector`

**Unlike every other gate, approving here does not only let the run continue: it grants a
mandate the calculation layer requires.** So the page leads with what confirming *blocks*
rather than with the classification itself.

- The classification
- Why this classification
- **What this sector means for the analysis**
- What remains available

A confirmed bank is valued on residual income over its book value, and the discounted cash
flow is **refused rather than footnoted**. A bank has no classified balance sheet, so current
assets and current liabilities are not thin — they are undefined, and asking for them would
produce a report describing a filer as disclosing poorly for keeping its accounts exactly as
it must.

---

## Conditional gate — the peer set · `/runs/{id}/peers`

**Every peer's rationale is rendered at full length.** A page that truncated them would invite
approving a set nobody read, which is the failure this gate exists to prevent.

- A notice: *these figures do not leave this machine*
- Proposed peers
- On what basis
- **Proposed and not used** — with the reason, grouped by reason

**The refusals are shown beside the set and are deliberately not part of what is hashed.** A
model proposing peers will name companies the registry cannot resolve, and a reviewer judging
the ones that *did* resolve is better off knowing what did not — but what they are approving
is the peer set, so the hash covers that and nothing else.

**Worth knowing:** confirming records the set; it fetches nothing. Computing a peer's multiple
needs its filings and its prices, and this workflow acquires neither. The gate says so. On a
recent run all eight proposed peers were excluded for exactly this reason, and it was not a
fault.

---

## Conditional gate — the themes · `/runs/{id}/themes`

Which stories this company is filed under. Every rationale at full length, for the same reason
as the peers: a theme shapes how every later reader of the library weighs the company.

The smallest gate. One block.

---

## Conditional gate — the assumptions · `/runs/{id}/assumptions`

**The one gate that approves work which has not happened yet.** Every other gate confirms
something the run produced and can be read back; this one confirms the numbers a discounted
cash flow is *about to be built on*.

Four blocks:

| Block | Contents |
|---|---|
| **Proposed assumptions** | Each with its value, its justification, and who proposed it |
| **Still outstanding** | Names nobody could put a number against — shown as outstanding, never quietly defaulted |
| **Refused** | |
| **Not derived** | |

Before the decision form, a warning that counts what is unresolved: *"N inputs still have no
value at all"*, plus unconfirmed ones.

**This gate has inputs of its own** — three forms per row, inside the gate page:

| Action | What it does |
|---|---|
| **Confirm** | Agree the run may rest on this value. Carries a hash of *the list that was displayed* |
| **Amend** | Change the value |
| **Create** | Supply a value for an outstanding input |

**Creating goes through the same proposal path a model uses, and produces a proposal, never a
confirmation** — because typing a value and agreeing the run may rest on it are separate acts.

**The rows are the truth, not the step's frozen output.** An earlier version rendered from the
step's output, so an operator typed the missing cost-of-capital values and watched the page
keep calling them outstanding. A saved value that stays invisible where the decision is made
reads as a save that failed.

*The same surface exists per-request at `/requests/{id}/assumptions`, and the forms are here
too because the operator standing at this gate is exactly the person that surface was built
for.*

---

## Gate 3 — the review · `/runs/{id}/review` · **always fires**

**The largest and most complex page in the product**, and the last decision before a report
exists. Nine blocks:

| Block | Contents |
|---|---|
| A trigger banner | **Only when something is wrong** |
| **Validation results** | Every evaluation metric and its verdict |
| **Source coverage** | Per section. *Not generated* across the row for a section that never ran, rather than zero coverage for an absence |
| **Disagreements** | Source conflicts — two documents saying different numbers |
| **The red team's challenges** | Its own section: dimension and severity, the objection **at reading width**, its basis, its cited evidence, and — while it is unsettled — a brief of what either answer commits the report to (ADR 0095) |
| **Cost** | Against the cap, with an alert threshold |
| **Sections in this draft** | Per section: outcome, evidence tally by kind, attempt count, the refusal in the producer's own words, and the causes counted |
| **Calculations** | Name, formula, period, the value in house style, input count. **Closed by default**, with a filter over name, period and formula |
| **The document** | The draft itself, as the report will carry it |

### Three things about this page that are load-bearing

**Disagreements and challenges are split by what they *are*.** A source conflict is a fault:
two documents say different numbers and somebody has to decide. A red-team challenge is the
adversary doing its job — and seven of them listed under "unresolved" read as seven problems
with the run rather than as the review the run paid for. **A run where the red team found
nothing would be the one worth worrying about.**

**The trigger banner means one thing: something is wrong.** Thesis disagreement was removed
from it entirely for the reason above.

**A disagreement can be settled, on the record.** Choose a side, give a rationale; the choice
is written under the operator's name beside the rule that escalated it, which is *not*
overwritten. The labels follow the kind — "keep the draft's position" and "accept the
challenge" for a red-team row, because asking somebody to choose between A and B on a thesis
is asking an unanswerable question. **A disagreement nobody settles keeps publishing both
sides**, which is the default and the honest outcome for most of them.

**An unsettled challenge carries a brief of the choice** (ADR 0095). The objection says what
is wrong with the draft; the brief says what each answer *commits the report to* — what
keeping the draft's position assumes and means, what accepting the challenge assumes and
means — and leans one way with a sentence of why. It was built because the page asked for a
decision between two paragraphs of argument and gave the reader nothing to compare them by.

It is advice beside the decision and never the decision: it settles nothing, prefills no
rationale, changes no row, is inside no approval hash, and appears in no rendered report.
The controls beneath it are unchanged. A challenge with no brief — a run from before the
step, one whose briefing failed, one past the eight a sitting briefs — renders exactly as
it did before, which is the fallback the feature is designed around rather than a gap.

### Two adjacent surfaces

- **`/runs/{id}/preview`** — the draft as the finished document. No navigation, no scripts,
  the print stylesheet included. Assembled by the same call, with the same inputs, as the
  render step will use — which is what makes looking at it before approving meaningful:
  **what is approved is what exists.**
- **`/runs/{id}/summary`** — the document narrowed to one page. Footnote numbers match the
  full note, so a marker here is an entry point into it.

---

## States

| State | Applies to |
|---|---|
| **Pending** — the form is live | Any gate the run is stopped at |
| **Already decided** — no form, and the explanation | Any gate reached after its decision |
| **Not reached yet** | A gate whose page is opened early |
| **Nothing to approve** | The review gate before drafting: *"This run has drafted nothing yet."* Rows exist from the moment a plan is approved, but content arrives only when the draft step runs — testing for rows rather than content would show an empty document and invite an approval of nothing |
| **Stale payload** | The page was served, something changed, the approval is refused |
| **Scripting off** | Every gate fully usable. Filters vanish; complete tables remain |

---

## What is wrong today

**Seven gates, seven layouts, one decision.** Each page was designed for its own content and
they share almost nothing visually — different heading structures, different placement of the
decision, different treatment of the "why". An operator who has learned one has not learned
the next.

**The decision is at the bottom of a long page, every time.** On the review gate that is after
nine blocks including a full report draft. There is no persistent affordance, no indication
while reading that a decision is pending, and no way to reach it without scrolling past
everything.

**Nothing says where you are in the sequence.** No gate mentions any other gate. An operator
at the peer gate does not know whether two more decisions or five are coming.

**The payload-hash guarantee is buried.** The best thing about this interface — *your approval
is bound to exactly what you read* — is a hidden input and a line of 11-pixel grey text.

**The review gate is unnavigable.** Nine blocks including a complete draft report, no
in-page navigation, no summary of what needs attention. The information is all there and
excellently reasoned; finding the one thing that should change your mind means reading all of
it.

**Cost appears differently on each gate.** Gate 1 has "cost and time" as an estimate; gate 3
has a cost block with an alert threshold; the others have nothing. The one number that is
constant across every gate — *what have I spent, against what ceiling* — is presented three
different ways and often not at all.

**The assumptions gate has three forms per row inside a page that is itself a form.** Confirm,
amend, and the outer approve. Nested decisions with no visual hierarchy saying which is which.

**"Approve and continue" and "Reject and stop" are equally weighted on some gates.** Rejecting
stops a run that has already cost money. It should not look like a symmetric choice.

---

## What to improve

**1. Give every gate the same skeleton.** *What you are deciding · Why it matters · The
evidence · The decision.* The content differs wildly; the shape should not. This is the single
highest-value change in the brief.

**2. Put the decision where it can always be reached** without losing the ability to read the
evidence first. A persistent bar, a two-pane layout, or an anchored summary — but note the
constraint: the buttons must be inside a real `<form>` that submits without JavaScript, and
the payload hash must travel with them.

**3. Show the sequence on every gate.** Which gates have passed, which is this, which are
conditional and may not fire.

**4. Make the hash guarantee a feature.** "You are approving exactly this, and if it changes,
this approval will not transfer." That is a *reassurance*, and it currently reads as a
technicality.

**5. Give the review gate a spine.** An "attention" summary at the top — what failed
validation, what the red team said, which sections did not generate, what it cost — with
everything else reachable from there. **Do not remove anything**; every block earns its place.
Rank them.

**6. Standardise cost across all seven.** Spent, ceiling, and what approving is likely to add.

**7. Weight the two buttons by consequence**, without making rejection hard to find.

**8. Make the red team's section feel like value received.** It is the most interesting output
of the whole run — a second model attacking the first's conclusion — and it currently reads
as a list of complaints.

**9. Adopt the financials gate's pattern more widely.** Sorted by what decides the question,
the comparison available but closed, and filtering that degrades to a complete table. That
page knows what question it is asking. Most of the others present their content and leave the
question implicit.

---

## What must not change

**A gate is a URL.** Not a wizard step, not a modal. It can be left, bookmarked and returned
to.

**The payload hash travels with the decision.** No exceptions. It is why a stale page's
approval fails, and that failure is a feature.

**Nothing is approved optimistically.** The button posts; the server decides; the page that
comes back is the truth.

**A decided gate offers no form.** *"A decision is not a state to be re-asserted."*

**Rationales are rendered at full length** on the peer and theme gates. Truncation invites
approving what nobody read.

**Refusals appear beside what was accepted**, and are not part of the hash.

**Red-team challenges are not listed as faults.** The adversary doing its job is not a
problem with the run.

**A settled disagreement does not overwrite the rule that escalated it**, and an unsettled one
keeps publishing both sides.

**The preview is the document.** Same call, same inputs, same output as the render step.

**Every gate works with scripting off**, filters included — which means the filter control is
revealed by script, never rendered dead.

---

## Done when

- All seven gates share one recognisable shape, and learning one teaches you the rest.
- The decision is always reachable, and always after the evidence rather than before it.
- Every gate says where it sits in the run and what is still to come.
- The hash guarantee reads as a reassurance.
- The review gate can be triaged in thirty seconds and read in full when it needs to be.
- Spend against ceiling appears identically on every gate.
- Rejecting looks like what it is: stopping something that has cost money.
