# ADR 0121 — A UK filer is acquired from its own accounts, not from an aggregate

**Status.** Proposed — V1.0_Alpha. Accepted when the change it argues lands.
**Date.** 2026-09-14
**Extends.** ADR 0058 (a dimensioned fact is a different observation) and ADR 0021's offline
iXBRL control, both of which were built for UK filings and have never been pointed at one.
**Amends.** ADR 0029 (the sector block is a type, not a check) — the block still works; what
changes is that it must be reachable from a second classification scheme.
**Required by.** Open question 6, answered by the operator on 14 September 2026: *cover both
UK and US.* The product documentation says "UK or US" and a domestic London listing cannot be
researched at all.

## Context

A company listed in London that files only with Companies House cannot get past `acquire`.
The step resolves every subject against EDGAR's ticker list, and a company with no SEC filings
is refused there. AstraZeneca works because it files a 20-F; Tesco does not exist as far as
this platform is concerned.

**Most of the UK half is already built, and that is the surprise.** Reading the code rather
than the plans:

| | State |
|---|---|
| `sources/uk/companies_house.py` | **Complete.** `resolve_entity` (search, then *confirm* — an ambiguous match is refused rather than taking the first hit), `fetch_profile`, `fetch_filing_history`, `fetch_document`, `discover_documents`. 32 tests against replayed fixtures |
| The fetch policy | **Cleared.** `.companieshouse.gov.uk` and `.company-information.service.gov.uk` are allowlisted; the rate limit (600 requests / 5 minutes) was verified against the developer specifications on 2026-09-04 and the bucket is set under it |
| Credentials | **Wired.** `runtime.py` builds the Basic auth header and attaches it per provider; the key never reaches a call site |
| `extract/ixbrl.py` | **Built for this.** Offline arelle — a control, not a setting: an iXBRL document names its taxonomy by URL and arelle's default is to fetch it, which would be a component other than `aer.fetch` making a request driven by an untrusted document |
| `companies.company_number` | **Exists**, `String(16)`, unique, and the `has_a_registry_identifier` check is `cik IS NOT NULL OR company_number IS NOT NULL` — a CIK-less UK company is already a legal row |

That last line corrects something this plan has said twice: that the company-number column
*fails a check constraint today*. It does not. The constraint was written for exactly this
case. The UK path needs no migration on `companies`.

**What is missing is four things, and only one of them is large.**

1. **`CompaniesHouseClient` has no `fetch_facts`.** It satisfies two thirds of the
   `SourceAdapter` protocol and not the third. This is not an oversight — see below.
2. **`acquire` names `sec_client` directly** and calls `fetch_company_facts`.
3. **Every sector profile's `sic_prefixes` are US SIC codes** — `602` for banks, `6798` for
   REITs, `737` for software. UK SIC 2007 is a different scheme with different codes.
4. **There is no GBP risk-free series.** `RISK_FREE_SERIES` holds `USD` only, and
   `risk_free_series_for` refuses rather than defaulting, because the Bank of England's
   `robots.txt` disallows the very CSV handler the Bank documents for programmatic downloads
   (ADR 0026's Resolution).

## The thing that shapes the whole decision

**Companies House has no companyfacts.**

EDGAR publishes, per registrant, one JSON document containing every figure the entity ever
tagged — a decade of history in a single fetch. `acquire` is built around it: one call gives
the platform its fact base, and the annual report is fetched afterwards for prose and for
segments.

Companies House publishes a **filing history** and, per filing, a **document**. There is no
aggregate. A UK filer's numbers exist only inside its accounts, as inline XBRL, one accounting
period at a time.

So a UK acquisition is not the same shape as a US one with a different client behind it. It is
*n* fetches and *n* parses, one per year of history wanted, and every fact the platform holds
about the company is the output of its own extractor rather than of the registry's aggregation.

## Decision

### 1. Acquisition dispatches on the resolved registry, not on a typed string

`acquire` stops naming `sec_client`. It asks a resolver which registry can identify the
subject, and takes the `SourceAdapter` for that registry. The resolution is recorded on the
request beside `company_id`, so a run's record says which registry answered and a replay two
years later reads it from the row rather than from the code version that was deployed.

A subject that resolves in both — a company with a London primary listing and a US ADR — is
**not** resolved automatically. It is the dual-listing case the portfolio already refuses to
guess at (`ADR 0093`), and it is refused here the same way: both choices named, the operator
picks. A holding priced and researched off the wrong listing is a report nothing downstream
can reconcile.

### 2. A UK filer's facts come from its own accounts, and the depth is a stated number

`CompaniesHouseClient.fetch_facts` is implemented as: take the accounts filings from the
filing history, newest first, fetch and hash each document, run `extract_ixbrl` over it, and
return the union of the facts.

- **How many is a setting with a default of four**, which on an annual filer is four years of
  history — enough for a growth series and a margin trend, and bounded so that a UK
  acquisition's cost is predictable rather than a function of how long the company has
  existed.
- **Each document is an artefact**, hashed and stored, exactly as a 10-K is. Invariant 1 is
  untouched: a UK fact traces to the accounts document it was tagged in.
- **Extraction that carries unmapped tags does not silently drop them.** The existing
  `needs_confirmation` verdict already covers this and it will fire more often here than on
  US filings, because UK filers extend the taxonomy routinely. That is what the unmapped-concept
  gate is for, and it is an operator surface rather than a failure.

### 3. The sector gate learns a second classification scheme

`companies` gains `sic_scheme` (`us_sic` or `uk_sic_2007`), and `SectorProfile` carries its
prefixes per scheme rather than as one tuple. `propose_from_sic` takes the scheme along with
the code.

**This is the part that must not be skipped, and the audit is why.** M&T Bank met no sector
gate because no run resolved the filer's industry code, so a bank took the standard model —
the exact thing ADR 0029 exists to forbid, and the reason its published net margin was 172.1%.
Shipping the UK path with US-only prefixes reproduces that hole for every UK bank, insurer and
REIT: the code would be present, match nothing, propose nothing, and the gate would not fire.

A scheme with no prefixes seeded for a profile proposes nothing **and says so** in the
rationale, rather than proposing "ordinary".

### 4. A sterling valuation needs a sterling risk-free rate, and will not borrow one

`risk_free_series_for` keeps refusing, and the refusal keeps naming why. For V1.0 the gilt
yield arrives as an **operator-owned assumption** — the mechanism the assumption gate already
has, where a value is proposed with its source, confirmed by a person, and recorded with both.
That is how the readiness audit supplied the US risk-free rate to its own runs, it needs no new
publisher and no new licence question, and it puts the number in front of the operator with its
provenance instead of behind an automated series they never see.

**An automated GBP series is a follow-up, not a prerequisite**, and it has a named candidate:
the OECD long-term government bond yield for the United Kingdom, republished by FRED, which is
already a wired source with a cleared licence. It is not adopted here because the series
identifier, its frequency and its terms have not been verified against the primary source, and
this repository does not adopt a data series on a recollection. Verifying it is a
commercial-check item in the roadmap's own sense.

**Nothing is substituted, ever.** A sterling valuation discounted at a US Treasury yield is
wrong by the whole of the rate differential and looks entirely ordinary, which is the failure
mode this platform is built to make impossible.

### 5. The claim in the documentation becomes true when the path ships

Until then, `docs/product/what-it-is.md` and `docs/users/getting-started.md` say what is
supported: a US registrant, or a foreign private issuer filing a 20-F. The operator's decision
is to build the path rather than narrow the sentence; the sentence still may not run ahead of
the code.

## What is given up, named rather than discovered

**Less history, and it costs more to get.** Four accounts documents is four fetches and four
arelle parses against one JSON. A deeper history is a setting away and is linearly more
expensive.

**Much less timeliness.** A UK company files annual accounts and, if listed, half-year
results. There is no 10-Q and no 8-K stream, so the recent-developments section on a domestic
UK filer will lean on the issuer adapter and on regulatory primaries rather than on the
registry. A UK report will be *quieter* than a US one, and the honest thing is to say so on the
report rather than to let the reader assume symmetry.

**More unmapped concepts.** UK filers extend the taxonomy routinely. The concept map's UK
coverage starts at nothing and is grown by curation sittings, which is a real recurring cost
and is already a roadmap item (§2.8).

**Comparables get harder.** A UK subject's peers are UK filers with the same acquisition cost
each. Peer acquisition is already deferred (open question 15); this makes it more deferred, not
less.

## Consequences

**The offline UK refusal test inverts.** `audit/smoke.py` currently proves that a domestic LSE
ticker is refused at `acquire`, and that proof was finding 1 of the readiness audit. It becomes
the opposite assertion: the same ticker resolves against Companies House, and the refusal test
is rewritten to cover a company that exists in neither registry.

**A fourth subject joins the test corpus**, and it is the first one whose facts the platform
extracted itself rather than received pre-aggregated. That makes it the most valuable subject
in the corpus for exactly that reason.

**`fetch_facts` becomes the boundary worth testing hardest.** It is the one place where a
figure's provenance changes shape: on a US run a fact comes from the registry's own
aggregation; on a UK run it comes from this platform's parse of a document. A parsing error is
a wrong number with a perfect audit trail, which is the failure `calc/plausibility.py` was
written for after the last one.

## Alternatives considered

**Route UK companies through their US listing.** This is what happens today, and it works for
the ~30 UK companies with an ADR and a 20-F. It does nothing for a domestic-only filer, which
is the entire gap, and it reports a sterling business in dollars.

**Buy a data vendor with UK fundamentals.** Faster, and it breaks the invariant the product is
built on: a vendor's normalised figure is not traceable to a filing, and "every number traces
to a hashed artefact" would become "every number traces to somebody else's database".

**Ship the adapter without the sector scheme.** Half the work, and it reproduces the 172.1%
defect on the first UK bank. Refused.

**Defer it again.** It has been deferred since the platform's first plan, and the product
documentation has claimed it the whole time. The operator's decision is to make the claim true.
