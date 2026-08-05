# EODHD (EOD Historical Data)

The only paid feed this platform uses, and the only source whose bytes have an expiry date.
It supplies end-of-day bars, splits, dividends and share counts for US and UK listings.

**Read `docs/adr/0030` before changing anything here.** The commercial position is the
constraint that shapes this adapter, not the technical one.

## The licence position, and what it forbids

The subscription in use is the **All World** plan at €19.99/month. It is a **personal-use**
plan. Commercial use is a separate product — *Internal Use* — at $399/month, which is
roughly four times this project's entire ≤£100/month operating ceiling before a single model
call.

The terms prohibit selling, retransmitting, redistributing or **displaying** the information
in its "original or **repackaged** form", and they define nothing as derived. There is no
derived-data safe harbour anywhere in them.

**Two consequences, both load-bearing:**

1. **Nothing computed from this data leaves the machine.** Not a multiple, not a beta, not a
   market capitalisation, not a chart. A comparables table built on these prices is an
   internal working paper. `aer/fetch/policy.py` records this as the licence note on every
   source document, in the terms' own words rather than in a summary somebody would have to
   trust.
2. **Every copy must be destroyed within one month of the subscription ending.** An artefact
   store with no delete path cannot honour that, which is why one now exists. Everything
   EODHD touches carries `RetentionClass.LICENSED` and is purgeable; see
   `docs/adr/0031-erasure-is-an-appended-event.md`. One `purge_provider` call satisfies the
   obligation and leaves the provenance intact.

A statement previously in `aer/fetch/policy.py` — "derived figures may be published, raw
series may not" — **was not supported by the terms and has been removed.** It would have been
stamped on every price document as though somebody had determined it.

## The two limits, which are different quantities

| Limit | Value | Enforced by |
|---|---|---|
| HTTP requests | 1,000 per minute | `aer/fetch/limits.py` token bucket, set to 8/s with headroom |
| Weighted API calls | 100,000 per day | `aer/sources/eodhd/budget.py` |

The second is not a rate. Endpoints carry different weights — end-of-day, splits and
dividends cost one call each; **fundamentals costs ten**; a whole-exchange bulk request costs
a hundred — so a limiter counting requests per second cannot see that a handful of bulk
requests have spent a morning's worth of the day's budget. The ledger is keyed on the **UTC**
date, because that is when the provider's counter resets, and the reservation happens
*before* the request so concurrent calls cannot both pass the check and then overshoot.

Responses carry `X-RateLimit-Remaining`. That figure is authoritative and overwrites the
local estimate on every response; the ledger is a model of the provider's counter, and a
model drifts.

## Point-in-time

**The clamp is in the adapter, not in the caller.** Every URL builder in
`aer/sources/eodhd/api.py` takes `as_of` as a required keyword argument with no default and
puts it in the `to` parameter — there is no code path that builds a URL without it, in the
same way `aer/sources/macro/fred.py` has no code path that omits an ALFRED vintage.

The parsers then apply the bound **a second time**, to what came back, and count what they
discarded. A provider that ignores `to`, or a cache serving a wider window, would otherwise
put a bar from after the as-of date into a valuation, and that error looks exactly like a
correct number. The cost of the second check is one date comparison per row.

**The fundamentals endpoint has no `to` parameter** — it returns a current snapshot. The
share count is therefore taken from the *historical* `outstandingShares` series, choosing the
most recent entry dated on or before the as-of date, and never from the undated
`SharesStats.SharesOutstanding` headline, which is today's. Pairing a correct June price with
next quarter's share count is a look-ahead that produces an entirely plausible market
capitalisation.

## Endpoints used

| Endpoint | Purpose | Weight |
|---|---|---|
| `/api/eod/{symbol}` | Daily bars | 1 |
| `/api/splits/{symbol}` | Splits and consolidations | 1 |
| `/api/div/{symbol}` | Cash dividends | 1 |
| `/api/fundamentals/{symbol}` | Share count only | 10 |

## Response shapes, and what has not been verified

The parsers were written against EODHD's published documentation. **This build environment
has no outbound network access**, so the shapes below have not been confirmed against a live
response. The parsers therefore accept the documented shape and *refuse* anything else with a
message naming the field, rather than coercing — a parser that guessed would turn a shape
change into a wrong number instead of an error.

**A split is written as a pair, not a number**: `"2.000000/1.000000"` is new shares over old.
A one-for-ten consolidation is `"1.000000/10.000000"`, which is a ratio of 0.1 and reads like
a ten if the slash is skipped — and a split ratio read wrongly restates every historical
price by a factor of ten.

**What to confirm on the first live run**, in order of how badly a wrong guess would hurt:

1. The `split` field's exact form on a consolidation, against a real one.
2. The key names under `outstandingShares` — `dateFormatted` and `shares` — and whether the
   quarterly series is keyed as documented.
3. Whether the dividend rows carry `currency` for a UK listing paying in a foreign currency,
   or whether the field is absent.
4. The exact `X-RateLimit-Remaining` header name and whether it counts weighted calls or
   requests.

Each is asserted by a cassette in `tests/fixtures/eodhd/`, so a divergence between the
documentation and the live feed will show as a parser refusal rather than as a bad figure.

## The pence problem

A London listing quotes in **pence**. Barclays at 250 means £2.50, and the number carries no
marker saying so. `securities.quote_currency` records `GBX` rather than `GBP`, and the
conversion to major units is a single traced calculation. See
`docs/adr/0032-the-adjusted-close-is-not-a-column.md`.

A dividend can be declared in a currency the share is not quoted in — a pence-quoted listing
paying in dollars is ordinary. The adjustment **refuses** rather than converting at a guess,
because converting needs a rate at the ex-date and no rate source is wired in (ADR 0026).

## Credentials

The key is `AER_EODHD_API_KEY`, read from the gitignored `.env` and never from anywhere else.

EODHD takes it as a **query parameter**, so it is part of every request URL. That is the
provider's choice and cannot be worked around, but a URL in this platform travels — to a log
line, to `source_documents.url`, and to a report's sources appendix.
`aer/fetch/credentials.py` redacts it at the fetch layer, and `aer/logging.py` carries the
same parameter list as a value-shape pattern so that a URL logged by a third-party library —
`httpx` logs the full request line at INFO — is redacted too. Both of those were real leaks
before this task; see `docs/adr/0033`.

A run with no key configured fails with `ConfigError` naming `AER_EODHD_API_KEY`, never with
an empty series — an empty series is indistinguishable from a company that has never traded.

## What is deliberately not used

- **Bulk endpoints.** A hundred weighted calls each, and this platform researches one company
  at a time.
- **News, sentiment and technical indicators.** Interpretation is the model's job over
  primary sources, not a vendor's score.
- **Intraday.** `docs/PLAN.md` fixes end-of-day as the granularity; intraday is a different
  subscription and answers no question this platform asks.
- **The vendor's `adjusted_close` as an answer.** It is stored as a cross-check against this
  platform's own adjustment and never used in a calculation.
