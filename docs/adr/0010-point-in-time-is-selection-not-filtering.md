# 10. Point-in-time is a selection over the full record, not a filter at acquisition

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

SEC EDGAR's `companyfacts` endpoint returns every XBRL fact a company has ever tagged.
That includes the same period many times over: FY2020 revenue appears in the FY2020 10-K,
again in the FY2021 10-K as a comparative, and again in FY2022 — and the values do not
always agree, because companies restate.

Each observation carries the accession number and filing date of the document that
reported it. This is what makes free, genuine point-in-time reconstruction possible, and it
is a real differentiator: most paid vendors serve only the restated view.

The question this ADR settles is **where** point-in-time is enforced, and what happens to
the observations that lose.

There is an obvious cheap answer: filter facts as they are parsed, keeping only those filed
on or before the as-of date, and discard the rest. It is one line and it is wrong in two
distinct ways, described below.

A second question arrived with the first: `docs/archive/PLAN.md` places an `extractions` table
between a source document and a fact. That table belongs to the extraction task, which has
not been built. Facts exist now and need provenance now.

## Decision

### Selection happens on the complete set, after parsing

`SecEdgarClient.fetch_facts` returns **everything**, unfiltered, and explicitly ignores the
`as_of_date` it accepts for interface compatibility.
`aer.sources.sec.pit.select_point_in_time` then performs the selection as a pure function
over that complete set.

The rule, stated exactly:

> Group facts by `(concept, unit, period_end, fiscal_period)`. Discard every fact filed
> after the as-of date. From what remains, choose the one filed **latest**.

A fact filed *on* the as-of date is included: a filing accepted on a day was public that
day.

### The result is a partition, not a filtered list

Every input fact appears exactly once in the output — in `chosen`, or in `rejected` with
one of three reasons:

| Reason | Meaning |
|---|---|
| `filed_after_as_of_date` | Did not exist yet. Using it would be look-ahead bias. |
| `superseded_by_later_filing` | A later filing, still within the as-of date, restated it. |
| `duplicate_tagging_in_same_filing` | One filing tagged the same number under two names. |

### Ties are broken deterministically, in three keys

`(filed_date, accession, raw_concept)`. Filing date is the rule; accession orders two
filings accepted on the same day (a 10-K and a same-day 10-K/A); the raw tag orders two
tags within *one* filing, which happens whenever a filer reports one number under two
names during a taxonomy transition.

### Only the `as_reported` basis is implemented

`select_point_in_time` **raises** `ValidationError` if asked for `restated` or
`vendor_standardised`. The enum values exist because a stored fact must be able to say
which basis it is; the selector refuses to produce them.

### Facts link to source documents, not to extractions

`financial_facts.source_document_id` is `NOT NULL`, with `ON DELETE RESTRICT`. The chain is
fact → source document → artefact → SHA-256, and it is unbroken. `accession`, `form`,
`raw_concept` and `taxonomy` are carried on the fact row so the eventual extraction
locator is reconstructible. `extraction_id` arrives with the extraction layer.

### Aggregate endpoints carry no publication date

`companyfacts`, `submissions` and the ticker file are generated on request from whatever
exists at that moment. They are recorded with `publication_date = NULL`, which quarantines
them under point-in-time rules.

## Consequences

### Why filtering at parse time is wrong

**It destroys the audit trail.** "Why is this figure not in the report?" is asked about
every report. A filtered list cannot answer it: a fact that was excluded for look-ahead and
a fact that was never reported look identical — both are simply absent. The partition makes
the difference recoverable, and `rejected_for_look_ahead` answers the question directly.

**It couples acquisition to one as-of date.** A run's as-of date can change; a request can
be re-run at a later date; a comparison can be made across two dates. Filtering at
acquisition means re-fetching, which costs a request against a rate limit that blocks
rather than throttles. Selection over a stored complete set is free and repeatable.

### Why "take the latest value" is the failure this exists to prevent

It is one line shorter and produces cleaner data, because restatements resolve accounting
messes and the restated figure is usually the more correct one *today*. It is also
look-ahead bias in its purest form: a model tested on restated history sees 2020 as it was
understood in 2023, including reclassifications made *because of* what happened in 2021 and
2022. It appears to predict things it could not have known, and the live version does not.

The failure is silent. Nothing raises, no figure looks implausible, and the backtest simply
looks better than reality. That is why this is deterministic Python with an exhaustive test
rather than a judgement made anywhere near a prompt — and why the test suite includes a
fixture where the original and restated values differ by a billion dollars, so a wrong
answer is obvious.

### What the quarantined aggregates cost

Every `companyfacts` fetch produces a quarantined source document under point-in-time
rules, which looks like noise in the quarantine list. It is not: you do not cite an API
endpoint as evidence, you cite the filing. The facts parsed out of the aggregate each carry
their own accession and filed date, which is the real point-in-time key and the real
citation target. When a filing document itself is fetched, its filing date is known and it
is fully admissible.

The alternative — dating the aggregate with the retrieval time — would have made a document
generated today appear to be contemporaneous evidence for a claim about a past date. Failing
closed is the only safe default when the honest answer is "this has no publication date".

### The deferred extraction layer

Deferring `extractions` means citation verification cannot yet re-read a verbatim excerpt
for a fact — it can only re-read the whole source document by hash. That is weaker than the
eventual design and stronger than nothing: the artefact is immutable and the fact names its
accession, so a claim is checkable by hand today and will be checkable automatically once
extractions exist.

Adding `extraction_id` later is an additive migration: a nullable column, backfilled or
populated forward. Building the table now, with no extractor to populate it, would have
meant designing a locator format against a single hypothetical consumer.

### Concept aliasing is a shared vocabulary, not an adapter detail

`aer.core.concepts` holds the canonical names and the `us-gaap` alias map. It is in `core`
because a UK adapter will map its own tags onto the *same* canonical names, and the point
of a canonical name is that there is exactly one of it. A test asserts every alias points
at a concept that exists and every concept is reachable from at least one alias — a typo in
either direction would otherwise produce facts nothing downstream could ever query for,
silently.

## Alternatives considered

**Filter at acquisition, as the source-document layer does for publication dates.**
Rejected for the two reasons above. Note the asymmetry is deliberate: a *document* is
filtered at acquisition because fetching it at all is the thing to avoid, whereas *facts*
arrive in a single aggregate whether or not they are wanted, so there is nothing to save by
discarding them and a full audit trail to lose.

**Include `raw_concept` in the grouping key rather than the tiebreak.** This would make
`Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax` separate groups, both
chosen, and push the ambiguity downstream to whatever asks for "revenue in FY2020" and gets
two answers. Resolving it here, where both facts and their filings are visible, is the
right place.

**Implement `restated` selection as well, for comparison.** Rejected: a working code path
that produces look-ahead-biased data is a footgun with no current consumer. If a genuine
need appears — showing an operator how much a figure was restated by, say — it needs its own
ADR and its own guard rails, not a branch in the selector.
