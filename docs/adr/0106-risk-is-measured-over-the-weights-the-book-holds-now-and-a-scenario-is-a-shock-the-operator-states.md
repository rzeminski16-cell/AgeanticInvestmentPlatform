# ADR 0106 — Risk is measured over the weights the book holds now, and a scenario is a shock the operator states

**Status.** Accepted
**Date.** 2026-09-03
**Required by.** Roadmap §3.9, and by ADR 0080, which admitted the `risk_analyst` role as
commentary over figures it cannot write and left the figures to ADRs 0003 and 0011.
**Extends.** ADR 0080 (the risk analyst comments on numbers it cannot write), ADR 0083 (a
position is a calculation, not a row), ADR 0082 (a rate is a dated observation), ADR 0097
(a numeral is checked against the figure), ADR 0032 (the adjusted close is not a column).

## Context

ADR 0080 settled what the risk role may say and refused it every number. It named the
figures — exposure, concentration, drawdown, volatility, a position's contribution to the
book's risk, scenario profit and loss, expected shortfall — and said each is forty lines of
Python. §3.2 has since built exposure and concentration as traced calculations over the
book as at a date. Four things the remaining figures need were left open, and each is a
choice with consequences rather than a detail.

**Over what series?** A book has no price. Its history is a walk of every holding against
the price history at every date (ADR 0083), and a year of daily valuations is two hundred
and fifty walks for one screen. The performance service already refuses a time-weighted
return that would need more than 120 of them.

**In which currency?** A sterling book holding a dollar listing moves with the dollar as
well as with the share. Folding the rate into every daily return needs a daily rate series
the rate store does not promise to hold (ADR 0082 stores what was fetched, when).

**What is a scenario?** ADR 0080 forbids the model choosing one. It did not say what one is
or where it lives.

**What stops the commentary restating a figure?** ADR 0080 leans on ADR 0063's contract
shape, which is a report section's. The risk role runs from a page, not from a report.

## Decision

### 1. Risk is ex-ante: the weights the book holds now, over the returns the holdings had

Each holding's daily total-return series over the year to the as-of date is read through
`adjusted_series_for` — the same adjustment a beta uses (ADR 0032) — and turned into daily
returns. **The book's return series is the weighted sum of those, with every weight held at
the value the book shows today**, over the dates every measured holding traded. Cash and
any holding that could not be measured contribute nothing, and the view states what share
of the book the measured holdings cover.

That is the standard ex-ante construction, and it answers the question a risk page asks:
*if the book stayed as it is, how would it have moved?* It is not the book's realised
history, which is the performance page's question and needs the walks. The assumption is
recorded on every figure that rests on it.

**Returns are measured in each listing's own currency.** The currency a position is
exposed to is shown as an exposure band already; folding a rate series into the returns
would fold in a series the store may not hold and hide the currency risk inside a
volatility figure. Stated on the figure, so nobody reads a volatility as though it were in
pounds.

### 2. The figures, each a traced calculation in `calc/risk.py`

- **Annualised volatility** — the square root of the daily variance times 252. The
  variance is `calc/prices.variance`, unchanged.
- **Maximum drawdown** — the worst peak-to-trough fall of an index compounded through the
  book's return series.
- **Expected shortfall** — the mean of the worst five per cent of daily returns. Historical
  and distribution-free, needing at least twenty observations so the tail holds one. The
  tail is a recorded parameter.
- **Beta to the book and risk contribution** — each measured holding's `calc/prices.beta`
  against the book's own series, and its weight times that beta: contributions sum to one
  over the measured holdings, which is the check.
- **Scenario profit and loss** — each holding's value times the shock that reaches it, and
  the total as a share of net assets.

Exposure and concentration are §3.2's and are shown, not recomputed. A holding with fewer
than `MIN_RETURN_OBSERVATIONS` daily returns in the window is *unmeasured*, named as such
with the reason, and excluded from the book's series rather than filled in.

### 3. A scenario is a named set of shocks the operator writes down

`risk_scenarios` belongs to a book and has a name; `risk_scenario_shocks` are its rows,
each a fraction applied to a target: every holding, a sector, a currency, a listing
country, or one holding. A shock's target is matched against the same classification the
exposure bands use, so a scenario about "United Kingdom" reaches exactly what the country
band calls United Kingdom. A holding two shocks reach takes them compounded. A currency
shock reaches cash in that currency, because cash is a position (ADR 0083); the book's own
currency never moves against itself.

No scenario is built in. ADR 0080's reason reaches code as well as the model: a default set
is a claim about what is worth worrying about, made for the operator by whoever wrote the
default. The form is small, and the page says what a scenario is until one exists.

### 4. The commentary's deterministic edge is the numeral check, and the refusal is recorded

The `risk_analyst` role runs in the web process, per book, on its own work order
(`tool="risk"`), after every figure is computed and persisted against the pass. Its input
is the rendered block as strings; its output is three commentaries and nothing else, no
field able to carry a number. Before the pass stores them, code refuses a commentary that
names a numeral the block does not hold (ADR 0097's rule, on this surface) or that reaches
for a size, a limit, an order or a recommendation (ADR 0080's list, as words). One retry
carries the problems back; a second refusal is recorded on the pass as what the model said
and why it was not shown.

## What was rejected

**Valuing the book daily for a realised drawdown.** The walks are the cost the performance
service already refuses past 120 points, and a realised drawdown answers the performance
page's question with the risk page's label.

**A covariance matrix.** Contribution as weight times beta-to-book is the same
decomposition with one series where a matrix has one per pair, and it is what a reader can
check by hand: the contributions add to one.

**A parametric expected shortfall.** A normal tail is a claim about the distribution; the
historical tail is what happened.

**Built-in scenarios.** See §3.

**A risk score.** ADR 0080 reserved `rating` against it, and the page has no field for one.

## Consequences

Risk is the eighth working tool. The page shows the exposure bands and concentration, the
book's volatility, drawdown and expected shortfall with the window and coverage beside
them, each measured holding's volatility, beta to the book and contribution, every stated
scenario's profit and loss, and the analyst's commentary or the reason it was refused. The
work list carries a pass that stopped and a book whose last reading predates its latest
trade.

The cost is that every figure is *as if the book stayed as it is*, said plainly on the page,
and a volatility in listing currency that understates a sterling book's dollar risk by
exactly the currency band's share.
