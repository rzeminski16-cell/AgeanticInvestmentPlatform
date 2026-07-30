# FCA National Storage Mechanism

**This platform does not fetch from the FCA.** The decision and its reasoning are ADR 0022;
this note is the operational summary and the manual route.

## What the NSM is

`data.fca.org.uk` is the FCA-operated official appointed mechanism where UK listed and
admitted-to-trading issuers file "regulated information" under the Disclosure Guidance and
Transparency Rules, the Listing Rules and the Prospectus Regulation Rules — annual and interim
reports, and RNS-class announcements. It is the single most complete index of UK regulated
disclosure in existence, which is why the question was worth asking carefully.

## The determination

Automated access is not permitted, so none happens.

The FCA's website legal terms prohibit using a scraper, robot, bot, spider, data mining,
computer code, or any other automated device, program, tool, algorithm, process or methodology
to access, acquire, copy or monitor the site or its content, **without the FCA's prior written
consent**. This project holds no such consent. Use of the NSM is separately subject to its own
Terms of Use and an Acceptable Use Policy.

There is also nothing to integrate against. The "NSM API" in FCA material — Primary Market
Technical Note 523.1, and the API work in PS24/19 — is the **submission** channel used by
Primary Information Providers. The consumer side is a search UI, document download, CSV export
of results, and an inline-XBRL viewer with xBRL-JSON and xBRL-CSV downloads. Where a publisher
offers a documented API, a client of that API is not a scraper; the NSM offers none, so any
automated collection here would be the activity the terms name.

**Verify before relying on this.** The primary text could not be read from the build
environment — its network policy blocks outbound HTTPS to every host, so the clause above is
corroborated from several independent search results rather than read at source, and the NSM's
own Acceptable Use Policy was not obtained. Read <https://www.fca.org.uk/legal> and the NSM
Terms of Use yourself before treating this as settled, and certainly before reversing it.

## How the refusal is enforced

Not by this document. `REFUSED_HOSTS` in `aer/fetch/policy.py` lists `.fca.org.uk`, and
`policy_for_url` checks it **before** the provider's allowlist and **before** `extra_hosts`, on
the original URL and again on each redirect hop.

Emptying the `FCA_NSM` allowlist would not have been sufficient. `extra_hosts` admits a host for
a single request — it is how an issuer's IR domain gets in once resolved — and allowlists are
per provider, so the same URL fetched as `ISSUER_IR` asks a different question. A refusal has to
be a property of the publisher or it is one keyword argument from being nothing.

`Provider.FCA_NSM` itself survives: an NSM document obtained **by hand** is still a Tier 1
regulatory filing and still needs the FCA's terms recorded against it. The provider's
`licence_note` says how such a document arrived, because that note is copied onto every source
document at acquisition.

## What replaces it

| Need | Now served by |
|---|---|
| UK annual/interim report documents | Issuer IR site (`docs/data-sources/issuer-ir.md`), Tier 2 |
| UK structured financials | Inline XBRL out of that report (`docs/data-sources/uk-ixbrl.md`) |
| Statutory accounts, entity graph, filing dates | Companies House (`docs/data-sources/companies-house.md`), Tier 1 |
| Publication date / point-in-time anchor | `aer/extract/dates.py` — extracted and scored, not taken from one index |

What is genuinely weaker is discovery of RNS-class announcements: the issuer's own RNS archive
is Tier 2 where the NSM would have been Tier 1. Task 19's disagreement ladder is where that
shows up, and a report that leans on it will say so.

## The manual route

A person may search the NSM, export results as CSV and download documents, under the FCA's terms
as a human user of the site. Such a document can enter the platform as a user-supplied source —
hashed, tiered and cited like anything else, with no automated access involved. That ingestion
path is not built; it is noted here so the option is not forgotten.

## If consent is obtained

The reversal is small and deliberately so: this note, ADR 0022, one entry in `REFUSED_HOSTS`,
and the `FCA_NSM` allowlist. The adapter itself was never written, so nothing has to be
un-written.

Sources: [FCA National Storage Mechanism](https://www.fca.org.uk/markets/primary-markets/regulatory-disclosures/national-storage-mechanism),
[FCA legal terms](https://www.fca.org.uk/legal),
[NSM Terms of Use](https://data.fca.org.uk/artefacts/NSM_Terms_of_Use.pdf),
[FCA PS24/19](https://www.fca.org.uk/publications/policy-statements/ps24-19-enhancing-national-storage-mechanism),
[XBRL International on the NSM viewer and download formats](https://www.xbrl.org/news/fca-opens-up-nsm-data-with-viewer-and-new-download-formats/).
