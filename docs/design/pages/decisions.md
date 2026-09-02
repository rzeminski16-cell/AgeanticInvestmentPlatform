# The Decisions tool

**`/decisions`** — what you decided to do about a thesis, when, and on what basis, written
before the outcome is known; and the trades that carried it out.

---

## At a glance

| | |
|---|---|
| **URLs** | `/decisions` · `POST /decisions` · `/decisions/{decision_id}` · `POST …/withdraw` · `POST …/revise` |
| **Who arrives** | The operator, between reading a thesis and placing a trade |
| **From where** | The launcher, the Decisions nav item, a thesis page, a work-list row, the trade form's *Carries out* |
| **What they came for** | *What did I decide, why, and did I do it?* |
| **Templates** | `decisions/index.html` · `decisions/detail.html` |
| **Token state** | Clean — built on the component set from the first line |

---

## The job

**Get the decision written before the trade, in enough detail that a later review can
score the decision rather than the result — and keep every entry, revised or withdrawn, as a
record of what was decided when.**

---

## Three ideas that shape everything on this screen

**1. A decision is a judgement seen from its consequence** (ADRs 0074, 0102, 0104). It has a
holder, a time and a basis, and it acts on a thesis. It is not evidence, it feeds no number,
and the schema has no column a calculation could read.

**2. The size is a sentence.** *"About two per cent of the book."* A stored intended weight
would be a judgement wearing a `Quantity`'s clothes; the reviewer compares the sentence with
the trades that followed, in prose.

**3. Nothing is edited and nothing is deleted.** Revising a decision writes a new entry that
supersedes the old, which stays, withdrawn as superseded. A decision quietly rewritten after
the outcome is the row the post-trade reviewer exists to read.

---

## What is on it

### The journal (`/decisions`)

**The verdict** leads: *"Three decisions are held; one is not yet carried out by a trade."*
Or, with nothing decided, a sentence pointing at the thesis it would act on.

**Held decisions**, newest first. Per row: *"{Action}: {statement}"* as the link, the thesis
title and the listing as the sentence, and a meta line — decided {date} · N trades carried it
out or *not yet carried out* · review by {date}. *Show withdrawn decisions* switches to
`?withdrawn=1`, where each row adds when and why.

**The record form**: **About the thesis** (a select over open theses) · **What you decided**
(open a position, add to it, trim it, close it, keep holding, not act) · **In a line** ·
**On what basis** · **Listing** (`TICKER.EXCHANGE`, from the listings held; empty if not yet
priced) · **Decided on** (date, defaults today, capped today) · **How much, in words** ·
**Intended holding period, in months** · **What would make you reverse it** · **Review by**.

With no open thesis the form is replaced by an empty state pointing at Theses.

### The decision (`/decisions/{decision_id}`)

Header: *"{Action}: {statement}"*; identity line with the thesis and the date decided; a
breadcrumb to the journal. A withdrawn decision leads with an info callout carrying the date
and reason.

**What was decided** — a definition list: the action, the line, the basis, the holder and
date, and the listing, size, horizon, exit plan and review date where given.

**The premises it was taken on** — the thesis's premises as they stand, each with what would
defeat it; a withdrawn one struck through with its reason. Links to the thesis.

**The trades that carried it out** — each trade that named this decision on the portfolio
form: kind, date, quantity, price. A decision that moves the book and has no trade shows
*Not yet carried out* with a link to the trade form; a hold or a pass says nothing carries
it out.

**Revise it** — the same fields, prefilled, with a fresh basis; submitting writes a new entry
that supersedes this one. **Withdraw it** — a reason and a button. Both absent on a withdrawn
decision.

### The trade form's *Carries out* (`/portfolio`)

One optional select over the operator's held decisions that move the book, labelled by
action, thesis and date. A trade recorded with one names the decision; a sale offered
against a decision to buy is refused with the reason.

---

## Inputs

| Control | Validation | Where it lives |
|---|---|---|
| Thesis | one of yours, and open | the handler, then `record_decision` |
| Action | one of six | the enum; the service refuses nothing else |
| Statement, basis | not blank | the service; the database repeats the statement check |
| Listing | held, and unambiguous | the handler, which refuses a typo rather than dropping it |
| Horizon | a positive number of months | the service, and `decision_horizon_is_positive` |
| Reason (withdraw) | not blank | the service |
| Carries out (trade form) | a held decision the trade's kind and security fit | `carry_out` |

Every refusal is a sentence on the problem page with the status the error carries.

---

## States

| State | What it shows |
|---|---|
| **No open thesis** | The verdict says so; the form is replaced by an empty state pointing at Theses |
| **Nothing decided** | *"Nothing decided yet"* above the form |
| **Ordinary** | Verdict, the held list, the form |
| **Not yet carried out** | The meta line says so; the detail's trades sheet points at the trade form; the work list carries a *not started* row |
| **Carried out** | The trades listed on the detail; the meta line counts them |
| **Review due** | A *not started* row on the work list, leading here |
| **Revised** | The old entry withdrawn as superseded; the new one linked from the journal |
| **Withdrawn** | The callout; no revise or withdraw forms; kept under `?withdrawn=1` |
| **Not yours, or no such decision** | 404, the same answer for both |

---

## What is wrong today

**The listing box is typed, and the dealable listings are only a datalist.** A decision about
a company the platform has never priced leaves it empty, which is right, but the box gives no
hint that the third door on the trade form will create the listing later.

**A decision names one book.** The operator's first open book is assumed; a second book is
not offered.

**A trade can name a decision only as it is recorded.** A trade already in the book cannot be
attributed to a decision afterwards from either page.

---

## What to improve

**1. The entry as a card.** Action, statement, basis and the four commitments are five kinds
of text at five weights, and the detail treats them as a definition list.

**2. Revise as a diff.** The revision form is the old entry prefilled; showing what changed
between the two entries is what a reviewer will want.

**3. Attributing an existing trade.** A picker on the decision page over the book's
unattributed trades of the right kind and security.

---

## What must not change

* **No number on this page enters arithmetic.** The size is a sentence and the horizon is
  compared with a date; the schema has no numeric size column (ADR 0074, ADR 0104).
* **The trade points at the decision, never the reverse.** The link is on the attestation;
  `aer.calc` has no word for a decision, and a test keeps it so.
* **Nothing is edited.** A revision is a new entry; a withdrawal is a reason on the old one.
* **The six sizing names are refused wherever a skill could declare them.**

---

## Done when

* A decision recorded before a trade reads as one entry with its commitments, and the trade
  recorded on the portfolio form against it appears on the decision's page.
* A revised decision shows the old entry withdrawn as superseded and the new one held.
* A decision not carried out appears on the work list as *not started* and leaves it once a
  trade names it.
* A fresh install explains that a decision acts on a thesis, and where to write one.
