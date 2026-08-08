# ADR 0044 — An aggregate is dated by its newest component

**Status:** Accepted
**Date:** 2026-08-08
**Supersedes:** nothing. Refines ADR 0015's treatment of undated sources.

## Context

`https://data.sec.gov/api/xbrl/companyfacts/CIK{n}.json` is not a filing. It is a view the
SEC assembles on request from every XBRL fact an entity has ever tagged, across every
filing it has ever made. It carries no publication date, because it was not published — it
was generated, for us, when we asked.

Task 15 made an undated source a quarantined source, and the reasoning was right: under
point-in-time rules a document nobody can date is a document nobody can prove was public
before the as-of date, and citing one is look-ahead bias waiting to happen.

The consequence was not right. Company facts is the only source most runs hold, so every
run quarantined its only document, no claim could cite anything, and the sources page said
`quarantined: no_publication_date` about the one thing the whole report stood on. The rule
was doing its job against a document it was not written for.

## Decision

**A generated aggregate takes the publication date of the newest thing inside it.**

For company facts that is `max(filed)` across the facts it carries, exposed as
`CompanyFacts.latest_filed` and recorded with a `publication_date_confidence` of 0.9 to
mark it as derived rather than read.

The claim this makes is narrow and true: *the document as fetched cannot have existed
before its newest component was filed.* It is not a claim that the document was published
on that date — nothing published it — but the admissibility question is only ever "could
this have been available to me on the as-of date?", and for that the earliest possible
moment of existence is exactly the right bound.

An aggregate carrying no facts keeps no date and stays quarantined. There is nothing to
infer from, and inventing a date would be worse than the quarantine it replaces.

## Consequences

**A current run gets a citable primary source**, which is the whole point. The document
dates a few weeks or months back, comfortably before today's as-of date, and the claims
that stand on it can finally be recorded.

**A historical run quarantines the aggregate, and should.** A run as at 2022 fetches the
company facts document *as it exists now*, containing 2025 filings; its derived date is
2025, later than the as-of date, and it is refused as published-after-as-of. That is not a
regression from `no_publication_date` — it is the same refusal for a reason that is
actually true, and the operator can see and override it on the sources page rather than
being told the platform simply could not tell.

The right source for a historical run was never the aggregate: it is the filings that
existed then, which is what the submissions index gives (see the acquire step). The facts
themselves are unaffected either way — point-in-time selection filters them on `filed_date`
at acquisition and again when they are read, so a quarantined aggregate never leaks a
figure that was not public.

**The confidence figure is load-bearing.** A date the platform worked out and a date
printed on a filing are both dates, and a reader comparing two sources deserves to see
which is which. 0.9 rather than 1.0 says "inferred, soundly".

## Alternatives considered

**Leave it quarantined and let the operator override.** The override exists and works, but
requiring it on every run for the only source every run has is not a policy, it is an
obstacle with a policy's paperwork.

**Use `retrieved_at`.** The date we fetched it is a fact about us, not about the document,
and under point-in-time it is always "today" — which would make every aggregate admissible
for every as-of date, including ones long past. That is precisely the look-ahead the rule
exists to prevent.

**Use the earliest `filed`.** It would keep historical runs admissible, which is the
tempting part, and it would be a lie: the document plainly contains material filed after
that date.

**Treat aggregates as a source kind whose admissibility is judged per fact.** Coherent, and
a bigger change than the problem warrants — citations point at documents, so the concept
would have to reach the claims table and the verifier. Worth revisiting if a second
aggregate source appears.
