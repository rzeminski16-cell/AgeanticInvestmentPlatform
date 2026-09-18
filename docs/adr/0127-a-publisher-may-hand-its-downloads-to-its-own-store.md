# ADR 0127 — A publisher may hand its downloads to its own store, and the credential stays behind

- **Status:** Accepted
- **Date:** 2026-09-18
- **Extends:** ADR 0022 (the standing refusal is checked before any of this) and ADR 0121
  (which needed a UK document and could not fetch one)
- **Decided by:** the operator, 18 September 2026, choosing the narrow rule over an allowlist
  entry when both were put to them with what each admits.

## Context

Three things were true at once, and none of them was visible from the tests.

**One.** Companies House does not serve filed documents itself. Its document endpoint,
`document-api.company-information.service.gov.uk/document/{id}/content`, answers **302** to a
pre-signed URL at `s3.eu-west-2.amazonaws.com`. `SafeFetcher` re-validates every redirect hop
against the provider's allowlist, so the first real UK document fetch raised
`UrlNotAllowedError` and no UK filing could be acquired at all.

**Two.** When that hop was admitted, the request carried the **Companies House API key** to
Amazon. The credential had always been attached by provider, with a comment saying a key for
one publisher could never travel to another's host — sound while every admitted host was one
of the provider's own, and no longer sound the moment one of them was not. S3 answered 400,
`Only one auth mechanism allowed`, **quoting the `Authorization` header back in its error
body** — which the fetcher then hashed and archived, exactly as it archives every failure.

**Three.** The document endpoint serves two representations of the same filing and hands over
the scanned PDF unless asked otherwise. A small company's accounts came back as a 20 KB PDF
with no extractable text by default and as 19.6 KB of inline XBRL carrying eight facts when
asked for by type — so `CompaniesHouseClient.fetch_facts` had been reading the wrong document
and would have reported every filing as untagged.

Thirty-two offline tests of that client pass over all three, because `respx` returns a body
where the register returns a redirect, and a mocked transport cannot test a policy.

## Decision

### 1. A delegated download: admitted because of where it came from

`FetchPolicy` gains `delegated_downloads`, a tuple of `DelegatedDownload(host, from_host,
reason)`. A host named there is admitted **only** as the target of a redirect issued by
`from_host`, and `policy_for_url` learns `came_from`, which the fetcher's own redirect loop
supplies and no caller can.

The alternative — adding `s3.eu-west-2.amazonaws.com` to the Companies House allowlist — is
one line and much wider than it looks: that host serves every AWS customer's bucket in the
region, so any URL redirecting there would be fetched under this platform's most trusted
provider, at `T1_REGULATORY`, with the Open Government Licence stamped on it. A pre-signed URL
that arrived in a search result, a filing footnote or a model's reply is not the register
handing over a document.

Properties that follow, each with a test:

- The same URL asked for directly is refused. The delegation is a door the register opens,
  not one standing open.
- A redirect from anywhere else does not admit it, and the register cannot redirect to
  anywhere else.
- **One hop.** A delegated host that redirects onward arrives with itself as the origin,
  matches no rule, and is refused — so a chain cannot walk out of the delegation.
- `REFUSED_HOSTS` is still checked first, so ADR 0022's refusal of the FCA's hosts cannot be
  reached through a redirect.
- Every other provider's tuple is empty, and the default is empty, so a provider added
  tomorrow delegates nothing.

**The bucket is not pinned.** The path could be, and it would be a false comfort: the register
may re-bucket at any time, and the origin check is what constrains this, not the name of the
bucket it happens to use today.

### 2. A credential goes to the publisher's own hosts, and nowhere else

`may_carry_credential(policy, url)` is checked beside the provider lookup: the `Authorization`
header is attached only when the host matches one of the provider's **standing** allowed
hosts. Not a delegated download, not a host admitted for one request through `extra_hosts`.

This is the fix for a defect that existed before the delegation and could not fire while the
hop was refused. Making the hop reachable is what exposed it, which is the argument for
measuring against the real publisher rather than reasoning about it.

### 3. The representation is asked for

`SafeFetcher.fetch` takes `accept`, sent as the request's `Accept` header and recorded nowhere
else — what came back is still described by what was sniffed from the bytes, because a server's
claim about its own content is not evidence (the sniffing rule predates this and is unchanged).

`CompaniesHouseClient.fetch_document` asks for `application/xhtml+xml` by default, because the
caller that wants figures is the one that would otherwise be silently wrong. A filing with no
tagged copy answers **406**, which the client reads as *this filing is not tagged*: a fact
about the filing, logged as one, rather than an extraction failure. `acquire_accounts` records
it in `skipped` in a sentence an operator can read, and does not store the scan.

## Consequences

**A UK filing can be fetched.** Measured after the change: Greggs' 2026 accounts, 8,637,710
bytes, 200, in 1.8 seconds, through the policy, the rate limiter and the store.

**A listed company's accounts are still worth nothing to a report**, and now the platform says
so in one round trip instead of downloading 14 MB to find out. That is ADR 0121's open
question, not this one's.

**The archive holds one artefact containing a credential** — S3's 400 body, from the
measurement that found this. It is a scratch store outside the repository and was deleted;
the key itself should be rotated, because it was transmitted to a third party and appears in
that party's error log.

**What this does not open.** No new host is fetchable on its own. No provider gains a
credential it did not have. No standing refusal is weakened. The rule adds exactly one
reachable edge — `document-api.company-information.service.gov.uk` → `s3.eu-west-2.amazonaws.com`
— and it is the edge the register itself publishes.

## Alternatives considered

**Allowlist the S3 host for Companies House.** One line, and it admits every bucket in the
region under the platform's most trusted provider. Refused; it is the option the operator was
shown and did not take.

**Follow redirects without re-checking.** What httpx would do by default, and what the fetcher
has always refused to do: the check that must not be skipped is the one on the hop actually
connected to.

**Fetch the scan and parse it.** A 236-page image PDF yields no text at all, so there is
nothing to parse — and the version of this that would work, optical character recognition
feeding a table reader, would put arithmetic behind a heuristic, which the first rule in
`CLAUDE.md` forbids.
