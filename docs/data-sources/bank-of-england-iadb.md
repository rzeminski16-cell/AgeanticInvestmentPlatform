# Bank of England Interactive Statistical Database

**This platform does not fetch from the Bank of England — yet, and not because it may not.**
The decision and its reasoning are ADR 0026; this note is the operational summary and what
has to happen before it changes.

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

## The access route: unresolved

The Bank's `robots.txt` disallows `/boeapps/iadb` and
`/boeapps/database/_iadb-FromShowColumns.asp` among others. The CSV endpoint third-party
clients use — `/boeapps/database/fromshowcolumns.asp?csv.x=yes&SeriesCodes=…` — is not one of
the disallowed paths under prefix matching, but published descriptions of *which* endpoint
serves CSV contradict each other, and one of the candidates is disallowed.

The Bank's website terms of use could not be read from the build environment at all: the
network policy blocks outbound HTTPS to every host. Whether they carry an automated-access
prohibition of the kind the FCA's terms do — the clause that decided ADR 0022 — is therefore
**unknown**, not "absent".

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
in `REFUSED_HOSTS`: that list records terms that have been read and found prohibitive, and
nothing here has been found prohibitive. An entry there would assert a determination nobody
made.

## Before this changes

Read three pages, in this order, and record what they say:

1. <https://www.bankofengland.co.uk/legal> — any automated-access, scraper, robot or
   data-mining prohibition?
2. <https://www.bankofengland.co.uk/robots.txt> — confirm the `Disallow` lines verbatim.
3. <https://www.bankofengland.co.uk/boeapps/database/help.asp> — which path serves CSV, and
   is it documented as a route for programmatic use?

If clean: add the provider, allowlist `www.bankofengland.co.uk`, rate-limit conservatively
(this is one series pull per run, not a crawl), parse the CSV, persist rates as facts with
the OGL attribution, and update ADR 0026 with a follow-up.

If not clean: the European Central Bank's euro reference rates are the fallback, at the cost
of crossing non-euro pairs through the euro — which makes a cross-rate a calculation rather
than a published observation, and should be recorded as such.
