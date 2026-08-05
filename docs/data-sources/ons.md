# Office for National Statistics

UK CPI comes from the ONS rather than from FRED, and the reason is not only the licence.

## Why not FRED's UK series

FRED carries UK CPI as `GBRCPIALLMINMEI`, which is OECD-sourced and carries OECD copyright —
so it fails this project's requirement to hold rights extending to future commercial use of
the software. The ONS publishes the same figures itself under the Open Government Licence.

That happens to also be the more authoritative source: the ONS is the producer, and the OECD
series is a re-publication with its own vintage and revision behaviour. So for once the rights
question and the quality question have the same answer.

## The licence: Open Government Licence v3.0

ONS material may be used and re-used, **commercially or privately, without applying for a
licence**, subject to the OGL's conditions. Reproduction requires source accreditation to the
ONS, which is stored on the `macro_series` row and travels into a report's sources appendix.

Unlike the FCA (ADR 0022) and unlike the Bank of England (ADR 0026), there is no scraping
question here: the ONS operates a **documented public API** at
<https://developer.ons.gov.uk/>, and a client of a documented API is not a crawler.

## What a vintage means here, and what it does not

**The ONS is not an archive.** ALFRED can return a series exactly as it stood on any past
date. The ONS timeseries endpoint returns the *current* series and reports the date of the
release it came from. There is no `realtime_start`.

So a UK observation's vintage is the **release date**, and that is a weaker claim than a FRED
vintage. It says "this is what the ONS was publishing as of that release", not "this is what
it was publishing on your as-of date". The distinction is carried in the schema:
`macro_observations.is_archived` is `true` for ALFRED and `false` for the ONS, so a UK figure
never borrows a US figure's point-in-time guarantee.

The one point-in-time check this source supports honestly is enforced: **a release dated after
the as-of date is refused**. A CPI edition published in September cannot inform a valuation
dated to June, whatever periods its observations cover — those are the revised figures,
published later.

Retrieving true UK vintages would mean reading the previous-version datasets the ONS publishes
per release, which is a larger piece of work and is not done. Until it is, a UK macro figure
used point-in-time carries the limitation rather than pretending otherwise.

## Endpoints and series

`https://api.ons.gov.uk/timeseries/{series}/dataset/{dataset}/data`. A series code is **not**
unique on its own — `D7BT` exists in several datasets meaning different things — so the
registry carries the dataset alongside it and `timeseries_url()` refuses a series without one.

| Key | Series | Dataset | What |
|---|---|---|---|
| `uk_cpi` | `D7BT` | `MM23` | CPI index, 2015 = 100 |
| `uk_cpi_annual_rate` | `D7G7` | `MM23` | CPI twelve-month rate, per cent |
| `uk_cpih` | `L55O` | `MM23` | CPIH, including owner-occupiers' housing costs |

The response carries months, quarters and years in one document. The parser reads the block
matching the frequency the registry declares, because reading the wrong one produces figures
that are real, correctly formed and at the wrong frequency — an error nobody would question.

## Verify before relying on this

As with every other source in this repository, the primary text could not be read from the
build environment: its network policy denies outbound HTTPS to every host. The OGL position
above is corroborated from independent search results rather than read at source. Read
<https://www.ons.gov.uk/methodology/geography/licences> and the developer hub's terms before
treating it as settled.

The adapter is tested against a cassette in `tests/fixtures/macro/ons_cpi.json`, written from
the documented response shape. No live call has been made from this repository.
