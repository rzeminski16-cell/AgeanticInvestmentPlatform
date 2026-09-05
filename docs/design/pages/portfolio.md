# The Portfolio tool

**`/portfolio`** — the book, as at a date, with every figure carrying its working and its
grade.

---

## At a glance

| | |
|---|---|
| **URLs** | `/portfolio` · `POST /portfolio` (create the book) · `POST /portfolio/transactions` |
| **Who arrives** | The operator, recording what they hold or reconciling it against a broker statement |
| **From where** | The launcher, the Portfolio nav item |
| **What they came for** | *What am I worth, and does it match my statement?* |
| **Templates** | `portfolio/index.html` (419 lines, **10 raw ramps**) · `empty.html` (**0**) · `broken.html` (8) |
| **Token state** | **Nearly clean.** The best-migrated tool in the product |

---

## The job

**Show what the book is worth as at a chosen date, from figures the operator can trust
because the screen says exactly how much to trust each one — and take new transactions
without ceremony.**

---

## Three ideas that shape everything on this screen

**1. There is no `positions` table. A position is a calculation.** Every figure here is
computed *on the way to the page*: a load walks the transactions, pools the cost, marks the
holdings and converts them. Nothing is stored, and nothing is written — a `GET` that wrote a
few hundred calculation rows would make a read a writer.

**2. Every figure states its grade.** A holding typed from memory and one parsed from a
contract note look identical on screen unless the screen says which.

| Grade | Chip | Meaning |
|---|---|---|
| `attested` | **Typed** | Self-certified. No document behind it, and every figure above it inherits that |
| `documented` | **Documented** | Extracted from a hashed document, with a citation behind it |

**"Typed", not "Attested", and the difference is not cosmetic.** The shell's provenance
vocabulary already spends the word *Attested* on a record *class* — a figure whose origin is
the operator's own book — and a documented attestation is every bit as attested in that
sense. This chip is a different axis: how strong the evidence is. Two vocabularies sharing one
word teach a reader that the word means neither.

**The chip is not the containment.** The containment is a type with no field for the figure at
all, so an attested number cannot reach a shareable surface. A chip can be ignored; a missing
field cannot. **So design the chip for reading, not for safety.**

**3. The clock is continuous, unlike research.** The book is *followed*. What it was worth
last March and what it is worth now are two different questions, both legitimate.

---

## What is on it

### The header
The book's name, and *"As at {date}, reported in {currency}"*.

### The date control
A `GET` form: a date input capped at today, and a **Show** button.

**A `GET`, so a view is a link somebody can keep.** "As it stood on the thirtieth" is a thing
an operator wants to send themselves, and a date held only in a form is a view that cannot be
returned to.

**The default is the last close the platform holds**, not today. A book shown at today's date
is a book with no prices for today — markets close, and a screen defaulting to now would show
every holding unpriced every evening and all weekend.

A malformed date in the URL falls back to the default rather than erroring: the control is a
date input, so anything else is a hand-typed URL, and the useful answer to one is the page.

### Four tiles
**Net assets · Securities · Cash · Unrealised.**

**All four go blank together, and this is the most important rule on the screen.** Each is a
sum over the rows that resolved, so if any row did not, *none* may be shown.

> The first draft of this screen showed a refused net asset value beside a cash tile reading
> £50,000 — a book whose dollars could not be converted, with its sterling summed and stated
> as though that were the cash. **A subtotal presented as a total is the most dangerous number
> on a financial screen.**

When incomplete, each tile shows `—` with the note *"Unavailable while a position cannot be
valued."*

### The holdings table
Columns: **Security · Quantity · Cost · Value · Unrealised · Weight**.

Per row: ticker, exchange, the grade chip, and figures right-aligned in tabular numerals.
Unrealised is coloured up or down — **and is also signed**, so the colour is not carrying it
alone.

**Cash is in the same table**, one row per currency, on a sunken background. Without it every
weight on the page is a fraction of the wrong denominator — silently, and in the direction
that overstates every holding.

**A row that could not be priced puts the reason where the number would be**, spanning the
columns it would have filled. A blank cell reads as nil, which is a claim. A closed position
says *Closed*.

### The transaction form
Below the table, on the same page. Nine controls:

| Control | Type | Notes |
|---|---|---|
| **What happened** | select | Buy · Sell · Dividend · Fee · Deposit · Withdrawal. **Not Split**: a split is derived from the corporate action (ADR 0094), and one that could be typed would be a share count with nothing behind it |
| **Security** | `<input list>` over a `<datalist>` | Typeable. **Empty means cash** |
| **Trade date** | date, defaults today, capped today | |
| **Quantity or amount** | decimal | *"Shares for a deal, money for anything else"* |
| **Price per share** | decimal | Buys and sells only. **In the dealing currency — pence for a London listing, if that is what the contract note says** |
| **Currency** | text, 3 chars, defaults to base | |
| **Dealing costs** | decimal | *"On a purchase they join the cost; on a sale they only take cash"* |
| **Carries out** | select, optional | The held decision this trade carries out (ADR 0104), from the operator's journal. A sale offered against a decision to buy is refused with the reason |
| **Note** | text | |

**Every amount is typed positive. The sign is the form's job.** Nobody types a minus in front
of a sale, and a book that required it would fill with additions that look like disposals.

**The security box is typed, not picked.** It was a `<select>` over every listing, which is
unusable at any real size and was *worse* than unusable at size zero: on a machine whose runs
had no market-data subscription it offered one option reading "cash, no security", and an
operator could neither type a ticker nor find out why not.

Three shapes are accepted, because all three are what somebody types: `MSFT`, `MSFT.US` — the
vendor symbol a research run stored — and `MSFT NASDAQ`.

**A dual listing is refused with both choices named**, never resolved by picking one: a
holding priced off the wrong exchange is a book nothing downstream can reconcile.

**A typed entry is always recorded at the attested grade.** There is no argument to the
handler that could make it otherwise — typing into a form produces no artefact.

---

## States

| State | What it shows |
|---|---|
| **No book** | `empty.html` — a form to create one: a name and a reporting currency. **Not a wizard**; both are changeable |
| **Book, nothing recorded** | *"Nothing recorded yet. Enter a deposit and a trade below and the book computes itself from them."* The form is still there |
| **Ordinary** | Four tiles, the table, the form |
| **A position cannot be valued** | **All four tiles blank**, the row carries the reason, a warning above |
| **The whole book will not compute** | `broken.html` — shown plainly. An empty table would read as "you hold nothing" |
| **No priced listing at all** | The form explains why, and that cash transactions work exactly as they will later |
| **An unresolvable ticker** | A refusal naming what to do about it — not a validation error |
| **A dual listing** | Refused with **both** choices named |
| **A refused transaction** | The database's check constraints are the real control, and their messages name the rule. A sell entered as a positive number lands here rather than in a holding that grew |

---

## What is wrong today

~~**Four tiles is not an overview.**~~ **Landed 2026-09-01 (§3.2).** Return and exposure are
two sheets below the tiles: a two-column return table — time-weighted and money-weighted,
since inception and per calendar year — and four exposure bands with a top-five figure that
says how many holdings it covers. Both were designed here first, which is why they fitted
without redrawing the page.

**A holding does not show its transactions.** Every figure is computed from the trades beneath
it and there is no way to see them from the row. Reconciling a discrepancy against a statement
— the tool's only external check — means working out what must have been entered.

**Nothing shows the transaction history at all.** Transactions are the only thing this tool
actually stores, and there is no page that lists them.

**The form is eight controls in a three-column grid, always fully visible.** Recording a
dividend means looking past "price per share" and "dealing costs"; recording a buy means
knowing to leave the security box empty for cash. The controls that matter change completely
depending on the first dropdown, and nothing responds to it. *(A no-JavaScript-friendly
answer exists: the kind could be a choice that leads to the right form, or the groups could be
disclosure sections.)*

**The grade chip is the same size and weight as everything else in the cell.** Since the
entire book will be `Typed` for most operators, the chip repeats on every row and adds noise
without adding information — while the one thing it exists to say, *this whole book is
self-certified*, is nowhere stated once.

**Unrealised is the only coloured figure**, so the eye goes to it regardless of whether it is
the interesting number.

**The empty state does not mention the research tool**, which is the one path that creates a
priced listing today. The form's empty case says it; the book's does not.

**The date control says "Show"** and looks like a filter, when it is the single control that
determines what every figure on the page means.

---

## What to improve

**1. ~~Design for return and exposure now.~~ Built 2026-09-01.** The two returns sit in one
table with a column each, because a screen showing a single "return" asserts which question
the reader meant. **Deposits and withdrawals are flows, not gains**, and the sheet says so in
its own subtitle rather than relying on the arithmetic being right unread. The unclassified
group is rendered from its own template branch, held out of the weighted list, so it cannot
be styled into looking like a sector the book is in.

**2. Make a holding openable.** Its transactions, its pooled cost, its price history. The
drawer is built and would suit this exactly.

**3. Give transactions a surface.** They are the record; everything else is derived from them.

**4. Make the form respond to what is being recorded.** A dividend and a purchase need
different fields. Must work without JavaScript.

**5. State the book's grade once, prominently, and calm the per-row chips.** "Everything in
this book is typed and self-certified" said once is stronger than a chip on every row, and
leaves the chip meaning something when a documented row eventually appears.

**6. Make the date control look like what it is** — the lens the whole page is seen through,
not a filter.

**7. Design the "unpriced holding" case as a first-class state.** It blanks the four tiles,
which is correct and drastic, and the page should explain the trade rather than leave four
dashes to do it.

**8. Point the empty state at the research tool.** Commissioning a report on a ticker is what
makes it dealable.

---

## What must not change

**All four tiles go blank together.** Never a subtotal wearing a total's clothes.

**Every figure carries its grade**, and the grade of a total is the grade of the weakest thing
beneath it.

**Cash is a position and lives in the same table.** Otherwise every weight is wrong.

**A row that cannot be priced shows the reason where the number would be.** Never a blank.

**The date is in the URL.**

**The default date is the last close held, not today.**

**Amounts are typed positive; the form signs them.**

**A dual listing is refused with both choices named.**

**A hand-typed transaction is attested.** No exceptions, no override.

**Figures are exact to the penny.** This screen is reconciled line by line against a
statement; the report's house style renders in millions and would show a £1.2m book as "£1m",
which cannot be reconciled against anything.

**Nothing on this page is written by loading it.** Looking is not a run.

---

## Done when

- The operator can answer "what am I worth, and how have I done?" without leaving the page.
- Any row can be opened to the transactions that produced it.
- Recording a dividend and recording a purchase each feel like a short, obvious form.
- The book's overall grade is stated once and clearly; per-row chips are legible without
  being noisy.
- An unpriceable holding explains the blank tiles rather than merely causing them.
- The page holds twenty holdings and four currencies without becoming a wall.
