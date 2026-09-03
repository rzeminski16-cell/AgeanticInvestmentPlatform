# The Post-trade review and Decision analytics tools

**`/review`** — each closed position, read for the quality of the decision behind it;
**`/analytics`** — what the reviewed positions have in common, every statistic with its `n`.

---

## At a glance

| | |
|---|---|
| **URLs** | `/review` · `POST /review/run` · `/review/passes/{pass_id}` · `POST …/confirm` · `/review/{review_id}` · `/analytics` |
| **Who arrives** | The operator, after a holding has returned to nil |
| **From where** | The launcher, the Review nav section, a work-list row (*waiting for you*, *not started*, *needs diagnosis*) |
| **What they came for** | *Was that a good decision, separately from whether it made money — and what do my decisions have in common?* |
| **Templates** | `review/index.html` · `review/proposal.html` · `review/detail.html` · `review/analytics.html` |
| **Token state** | Clean — built on the component set from the first line |

---

## The job

**Get each closed position scored against the process that was supposed to be followed,
by the operator, with the reviewer's proposal beside the outcome code computed — and count
the scores only once there are enough to say anything.**

---

## Four ideas that shape everything on these screens

**1. A closed position is an episode of trades** (ADR 0105 §1). There is no positions
table; a position closes when the walk over a security's trades brings the holding to nil.
A holding still open is not reviewed, and the list does not offer it.

**2. The outcome is code's, and every figure in it is a recorded calculation** (ADR 0105
§2). Cost, proceeds and the realised return are `@traced` functions with every flow
converted at its own trade's date; the page links each to its formula. The reviewer quoted
them and could not restate them.

**3. The reviewer proposes; the operator confirms; the review is the operator's judgement**
(ADR 0105 §3). The draft lands on the pass's job step and is nobody's until a person
confirms it, amending any verdict, the quality, the basis or the lessons. The proposal is
kept beside what was confirmed, so whether the operator agreed with the reviewer is a
question the analytics can answer.

**4. Every statistic carries its `n`, in the type** (ADR 0105 §4). `Statistic` cannot be
built without a count. Below three reviewed positions a breakdown is a tally, not a
percentage.

---

## What is on it

### The list (`/review`)

**The verdict** leads: *"One proposal is waiting for you to confirm; two closed positions
have not been reviewed; one position has been reviewed."* With nothing closed, a sentence
saying a review starts when a holding returns to nil.

**Waiting for you** — proposals not yet confirmed. Per row: ticker and book as the link to
the pass, the dates held and the trade count, and *a proposal, not a judgement*.

**Not yet reviewed** — closed positions with no pass. Per row: ticker and book, dates held,
and **Run the reviewer**, a form naming the book, the listing and the close date. The pass
runs in the web process, like the skill dry run: one call, no tools, over a position that
will not change. With no book at all, an empty state points at the portfolio.

**Stopped at a ceiling** — passes that failed at a spending cap, with the reason and **Run
it again**. Shown only when there is one.

**Reviewed** — the confirmed reviews, newest close first, each the link to its review.

### The proposal (`/review/passes/{pass_id}`)

Header: *"The reviewer's proposal"*; identity line with the ticker, the thesis and the
date read; a breadcrumb to the list. A confirmed pass leads with a success callout linking
to the review; a stopped pass with a refusal callout carrying the reason.

**What happened** — the listing and the dates held, then four figures: realised return as
a signed percentage, cost, proceeds (each linking to its calculation), and the holding
period in days against the intended horizon in months. Where a flow could not be converted,
a warning callout with the problem and no return at all.

**What the reviewer read** — the decisions as written before the outcome (action, statement,
basis, size, horizon, exit plan, how many trades carried each out), each linking to the
journal; and what the monitor found while the position was open.

**Confirm the review** — absent on a stopped or already-confirmed pass. Per premise: the
statement (struck through if withdrawn), a **Verdict** select over the five verdicts and a
**Note**, prefilled from the draft. Then **Process quality** (sound, questionable, flawed),
**On what basis** and **Lessons**, prefilled from the draft. The button reads *Confirm as
my review*, in the decision tone.

### The review (`/review/{review_id}`)

Header: *"Review of {ticker}"*; identity line with the book and dates held. **The verdict**
is the process quality with its meaning, and beside it whether the review was confirmed as
proposed or amended.

**What you concluded** — quality, basis, lessons, holder and date; links to the thesis and
the pass. Where the quality was amended, the two chips sit side by side, labelled *You
confirmed* and *The reviewer proposed*.

**Each premise, as the record found it** — position, statement, note, and the verdict as a
status; where the reviewer proposed a different verdict, the two side by side under the same
two labels, with the reviewer's note beneath its chip.

**What happened** — the same four figures, linked.

**What the reviewer proposed** — the proposed quality, basis and lessons as they arrived.

### The analytics (`/analytics`)

**The verdict** states the sample: nothing reviewed; fewer than three, so every breakdown
is a tally; or the count. With nothing reviewed, an empty state points at the list.

Six sheets, each headed by its label and *n = …*. The first is a two-by-two; the rest are
tables with a **Count** column and, only once the sample can bear one, a **Share** column:

1. **Process against outcome** — the four cells laid out two by two: *Sound process* and
   *Flawed or questionable process* down, *Gain* and *Loss* across, both axes as table
   headers, the count large in each cell with its share beside it once the sample can bear
   one, and a row of its own for *outcome not computed* where a flow could not be converted.
2. **Process quality** — sound, questionable, flawed.
3. **Premise verdicts** — held, partially held, failed, untested, unobservable, over every
   verdict row.
4. **Holding period against the intended horizon** — no horizon stated, closed early, near
   the horizon, held past it.
5. **A decision on record for the position** — yes or no, by whether a review has a thesis.
6. **Confirmed as the reviewer proposed** — agreed, amended, no proposal.

---

## Inputs

| Control | Validation | Where it lives |
|---|---|---|
| Run the reviewer | a book of yours, a closed episode in it | the handler, then `run_review`, which also refuses a reviewed position |
| Verdict, per premise | one of five | the enum; the handler refuses anything else with a 400 |
| Process quality | one of three | the enum, likewise |
| Basis | not blank | `confirm_review` |
| Lessons, notes | free text, may be empty | — |

Every refusal is a sentence on the problem page with the status the error carries. A pass
that hits its cost ceiling is not a refusal of the form: it fails the pass with the reason
and leaves the position unreviewed.

---

## States

| State | What it shows |
|---|---|
| **No book** | The list's verdict says nothing has closed; the empty state points at the portfolio |
| **Nothing closed** | The empty state explains that an open holding is not reviewed |
| **Unreviewed** | A row with *Run the reviewer*; a *not started* row on the work list |
| **Proposed** | A row under *Waiting for you*; a *waiting for you* row on the work list; the pass page carries the form |
| **Stopped** | A row under *Stopped at a ceiling* with the reason and *Run it again*; a *needs diagnosis* row on the work list; the pass page carries a refusal callout and no form |
| **Outcome not computable** | The pass page shows the problem and no return; the analytics count it as *outcome not computed* |
| **Reviewed** | A row under *Reviewed*; the pass page links to the review instead of offering the form; the work list asks nothing |
| **Fewer than three reviewed** | Every analytics sheet is a tally with no share column |
| **Not yours, or no such pass or review** | 404, the same answer for both |

---

## What is wrong today

**The pass blocks the request.** The reviewer runs in the web process and the operator
waits on the response. One call on a closed position is short, but a slow model is a slow
page with no progress shown.

**A review names the outcome once.** The figures on the review are the outcome as recorded
at review time; a trade recorded later against the same episode does not reopen it.

**The basis and lessons are not compared.** An amended quality or verdict is shown side by
side; an amended basis or lessons paragraph is shown as the operator's, with the reviewer's
under *What the reviewer proposed* further down. A field-level was/now for prose, as the
decisions page does for a revision, is the obvious next step.

---

## What to improve

**1. The cells as a two-by-two** — done. Quality down, sign across, the off-diagonal cells
where the eye lands, the remainder in its own row.

**2. The proposal as a diff** — done for the verdicts and the quality: side by side, each
labelled, the reviewer's note beneath its chip. Not yet for the basis and lessons.

**3. A pass progress state.** If the reviewer moves to the worker, the pass page needs the
*running* state the monitor's has.

**4. The basis and lessons as was/now.** See above.

---

## What must not change

* **No figure on these pages is written by the model.** The outcome is computed before the
  reviewer is asked anything and linked to its formula (ADR 0105 §2).
* **A proposal is never a judgement.** The review row exists only once a person confirmed
  it, held by them, on their basis (ADR 0105 §3).
* **The proposal is kept, never overwritten.** Agreement is decision data.
* **Every statistic carries its `n`, and below three it is a tally** (ADR 0105 §4).
* **No recommendation, no size, no methodology change**, on any of the four screens (ADR
  0081).

---

## Done when

* A closed position appears on the list as not reviewed and on the work list as not
  started; running the reviewer from the row lands on a proposal with the realised return
  linked to its calculation.
* Confirming the proposal with an amended quality produces a review that shows the
  operator's quality, says *amended*, and shows what the reviewer proposed.
* The analytics page shows one review as a tally with no share column, and says why.
* A fresh install explains that a review starts when a holding returns to nil.
* One review confirmed as *questionable* over a gain puts a 1 in the flawed-or-questionable
  gain cell and a 0 in the sound-gain cell, with no share column until three are reviewed.
* A verdict amended from *held* to *partially held* shows both chips on the review, labelled.
