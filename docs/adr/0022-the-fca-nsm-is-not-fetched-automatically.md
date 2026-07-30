# 22. The FCA National Storage Mechanism is not fetched automatically

- **Status:** Accepted
- **Date:** 2026-07-30
- **Supersedes, in part:** the "Core. UK filing discovery & PIT anchor" role given to the NSM
  in `docs/PLAN.md` §1.3

## Context

Task 18 of `docs/phase-2-plan.md` is conditional, and the condition is written into the plan:

> Preceded by a written determination of what NSM permits. If programmatic access is not
> permitted, the task becomes an ADR recording why, and the UK path relies on Companies House
> alone. **Do not build first and check later.**

The standing constraint on this project is stricter than "be careful". It forbids any data
collection method that breaches a website's terms of use, circumvents access controls or
robots restrictions, or uses sources where the rights needed for future commercial use are
not held. That is not a preference to be traded against coverage; it decides this question on
its own once the terms are known.

The NSM is attractive precisely because it is the one place every UK regulated disclosure
lands. `docs/PLAN.md` §1.3 named it "Core" for UK filing discovery and as the point-in-time
anchor. So the determination matters: the difference between having it and not having it is
the difference between one index and a per-issuer hunt.

## What was established

**There is no public read API.** The "NSM API" in FCA material — Primary Market Technical Note
523.1, and the schema-and-API work in PS24/19 — is the channel by which *Primary Information
Providers submit* regulated information to the NSM. The consumer side remains a web search UI,
document download, CSV export of search results, and (after PS24/19) an inline-XBRL viewer with
xBRL-JSON and xBRL-CSV downloads. There is nothing to integrate against that is offered as an
integration.

**The FCA's terms prohibit automated access without prior written consent.** The FCA's website
legal terms prohibit using a *"scraper", "robot", "bot", "spider", "data mining", "computer
code", or any other automated device, program, tool, algorithm, process or methodology* to
access, acquire, copy or monitor any portion of the website or the data and content on it,
without the FCA's prior written consent. This project holds no such consent.

**Use of the NSM is additionally governed by its own terms.** The NSM carries a separate Terms
of Use, which incorporates an Acceptable Use Policy setting out permitted and prohibited uses.

**Absent a read API, automated collection would have to be scraping.** These two findings
compound rather than sit side by side. Where a publisher offers a documented API, an API client
is not a scraper and the anti-scraping clause does not bite — that is the reasoning under which
this platform reads SEC EDGAR and Companies House. The NSM offers no such contract, so any
automated NSM collection would be exactly the activity its operator's terms name.

### What could not be established, and why that does not change the answer

The primary text of neither instrument could be read from the build environment: its network
policy blocks general outbound HTTPS, so `WebFetch` returns 403 for every host including
`fca.org.uk`, and only search is available. The clause above is therefore corroborated across
several independent search results rather than read at source, and the NSM Acceptable Use
Policy's own wording was not obtained at all.

That uncertainty argues **for** this decision, not against it. The constraint is not "collect
unless the terms are shown to forbid it"; a determination that automated access is permitted is
the one that would need proof, and there is none. The user should still confirm the wording at
<https://www.fca.org.uk/legal> and in the NSM Terms of Use before any future reversal.

## Decision

**The FCA's hosts are not fetched by this platform.** Task 18 is closed as declined. No NSM
adapter, no NSM discovery, no code that constructs an `fca.org.uk` URL.

**The refusal is structural, not documentary.** `aer/fetch/policy.py` gains `REFUSED_HOSTS`, a
provider-independent list checked in `policy_for_url` *before* the allowlist and *before*
`extra_hosts`, on the original URL and again on every redirect hop. Removing `.fca.org.uk` from
the `FCA_NSM` allowlist alone would not have been enough:

- `extra_hosts` exists so a call site can admit one host for one request, and issuer-IR
  discovery passes whatever domain it just resolved — an NSM host supplied there would have
  been admitted.
- Allowlists are per provider, so the same URL fetched under `ISSUER_IR` or `WEB_SEARCH` asks a
  different question.
- A permitted host that redirects into `data.fca.org.uk` would have walked in through the back
  door, which is the same shape as the redirect-to-private-address bypass the fetcher already
  guards against.

An empty allowlist says "nobody has needed this host yet". A refusal says "a decision was taken
about this host", and only the second survives a keyword argument.

**`Provider.FCA_NSM` stays.** The enum member, the Tier 1 mapping in `aer/sources/tiering.py`
and the fetch policy remain, because a human who downloads an NSM document by hand and supplies
it to the platform has supplied a Tier 1 regulatory filing, and it must be recorded with the
FCA's terms attached. What is removed is the ability to *fetch* one. The policy's
`licence_note` is rewritten to say so, since that note is copied onto every source document at
acquisition and has to describe how the document actually arrived.

**The refusal names its own reversal.** Each `HostRefusal` carries the repository-relative path
of the ADR that created it, and the error raised quotes the reason. A test asserts the file
exists. If the FCA grants written consent, the change is this document plus one tuple entry.

## Consequences

**The UK path relies on Companies House and the issuer's own site**, exactly as
`docs/phase-2-plan.md` anticipated. This costs less than it appears to:

- **Documents.** A UK listed company's IFRS consolidated annual report is published on its own
  investor-relations site, which task 16's discovery already finds and task 17's iXBRL extractor
  already reads. The NSM is a convenient index of those documents, not their only home.
- **Point-in-time.** The NSM was named as the PIT anchor, but task 15 moved that job into
  `aer/extract/dates.py`, which extracts and scores a publication date from four kinds of
  evidence rather than trusting one index. Losing the NSM loses one high-confidence
  `FILING_INDEX` candidate for UK issuers; it does not remove the mechanism.
- **What is genuinely lost** is completeness of discovery for RNS-class announcements. An
  issuer's own RNS archive is the fallback, and it is Tier 2 rather than Tier 1. The
  disagreement ladder in task 19 is where that matters, and a report drawing on it will say so.

**A manual route stays open and is not code.** The NSM's search UI, CSV export and document
download are available to a person. A document obtained that way can enter the platform as a
user-supplied source under `Provider.USER_SUPPLIED`, hashed and cited like anything else, with
no automated access involved. Building that ingestion path is not part of task 18 and is not
done here.

**`docs/PLAN.md` §1.3 is now wrong in one cell** and stays as written, with a pointer to this
ADR beneath the table. The table is a record of what the research found; this is the record of
what was decided about it, and rewriting the first to match the second would erase the reason
the question was asked.

## Alternatives considered

**Request written consent from the FCA and build afterwards.** The correct route if the NSM is
wanted, and nothing here prevents it. Rejected as a blocker: it is an open-ended external
dependency in front of a phase that has three tasks left, and the ADR is cheap to reverse.

**Read the NSM at "polite" rates and rely on it being a public regulator.** Rejected. The terms
do not have a volume threshold, being a public body is not a licence, and this is precisely the
reasoning the standing constraint exists to refuse. It would also be the kind of decision that
looks fine until somebody reads the code.

**Honour `robots.txt` and treat that as the publisher's position.** Rejected. `robots.txt` is a
crawling convention, not the terms of use, and where the two differ the terms govern. The
fetcher honours robots as well; that has never been the whole test.
