# ADR 0083 — A position is a calculation, not a row

**Status.** Accepted
**Date.** 2026-08-23
**Required by.** The portfolio tool, which is the second tool this platform will have and
the first one whose figures are about money the operator actually has.
**Extends.** ADR 0073, which admitted the attestation as the fourth record class, and ADR
0011, which made a calculation a stored thing with a formula, its inputs and a code
version. This record decides which of the two a *position* is.

## Context

A portfolio screen shows a table: security, quantity, cost basis, last price, market value,
unrealised profit, weight. Seven columns, and the question this record answers is where
each of those numbers comes from.

The obvious design is a `positions` table with those columns on it, updated when a trade is
entered. It is what almost every portfolio tool does, it is one query to render the page,
and it is wrong here — for a reason this repository has already written down twice.

`aer/calc/units.py` closes `SourceKind` at three values and says why:

> Three kinds, and the list is deliberately closed. Every number in a report resolves,
> eventually, to a fact somebody filed, an assumption somebody made and justified, or a
> calculation over those two. **A fourth kind would be a way in for a number with no
> story.**

`CLAUDE.md` states the same rule as invariant 3: **no figure reaches a report unless it is
a stored fact or a recorded calculation.** A `positions.market_value` column is neither. It
is a number somebody's code wrote once, with no formula attached, no inputs recorded, and
no way to ask why it says what it says. The moment it disagrees with a broker statement —
which is the entire reason a person opens this screen — there is nothing to reconcile
against.

The stakes are higher here than in a report, not lower. A wrong revenue figure in a
research note is embarrassing; a wrong cost basis is a tax return.

## Decision

**The record is the transaction. A position is a recorded calculation over transactions,
as at a date, and is never stored as a row.**

### What is stored

`transactions`, as an attestation subtype under ADR 0073. One row per thing that happened
to the book: a buy, a sell, a dividend received, a fee charged, a deposit, a withdrawal, a
currency exchange. Each carries the portfolio, the security or the currency, a trade date
and a settlement date, a quantity, a price, a currency, fees, and — from ADR 0073 — a
**grade**:

- *documented*, extracted from a hashed artefact: a contract note, a custodian statement.
  Full chain, artefact through to citation.
- *attested*, typed by the operator and self-certified, with no artefact behind it.

Nothing else about the book is stored. There is no `positions` table, no `holdings` table,
and no cached net asset value.

### What is computed

Everything the screen shows, through `aer/calc`, recorded in `calculations` with its
formula, its inputs and the code version that produced it — the same machinery a
discounted cash flow already uses:

- **quantity held** — the signed sum of transaction quantities up to the as-of date
- **cost basis** — the consideration paid for what is still held, with fees
- **market value** — quantity times a `price_bars` close for that security on or before
  the as-of date, converted through a dated rate (ADR 0082)
- **cash** — a balance per currency, over the same transactions
- **net asset value** — market value plus cash
- **weight** — a position's market value over net asset value

Every one of those is a `SourceRef` chain a reader can walk: a price traces to a bar, a bar
traces to a `source_document_id`, a quantity traces to the transactions that made it. That
is the property a stored column cannot have, and it is worth more than the query it costs.

### The grade propagates, and the type carries it

ADR 0073's containment applies here without amendment. A position computed from any
*attested* transaction is an attested position, and **a lineage containing an attested node
cannot reach a shareable rendering** — enforced by a return type with no field for the
figure, the shape ADR 0034 used for `WithheldComps`. On screen it renders with its grade
stated. In an export it is refused, by construction rather than by a flag somebody can
argue with.

This matters more than it looks. A portfolio typed from memory and a portfolio parsed from
contract notes produce identical-looking tables, and only one of them is evidence.

### Cash is a position

A cash balance is derived from the same transactions, per currency, and is part of net
asset value. Without it, every weight on the page is a fraction of the wrong denominator —
silently, and in the direction that overstates every holding.

### The two clocks, from ADR 0075

The page is *as at* a date, defaulting to the latest close. That is not a nicety: it is what
makes the screen reconcilable against a statement, which arrives dated and is the only
external check this tool has. Positions are computed to the as-of date; prices are the last
bar on or before it; rates likewise.

A portfolio is continuous and a research run is a point (ADR 0075). This record does not
change that — it says only that when the continuous thing is *shown*, it is shown at a
stated instant, and the instant is chosen by the reader rather than by the clock.

## What this costs

**Every page load computes.** A position over a few hundred transactions is arithmetic over
a small table, and the price lookup is one indexed row per holding — but this is genuinely
slower than reading seven columns, and it grows with history rather than with holdings.
Accepted, and mitigated the way the badge counts already are: if it ever gets slow, the
answer is a cache with the recorded calculation still underneath it, never a column that
becomes the truth.

**A corporate action has to arrive as a transaction.** `corporate_actions` exists and
`adjusted_series_for` already applies splits to a *price* series. Applying one to a
*holding* is a different thing — it changes the share count — and reading it off the table
automatically would mean a quantity that changed without a transaction behind it, which is
the exact property this record refuses. So in the first version a split is entered like
anything else. Deriving it from `corporate_actions` and writing the transaction it implies
is worth doing, and it is worth doing as a transaction rather than as an exception.

> **Done 2026-08-30, as ADR 0094 (roadmap §2.6).** A split is a derived transaction whose
> quantity is the ratio, pointing at the corporate action behind it. The cost this
> paragraph named is paid.

**Nothing is faster to build.** A `positions` table would be one migration and one update
statement. This is a calculation module, its unit tests and its property tests. That is the
correct trade for the one screen in the platform where a wrong number has a cost measured
in money.

## Consequences

**The portfolio page is auditable in exactly the way a research report is**, and by the
same machinery: click a figure, see the formula, see the inputs, see where each input came
from. Nothing new was invented for it.

**A disagreement with a broker statement is a question with an answer.** "Why does this say
1,340 shares" resolves to a list of transactions and the arithmetic over them, rather than
to a column and a shrug.

**`SourceKind` still has four values and none of them is "portfolio".** A fill price is an
attestation, a share count is a calculation, and the portfolio tool introduces no fifth
kind — which is what ADR 0073 promised when it argued for the fourth.

**There is no `positions` table to migrate later.** If performance ever demands one it
arrives as a cache with a recorded calculation behind it, and the test that keeps it honest
is that recomputing reproduces it — the shape `tests/test_run_replay.py` already uses for
calculations generally.
