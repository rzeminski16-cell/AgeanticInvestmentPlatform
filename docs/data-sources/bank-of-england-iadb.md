# Bank of England Interactive Statistical Database

**This platform does not fetch from the Bank of England, and the reason is now settled.**
The Bank documents a CSV download route for programmatic use *and* disallows that same route
in its `robots.txt`. The determination and its evidence are in ADR 0026's Resolution section;
this note is the operational summary.

## What the IADB is

`bankofengland.co.uk/boeapps/database` is the Bank's Interactive Statistical Database: the
published home of its statistical series, including the daily spot exchange rates this
platform wants (`XUDLUSS`, `XUDLGBD`, `XUDLERS` and the rest of the `XUDL*` family), Bank
Rate (`IUDBEDR`), and gilt yields. Free, no key, no registration.

## The licence: clear

Reproduction is under the **UK Open Government Licence**, which permits commercial use with
attribution. That satisfies this project's standing requirement that every source hold rights
extending to future commercial use of the software. Attribution belongs in the source
document's `licence_note` when an adapter is built.

## The access route: resolved, and the answer is no

Read at source on 2026-08-05.

**The terms carry no automated-access prohibition.** Unlike the FCA's (ADR 0022), the Bank's
legal terms contain no scraper, robot or data-mining clause. Restrictions on automated access
are expressed through `robots.txt`, reuse is licence-dependent, and excessive use may get an
address blocked. This is why `bankofengland.co.uk` is **not** in `REFUSED_HOSTS` — that list
is for publishers whose terms forbid automated access.

**But `robots.txt` disallows the download route.** Confirmed verbatim:
`/boeapps/database/ShowChart.asp`, `/boeapps/database/_iadb-FromShowColumns.asp`,
`/boeapps/iadb`, `/boeapps/titan`, plus `/error`, `/forms`, `/mfsd`, `/search` and
`/test-folder`.

**And the underscored handler is the real one.** The endpoint that serves CSV, and that the
Bank documents for automatic parameterised downloads, is
`_iadb-FromShowColumns.asp` — the disallowed one. The plain `fromshowcolumns.asp` viewer is
not on the list, but it is a viewer: it redirects to, proxies or invokes the handler that is.
Using it *because* it is unlisted would be circumventing a stated restriction to reach content
the publisher's own machine-readable policy asks automated clients to leave alone.

## What is built anyway

`aer/calc/fx.py`, in full. It is pure conversion arithmetic and does not care where a rate
came from:

- `select_rate(...)` — the most recent observation on or before the as-of date. Anything
  later raises `LookAheadRateError`; nothing older than `MAX_STALENESS_DAYS` (7) is used.
- `convert(...)` / `convert_at(...)` — `@traced`, so a converted figure resolves to a
  calculation with the rate as a recorded input. A rate applied upside down raises.
- `invert(...)` — the same observation the other way round, keeping its source.
- `round_trips(...)` — whether out-and-back reproduced the original within
  `ROUND_TRIP_TOLERANCE`.

A rate supplied by hand today works exactly as one fetched tomorrow will, because both are
`FxRate` objects carrying a `SourceRef`.

## What is not built

No `Provider` member, no allowlisted host, no adapter, no rate limit. The host is also **not**
in `REFUSED_HOSTS`: that list records publishers whose *terms* forbid automated access, and
the Bank's do not. An entry there would assert something the terms do not say, and would
wrongly refuse the rest of the Bank's site along with it.

## The enforcement

`tests/test_robots.py::TestTheBankOfEnglandDetermination` pins the `robots.txt` above and
asserts what it means: the documented handler is refused, the database root is refused, the
viewer path is not treated as a way round it, and the rest of the Bank's site — speeches, the
Financial Stability Report, statistical releases — stays fetchable, because the refusal is a
set of paths rather than a publisher.

## What would change this

1. **Ask the Bank's Data and Statistics Division.** A documented download route disallowed by
   the same organisation's `robots.txt` is plausibly an oversight, and it is theirs to
   resolve. Written consent, or an amended `robots.txt`, closes this outright: the adapter is
   then a provider, an allowlisted host, a conservative rate limit and a CSV parser, and the
   arithmetic it feeds is already built and tested.
2. **The UK Debt Management Office** publishes gilt prices and yields. Its terms have **not**
   been examined, and nothing here should be read as suggesting they are permissive.
3. **The ECB's euro reference rates**, crossing non-euro pairs through the euro — which makes
   a cross-rate a calculation rather than a published observation, and would have to be
   recorded as such.

Until one of those lands, a rate supplied by hand works exactly as a fetched one will: both
are `FxRate` objects carrying a `SourceRef`.
