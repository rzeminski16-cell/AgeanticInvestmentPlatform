# ADR 0030 — EODHD is not €19.99, and the immutable archive cannot comply with its terms

**Status.** Proposed — the decision belongs to the operator; this records the findings and the
options
**Date.** 2026-08-05
**Supersedes in part.** `docs/PLAN.md` §1.4's recommendation, whose central premise turns out
to be wrong.

## Context

`docs/PLAN.md` §1.4 recommends EODHD *All World* at €19.99/month as "the only sub-£20 option
with contracted LSE + US EOD", and `docs/phase-3-plan.md` makes tasks 29 and 30 conditional on
buying it. The operator subscribed, then read the terms. Three of the four answers change the
design and one of them changes the recommendation.

## Findings

### 1. The €19.99 plan is a personal-use plan

EODHD's self-service tiers, *All World* included, are labelled **personal use**. Commercial
use is a separate product — the **Internal Use** plan at **$399/month** — under which data may
be used within the company and may not be displayed or shared outside it.

**This is the same failure that disqualified yfinance** (§1.4), and for the same reason: the
operator's standing constraint is to use no source whose rights do not extend to future
commercial use of the software. A personal-use subscription serves today's personal research
tool honestly and fails that test tomorrow.

**It also inverts the vendor comparison.** §1.4 ranked EODHD first on price. At the commercial
tier it is $399/month against Tiingo's $50/month explicit commercial plan — so EODHD goes from
cheapest to roughly eight times the nearest alternative, and Tiingo's disqualifying weakness
(no UK/LSE coverage) has to be re-weighed against a 4× budget overrun rather than against £17.
§1.4 needs revising and this ADR does not do it unilaterally.

### 2. There is no derived-data safe harbour

The terms prohibit selling, retransmitting, redistributing, **displaying** or granting access
to the information in its "original or **repackaged** form". Nothing defines derived data and
nothing exempts it.

So the assumption written into `aer/fetch/policy.py` — *"derived figures may be published, raw
series may not"* — **was not supported by the terms and has been corrected**. That sentence was
stamped on every EODHD source document and is what would have answered "may we quote this?"
years later; an overstatement there is worse than an absence, because it reads as a
determination somebody made.

The honest classification, worst to best:

| Output | Position |
|---|---|
| Raw series, or reformatted tables of it | Prohibited |
| A chart of prices or cumulative returns | Probably display of repackaged data |
| A computed P/E from EODHD inputs | Ambiguous; not expressly permitted |
| A computed beta or factor score | More transformed, still no express exemption |
| "Valuation appears elevated" | Commentary rather than a data value; unaddressed |

Only the last is comfortable, and only because it is prose.

### 3. The retention clause and the artefact store contradict each other

Data may be held while the subscription is active. **Within one month of it ending, all copies
must be deleted**, and EODHD may ask for confirmation.

`aer/storage/protocol.py` has no `delete`, no `update` and no `move`, deliberately (ADR 0008).
That is invariant 1 — every externally derived fact traces to a hashed artefact — expressed as
a type. An immutable store is precisely a store that cannot satisfy a deletion obligation.

**EODHD did not create this problem; it exposed one already on the list.** `docs/PLAN.md`'s
risk register T16 calls for a retention policy — artefacts referenced by an immutable report
never deleted, unreferenced artefacts collected after 90 days, soft-delete plus an audit event
— and it has not been built. A licensed source is simply the first one that makes it
load-bearing rather than prudent.

The shape of the answer is clear enough: **separate the deletable payload from the
undeletable provenance.** The request, the endpoint, the timestamp, the code version, the
content hash and the calculation lineage stay for ever; the response bytes live in a store
that can be purged, and a purged artefact reads as *deleted under licence* rather than as
missing. A citation into a purged payload can then no longer be re-verified, and that is a
real loss which has to be stated rather than engineered around.

Unresolved even then: whether "subscriber's premises" covers a controlled cloud environment,
whether backups and replicas count, whether key destruction satisfies deletion, and whether
hashes and derived outputs may persist. Those need a written agreement, which EODHD directs to
its Custom plan.

### 4. The rate limits are settled, and generous

1,000 HTTP requests per minute; 100,000 **weighted** calls per day, resetting at midnight GMT.
Weights differ by endpoint: technical, intraday and news cost 5, fundamentals and options cost
10, whole-exchange bulk requests cost 100.

`requests_per_second` is now set from the published ceiling with headroom rather than guessed.
The daily weighted allowance needs a **second limiter** the fetch layer does not have — a
rolling request limiter cannot see consumption units — and `X-RateLimit-Limit` and
`X-RateLimit-Remaining` should drive back-off. That is adapter work, and it is the one part of
this finding that is straightforwardly good news.

## Decision

**Not taken here.** Tasks 29 and 30 are held. Three routes, and the choice is a spending and
risk decision rather than a technical one:

1. **Internal Use at $399/month.** Resolves the commercial-use question outright. Roughly 4×
   the stated ≤£100/month ceiling, before any Claude spend. Still leaves the derived-data
   question needing written confirmation, and still needs a retention amendment.
2. **Keep the personal plan; build for internal use only.** Coherent for the tool as it stands
   — it publishes nothing, and the reports are local. Requires: the comps section marked
   internal-only, no price-derived chart or figure in anything shared, and a retention path
   before the subscription can ever lapse. Records the personal-use limitation the way
   yfinance's was, and accepts that a future commercial version needs a different licence.
3. **Decline and drop tasks 29 and 30**, as `docs/phase-3-plan.md` already provides for. The
   valuation surface ships with the comps section stating that no market-data source is
   configured. The DCF, ratios, earnings quality, statements, macro and WACC are unaffected.

## Consequences, whichever is chosen

**The corrected licence note ships now**, because the old one was wrong under every route and
is pinned by `tests/test_fetch_policy.py::TestTheEodhdLicenceNote`.

**Under routes 1 and 2, the retention work comes before the adapter, not after.** Building an
archive that cannot comply and adding deletion later means the non-compliant copies are the
ones already written.

**Under route 3, the sector block has nothing behind it for four profiles.** Task 28 blocks
FCFF for banks, insurers, REITs and pre-revenue biotech and offers comparables instead;
comparables need prices, and dividend discount and net asset value are not implemented. That is
a real gap in the product and it should be stated in the report rather than left for a reader
to discover.

## What would settle this

The operator is now a subscriber and can see something neither the public pricing page nor
this repository can: **the agreement actually accepted at checkout**, and the account's own
licence page. If those differ from the public marketing pages — which is common, and the
labelling of self-service tiers as "personal use" is exactly the sort of thing that reads
differently in the executed terms — finding 1 may soften. It is worth ten minutes before
spending $399 or dropping two tasks.
