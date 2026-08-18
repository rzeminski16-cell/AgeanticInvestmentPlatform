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

1. **The series itself never leaves the machine.** Not the bars, not a chart of them. That
   is what "displaying in original or repackaged form" prohibits, and it is not ambiguous.
   `price_relative` is built with `exportable=False` and the report assembler refuses any
   non-exportable chart outright, so the containment is structural rather than a rule a
   template has to remember.
2. **Every copy must be destroyed within one month of the subscription ending.** An artefact
   store with no delete path cannot honour that, which is why one now exists. Everything
   EODHD touches carries `RetentionClass.LICENSED` and is purgeable; see
   `docs/adr/0031-erasure-is-an-appended-event.md`. `aer purge-licensed --provider eodhd
   --reason "…"` satisfies the obligation in one command and leaves the provenance intact.

## Derived figures: permitted, by the operator's determination

On **2026-08-09** the operator — who is the subscriber and can read the executed agreement —
determined that figures *computed from* this data may be published: multiples, ratios, other
derived values. `FetchPolicy.derived_figures_publishable` records it, and a comparables table
now reaches an exported report.

**The basis matters as much as the permission.** The public terms contain no derived-data
exemption, so this is not something the code can infer and must never be written as though
it were. The licence note stamped on every price document says whose determination it is and
when it was made, in those words.

This is the second time the note has said derived figures may be published. The first time
— removed on 5 August 2026 — asserted it as though the terms said so, when they say nothing
of the kind, and it would have been stamped on every price document as a determination
nobody had made. The sentence is similar; the honesty of it is not, and
`tests/test_fetch_policy.py::TestTheEodhdLicenceNote` pins the difference.

The permission is scoped. It covers derived figures and stops there: the series and any
plot of it remain prohibited, and `CompsTable.for_audience` still withholds by default, so a
paid feed added tomorrow inherits nothing from this decision.

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

## What this subscription actually entitles, and the one mismatch

Confirmed against the operator's account on **2026-08-18**. Entitlements are per feed rather
than per plan, so the table above is a statement about the *code* and this one about the
*account*; where they disagree the account wins, and the run gets an entitlement error
rather than data.

| Feed | Enabled | What this platform does with it |
|---|---|---|
| EOD Historical Data | yes | `/api/eod` — the price series. Used. |
| Split Data Feed | yes | `/api/splits` — used, and load-bearing: an unadjusted series across a split is a chart that lies. |
| Dividends Data Feed | yes | `/api/div` — used, for the total-return adjustment. |
| Exchanges List | yes | Not used. Would replace the hand-maintained market-proxy table in `sources/eodhd/proxies.py`. |
| News API | yes | Not used — see *What is deliberately not used*. |
| **Fundamental Data** | **no** | **`/api/fundamentals` is called anyway** — gap A47. It is the only source of the share count, so the market capitalisation, every enterprise-value multiple and the equity bridge's share count all rest on an endpoint this account cannot reach. |
| Technical, Calendar, Exchange Details, Tick, All-In-One | no | Not used, and none is wanted. |

**The share count should come from the filings regardless.** It is already in the concept
map, and `dei:EntityCommonStockSharesOutstanding` sits on the cover page of every 10-K,
dated and primary. A vendor was the weaker source before the entitlement made it the
unavailable one, and dropping the call removes ten weighted units from every run.

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
- **Sentiment scores and technical indicators.** Interpretation is the model's job over
  primary sources, not a vendor's score.
- **The News feed — not on principle, and worth revisiting.** It is enabled on this
  subscription and unused. The objection above is to a vendor's *score*; a dated headline
  with a link is ordinary evidence, and the recent-developments worker currently has nothing
  current to read but filings. Three things would have to be settled first: it enters at
  tier 4 (licensed), so it may support colour and never a figure; ADR 0030's withholding
  rules apply in full, so a shareable report carries the reading and not the feed; and
  article text is untrusted content reaching a model only inside the wrapper, exactly as a
  fetched page does. Not built, not refused — recorded so it stays a decision.
- **Intraday.** `docs/PLAN.md` fixes end-of-day as the granularity; intraday is a different
  subscription and answers no question this platform asks.
- **The vendor's `adjusted_close` as an answer.** It is stored as a cross-check against this
  platform's own adjustment and never used in a calculation.
