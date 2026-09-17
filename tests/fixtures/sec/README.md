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
| `companyfacts_bank.json` | **The missing top line.** A filer whose only ASC 606 tag is fee income, and whose revenue must be assembled from the spread and non-interest income (ADR 0114) |

## The bank fixture

`companyfacts_bank.json` is M&T's shape rather than its full filing history: net interest
income and non-interest income for two years, the ASC 606 caption for one, and the net
income and assets the plausibility guard reads. The FY2025 figures are the ones ADR 0114
records — $6,948m, $2,742m and the $1,657m contract revenue that reached the front page as
"revenue" and produced a 172.1% net margin.

FY2024 deliberately carries both halves and no ASC 606 caption, so a run over this fixture
derives two years and would have shown a revenue line for neither.

## The restatement fixture

`companyfacts_msft.json` is the one that matters. It contains, for the year ended
2020-06-30:

| Value | Accession | Filed | Reported in |
|---|---|---|---|
| 143,015,000,000 | 0000789019-20-000039 | 2020-07-30 | the FY2020 10-K |
| 142,000,000,000 | 0000789019-22-000010 | 2022-07-28 | the FY2022 10-K, restated |

Selection must return the second — the latest filing's word on the period (ADR 0113) — and
record the first as superseded by it, naming the accession that replaced it. Both must
record the filed date they came from. That is the whole selection claim in one fixture,
and the numbers differ by enough to make a wrong answer obvious.

The FY2020 figure of 143,015,000,000 is Microsoft's actual reported revenue for that year.
The 2022 restatement is invented: Microsoft did not restate it. It exists so the test has
two different values to tell apart.
