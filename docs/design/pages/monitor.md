# The Monitor tool

**`/monitor`** — what has happened since a thesis was written that bears on it: the findings
the platform raised, the reviews you promised, and the one gate a contradicted premise opens.

---

## At a glance

| | |
|---|---|
| **URLs** | `/monitor` · `POST /monitor/run` · `/monitor/findings/{finding_id}` · `POST …/decide` · `POST …/resolve` |
| **Who arrives** | The operator, from the work list on the main menu, or on a morning round |
| **From where** | The launcher, the Monitor nav item, an attention row on Overview |
| **What they came for** | *Has anything been filed that bears on what I believe, and what did I do about it?* |
| **Templates** | `monitor/index.html` · `monitor/finding.html` |
| **Token state** | Clean — built on the component set from the first line |

---

## The job

**Put every question the monitor raised in front of the operator, labelled as a question,
and record what they did about each — including nothing, with the reason.**

---

## Three ideas that shape everything on this screen

**1. A finding is not a decision** (ADR 0078). The list is not an inbox of approvals and the
words on it say so. A contradicted premise is *a decision waiting*: it opened a gate, and
nothing about the premise moves until a person chooses. Everything else is *a question
raised*: a finding with no approval semantics, which stays until a person says what they did
about it. Nothing on the page approves anything except the gate, and the gate asks what to do
about the premise, never whether to approve.

**2. Code measured the crossing; the model read the rest** (ADR 0103). Each finding shows two
things apart: *what code measured* — the metric, the value, the threshold, whether the
predicate holds, beside the calculation it came from — and *what the monitor read into it*,
an interpretation that names the documents it rests on and is never evidence. The first is
deterministic and the second is bounded by it.

**3. A finding is closed by an act with a reason, never by the condition going away.**
Dismissing, withdrawing the premise, deciding the gate, reopening — each is an appended row
with a reason and an actor. A finding that was read and left alone is a row worth having;
a queue that empties itself teaches the operator that ignoring it works.

---

## What is on it

### The list (`/monitor`)

**The verdict** leads: *"One premise was contradicted and is waiting for your decision; two
findings are raised and not yet acted on; one premise is due for your review."* Warning tone
when a gate is open, info otherwise. With nothing to monitor it says so and points at Theses.

**Decisions waiting** and **Questions raised** share one shape: a card per thesis, a line
per finding. The card carries the subject as its eyebrow and the thesis title as a link to the
thesis; each line is a bordered card of its own with the premise, the sentence code measured
(where there is one) in the data face, the justification, *Raised {date} · a decision waiting*
or *· a finding, not a decision* with the link to the finding — *Decide what to do* or *Say
what you did* — and a status chip (Contradicted, Unchanged, Weakened, Strengthened,
Unobservable, Stopped at its ceiling). A thesis with three findings is one card with three
lines. *Show resolved findings* switches to `?resolved=1`, where each row carries what was
done, by whom, when and why.

**Reviews due** — held premises with no predicate whose `review_by` has passed, linking into
the thesis at the premise. The monitor does not read these; a person does.

**Run the monitor** — one button, *Run the monitor over N theses*, which queues one pass per
open thesis on the worker; beneath it the recent passes with status, date, findings and cost.
With no open thesis, an empty state pointing at Theses.

### The finding (`/monitor/findings/{finding_id}`)

Header: the thesis title; eyebrow *A DECISION WAITING* or *A FINDING*; identity line with the
company and the date raised; the status chip with its sentence.

**The premise** it is about, linking to the thesis. **What code measured**: two figures side
by side — the metric's value with the period beneath and a link to the calculation it came
from, and the threshold the premise set — the verdict on the predicate as a chip, and the
same thing as one sentence beneath them. **What the monitor read into it**, with the
documents named. Then one of three:

- **The gate** (contradicted, open): a decision panel with the question *What do you do about
  this premise?*, the consequence, a required reason, and two buttons — *Withdraw the premise*
  and *Keep the premise despite this* — over the hash of exactly what the page shows.
- **What did you do about it?** (any other open finding): a required reason and two buttons —
  *Read, and leaving the premise as it is* and *Withdraw the premise*.
- **What was done** (resolved): the history, and a reopen form with a reason.

---

## Inputs

| Control | Validation | Where it lives |
|---|---|---|
| Reason (every form) | not blank | `services.thesis_monitor` — a resolution without a reason is refused |
| Decision | one of two | the service; a third value is a 400 |
| Payload hash | must match what the page shows now | `decide_finding`; a stale page is refused with the reason |
| Action | dismissed, withdrawn, reopened | the service; a contradicted finding refuses all but its gate |

Every refusal is a sentence on the problem page with the status the error carries.

---

## States

| State | What it shows |
|---|---|
| **No thesis to monitor** | The verdict says so; the run sheet points at Theses |
| **Nothing waiting** | Every sheet's empty state; the verdict names how many theses are read |
| **A gate open** | The verdict in the warning tone; the row under *Decisions waiting* |
| **Findings raised** | Rows under *Questions raised*, each with its chip |
| **A stopped pass** | A row with the refusal chip and the cap it hit; the pass is FAILED, never paused |
| **Passes queued** | An info callout naming the count; a warning if the queue could not be reached |
| **A resolved finding** | History, no forms but reopen |
| **Not yours, or no such finding** | 404, the same answer for both |

---

## What is wrong today

**Segment lines are unobservable.** "Azure revenue growth" resolves to nothing because the
analysis reads consolidated facts only. The finding says so; the operator may still want it.

**A finding's sources are titles, not links.** No page shows one source document on its own,
so the documents a justification names are listed by title and go nowhere. The research
tool's sources page is per run, and a monitor pass is not a run.

**The resolved list is flat.** Open findings are grouped by thesis; resolved ones are still
one row each, because a history reads in time order. Whether that is the right call at fifty
resolved findings is untested.

---

## What to improve

**1. The finding row as a card** — done. Premise, measurement, interpretation and status at
four weights, each line a bordered card inside its thesis.

**2. Grouping by thesis** — done. One card per thesis, a line per finding, the thesis named
once and linked to.

**3. The observation as a small figure** — done, with `ui.figure`: the value against the
threshold, the period beneath, the calculation linked, the verdict as a chip.

**4. The metric field's list** — done on the theses page: the premise form offers the names
the monitor resolves.

**5. A page for one source document.** The justification names documents it read; the reader
should be able to open one. The research tool has the artefact and the excerpt machinery;
what is missing is a URL that does not assume a run.

---

## What must not change

* **Every finding is labelled a finding**, and only the gate uses the decision panel. The
  failure this prevents is a queue worked like an inbox of approvals (ADR 0078).
* **Every act carries a reason and appends a row.** Nothing on a finding is flipped.
* **Nothing here reads a price.** The evidence is filings; the observation has no field for a
  mark (ADR 0079).
* **The measurement is code's and the status is bounded by it.** The page shows the two
  apart so a reader can see which is which.

---

## Done when

* A contradicted premise appears on the work list as *waiting for you*, its row leads to the
  gate, and deciding it removes the row and records the reason on the premise.
* A weakened finding appears as a question, is dismissed with a reason, and is visibly kept
  under *resolved* with that reason.
* A stopped pass appears as *needs diagnosis* and names the cap it hit.
* A fresh install says there is nothing to monitor and where to go.
* Two findings on one thesis appear as one card naming the thesis once, with a line, a chip
  and a link for each.
* A contradicted finding's page shows the value, the threshold and *does not hold* before the
  sentence, and the value links to its calculation.
