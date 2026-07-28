# SEC EDGAR

The primary US fundamentals source. Free, complete for US registrants, and — uniquely
among free sources — genuinely point-in-time.

## Why it is the core US source

Every fact EDGAR returns carries the accession number and the filing date of the document
that reported it. That makes "what did this company say its FY2020 revenue was, as at
March 2021?" a question with a determinate answer rather than an approximation. Paid
vendors generally serve *restated* figures, which are cleaner and wrong for any
backward-looking analysis.

## Endpoints used

| Endpoint | Purpose | Module |
|---|---|---|
| `www.sec.gov/files/company_tickers_exchange.json` | ticker + exchange → CIK | `sources/sec/tickers.py` |
| `data.sec.gov/submissions/CIK##########.json` | filing history with dates | `sources/sec/submissions.py` |
| `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | every XBRL fact ever tagged | `sources/sec/companyfacts.py` |
| `www.sec.gov/Archives/edgar/data/<cik>/<accession>/<doc>` | the filing documents | `sources/sec/client.py` |

Not used yet: the frames API (peer screening) and full-text search (`efts.sec.gov`).

## Access conditions

**A descriptive `User-Agent` is mandatory.** The SEC requires it to identify the operator
and makes it a condition of access. `AER_HTTP_USER_AGENT` has no default for exactly this
reason — a shared placeholder would get everyone using it blocked together.
`SafeFetcher` refuses to construct without one.

**The rate limit is roughly 10 requests per second, aggregated across `sec.gov`.**
Exceeding it gets an IP **blocked**, not throttled, and the block affects everything on
that machine for as long as it lasts. Two controls apply:

- `fetch/policy.py` sets the shared Redis token bucket to **8/s**, leaving headroom for
  clock skew between workers sharing the bucket.
- `sources/sec/client.py` adds a **100 ms pause between sequential requests**, so one loop
  cannot spend the whole allowance in a burst and leave nothing for a concurrent run.

`robots.txt` is not consulted for these endpoints: access is by a documented API contract
rather than by crawling. `honours_robots=False` in the policy records that.

## Licence

US Government work, not subject to copyright in the United States. Freely usable including
commercially. The licence note is attached to every `source_documents` row at acquisition:

> US government work; not subject to copyright in the United States. Access is conditional
> on sending a descriptive User-Agent identifying the operator.

## Coverage and its edges

**Covered.** Any company that files with the SEC — every US registrant, plus foreign
private issuers filing 20-F or 40-F.

**Not covered.** A UK company with no US listing files nothing with the SEC and does not
appear in the ticker file at all. Resolution fails with a message saying so rather than
returning an empty result three steps later. UK fundamentals need Companies House, the FCA
NSM, or the issuer's own iXBRL accounts.

**XBRL only from around 2009.** Large filers were phased in from 2009 to 2011; smaller
ones later. `companyfacts` for an older period may be empty or partial even though the
filing exists in the archive.

**`filings.recent` is roughly the last 1,000 filings.** Older ones live in additional files
listed under `filings.files`. Those references are parsed and exposed; fetching them is not
yet implemented, so deep history needs one more request that nothing currently makes.

## Known quirks that shaped the code

**`filings.recent` is columnar, not a list of records.** It is a set of parallel arrays,
where row *i* of a filing is element *i* of every array. Arrays of differing lengths would
zip into an index attributing filings to the wrong dates — wrong in a way nothing
downstream could detect. Lengths are checked before anything is zipped, and a mismatch is
refused.

**The archive URL format differs from every other endpoint.** Archive paths want the CIK
with leading zeros *stripped* and the accession with dashes *removed*; everything else
wants them padded and dashed. Built in one place (`Filing.url`) rather than formatted at
each call site.

**Values exceed what a float can represent exactly.** Revenue in raw dollars routinely
passes 2⁵³, above which a float cannot represent consecutive integers. `json.loads` is
called with `parse_int=Decimal` and `parse_float=Decimal` so nothing round-trips through
binary floating point.

**Tagging is inconsistent across filers and across time.** ASC 606 replaced the revenue
tags in 2018, so filings either side of it use different names for the same line. A filer
may also tag one number under two names in a transition year. `core/concepts.py` holds the
alias map; `sources/sec/pit.py` resolves the duplicate-tagging case deterministically and
records it as such rather than calling it a restatement.

**Filer extension namespaces are skipped.** A concept defined by one filer (`msft:...`)
cannot be compared across companies. Those are counted and reported in
`CompanyFacts.extension_concepts`, not turned into facts — so "why is there no segment
revenue?" has an answer.

**Unmapped shared-taxonomy tags are kept.** A `us-gaap` tag with no canonical concept still
produces a fact, under its raw tag, and is listed in `CompanyFacts.unmapped`. Dropping it
would silently lose real data whenever the alias map falls behind the taxonomy.

## The aggregate endpoints are not citable documents

`companyfacts`, `submissions` and the ticker file are **generated on request** from
whatever exists at that moment. They have no publication date of their own, so they are
recorded with `publication_date = NULL` and are therefore quarantined under point-in-time
rules.

That is correct rather than unfortunate. You do not cite "the companyfacts endpoint" as
evidence for a claim — you cite the **filing**, identified by its accession number. Every
fact parsed out of the aggregate carries that accession, and the filing itself, when
fetched, has a real filing date and is fully admissible.

## Recording provenance is the caller's job

`SecEdgarClient` holds no database session, deliberately: it can be tested without one, and
the fetch-and-parse layer stays free of persistence concerns. `SafeFetcher` archives every
response to the artefact store regardless, so the bytes are always on disk — but the
`artefacts` and `source_documents` **rows** are created only when a caller passes the
`FetchResult` to `aer.services.acquisition.record_acquisition`.

A consequence to be aware of: a run that fetches the ticker file, the submissions index and
`companyfacts` puts three artefacts on disk and creates rows only for the ones its caller
chose to record. The orchestration that records every acquisition of a research run arrives
with the Phase 1 vertical slice; until then, callers record explicitly. Little is lost
either way — the store is content-addressed, so an unrecorded artefact is still hashed,
deduplicated and re-readable — but a retention policy will eventually need to reconcile the
two.

## Point-in-time selection

The rule, implemented in `sources/sec/pit.py`:

> Group facts by `(concept, unit, period_end, fiscal_period)`. Discard every fact filed
> after the as-of date. From what remains, choose the one filed **latest**.

Ties are broken by accession, then by raw tag, so the result never depends on input
ordering. Every input fact appears exactly once in the output, either in `chosen` or in
`rejected` with one of three reasons:

| Reason | Meaning |
|---|---|
| `filed_after_as_of_date` | Did not exist yet. Using it would be look-ahead bias. |
| `superseded_by_later_filing` | A later filing, still within the as-of date, restated it. |
| `duplicate_tagging_in_same_filing` | One filing tagged the same number twice. |

Only the `as_reported` basis is implemented. `select_point_in_time` **raises** if asked for
`restated`, because a convenience function for selecting restated figures is a convenience
function for introducing look-ahead bias.

## Testing

Every test runs against fixtures in `tests/fixtures/sec/`; none touches the network, and
`no_real_sockets` fails any test that tries. **The fixtures are constructed rather than
recorded** — see `tests/fixtures/sec/README.md`. They prove the parsers handle the
documented shape; they cannot prove the shape is still what the SEC serves. Re-record them
against the live API before relying on this adapter in anger.

## References

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC Developer Resources](https://www.sec.gov/about/developer-resources) — access conditions and rate limits
- [EDGAR Full-Text Search FAQ](https://www.sec.gov/edgar/search/efts-faq.html)
