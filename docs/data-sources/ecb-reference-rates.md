# European Central Bank — euro foreign-exchange reference rates

The FX source, and it exists because the Bank of England's is closed to this platform.
ADR 0026 determined that the Bank documents a CSV download route for programmatic use and
disallows that same route in its own `robots.txt`; ADR 0045 records the decision to pivot on
the euro instead.

## The licence

ECB material may be re-used, **including commercially**, with the ECB credited as the source.
That satisfies this project's standing constraint on rights extending to future commercial
use of the software. The note stored on every source document says so, and it also carries
the sentence that matters most about what the figures are:

> Euro foreign-exchange reference rates are indicative and not intended for use in market
> transactions.

## The access route: a documented API, and no conflict

The ECB Data Portal serves the `EXR` dataflow over a documented machine-readable API at
`data-api.ecb.europa.eu`. Unlike the Bank of England's IADB, nothing in `robots.txt`
disallows the interface the ECB publishes for programmatic access. A client of a documented
API is not a crawler, which is the same reasoning applied to the ONS.

`requests_per_second` is 2.0 with `honours_robots=False`, matching the other official-
statistics providers. A run needs a handful of currency series at most.

## What the rates are, and what they are not

They are **reference** rates, published around 16:00 CET each working day. They are the
right instrument for translating a reported balance sheet, which is what this platform uses
them for, and the wrong one for anything execution-shaped. No part of the adapter offers a
bid, an ask or an intraday value, and the tier is `T3_OFFICIAL_STATS` rather than
`T4_LICENSED_MARKET` — calling a central bank's published statistic "market data" would
claim a tradability it does not have.

The ECB publishes a row for every calendar day. Weekends and TARGET holidays carry an empty
value, and the parser skips them: a zero there would be a rate, and a catastrophic one.

## Every rate has the euro on one side

This is a property of the source, not a limitation of the adapter. The ECB publishes the
reference rates *of the euro* — one figure per currency, in units of that currency per euro.
So:

* A `USD` observation of `1.0712` means **one euro buys 1.0712 dollars**. The rate converts
  euros into dollars, and its unit is `USD/EUR`.
* A GBP/USD rate **does not exist here**. It is a cross: pounds-per-euro over
  dollars-per-euro, with the euro cancelling.

`aer.calc.fx.cross` performs that division as a `@traced` calculation, so a derived rate is
never mistaken for a published one. It refuses legs from different days, legs sharing no
pivot, and two legs in the same currency.

## Currencies retrieved

The allowlist in `aer.sources.macro.ecb.REFERENCE_CURRENCIES` — currently USD, GBP, JPY,
CHF, CAD, AUD, SEK, NOK, DKK, PLN, CZK, HUF. The ECB publishes around thirty; the registry
is what stops a caller, or a string that reached one, from constructing a request for a
series nobody has ruled on. There is no method that takes a URL.

## Point-in-time

`endPeriod` is set from the run's as-of date so the portal is not asked for observations the
run may not use. **That is a saving, not the control.** `aer.calc.fx.select_rate` applies
the bound again over whatever comes back, because a check that lives only in a query
parameter disappears the moment a response is cached or replayed.

The Data Portal is **not an archive**: it serves the series as it stands, so there is no
vintage to record and `ReferenceRateResponse` does not pretend to have one. For an exchange
rate this matters far less than it does for GDP — reference rates are not revised — but the
type stays honest about it rather than borrowing ALFRED's guarantee.

## Unverified, and how the code handles it

The exact SDMX-CSV column names were **not confirmed against a live response**: this build
environment's egress policy blocks the host. The parser therefore reads by column name,
requires exactly `TIME_PERIOD` and `OBS_VALUE`, ignores every other column, and raises an
`ExternalServiceError` naming the columns it did find when those two are absent — so a wrong
guess is a loud error on the first real fetch rather than silently wrong rates.

**Worth ten minutes on a machine with network access**: fetch the URL that
`reference_rate_url("USD")` builds and look at the header row. If the columns differ, the two
constants at the top of `aer/sources/macro/ecb.py` are the whole change.

## What this does not provide

**A GBP risk-free rate.** The UK proxy is the ten-year gilt yield, published by the Bank of
England, which is the source ADR 0026 closed. `RISK_FREE_SERIES` still has no `GBP` entry and
a sterling valuation still refuses rather than discounting at a US Treasury yield.
