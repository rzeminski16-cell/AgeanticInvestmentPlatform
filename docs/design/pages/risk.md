# The Risk tool

**`/risk`** — what the book is exposed to and what a stated scenario would do to it,
commented on rather than scored.

---

## At a glance

| | |
|---|---|
| **URLs** | `/risk` · `POST /risk/read` · `POST /risk/scenarios` · `POST /risk/scenarios/{scenario_id}/withdraw` |
| **Who arrives** | The operator, with a book that holds something priced |
| **From where** | The launcher, the Risk nav item, a work-list row (*not started*, *needs diagnosis*) |
| **What they came for** | *What am I exposed to, how would this book move, and what would the thing I am worried about do to it?* |
| **Templates** | `risk/index.html` |
| **Token state** | Clean — built on the component set from the first line |

---

## The job

**Show every risk figure as a recorded calculation the operator can open, over the book as
it stands, beside the scenarios they stated and an analyst's reading that names no number
of its own — and never a size, a limit, a ranking or a score.**

---

## Four ideas that shape everything on this screen

**1. The figures are code's** (ADR 0080). Volatility, drawdown, expected shortfall, each
holding's beta to the book and its contribution, each scenario's profit and loss — every
one is a `@traced` calculation, and every one on the page links to its formula.

**2. Every figure is ex-ante** (ADR 0106 §1). The book's return series is its holdings'
daily returns over the year to the date, with today's weights held fixed. It answers *if
the book stayed as it is, how would it have moved?* — not how it did, which is the
portfolio page's return. The page says so beside the numbers.

**3. A scenario is a shock the operator states** (ADR 0106 §3). No scenario is built in.
A shock reaches what the exposure bands say it reaches, cash in a currency included and the
book's own currency excluded.

**4. The analyst reads and cannot write** (ADR 0106 §4). Its three commentaries are checked
against the block's own numerals and against the words of a prescription; a refusal is
recorded on the pass and shown as such.

---

## What is on it

**The verdict** leads with the counts: *"Two holdings are measured; one holding could not
be measured; one scenario is stated."* Its detail line is the reading's state: not read,
read on a date, the book has traded since, or stopped at its ceiling.

**What the book would have done** — four figures with their lineage: annualised
volatility, maximum drawdown, expected shortfall over the worst five per cent of days, and
coverage (the share of net assets in measured holdings). The sheet's subtitle names the
window and the currency convention. Where something could not be measured, an info callout
says what.

**Exposure** — the largest five holdings' share, then the four bands (holding, sector,
currency, listing country), each showing its largest five slices with a bar and its
unclassified group named beneath. The whole of each band is on the portfolio page.

**Each holding** — a table: listing, weight and contribution each with a bar on one scale
beneath the figure (the weight in the verification colour, the contribution in the decision
colour, so the rows where the two differ show the gap), then volatility, beta to the book,
days of returns. An unmeasured holding says why in its row.

**Stated scenarios** — a table: name, shocks in words, what it reaches, profit and loss
(linked), share of the book, and **Withdraw**. Under the table, one closed disclosure per
scenario — *What {name} does, position by position* — opening to a table of each reached
position: what it is worth, what it moves by once every shock reaching it is combined, and
its own profit and loss (`position_pnl`, a recorded calculation; the rows sum to the total).
Beneath, the form: **What it is a scenario of** and three rows of **Reaches** (every holding,
a sector, a currency, a listing country, one holding) · **Named** · **Moves by, %**.

**The analyst's reading**, as marginalia: each commentary sits inside the sheet it reads —
the movement under the four figures, the exposure under the bands, the scenarios under the
table — as an aside headed *The analyst read* (with *before the book last traded* when it
is stale). The reading sheet at the end keeps what is about the pass: a refusal callout
listing what was refused and why, or a stopped callout, or *nothing to read*; the date, the
as-of date and the cost; and **Read the book as at {date}**, which runs the pass in the web
process.

---

## Inputs

| Control | Validation | Where it lives |
|---|---|---|
| `as_of` (query) | a date; anything else falls back to the last close | the handler |
| Scenario name | not blank | `state_scenario` |
| Reaches | one of five | the enum; the handler refuses anything else |
| Named | not blank unless every holding | `state_scenario` |
| Moves by | a number of per cent, not nil, above a total loss | the handler for the number; the service and `shock_moves_something_and_leaves_something` for the bounds |
| Read the book | a book of yours | `run_reading` |

Every refusal is a sentence on the problem page with the status the error carries. A pass
that hits its cost ceiling is not a refusal of the form: it fails the pass with the reason.

---

## States

| State | What it shows |
|---|---|
| **No book** | The empty state pointing at the portfolio |
| **Nothing priced** | The verdict says so; the figures sheet says nothing to measure; the reading is not sent to the model |
| **Not enough history** | The holding's row names the reason; the book figures are absent with an info callout; coverage is absent |
| **Measured** | The four figures with lineage, the table, the bands |
| **No scenario** | The empty state above the form |
| **A scenario reaching nothing** | The row says so where its profit and loss would be |
| **Not read** | The verdict's detail says so; a *not started* row on the work list |
| **Read** | The commentaries with the date and cost |
| **Read, then traded** | The detail says the book has traded since; a *not started* row on the work list |
| **Refused** | The refusal callout with each problem |
| **Stopped** | The stopped callout; a *needs diagnosis* row on the work list |

---

## What is wrong today

**One book.** The operator's first open book is assumed; a second is not offered.

**The pass blocks the request**, as the reviewer's does.

**The exposure bands are shown twice** — here in summary and on the portfolio page in full —
and a reader has to know which is which.

**A currency shock reaches cash by currency and holdings by quote currency**, which is
right, but a holding quoted in dollars for a company earning in euros is dollar exposure on
this page and euro exposure in life.

**The position-by-position table shows the shocked loss, not the shocked value.** A reader
who wants "what would BARC be worth" subtracts. The worth after the shock is one more
calculation away and was left out to keep every figure on the page one the total is made
of.

---

## What to improve

**1. The contribution as a bar beside the weight** — done, on one scale, in two colours.

**2. A scenario as a diff of the book** — done, as a closed disclosure per scenario: the
reached positions with their worth, their combined shock and their own profit and loss.

**3. The reading as marginalia** — done. Each commentary beside the sheet it reads; the
reading sheet keeps the pass.

**4. The shocked value beside the loss.** See above: a fourth column, one traced
calculation, if readers turn out to want it.

**5. A book control**, once a second book exists in practice.

---

## What must not change

* **No figure on this page is written by the model.** The block is rendered before the
  analyst is asked, and the commentary is checked against it (ADR 0106 §4).
* **Every figure is ex-ante and says so.** The construction is on the sheet, not in a footnote.
* **No scenario is built in.** What is worth worrying about is the operator's statement.
* **No size, no limit, no ranking, no score**, on the page or in the role's contract (ADR 0080).

---

## Done when

* A book with a year of closes shows four figures, each opening to its calculation, and a
  holdings table whose contributions add to one over the measured rows.
* A scenario stated on the form shows its profit and loss in the book's currency, linked,
  and withdrawing it removes the row.
* The reading shows the analyst's three commentaries, and a commentary naming a number the
  figures do not hold is shown as refused with the reason.
* A fresh install explains that risk is measured over a book, and where to record one.
* A scenario reaching one holding opens to one row whose profit and loss is the scenario's
  total, and a holding that is the whole of the book's risk shows a contribution bar at
  full width.
* After a reading, the exposure commentary is found inside the exposure sheet and nowhere
  else.
