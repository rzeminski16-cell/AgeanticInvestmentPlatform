# The Theses tool

**`/theses`** — what you believe about a company, written down as premises, each with the
evidence it rests on and the question that would defeat it.

---

## At a glance

| | |
|---|---|
| **URLs** | `/theses` · `POST /theses` · `/theses/{thesis_id}` · `POST …/premises` · `POST …/premises/{judgement_id}/withdraw` · `POST …/retire` |
| **Who arrives** | The operator, after reading a report and before deciding anything |
| **From where** | The launcher, the Theses nav item |
| **What they came for** | *What do I actually believe here, and what would show me I was wrong?* |
| **Templates** | `theses/index.html` · `theses/detail.html` |
| **Token state** | Clean — built on the component set from the first line |

---

## The job

**Make the operator write down a view as premises a person could later test, and keep every
one of them — held, withdrawn, retired — as a record of what was believed when.**

---

## Three ideas that shape everything on this screen

**1. Nothing here is a figure** (ADR 0074). A thesis is a document a person wrote. The one
number a premise may carry — its threshold — sits beside the metric it tests, in a sentence,
and enters no arithmetic anywhere. There is no conviction score, no confidence and nothing a
calculation could consume, and the schema has no column for one.

**2. A premise says what would defeat it, or who will look again** (ADR 0079). Two answers,
styled the same. *Tested by a threshold* is a metric, a comparator and a number with a unit
that code will test against stored facts. *Reviewed by a person* is a date. The second is not
second-class: the premises that decide whether a position works are disproportionately the
unquantifiable ones, and a page that greyed them out would teach the operator to invent
thresholds so it lights up.

**3. Nothing is deleted.** A withdrawn premise stays, struck through, with the reason beside
it. A retired thesis keeps its premises and its reason, and takes no new ones. "I held this
and gave it up because…" is the row the post-trade reviewer reads (ADR 0081).

---

## What is on it

### The list (`/theses`)

A work queue of documents, not a table of figures. Per row: the **title** as the link, the
**subject** (company name and ticker) as the sentence, and *"n premises held · written {date}"*
as the meta line. Open theses by default; *Show retired theses* switches to `?retired=1`.

### The write form

Three controls: **Title** (required — "what this thesis claims, in a line"), **About** (a
select over the companies the platform can resolve), **Written on** (date, defaults today,
capped today, for backdating a view already held).

**A thesis can only be about a company the research tool has resolved.** A fresh install
offers no companies, and the form is replaced by an empty state pointing at the research
request. A thesis about a ticker nobody has looked up would be a view about a string.

### The detail (`/theses/{thesis_id}`)

Header: the title, *"{subject} · written {date}"*, a breadcrumb to the list.

**The verdict** leads: *"3 premises held; 2 tested by a threshold, 1 reviewed by a person; 1
withdrawn, with the reason kept."* Composed from the rows, in the platform's own voice, and
never a number anything rests on.

**Premises**, as an ordered list. Per premise: the position and statement; the basis in full;
a meta line — *Tested by a threshold: revenue growth at least 25 percent* or *Reviewed by a
person: 31 March 2027* — then *held by {email} on {date}*. A withdrawn premise is struck
through with *Withdrawn on {date}: {reason}* beneath. Each held premise carries a one-line
withdraw form: a reason and a button.

### The add-premise form

**The premise** (textarea) · **On what basis** (textarea) · **Held since** (date) · **What
would defeat it** (radios: *A threshold code can test* / *A person will look again*, each with
its consequence) · the threshold fields (**Metric**, **Comparator** as words, **Threshold**,
**Unit**) · **Review by** (date, from today).

The radio decides which fields count. A review date typed beside a threshold is a premise with
two answers; the one the operator chose is the one recorded.

### The retire form

One reason and a button. Absent on a retired thesis, along with the add form; the retired
notice takes their place.

---

## Inputs

| Control | Validation | Where it lives |
|---|---|---|
| Title | not blank | `services.theses.write_thesis` |
| About | a company id the platform holds | the handler, then the form's select |
| Statement, basis | not blank | the service; the database repeats it as a check |
| Threshold | a number; the unit not blank | `Predicate.__post_init__` — a bare number cannot be compared with a fact |
| Review by | required exactly when there is no predicate | the service, and `premise_without_a_predicate_is_reviewed` |
| Reasons | not blank | the service; both-or-neither checks in the database |

Every refusal is a sentence that names what to do, rendered on the problem page with the
status the error carries.

---

## States

| State | What it shows |
|---|---|
| **No companies** | The write form is replaced by an empty state pointing at `/requests/new` |
| **No theses** | *"Nothing written yet"* above the form |
| **A thesis with no premises** | The verdict says so, and the premises sheet says to add the first |
| **Ordinary** | Verdict, premises, the add form, the retire form |
| **A withdrawn premise** | Struck through, reason beneath, no withdraw form |
| **Retired** | An info callout with the date and reason; no add form, no retire form, no withdraw forms |
| **Not yours, or no such thesis** | 404, the same answer for both, so ids cannot be enumerated |
| **A refused write** | The problem page, with the reason the service gave |

---

## What is wrong today

**The threshold fields and the review field are both always visible.** The radio decides
which counts, and nothing on the page responds to it. A no-JavaScript answer exists — two
disclosure sections, or the choice leading to the right form — and the redesign's rule is
that chrome may be the client's (ADR 0077).

**The metric is free text with no help.** The monitor (§3.6) will resolve it, and until it
exists there is no list to offer. When it does, the field should offer what the platform can
read and still accept what it cannot.

**A thesis does not show the report it was written against.** `report_id` is stored and not
yet rendered, because the write form does not yet offer a report to choose.

---

## What to improve

**1. The add form's shape.** Two branches in one form is the same problem the portfolio form
has, one size smaller. The choice should lead to the fields it needs.

**2. The premise as a card.** Statement, basis, defeat condition and holder are four kinds of
text at four weights, and the list treats them as a paragraph. A designer would find the
hierarchy.

**3. Linking a thesis to its report and its position.** The subject is a company; the
research tool has reports about it and the portfolio may hold it. Both are queries over the
subject, never foreign keys (ADR 0064), and both belong on this page as links.

---

## What must not change

* **No figure on this page enters arithmetic, and the page carries no conviction.** The
  containment is the schema (ADR 0074); the page must not invent a number the schema refused.
* **A premise without a predicate is styled no lower than one with.** The failure this
  prevents is the operator fabricating precision to make the interface light up.
* **Nothing is deleted.** A withdrawal and a retirement each carry a reason and leave the row.

---

## Done when

* A thesis written with two premises — one tested, one reviewed — reads as one document with
  a verdict that counts them correctly.
* A withdrawn premise is visibly still there, with its reason.
* A retired thesis is visibly closed and accepts nothing.
* A fresh install explains why nothing can be written yet, and where to go.
