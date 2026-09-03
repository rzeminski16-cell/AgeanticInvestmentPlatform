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
| **Templates** | `theses/index.html` · `theses/detail.html` · `static/js/branches.js` on the detail |
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

Three controls, and a fourth once a report exists: **Title** (required — "what this thesis
claims, in a line"), **About** (a select over the companies the platform can resolve),
**Written against** (a select over the approved reports, blank by default, shown only when
there is one; a report about a different company is refused with a sentence), **Written on**
(date, defaults today, capped today, for backdating a view already held).

**A thesis can only be about a company the research tool has resolved.** A fresh install
offers no companies, and the form is replaced by an empty state pointing at the research
request. A thesis about a ticker nobody has looked up would be a view about a string.

### The detail (`/theses/{thesis_id}`)

Header: the title, *"{subject} · written {date}"*, a breadcrumb to the list.

**The verdict** leads: *"3 premises held; 2 tested by a threshold, 1 reviewed by a person; 1
withdrawn, with the reason kept."* Composed from the rows, in the platform's own voice, and
never a number anything rests on.

**Reports on {subject}**: every approved report about the company, newest first, as a record
list — *Report as of {date}* linking to the report, *approved {date}* as the meta line, and
the one the thesis names as written against carrying that sentence and a *Written against*
mark. A query over the subject (`history.approved_reports_for`), so a report approved after
the thesis appears too. With none, an empty state pointing at the research request.

**Premises**, as an ordered list of cards, four kinds of text at four weights: an eyebrow
*Premise {n}* (*· withdrawn* when it is); the **statement** as the subheading, struck through
when withdrawn; the **basis** as body text under the label *On the basis that*; then a compact
definition list — *Tested by a threshold* / *Reviewed by a person* with the predicate or the
date in the data face, *Held* by {email} since {date}, and *Withdrawn* on {date}: {reason} when
it is. Each held premise carries a one-line withdraw form: a reason and a button.

### The add-premise form

**The premise** (textarea) · **On what basis** (textarea) · **Held since** (date) · **What
would defeat it** (radios: *A threshold code can test* / *A person will look again*, each with
its consequence) · the threshold fields (**Metric**, **Comparator** as words, **Threshold**,
**Unit**) · **Review by** (date, from today).

The radio decides which fields count, and the choice leads to its fields: `branches.js` hides
the branch the radio did not choose and shows it back when the choice returns. Chrome, not
state (ADR 0077) — the form carries `data-branches` naming the group and each branch carries
`data-branch` with the value that shows it, and with scripting off both branches stay on the
screen as the form always was. A review date typed beside a threshold is still a premise with
two answers; the one the operator chose is the one recorded.

**Metric** offers the names the monitor resolves (`measurable_metrics()`: ratios, growths and
levels) as a `datalist`, and still takes any words: a premise about something the monitor
cannot measure is recorded and read as unobservable, and the list is there so that happens by
choice rather than by spelling.

### Decisions taken on it

Every decision recorded against the thesis (ADR 0104), newest first: *"{Action}: {statement}"*
as a link to the decision, and *decided {date} · N trades carried it out*. A withdrawn
decision is struck through. With none, an empty state pointing at Decisions.

### The position

What the default book holds, or held, in the company — a query over the subject through the
listings the book dealt in (`Security.company_id`), never a foreign key (ADR 0064). One walk of
the book's trades (`post_trade.positions_of`) answers both questions, so the open position and
the closed ones cannot disagree about the book. Per row: *{ticker} on {exchange}, open in
{book}* linking to the portfolio, with *Opened {date}* and *n trades on record*; or *{ticker}
on {exchange}, closed {date}* linking to its review — or to the review list, saying *Not yet
reviewed* — with *Held from {date} to {date} in {book}*. No figure: how large the position is
belongs to the book's own page, which records the calculation.

Three empty states: a book that never dealt in the company says so and points at Decisions,
because a position starts with one; no book at all points at the portfolio.

### The retire form

One reason and a button. Absent on a retired thesis, along with the add form; the retired
notice takes their place.

---

## Inputs

| Control | Validation | Where it lives |
|---|---|---|
| Title | not blank | `services.theses.write_thesis` |
| About | a company id the platform holds | the handler, then the form's select |
| Written against | blank, or an approved report about the company named | the handler: a draft is not offered, a report on another company is refused with 422 |
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
| **No approved report** | The write form has no *Written against* control; the detail's reports sheet points at the research request |
| **A thesis with no premises** | The verdict says so, and the premises sheet says to add the first |
| **No book, or a book that never dealt in the company** | The position sheet says which, and points at the portfolio or at Decisions |
| **Ordinary** | Verdict, reports, premises, decisions, the position, the add form, the retire form |
| **A withdrawn premise** | Struck through, reason beneath, no withdraw form |
| **Retired** | An info callout with the date and reason; no add form, no retire form, no withdraw forms |
| **Not yours, or no such thesis** | 404, the same answer for both, so ids cannot be enumerated |
| **A refused write** | The problem page, with the reason the service gave |

---

## What is wrong today

**The write form's report select is flat.** It lists every approved report across every
company and refuses a mismatch after the fact. The right shape is a select that follows the
company chosen — which is either a round trip on the company field or a second `branches.js`
case keyed on a select rather than a radio.

**The position is the default book's only.** An operator with two books sees the first; the
page has no book control and the portfolio page's own choice of book is not remembered here.

**The premise cards are separated by rules, not framed.** The hierarchy is in the type scale
now; whether a bordered card would read better than a ruled list is a question for the design
system, since a card there is a component and not a class.

---

## What to improve

**1. The add form's shape** — done. The choice leads to the fields it needs, by a small chrome
script that fails open; the same script is available to the portfolio form, which has the same
problem one size larger, and has not yet been applied there.

**2. The premise as a card** — done. Eyebrow, statement, basis under its label, and a
definition list for what defeats it and who holds it.

**3. Linking a thesis to its report and its position** — done. Reports on the subject as a
query, the one written against named by the thesis (`report_id`, now offered by the write
form), and the book's open or closed positions in the subject's listings, the closed ones
linking to their review.

**4. The report select following the company.** See above. The cheapest honest version is the
company select reloading the form with its reports; the neater one is a second branch keyed on
a select, which `branches.js` does not do today and should be extended rather than copied.

**5. The premise list at forty premises.** The cap is twenty-four (`MAX_PREMISES`); a thesis
near it is a long page. Whether withdrawn premises should collapse under a disclosure once
they outnumber the held ones is worth a mockup.

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
* Choosing *A person will look again* puts the threshold fields away, and choosing the other
  answer brings them back; with scripting off both are on the screen and the record is the
  same.
* A thesis written against a report shows that report marked among the others on the company,
  and a thesis whose company the book holds shows the position with a link to the book.
