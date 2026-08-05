# ADR 0032 — The adjusted close is not a column, and pence is not pounds

**Status.** Accepted
**Date.** 2026-08-05
**Extends.** ADR 0027, which established that a per-cent is a convention rather than a unit.
**Required by.** Task 29, whose price series has to be reproducible from an archived response
and a list of recorded adjustments alone.

## Context

Every price vendor ships two closing prices per bar: the one the exchange printed, and an
*adjusted* one that restates history for splits and dividends. The adjusted figure is the one
almost every calculation wants — returns, beta and momentum are all wrong without it — so the
tempting schema is one column, adjusted, and no corporate-action table at all.

That column is a lie in a specific way. It is not that its value is wrong; it is that its
value **changes retroactively**. A company splits its stock in September and every adjusted
close from 1998 onward becomes a different number, silently, on the vendor's next refresh.
A report published in August cited a figure that no longer exists anywhere, and nothing in a
single-column schema can say what changed it or when.

The second problem arrived with the first London listing. A Barclays quote of `250` means
£2.50. The number carries no marker saying so, and the LSE convention is not universal even
within London — some instruments quote in pounds. A schema that stores `250` beside a Microsoft
`446.95` and calls both "the close in GBP/USD" has recorded two figures that are not on the
same scale, and the mistake surfaces as a market capitalisation a hundred times too large.

## Decision

### The raw series is stored; the adjusted series is a calculation

`price_bars` holds open, high, low, close and volume exactly as published.
`corporate_actions` holds splits and dividends, each dated by its **ex-date** — the day the
market price actually stepped, which is the only one of the four dates on a corporate action
that decides which bars an adjustment touches.

The adjusted series is produced by `aer.calc`, from those two tables, as a traced calculation
carrying its formula, its inputs and the code version that produced it. Nothing stores it.

Three things follow, and each is why the extra table earns its keep:

- **The point-in-time clamp needs no separate machinery.** A valuation as of June applies only
  actions whose ex-date had arrived by June, because a split announced in September had not
  happened. `WHERE ex_date <= as_of_date` is the whole rule, and it lives in the same predicate
  as `WHERE bar_date <= as_of_date`. Under a single adjusted column there is no honest way to
  clamp at all: the vendor already folded September's split into the June figure, and the
  look-ahead is invisible.
- **A retroactive restatement becomes an event, not a silent overwrite.** A new corporate
  action is an insert. The old adjusted series is still derivable by asking for it as of the
  old date.
- **The vendor's adjusted close is retained as a cross-check**, in `adjusted_close`, and is
  never the answer. Where this platform's adjustment and the vendor's disagree, that is worth
  surfacing — and it cannot be surfaced at all if only one of them is stored.

### A vendor correcting history collides rather than overwrites

`(security_id, bar_date)` is unique. A second bar for a day this platform already has is
rejected, which routes the correction into the disagreement ladder built in task 19 rather
than quietly changing a number a report already cited. Silent last-write-wins is exactly the
failure this whole platform exists to avoid.

### Pence is recorded as `GBX`, and the conversion is a calculation

`securities.quote_currency` holds what the **prices** are denominated in, which is not always
what the company reports in. A London listing quoting in pence gets `GBX`, not `GBP`, and the
conversion to major units is a single traced calculation with a unit on each side.

This is ADR 0027's argument applied to a second dimensionless convention. There, a figure of
`4.5` meant either 4.5% or 450% and the number said nothing; the fix was to record the
convention where the series was declared and to convert in exactly one place. Here a figure of
`250` means either £250 or £2.50. Same shape, same fix, and the same reason for preferring it
to a rule somebody remembers: a division by 100 that lives in a developer's head is a division
that gets skipped on the path nobody tested.

`GBX` is not an ISO 4217 code. It is a widely used market convention (`GBp` in some feeds), and
recording it as though it were a currency is the smallest lie available — the alternatives are
a second boolean column meaning "divide by a hundred", which is the convention-in-someone's-head
problem with extra steps, or normalising on the way in, which makes a stored bar stop meaning
what the exchange printed.

### A security is not a company

`securities.company_id` is nullable, and one company may have several rows. A dual listing, an
ADR and two share classes trade at different prices in different currencies; prices belong to
the listing. The nullability is what lets task 30's peer set carry price series for companies
this platform has never researched and may never resolve against a registry — the alternative
was resolving every peer against EDGAR before it could be compared, or having no comparables.

### Only two kinds of action, and the gap is reported rather than guessed

`split` and `dividend`. Rights issues, spin-offs and returns of capital adjust a price series
too, and none is modelled, because each needs its own arithmetic and a wrong adjustment is
worse than an absent one: it is wrong by an amount nobody can see. A run whose company had one
of those is a run whose adjusted series is incomplete, and it says so.

### The two kinds are unique differently

Two partial unique indexes rather than one whole one, because the obvious single constraint is
wrong in both directions:

- Over `(security_id, kind, ex_date)` it rejects an ordinary and a special dividend sharing an
  ex-date, which is an ordinary thing for a company to do.
- Over `(security_id, ex_date, dividend_amount)` for everything, it lets a duplicated split
  through, because a split's `dividend_amount` is null and null is distinct from null in a
  unique index.

So splits are unique on `(security_id, ex_date)` — two splits on one day is arithmetically one
split — and dividends on `(security_id, ex_date, dividend_amount)`, where the amount is part of
the payment's identity. The two indexes do not see each other, which is correct: a split and a
dividend routinely share an ex-date.

## Consequences

**Every price read costs a join and an adjustment.** Accepted. The alternative is a column that
rewrites itself, and this platform's whole claim is that a figure in a report can be traced to
a formula and a source.

**The adjustment code is now load-bearing and must be tested against the vendor's own answer.**
`adjusted_close` exists for exactly this, and a systematic divergence is a bug in this
platform's arithmetic, not a vendor curiosity.

**`GBX` will not validate against an ISO 4217 list**, and any code that assumes three-letter
currency codes are ISO codes has to be told otherwise once, at the boundary.

**Everything these tables describe is licensed.** The rows are ordinary; the payloads they were
parsed from carry `RetentionClass.LICENSED` and are purgeable under ADR 0031, and
`source_document_id` is what still answers "where did this come from?" once the bytes are gone.
Under ADR 0030 route 2 nothing derived from them leaves the machine.

## Alternatives rejected

**Store the vendor's adjusted close and nothing else.** Cheapest, and it makes point-in-time
valuation impossible to do honestly — the look-ahead is already baked into the figure and
cannot be removed by filtering.

**Store both series and recompute nothing.** Half the cost and none of the benefit: the
adjusted series still changes underneath a published report, and there is still no record of
which action changed it.

**One `corporate_actions` row per adjustment factor, kind-free.** Collapses splits and
dividends into a single multiplier and loses the distinction between a price adjustment and a
total-return adjustment, which are not the same series and must not be mixed.

**Normalise pence to pounds on ingest.** Makes `close` stop meaning what the exchange printed,
and puts the conversion in the parser — the place least able to record it as a calculation.
