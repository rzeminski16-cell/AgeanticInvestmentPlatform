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

---

## What the register actually holds — read on 18 September 2026, before the code

This section is appended rather than folded in: the decision above was argued on a premise,
the premise was tested against the live register while §1 was being built, and it did not
hold. **This ADR stays Proposed until the operator decides what follows.**

### 1. A listed company's filed accounts are a PDF, not inline XBRL

The sentence the whole of §2 rests on — *"A UK filer's numbers exist only inside its accounts,
as inline XBRL, one accounting period at a time"* — is true of the small and medium companies
that file through accounting software. It is not true of the companies this platform
researches. The newest three accounts filings of four London-listed companies, read from the
register's own filing-history and document-metadata endpoints:

| Company | Number | Newest three accounts filings |
|---|---|---|
| Tesco PLC | 00445790 | `application/pdf` 14.3 MB, 15.0 MB, 15.7 MB — all `paper_filed` |
| Barclays PLC | 00048839 | `application/pdf` 31.7 MB, 35.9 MB, 31.7 MB — all `paper_filed` |
| AstraZeneca PLC | 02723534 | `application/pdf` 16.9 MB, 18.8 MB, 18.2 MB — all `paper_filed` |
| Greggs PLC | 00502851 | `application/pdf` 8.6 MB, 8.4 MB, 8.1 MB — all `paper_filed` |

Twelve filings, two indices of the market, four filing agents, and **not one inline-XBRL
resource among them**. `resources` carries the single key `application/pdf` in every case.

**Inline XBRL at this register is real, and it belongs to the companies nobody researches.**
The same endpoint, for four small active companies picked out of a name search:

| Company | Number | Newest accounts filing |
|---|---|---|
| JOINERY LIMITED | 03637467 | `application/pdf` 20.8 KB **and `application/xhtml+xml` 19.6 KB** |
| ASHWOOD & BIRCH BESPOKE JOINERY LTD | 10774560 | pdf 21.3 KB **and xhtml 19.7 KB** |
| JOINERY AND BUILDING SOLUTIONS LTD | SC644321 | pdf 30.4 KB **and xhtml 24.3 KB** |
| JOINERY & CONSTRUCTION SUPPLIES LTD | SC360429 | pdf 90.5 KB **and xhtml 146.8 KB** |

All four are `paper_filed: false` — filed through accounting software, which is what produces
the tagged copy. So `fetch_facts` is not wrong; it is right about the wrong companies.

Two consequences for the code as it stands. The document endpoint **content-negotiates** —
`resources` names what each filing has — and the client asks for neither, so it takes whatever
the register serves. It should ask for `application/xhtml+xml` and read "no xhtml resource" as
*this filing is not tagged*, which is a fact about the filing worth recording rather than an
extraction failure to log four times.

So `fetch_facts` as specified — four filings deep, `extract_ixbrl` over each — would fetch
about 60 MB per run and return **no facts at all**, logging four documents it could not read.
The code is not wrong; the premise it was written against is.

**This does not touch §1 or §3.** Dispatching on the register, recording which register
answered, resolving a company number, the UK SIC scheme and the sector gate are all unaffected
and all still needed: a UK run can identify its subject, acquire its statutory accounts as
hashed artefacts, excerpt them for citation, and read the company's own classification from
the profile endpoint. What it cannot do from this register is get *tagged numbers*.

**What replaces it is a decision, not a fix.** Four candidates, none of them free:

1. **The issuer's own ESEF annual financial report**, under `ISSUER_IR`. Since ESEF, a London
   -listed issuer's annual report is published as inline XBRL, and issuers put it on their own
   investor-relations site. Already an allowlisted provider, already discovered per issuer.
   Costs a discovery step and lands at the issuer tier rather than the regulatory one.
2. **The licensed feed's fundamentals** for LSE tickers. Fastest, and it is the alternative
   this ADR already refuses above: a vendor's normalised figure is not traceable to a filing.
3. **Parse the figures out of the PDF.** Puts arithmetic-grade numbers behind a heuristic
   table reader, which is the one thing this repository's first rule forbids.
4. **Reopen ADR 0022** and seek the FCA's written consent for NSM access. The NSM is where a
   UK issuer's ESEF report is *required* to land; ADR 0022 refused it on the FCA's terms of
   use, and only written consent changes that.

### 2. The document endpoint redirects to a host the fetch policy refuses

`document-api.company-information.service.gov.uk/document/{id}/content` answers **302 to a
pre-signed `s3.eu-west-2.amazonaws.com` URL**, and `SafeFetcher` checks the allowlist on every
redirect hop. The first real UK document fetch therefore raises `UrlNotAllowedError`. Thirty-two
offline tests pass over it because `respx` returns the body where the register returns a
redirect.

Allowlisting the S3 host for `COMPANIES_HOUSE` would be wider than it looks: that host serves
every AWS customer's bucket in that region, so any URL redirecting there would be admitted
under this platform's most trusted provider. The narrower control — admit a hop because of the
host it came *from* — does not exist in `policy.py` today. It is a security control either
way, so it is the operator's to approve and it needs its own ADR.

### 3. What the operator decided, 18 September 2026

Both questions were put with what each option admits, and both were answered the same day.

**Where a London-listed company's numbers come from: its own ESEF annual financial report.**
Since ESEF, a UK issuer's annual report is published as inline XBRL, and issuers put it on
their own investor-relations site — `ISSUER_IR` is already an allowlisted provider with a
licence position and a per-request host admission. It costs a discovery step per issuer and
lands at the issuer tier rather than the regulatory one, and the numbers stay traceable to the
company's own tagged document, which is the invariant that matters. The licensed feed was
refused for the reason this ADR already gives; the NSM stays refused under ADR 0022.

**The redirect: the narrow rule.** Landed the same day as **ADR 0127**, which also closed a
credential defect and a content-negotiation defect that the first successful request exposed.
Companies House documents are fetchable, the tagged copy is asked for by type, and a filing
with no tagged copy is recorded as untagged rather than downloaded as a scan.

**So §2 of this record is superseded in substance**: `fetch_facts` stays, correct for a filer
that files through software, and it is no longer where a *listed* company's numbers come from.
The ESEF route is a decision of its own and gets its own record when it is built.

### 4. What was built anyway, because it holds under every option above

The register vocabulary (`registry_of`), `research_requests.register`, the client's resolution
by registered name and by company number, the profile's SIC codes and accounting reference
date, `upsert_company` writing a company number rather than a CIK, and `acquire_accounts`.
None of it assumes the documents are tagged.

The dispatch in `acquire` was **held** while the questions above were open, so that a UK run
refused at the request form rather than failing three layers down on a redirect. With both
answered it is no longer blocked, and it lands with the ESEF route it now has to reach for.

### 5. The dispatch, and why it landed without the ESEF route — 18 September 2026

The ESEF route did not survive being probed either (ROADMAP §3.19 item 25), and what replaced
it is **ADR 0128**: a run that cannot succeed does not start. That check asks Companies House,
before the job exists, whether a UK subject's newest accounts are tagged, and admits the run
when they are.

Which is exactly why the dispatch could not stay held. The check's own rule is that it asks the
question the run will ask; a run admitted against Companies House and then resolved against
EDGAR would be the defect the check was built to prevent. So `acquire` and `extract` both split
on `registry_of(exchange)`:

- **`acquire`** resolves a UK subject at Companies House, reads its profile for the UK SIC 2007
  codes and the accounting reference date, writes `register = companies_house` on the request,
  and calls `acquire_accounts`. The US half is unchanged but now states its own register.
- **`extract`** parses each stored accounts document once (`aer.services.accounts`), splits what
  it finds into the consolidated figures and the single-axis dimensioned ones, and puts the
  first through the same retagging and latest-filing selection the aggregate goes through. Each
  chosen figure is persisted against the filing that stated it.
- **Which of several declared SIC codes is kept** is decided rather than taken by position:
  Companies House allows four and ranks none, so `principal_sic` prefers a code that reaches a
  profile blocking a valuation model. Firing the sector gate asks a person; not firing takes
  the standard model in silence, and ADR 0029's posture is that the permissive state is reached
  by deciding.

**Two of this record's assumptions did not hold and are corrected by the code.** §2's premise —
that `fetch_facts` is where a listed company's numbers come from — was already superseded above;
the workflow does not call it, because the accounts are fetched once by `acquire_accounts` and
parsed from the artefact rather than fetched a second time. And the fact-level excerpt the US
path records cannot exist here: an inline document presents its figures scaled, so a search of
the extracted text for the stored value matches nothing on every UK filing (ROADMAP §3.19 item
26). The document's own paragraphs, recorded at acquisition, are what a UK numeric claim cites.

**What this does not make true.** No London-listed company has been found that files tagged
accounts, so the subject this path researches end to end is a UK company that files through
accounting software. A London listing meets ADR 0128's refusal, by name and with the reason.
That is the honest reading of "a London listing can be researched", and §3.17 of the roadmap
now states it as the phase's exit.

### 6. The smoke harness proves the refusal it actually gives — 18 September 2026

`audit/smoke.py`'s second scene drove Tesco with the **real EDGAR client** and passed when the
run reached `FAILED`, "because EDGAR's ticker list has no such company". After §5 that sentence
is false twice: the venue now sends Tesco to Companies House, and the scene's services carried
no client for it — so it failed on a missing credential while its verdict still read `FAILED`.
**That is how a scene keeps passing while proving something else**, and it is the reason the
item was worth doing rather than deleting.

It also found a third caller. ADR 0128 put the check at the API route and the web page; the
audit driver commissions runs too, and went straight to `start_run`. A harness able to start a
run the product refuses at its own front door proves the opposite of what it exists for — and
this one spends money — so `drive` now checks, against the real registers unless a scene
supplies its own. The request survives the refusal, exactly as at the route.

The scene itself is now offline and deterministic: the register answers from the three
documents recorded on 18 September 2026 and the 406 that produced this decision, and the
verdict asserts that **no job exists** and that the refusal names the *filing*. "This company
cannot be researched" would be a different and untrue statement about Tesco.
