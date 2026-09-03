# Watchlist

Companies you follow and have not commissioned research on, the queue of what to research
next and what it would cost, and the standing budget the queue spends.

> **Following costs nothing; commissioning is a research run.** Every run the queue starts
> is an ordinary research request with the same gates and the same report, and it stops at
> gate one for you. The watchlist automates commissioning, never approval.

---

## The idea in one line

**A watchlist is followed continuously and researched as at a date, and the queue spends a
standing budget that is not one run's cap.**

Following a company is a standing intention: you started on a date, and you have not
stopped. Researching it is a run dated as at a day. The page keeps the two apart, because
"is this researched?" only has an answer with a date on it.

## Following a company

Open **Watchlist** from the menu and fill in the form: the company's name, its ticker, its
exchange, and **what would make it worth researching** — a sentence, so the queue is a list
of reasons rather than a list of tickers. The listing is checked against the same universe
a research request is: a UK or US listing, not a fund or an OTC quote.

Each entry shows when it was followed and where it stands:

- **Queued** — followed and not yet researched.
- **Commissioned** — a run is alive; it stops at gate one for you.
- **Researched** — a report exists for the latest commission, linked from the row.
- **Stopped** — the last run died, so the entry is back in the queue.

## The standing budget

The queue may start research up to a pound figure a month, set by
`AER_WATCHLIST_BUDGET_GBP` (thirty pounds by default). The page shows the budget, what this
month's commissioned runs have spent, what the ones still alive may yet spend up to their
caps, and the room that leaves — with how many more runs at the per-run cap it holds.

A run the queue starts keeps its own per-run cap, and the month's cap applies on top. The
standing budget only bounds what the queue may *start*; it takes nothing from the two
guards that bound what any run may spend.

## Commissioning

**Commission** on a row starts a research run on that company as at today, or as at a date
you enter, with the request form's own defaults: the standard depth, a year's horizon,
point-in-time on, the per-run cap. You land on the run, which stops at gate one for your
approval like any other.

**Commission the next the budget affords** walks the queue in the order you followed and
starts runs until the room is used up, then says which company it stopped at and why. From
a terminal, `aer queue` does the same and exits non-zero when it stopped short — for a
scheduler that commissions on a morning and leaves the gates to you.

A commission the budget cannot afford is refused by name, with the room, the cap, and the
three ways out: commission by hand from the requests page, raise the watchlist budget, or
wait for next month.

## Stopping

**Stop following** takes the entry off the list with a reason. It is kept, so what you once
followed and why you stopped is a record rather than a memory.

## What this tool does not do

- It does not approve anything. Every run stops at gate one for you.
- It does not decide what is worth researching. The *why* is yours, and so is the order.
- It does not research on a cadence. Whether a report is stale is the monitor's question,
  asked of a thesis rather than a calendar.
- It does not spend past the standing budget, and the budget does not raise any run's cap.
