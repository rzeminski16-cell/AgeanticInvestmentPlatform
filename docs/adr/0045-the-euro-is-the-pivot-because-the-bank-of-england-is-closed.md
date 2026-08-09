# ADR 0045 — The euro is the pivot, because the Bank of England is closed to us

**Status.** Accepted
**Date.** 2026-08-09
**Extends.** ADR 0026, whose determination stands unchanged. This does not reopen it.

## Context

ADR 0026 shipped `aer/calc/fx.py` complete — rate selection with a look-ahead refusal, a
staleness limit, traced conversion, inversion, round-trip checking — and deliberately
shipped **no fetcher**. The Bank of England's Interactive Statistical Database is the
natural source for sterling rates and its licence is permissive (Open Government Licence,
commercial use expressly allowed), but its `robots.txt` disallows
`/boeapps/database/_iadb-FromShowColumns.asp`, which is the very handler the Bank documents
for programmatic CSV download. Both statements are the Bank's and they conflict.

That determination was made against the terms and the `robots.txt` as read by the operator
on a machine that can reach them. It has not changed and this ADR does not change it.
Reaching the data through the unlisted viewer path *because* it is unlisted would still be
circumventing a stated restriction, and remains prohibited.

The consequence was that every exchange rate had to be supplied by hand. The arithmetic
worked; there was nothing to feed it.

## Decision

**Take the euro reference rates from the ECB Data Portal, and treat every non-euro pair as
a cross-rate computed here.**

The ECB publishes daily euro foreign-exchange reference rates through a documented
machine-readable API at `data-api.ecb.europa.eu`, under terms that permit re-use including
commercially with the ECB credited. There is no conflict between what it documents and what
it disallows: the API is the published interface, and nothing in `robots.txt` reaches it.

`aer/sources/macro/ecb.py` is the adapter. It follows the same two rules the other source
clients follow. No method takes a URL — every one takes a currency code that must be in
`REFERENCE_CURRENCIES`, and the URL is built from it. And the response is parsed by column
name, requiring exactly `TIME_PERIOD` and `OBS_VALUE` and ignoring the rest.

## What follows from the euro being on every rate

The ECB publishes the reference rates *of the euro*: one figure per currency, in units of
that currency per euro. **A GBP/USD rate does not exist at this source and never will.**
Getting one means dividing pounds-per-euro by dollars-per-euro, and that division is not a
detail of implementation — it is the difference between a published observation and a
derived one.

So `aer.calc.fx.cross` is a `@traced` calculation rather than a helper function. A reader
following a converted figure back reaches two source documents and a formula, rather than a
number that looks as though somebody published it. Three refusals fall out of the same
reasoning and each is enforced:

* Legs from **different days** are refused. Both come from one daily publication, so a
  mismatch means one was selected wrongly, and the cross would be a rate nobody could have
  transacted at.
* Legs with **no shared pivot** are refused. Dividing them produces a unit no amount can be
  converted into, which the unit algebra would catch later and less clearly.
* Legs in the **same currency** are refused, because the answer is one.

The recorded assumptions say what a cross is not: no bid-offer spread and no cross-currency
basis are modelled. A reference rate is not a market rate — the ECB says so itself, and the
licence note carries the sentence — so nothing here should be read as executable. For
converting a reported balance sheet, which is what this platform does with them, that is
exactly the right instrument.

## What this does not fix

**The GBP risk-free rate is still missing, and this ADR must not be read as having supplied
one.** `RISK_FREE_SERIES` has no `GBP` entry because the UK proxy is the ten-year gilt
yield, published by the Bank of England, which is the source ADR 0026 closed. The ECB
publishes exchange rates, not gilt yields. A sterling valuation still refuses at
`risk_free_series_for("GBP")` rather than discounting at a US Treasury yield, and that
refusal is correct. `TestTheBankOfEnglandStaysRefused` pins both halves of this.

ADR 0026's three routes out of that gap remain open and remain unattempted: written consent
from the Bank's Data and Statistics Division, a terms determination on the UK Debt
Management Office, or an operator-supplied series.

## The one thing that could not be verified from here

**The exact SDMX-CSV column names were not confirmed against a live response.** This build
environment's egress policy returns 403 for `data-api.ecb.europa.eu`, as it does for the
Bank of England and the DMO, so the fixture in `tests/fixtures/macro/` is written to the
documented SDMX-CSV shape rather than recorded from the wire.

The parser is built around that uncertainty rather than in spite of it. It reads by column
name, needs exactly two columns, ignores every other, and raises an `ExternalServiceError`
naming the columns it actually found when those two are absent. So the failure mode of a
wrong guess is a loud, specific error on the first real fetch — not silently wrong rates.
`test_a_response_with_no_observation_columns_is_an_error_not_an_empty_series` is what keeps
that true.

**This is worth ten minutes of an operator's time on a machine with network access**: one
request to the URL `reference_rate_url("USD")` builds, and a look at the header row. If the
columns differ, the two constants at the top of `ecb.py` are the whole change.

## Consequences

A run that needs to compare a sterling reporter with a dollar one can now do so from
fetched, hashed, citable evidence rather than from a hand-typed number. Every conversion
remains a recorded calculation, every rate remains point-in-time filtered by `select_rate`
after parsing — the `endPeriod` bound on the request is a saving and a courtesy, never the
control — and a cross carries its own lineage back to two published figures.

The euro becomes a load-bearing intermediary in sterling-to-dollar arithmetic, which is a
real modelling choice and is why it is written down here rather than left implicit in a
division.
