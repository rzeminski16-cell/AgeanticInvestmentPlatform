# ADR 0128 — A run that cannot succeed does not start

- **Status:** Accepted
- **Date:** 2026-09-18
- **Amends:** ADR 0121 (the UK path) — this is what §2's open question resolved to
- **Decided by:** the operator, 18 September 2026: *"When a run is first started can we check
  whether or not we will hit this issue on this run. If we do, we don't allow the run to be
  executed and inform the user that this ticker is not available due to this reason."*

## Context

Three sources were considered for a London-listed company's tagged figures and each was
measured rather than assumed. The measurements are in ADR 0121's appended section and
`ROADMAP.md` §3.19 items 21–25; the short version:

- **Companies House holds no tagged accounts for a listed company.** Nine were checked —
  Tesco, Barclays, AstraZeneca, Greggs, Cranswick, Chemring, Gamma, Judges Scientific,
  Nichols, across the FTSE 100, 250 and AIM — and every one files a scanned PDF. Tesco's is
  14.3 MB over 236 pages and yields no tagged figures *and no extractable text*. Inline XBRL
  at that register belongs to companies that file through accounting software.
- **The issuer's own ESEF report is not reliably reachable.** Of five issuer sites, three
  (Tesco, AstraZeneca, Unilever) answer **403** to this platform's fetcher — bot protection
  reacting to a User-Agent that identifies the operator and must not pretend to be a browser.
  Of the two that answer, one serves its document list client-side: Tesco's results page is
  52 KB of HTML with **zero anchors**. There is no web search in this platform to find a
  document URL with.
- **The FCA's National Storage Mechanism stays refused** under ADR 0022.

So the honest position is that some subjects can be researched and some cannot, and which is
which is a property of how the company files rather than of where it is listed.

**The old behaviour told the operator far too late.** A subject with no readable source was
discovered at `acquire` — after the planner had spent a model call, after the plan gate had
been rendered, read and approved. The cost of finding out was a real amount of money and the
operator's attention; the information was available before any of it.

## Decision

**Availability is checked where a run is commissioned, before the job row exists.**
`aer.services.availability.check_availability` resolves the subject against the register its
venue names (ADR 0121's `registry_of`) and confirms the register holds something this
platform can read. A "no" is a sentence the operator reads, not an exception: the request is
left exactly as it was, so changing the ticker or the exchange and trying again is one edit.

**It asks the question the run will ask, in the same words.** For a US listing, EDGAR's own
ticker resolution — so the refusal is the one `resolve_ticker` already writes, which
distinguishes a missing symbol from one listed elsewhere from one belonging to another
company. For a UK listing: the company resolves, it has fetchable accounts, and the newest
set comes back **tagged** — asked for by media type, exactly as `acquire` asks (ADR 0127), so
the check and the run cannot disagree. A check that asked some cheaper question would be a
check that passes runs which then fail.

**A refusal names the filing, not the company.** *"TESCO PLC files its accounts with
Companies House as a scanned document rather than as tagged data. This platform reads figures
from tagged filings, so that every number in a report traces to the filing that states it —
and there is nothing in a scan to trace to."* The distinction matters: nothing is wrong with
Tesco, and an operator told "this company cannot be researched" would reasonably conclude
otherwise.

**Four refusals, not one**, because they lead an operator to do different things: no such
company; several companies of that name; no fetchable accounts; accounts that are not tagged.
A register having a bad moment is a fifth and says to try again, because telling somebody
their company is unresearchable when a server was briefly down would be false.

**The clients are injected.** `AppState.registers` is a field on the same terms as the
provider and the store, and the test fixtures default it to a stub that admits everything — a
check that reached EDGAR from the suite would make the suite need a network, which it may
not. A test about the check passes its own.

## Consequences

**Every domestic London listing is refused today**, with the reason, at the moment it is
commissioned. That is not a narrowing of the product: it is the product saying what was
already true, at the only point where saying it is useful. The UK register work stands and is
what makes the refusal specific — without it the platform could only have said "not
supported".

**A US listing gains the same protection** for free: a mistyped ticker, or one EDGAR does not
list, stops before the planning call rather than after it.

**What is still supported, in full:** US listings; UK companies with a US listing, through
their 20-F (AstraZeneca and around thirty others); and UK companies that file tagged accounts
with Companies House, which the register does serve and which this platform now reads. The
product documentation's "UK or US" claim is corrected to say exactly that.

**The check costs one or two free requests**, against registers this platform is already
authorised to read, at the moment a person clicks a button. That is the only place in the web
process that fetches other than the portfolio's third door (ADR 0093), and it is the same
fetch stack: same policy, same rate limit, same archive.

## Alternatives considered

**Refuse by exchange.** One line: no LSE runs. It would refuse the UK companies that *can* be
researched, and it would say nothing useful about why — the rule is about the filing, and a
rule written about the venue would be wrong in both directions the day a listed company
starts filing tagged accounts.

**Warn and run anyway.** The run would reach `acquire`, find nothing readable, and stop —
having spent the plan. A warning nobody can act on before the money goes is a warning that
costs money.

**Check at request creation.** No outbound call is made while a request is being written, by
design, and the universe rules there are pure functions over what the operator typed. A
request is also a note to itself until a run exists; refusing to *save* one because a register
was unreachable would be refusing the wrong thing.
