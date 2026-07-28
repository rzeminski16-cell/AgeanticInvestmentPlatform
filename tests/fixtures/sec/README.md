# SEC fixtures

**These are constructed, not recorded.** They reproduce the documented shape of each EDGAR
endpoint faithfully — field names, nesting, the columnar layout of `filings.recent`, the
four-level taxonomy/concept/unit/observation structure of `companyfacts` — but the values
are chosen to exercise specific cases, and they are far smaller than a real response.

Constructed rather than recorded because the sandbox this was built in cannot reach
`sec.gov`. That is a genuine limitation and worth knowing about: these fixtures prove the
parsers handle the *shape* correctly, and they cannot prove the shape is still what the SEC
serves today. Re-record them from the live API before relying on this adapter, and keep the
cases below.

## What each fixture is for

| File | Case |
|---|---|
| `company_tickers_exchange.json` | The columnar ticker file, with an exchange column |
| `company_tickers.json` | The older row-keyed shape, with no exchange |
| `company_tickers_ambiguous.json` | One ticker on two exchanges — resolution must refuse to guess |
| `submissions_msft.json` | A filing index with 10-K, 10-Q and 8-K rows |
| `submissions_ragged.json` | Parallel arrays of differing lengths — must be refused |
| `companyfacts_msft.json` | **The restatement fixture.** FY2020 revenue reported twice, two years apart, with different values |
| `companyfacts_unmapped.json` | A filer extension concept and an unmapped `us-gaap` tag |

## The restatement fixture

`companyfacts_msft.json` is the one that matters. It contains, for the year ended
2020-06-30:

| Value | Accession | Filed | Reported in |
|---|---|---|---|
| 143,015,000,000 | 0000789019-20-000039 | 2020-07-30 | the FY2020 10-K |
| 142,000,000,000 | 0000789019-22-000010 | 2022-07-28 | the FY2022 10-K, restated |

An as-of date in 2021 must return the first. An as-of date in 2023 must return the second.
Both must record the filed date they came from. That is the whole point-in-time claim in
one fixture, and the numbers differ by enough to make a wrong answer obvious.

The FY2020 figure of 143,015,000,000 is Microsoft's actual reported revenue for that year.
The 2022 restatement is invented: Microsoft did not restate it. It exists so the test has
two different values to tell apart.
