# Risk

What the book is exposed to, how it would have moved as it stands, what a scenario you
state would do to it, and an analyst's reading of all of that.

> **Every number on this page is a calculation you can open, and none of them is advice.**
> The analyst reads the figures and says what the pattern means. It cannot write a number,
> size a position, set a limit, rank anything or score the book — there is no field for any
> of those, and a commentary that tried is refused and shown as refused.

---

## The idea in one line

**The figures are the platform's, the scenarios are yours, and the reading is a comment on
both.**

## What the figures mean

Open **Risk** from the menu. The page shows the book as at its latest close, or any date
you put in the address as `?as_of=`.

**What the book would have done** is *ex-ante*: the platform takes a year of daily returns
for each holding, holds the book's weights fixed at what they are today, and asks how that
book would have moved. It is not the book's history — the portfolio page's return is — and
the page says so beside the figures. Four figures:

- **Annualised volatility** — how much the book moves, scaled to a year.
- **Maximum drawdown** — the worst peak-to-trough fall.
- **Expected shortfall** — the average of the worst five per cent of days.
- **Coverage** — the share of the book in holdings that could be measured. Cash and any
  holding without enough price history earn nothing in the series, and the figure tells
  you how much of the book that leaves out.

Returns are measured in each listing's own currency, so a dollar holding's currency risk is
in the **currency** exposure band rather than folded into the volatility.

**Exposure** repeats the largest slices of each band from the portfolio page. **Each
holding** shows its own volatility, its beta to the book, and its contribution — its weight
times that beta — which add to one over the measured holdings.

Every figure links to its calculation, with its formula, its inputs and the price history
each input came from.

## Stating a scenario

A scenario is a named set of shocks: the whole book down a fifth, one currency down a
tenth, a sector or a listing country or one holding moved by whatever you say. Fill in
**What it is a scenario of** and up to three rows — what the shock reaches, the name it
reaches it by (as the exposure bands spell it), and the move in per cent — and **State the
scenario**.

The page applies it to the book as it stands: each position the scenario reaches, the
profit and loss in the book's currency, and the share of the book that is. A shock to a
currency reaches cash held in it too, because cash is a position; the book's own currency
never moves against itself. A scenario that reaches nothing says so.

No scenario is built in. Which shocks are worth worrying about is a judgement, and it is
yours: a default set would decide what you see and, by omission, what you never see.

**Withdraw** a scenario from its row. It is kept, marked withdrawn, as a record of what you
once watched.

## The analyst's reading

**Read the book** runs the analyst over the figures on the page. It spends against your
per-run cap, and it returns three short readings — of the exposure, of the movement, and of
the scenarios — that say what the table does not: that three of the largest exposures are
one end market under two sector codes, that a scenario about one currency reaches most of
the book through cash.

Before the reading is shown, the platform checks it. A commentary that names a number the
figures do not hold, or that says what to buy, sell, trim, cap or stop, is refused; the
analyst is told why and tries once more; a second refusal is kept and shown as such. What
you never see is a number the analyst made up.

A reading is of the book as it was. When the book trades, the work list says it has not
been read since, and the page says the same beside the reading.

## What this tool does not do

- It does not size a position, set a limit, rank holdings or produce a risk score (ADR
  0080). Nothing on the page can, and the role's output has no field for it.
- It does not choose scenarios for you.
- It does not measure the book's history. That is the portfolio page's return.
- It does not read prices as marks for anything but the book's value as at the date. A
  scenario is arithmetic over that value and your stated shocks.
