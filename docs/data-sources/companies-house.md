# Companies House

The UK register, and the accounts filed with it. Tier 1: filing incorrectly is an offence rather
than an embarrassment.

## Access

Free, and requires a key — register at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk).
Set it as `AER_COMPANIES_HOUSE_API_KEY`.

**Authentication is HTTP Basic with the key as the username and an empty password**, which is the
scheme the API documents. `basic_auth_header` builds it, and the credential is handed to
`SafeFetcher` **once at construction**, keyed by provider.

Not in `FetchPolicy.extra_headers`: that is a module-level constant which gets logged, repr'd and
imported by anything wanting to know a rate limit. And not a per-call argument: a secret threaded
through every call site is a secret with many chances to be logged. It is attached only to
requests for the provider it belongs to — a test asserts it reaches Companies House and is absent
from a SEC request — and `aer.logging` redacts anything named `authorization` as a backstop.

Rate limit is 600 requests per five minutes, which is 2/s sustained; the policy sets 1.8 to leave
margin for clock skew between workers sharing the bucket.

Licence: **Open Government Licence v3.0**, attribution required. Recorded on every source
document at acquisition.

## A company number is not a ticker

Companies House registers **companies**. It knows nothing about listings, securities or tickers.

So resolving a name to a company number is a search followed by a judgement, and the judgement
this adapter makes is to **refuse an ambiguous match rather than take the first hit**. A search
for a short name routinely returns a dozen businesses: dormant subsidiaries, pension trustees, a
holding company and its operating arm.

Picking one by search rank would put another business's accounts under this company's name, and
**nothing downstream would notice** — every figure would be internally consistent and about the
wrong firm. There is no later check that could catch it. When the search is ambiguous the error
lists the candidates and asks for a company number instead.

Dissolved companies do not count as matches. A register full of dead namesakes would make almost
every lookup ambiguous.

## Identifiers

**Company numbers are zero-padded to eight characters.** `102498` and `00102498` are the same
company and only the padded form resolves — a 404 the first time it is missed. Scottish and
Northern Irish numbers carry a two-letter prefix (`SC123456`, `NI123456`).

The number is validated before it goes into a URL path rather than trusted.

**Document identifiers come out of a response body**, so they are escaped into the path. A crafted
one must not be able to climb out of the path segment it belongs in.

## What is worth acquiring

A filing history is mostly officer appointments, registered-office changes and confirmation
statements — real records, and not ones a research report cites. Only `accounts` are offered for
acquisition, and the filter goes in the **query** rather than being applied afterwards: a
long-lived company's history runs to hundreds of entries, and asking the register to narrow it
costs one request instead of several pages.

Older entries are index records with **no document behind them**. Those are marked unfetchable,
which beats constructing a URL that 404s and recording the failure as provenance.

Documents live behind `document-api.company-information.service.gov.uk`, a different host from
the rest of the API. Both are covered by the `.company-information.service.gov.uk` allowlist
entry.

## What comes back

A UK company's accounts are usually **inline XBRL** — see `docs/data-sources/uk-ixbrl.md` for how
the facts are read, why arelle runs offline, and what raises the taxonomy-extension gate.

## Testing

`tests/fixtures/uk/` holds constructed responses in the documented shape: a profile, a filing
history mixing accounts with a confirmation statement and one pre-document entry, and two search
results — one unambiguous and one with two live companies, which is the case the adapter must
refuse.
