# 0061 — Evidence is scoped to the subject, not to the request

Date: 2026-08-18
Status: accepted

Task P1 of `docs/archive/polish-phase-1.md`. Amends nothing; it states a rule the invariants
assumed and never wrote down.

## Context

The first complete run of `vertical_slice_v1` produced an Amazon research note whose
Sources list cited Walmart, Alibaba, eBay, JD.com, MercadoLibre and Target. Its front page
carried a revenue, a net income and an earnings per share that could not all belong to one
company — the last two imply a share count two orders of magnitude from Amazon's own. Its
historical financial analysis said so in as many words: *"The annual-basis, tagged line
items available … all carry a fiscal year ended in March 2026 and are denominated in
renminbi alongside US dollars. They belong to Alibaba Group Holding, not to Amazon."*

Every one of those figures satisfied invariant 1. Each traced to a hashed artefact, each
footnote resolved, and all 86 citations verified against the archived bytes twice.
**Provenance was intact; identity was not.** A fact's lineage answers *where did this come
from*. It does not answer *who is this about*.

The mechanism was three lines of SQL. `peer_discovery` records a peer's companyfacts as a
`SourceDocument` carrying the subject's `request_id`, and both evidence builders selected
by request:

```
src/aer/sections/evidence.py:373 — .where(SourceDocument.request_id == request.id, …)
src/aer/render/glance.py:136     — .where(SourceDocument.request_id == request.id, …)
```

`request_id` was a sufficient proxy for "about the subject" for as long as a request could
only ever touch one company. ADR 0059 ended that, and nothing noticed, because nothing had
ever needed to say which company a document was about.

Two orderings turned a leak into a takeover. Facts sort `period_end DESC`, so a March year
end outranks a December one and fills the 400-row pool first; a section asking for annual
figures could be handed a pool with no subject in it. Sources sort `retrieved_at DESC`, and
peers are fetched last, so they occupied the top of a listing capped at forty. Of the facts
under that request, 77,900 were peers' and 14,789 were the subject's.

**This was derived once already.** `aer/services/research.py` fixed the same bug for the
research workers under a different symptom, and left the reasoning in its docstring: *"every
consumer here joined through `source_documents` to `request_id`, so those facts belonged to
the earlier run's document and this run could not see one of them. Five workers spent sixty
tool calls searching an empty table."* The rule was applied to one module and never
generalised. An ADR is the mechanism this repository has for a lesson learned in one place
binding the next one, and this one did not get written.

## Decision

**A fact is scoped by company. A source document is scoped by company and by request.**

The asymmetry is the substance of the decision, not an inconsistency:

- **Facts outlive the run that fetched them.** They deduplicate on an observation key that
  deliberately excludes the source document, so the second run of a company inserts nothing
  — `supplied: 18588, inserted: 0` in the live log is the dedupe working — and those rows
  hang off the *first* run's document. Adding the request back would hide every fact the
  first run wrote. Scoping to the company alone is what keeps a repeat run from producing a
  report with none of its subject's facts in it.
- **A source document is this run's account of itself.** "What did this run acquire?" is
  exactly what the sources page asks, and a document fetched by some other run is not part
  of the answer.

Both predicates live in one place each — `aer.services.facts.visible_facts` and
`aer.services.sources.visible_sources` — and every consumer calls them. Three copies of a
predicate is how the first two diverged.

**The subject is a column, not a lookup.** `research_requests.company_id`, written by
`acquire` from the row `upsert_company` returned. Matching `Company.ticker` and
`Company.exchange` back to the request's strings is a weaker key that a re-used or re-listed
ticker defeats silently; it survives only as the fallback for a request looked at before
`acquire` has run.

**A document says whose it is.** `source_documents.company_id`, nullable. NULL means *not
about an issuer* — a macro series, a regulator's note, an index page — never *we did not
record it*, and those documents stay visible to every run that fetched them.

**The date filter is part of the scope.** Request scope happened to bound a consumer to one
acquisition; company scope does not, so `visible_facts` filters on `filed_date` against the
as-of date under point-in-time. Without it, company scoping would newly expose a fact filed
after the as-of date by some later run's acquisition. Removing the request from the scope
and adding the date is one change, not two.

## Consequences

- The three wrong consumers are fixed: `sections.evidence`, `render.glance`, and
  `research.search_sources`. `research.search_facts` was already right and is now the
  shared helper.
- **The other four modules that filter on `request_id` are correct as they stand**, and the
  reason is recorded here so nobody re-derives it: `evaluations`, `red_team`, `escalation`
  and `sources` report *on the run* rather than *about the company*, and each legitimately
  wants everything the run touched. The tier lookup in `evidence.py` that resolves a cited
  source's authority is the same case — it must resolve any id the content cited.
- Peer acquisition now stamps the peer's own identity on the peer's own document, so it is
  contained rather than removed. Task P4 withdraws the acquisition for an unrelated reason
  — no price feed means no computable multiple — and the scoping rule stands either way,
  which is the point of doing both.
- The company row is now created before the fetch on both the subject and the peer paths,
  because a document cannot record whose it is before the company exists. The cost is a
  company row for an issuer whose filings could not be read: inert, and honest, since EDGAR
  resolved the entity either way.
- **An unresolved request sees nothing rather than everything.** `visible_facts` with a NULL
  company returns no rows by construction. That is the conservative direction: an empty
  at-a-glance block is a worse report than a full one and a much better one than three
  issuers presented as a single quarter.
- Migration 0042 backfills both columns. A document's issuer comes from the facts parsed out
  of it, and only where they agree — `HAVING COUNT(DISTINCT company_id) = 1` — so a document
  this platform cannot attribute is left NULL rather than guessed at. A request's subject
  comes from ticker and exchange, deliberately *not* from the documents-agree rule: a
  request that ran with a peer set holds nine companies' documents, so that rule answers
  NULL, and a NULL subject would blank a report the operator already has. It is the
  historical resolution applied once to historical rows, not a new dependency on a weak key.
- `tests/test_evidence_is_the_subjects.py` is the enforcement, and is the real deliverable
  of this ADR. Two lines of predicate are exactly what a later refactor drops without
  noticing, so the tests pin the four properties by the shape of the live failure: a peer
  outranking the subject on both orderings, an issuer-less document staying visible, and a
  second run of the same company still seeing the first run's facts.

## Alternatives considered

**Stop putting peer documents under the request.** Giving peer acquisitions their own scope
would have closed today's leak without a new column. Rejected: the sources page would then
be unable to show what the run actually fetched, and the next feature that legitimately
touches a second company — an acquirer and a target, a parent and a subsidiary — would meet
the same failure with nothing in place to catch it.

**Filter in the section prompt.** Rejected on the platform's oldest rule. Asking a model to
ignore evidence it has been shown is not a control; the model in this very run did refuse
the foreign data and say why, which was admirable and is not something to depend on.
