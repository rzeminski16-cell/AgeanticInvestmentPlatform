# The portfolio

Recording what you hold, and why no holding is ever stored as a number.

> **This is a personal record-keeping tool, not a broker connection and not a tax
> computation.** It does not execute trades, does not connect to any account, and does not
> compute a tax liability.

---

## The idea in one line

**A position is a calculation, not a row.** There is no `positions` table, and there will
not be one.

You record what happened — bought, sold, dividend received, fee charged, cash in, cash out
— and every holding figure is recomputed from those transactions each time it is asked
for, as at whatever date you asked about. Quantity, cost basis, market value, cash balance,
net assets and weight are each a recorded calculation with a formula, inputs and sources,
exactly like a ratio in a research report.

The alternative — storing a position and updating it — has one failure mode that matters:
a stored quantity and the transactions behind it can disagree, and nothing notices. A
derived one cannot.

## What you record

Six transaction kinds, and the list is deliberately short. Each needs its own arithmetic,
and a wrong kind is worse than an absent one.

| Kind | Effect |
|---|---|
| `buy` | Units in, cash out |
| `sell` | Units out, cash in |
| `dividend` | Cash received |
| `fee` | Cash charged — commission, stamp duty, custody |
| `deposit` | Cash paid into the account |
| `withdrawal` | Cash taken out |

**A trade can name the decision it carries out.** If you wrote the decision down first in
[Decisions](decisions.md), choose it under **Carries out** when you record the trade; the
decision's page then lists the trade. The form refuses a pairing that cannot be what it says,
such as a sale against a decision to buy.

**Exchanging currency is not on that list**, on purpose. It is one event touching two
currencies, and a transaction row holds one — so it would need either a second currency
column nothing else uses, or a pair of rows whose "these two are one event" invariant no
database constraint can see. Getting that wrong double-counts a cash balance silently, in
the direction that flatters. Until it has a shape of its own, record an exchange as a
withdrawal and a deposit. What is lost is the rate, which was never this table's to assert.

**A share split arrives on its own, and you do not enter it.** When the platform acquires
a price series it records the splits with it, and each one becomes a written transaction
in every book that has dealt that listing — never a quantity that changed with nothing
behind it. The row carries the *ratio*, not a number of shares, so it stays right if you
later record a trade dated before the split: the multiplication lands at its place in the
history rather than being frozen at the moment the row was written. Your share count
multiplies; what you paid does not change, so the average cost per share divides by the
ratio. There is no Split option on the form, on purpose — a split you can type is a share
count with nothing behind it. If a split you know about has not appeared, acquire the
listing's price series; that is what records the corporate action.

## Two grades of evidence

Everything you record carries a grade, and this is the part worth understanding.

- **`documented`** — extracted from a hashed document you supplied: a contract note, a
  custodian statement, a dividend advice. The full chain applies unchanged — artefact,
  extraction, locator, citation — and it is verified by the same code that verifies a
  20-F. **As citable as a filing.**
- **`attested`** — typed by you and self-certified, with no document behind it.
  Admissible, and marked.

Operator-supplied data is *not* inherently weaker than a filing. What is weaker is a number
somebody typed. The distinction is a property of the stored row, never of a rendering, and
**the grade propagates**: any figure whose lineage contains an attested input inherits the
grade all the way up. A net asset value computed from one attested holding is an attested
net asset value, and reaches a shareable surface as a type with no field for a bare number.

That is what stops "I'll document it later" from quietly becoming the way the whole book
gets entered.

## Reading the portfolio screen

`/portfolio` shows what you hold as at a date, defaulting to the latest close. Every figure
carries its grade, and every one drills through to the transactions and prices behind it —
the same provenance walk a research figure gets.

**Nothing is persisted by looking.** A page load is not a run: it has no job to hang a
calculation on and writes nothing. What you see is derived on the spot from what you
recorded.

**A refused net asset value is normal.** If a holding cannot be marked — no price, a stale
one, a currency with no rate on that date — the total refuses rather than silently omitting
the holding, and says which one defeated it. A net asset value that quietly dropped a
position would be worse than none.

**The page leads with your reasons, not your profit.** Under *The book* is one sentence: how
many positions have a thesis that currently holds, and what the rest have instead — a premise
that broke, a thesis under review, or nothing written down at all. The table sorts the same
way, the positions with nothing written down first. Beside it, the **Risk** card reads the
five largest, the largest sector, how many positions are past a ceiling you set, cash, and
the latest shock you stated on the risk page with what it would do; beneath that, **Needs a
decision** appears while a premise stands broken or a holding has no thesis, and leads to
where that is settled.

**Your limits, where you set them.** A ceiling on one position, on the five largest or on a
sector is yours, stated on **Platform → Book**; nothing proposes one. Once stated, each weight
bar carries its line, a holding past it reads *over ceiling* and the *Over ceiling* filter
finds it. A ceiling blocks nothing.

**One position** opens from its value: how it was built — each trade with the decision it
carried out, where you said which — what it is worth, and what it does to the book against
your ceilings.

## Prices and rates

- **Prices** come from the licensed end-of-day feed. If the subscription lapses, the price
  bars go, but **recorded net asset values survive as derived output**: nothing is
  recomputed backwards and nothing is interpolated to fill a gap.
- **Rates** are dated observations with a source, not constants in a column. A rate outlives
  the request that fetched it, so a valuation dated last March uses last March's rate rather
  than today's.

## What it deliberately does not do

- **No trade execution and no broker connection.** Out of scope, not on a roadmap.
- **No portfolio optimiser** — no efficient frontier, no allocation solver.
- **No tax computation.** Cost basis is a **pooled average**, which is a convention chosen
  for being defensible and stable, not for matching any tax authority's rules. It is not
  first-in-first-out and it is not a Section 104 calculation. Do not file with it.
- **No risk analysis on this page.** That is the [risk page](risk.md), reached from the Risk
  card; this page shows its figures and links to the working.

---

**Next:** [reading a report](reading-a-report.md) · [the roadmap](../plan/ROADMAP.md)
