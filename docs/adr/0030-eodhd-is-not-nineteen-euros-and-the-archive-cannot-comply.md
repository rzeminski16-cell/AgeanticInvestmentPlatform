# ADR 0030 — EODHD is not €19.99, and the immutable archive cannot comply with its terms

**Status.** Accepted (2026-08-09) — **route 2**. The findings below are unchanged; the
decision that was left open has been taken and is recorded at the end.
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

## Decision (recorded 2026-08-09)

**Route 2: keep the personal plan and build for internal use only.** The three options as
they stood are kept below, because the reasoning for the one chosen is only legible against
the two that were not.

The routes, as they stood:

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

---

## The decision, taken 2026-08-09

**Route 2.** The personal subscription is kept, and the platform is built so that nothing
price-derived can leave the machine. This is coherent for the tool as it stands — it
publishes nothing, and the reports are local — and it accepts, explicitly, that a future
commercial version needs a different licence. The personal-use limitation is recorded here
the way yfinance's was in §1.4, so a later reader finds the constraint rather than
rediscovering it.

Route 1 was declined on cost: $399/month against a stated ≤£100/month ceiling that also has
to cover model spend, for a resolution the tool does not currently need. Route 3 was
declined because task 28 blocks FCFF for banks, insurers, REITs and pre-revenue biotech and
offers comparables as the alternative — dropping comparables would leave four sector
profiles with no valuation route at all.

### What route 2 required, and where each part is

Most of it was already built by the tasks that followed this ADR, which is why adopting the
route is a small change rather than a large one:

| Requirement | Where it lives |
|---|---|
| The comps section marked internal-only | `CompsTable.for_audience` returns `WithheldComps`, which **has no rows** — the restriction is what the object contains, not a flag a template is trusted to read (ADR 0034) |
| No price-derived chart in anything shared | `exportable_charts_for` and `internal_charts_for` are separate functions, and `aer.render.document` refuses any chart that is not exportable |
| The corrected licence note | `aer/fetch/policy.py`, pinned by `tests/test_fetch_policy.py::TestTheEodhdLicenceNote` |
| Payload separable from provenance | ADR 0031: `artefact_purges` keeps the row, the hash and the lineage; the bytes go |
| The daily weighted-call ledger | `aer/sources/eodhd/budget.py` — reserves before the request, refuses rather than warns |
| **A retention path that can actually be run** | **Was missing.** `purge_provider` existed with no caller in `src/` at all, so the obligation could be honoured only by writing Python at a REPL against a live database. `aer purge-licensed` is that caller. |

### The one gap this closed

`aer purge-licensed --provider eodhd --reason "…"` deletes every stored payload from a
licensed provider and records each deletion in `artefact_purges` with the reason, the actor
and the terms in force at the time. It refuses a provider with no deletion obligation, and
it refuses a blank reason — "licence" is not a reason; the obligation has to be named,
because somebody reads it two years later when a citation will not resolve.

This is what makes route 2 honest rather than aspirational. The subscription can now lapse
and the agreement can be complied with, from a command, in one step, with a record.

### What is still true and still uncomfortable

The derived-data question is **unresolved, not resolved in our favour**. The terms contain
no derived-data exemption, so "may we publish a P/E computed from this?" has no answer and
the platform behaves as though the answer is no. That is the conservative reading and it is
what the withholding enforces.

If the executed agreement at checkout differs from the public marketing pages — which the
"What would settle this" section above flags as common — finding 1 may soften and route 1
may become unnecessary rather than merely expensive. Reading it remains worth ten minutes.
