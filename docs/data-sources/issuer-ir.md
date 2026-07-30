# Issuer investor relations

Tier 2 material published by the company itself: annual report PDFs, results presentations,
transcripts. Authoritative where a regulatory filing does not contradict it, and frequently the
only place a presentation exists at all.

## What makes this adapter different

**It is the first one whose candidate URLs come out of untrusted content.** Every other adapter
builds URLs from identifiers a regulator issued. This one reads links off a page the issuer
controls, and a page can link anywhere — to a tracker, to an internal address, to whatever a
compromised CMS was told to serve.

## The controls, in order

1. **The domain is supplied by the operator, never discovered.** `discover_documents` takes the
   host it may read. There is no code path that learns a new domain from a page and then fetches
   it, because that is precisely what an attacker who controls a page wants.

   The SEC's submissions API does **not** carry an issuer website field, so there is nothing to
   resolve it from automatically even if that were wise. It is an input.

2. **Links off that host are dropped**, matched with `host_matches` rather than `endswith` —
   `endswith("investors.example.com")` also admits `evil-investors.example.com`, which is a host
   an attacker can register.

3. **`<base href>` is not honoured.** Relative links resolve against the URL the fetch actually
   used. Honouring the page's own base would make every relative link resolve to a domain the
   page chose, and each would then pass a host check against that domain.

4. **Only `http` and `https` are followed.** `data:` matters most: it has no host to check at
   all, which is the appeal of it.

5. **The fetch layer checks again.** Every request goes through `SafeFetcher` with the domain
   passed as `extra_hosts`. `ISSUER_IR` has an **empty standing allowlist**, so everything is
   refused unless the operator names the host for that request. The check in the adapter is the
   cheap one; this is the control, and the tests for it would still pass with
   `aer.sources.issuer` deleted.

6. **robots.txt is honoured**, unlike the regulator APIs. Reading a company's website is
   crawling, and a company's stated wishes about crawling apply. Access to EDGAR is by a
   documented API contract, which is a different thing.

## Dates

**Most IR documents have no discoverable publication date.** A date is taken from the link's own
text only when there is exactly one unambiguous candidate — `Interim results — 28 July 2022` is
real evidence; a year in a filename is not, and two dates in one link is a range.

Where no date is found, `publication_date` is `None` and the document is **quarantined under
point-in-time rules**. That is the honest outcome: a date invented from a URL slug would be worse
than none, because it would pass the check. An operator who knows the date can record an override
with a reason.

## Licensing

Issuer-published material is the issuer's copyright. It is quoted under fair dealing for research
and reported with attribution; it is not reproduced. The licence note is recorded on every source
document at acquisition.

## Testing

`tests/issuer_fixtures.py` is one page carrying the document links **and** the ones that must not
be followed — the off-domain mirror, the lookalike host, the `mailto:`, the `javascript:`, the
`data:` URI, and a variant with a hostile `<base href>`. Written as one page because that is how
they arrive: a real IR page has the document you want and forty things you do not.
