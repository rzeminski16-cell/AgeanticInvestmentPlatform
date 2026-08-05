# FRED and ALFRED (Federal Reserve Bank of St Louis)

**This platform reads ALFRED, not FRED**, and it retrieves an allowlist of series rather than
whatever a caller asks for. Both are deliberate; this note says why.

## What ALFRED is, and why it and not FRED

FRED serves a series as it stands today. ALFRED is the same service with `realtime_start` and
`realtime_end`, which select a **vintage**: the series as somebody could have seen it on a
chosen date.

That distinction is the whole reason this adapter exists. US GDP for the first quarter of
2020 was first published at 21,561.139 billion dollars; by mid-2024, after three revisions
and a rebasing, the same quarter reads 21,727.657. A valuation dated to June 2020 that
discounts using the 2024 figure has used four years of hindsight, and nothing in the output
would show it — a GDP number looks like a GDP number whichever year it was published in.

Omitting the two parameters returns today's data. `aer/sources/macro/fred.py` therefore takes
the vintage as a **required argument** with no default, so there is no code path that can
produce the silent version of this error.

## The licence position: per series, not per provider

The FRED API Terms of Use permit free use and then say:

> Redistributing copyrighted data series for commercial use is not allowed unless the data
> copyright owner authorizes it.

FRED carries both kinds of series, so neither "FRED is fine" nor "FRED is refused" is a
correct determination.

**Redistributable** — works of the US federal government, not subject to copyright under
17 U.S.C. § 105:

| Series | Produced by |
|---|---|
| `GDP`, `GDPC1` | Bureau of Economic Analysis |
| `CPIAUCSL`, `CPILFESL`, `UNRATE` | Bureau of Labor Statistics |
| `DGS10`, `DGS30`, `DGS2`, `FEDFUNDS` | Board of Governors of the Federal Reserve System (H.15) |

**Not redistributable**, and refused by name in `aer/sources/macro/series.py`:

| Series | Copyright | Why |
|---|---|---|
| `CSUSHPINSA` | S&P Dow Jones Indices | Commercial redistribution needs S&P's permission |
| `BAMLH0A0HYM2` | ICE Data Indices | FRED's own page carries the restriction |
| `GBRCPIALLMINMEI` | OECD | OECD copyright, and unnecessary — the ONS publishes UK CPI itself |

The registry is the enforcement, not this document. `series_for()` takes a *key* and refuses
anything not on the list, so a copyrighted series cannot be retrieved by somebody who did not
know it was one. Adding a series means establishing its copyright position first — the
refusal message says so.

**Attribution.** FRED is credited as the distributor and the originating agency as the source.
Both are stored on the `macro_series` row rather than looked up at read time, so a report
published today states the terms it was published under even if the registry changes later.

## The API key

`AER_FRED_API_KEY`, a free key from <https://fredaccount.stlouisfed.org/apikeys>. It is a
`SecretStr` in `Settings` and masks in both `repr()` and `str()`.

**FRED takes it as a query parameter**, which means it is part of every URL, which means it
would otherwise reach every log line, the stored artefact's recorded URL, and a published
report's sources appendix. `aer.sources.macro.client.redacted()` strips it, the client logs
only the redacted form, and a test asserts the real key appears in neither.

It matches on the parameter rather than on the key's value, so a rotated key is still hidden
in an old log line.

## Verify before relying on this

The primary text could not be read from the build environment — its network policy denies
outbound HTTPS to every host, `api.stlouisfed.org` included — so the terms above are
corroborated from independent search results rather than read at source, and **no live call
has ever been made from this repository**. Read
<https://fred.stlouisfed.org/docs/api/terms_of_use.html> and
<https://fred.stlouisfed.org/legal> before treating the licence position as settled, and check
each series' own FRED page for a copyright notice before adding it to the registry.

The first real call happens on the operator's machine. The adapter is tested against cassettes
in `tests/fixtures/macro/`, written from the documented response shape.
