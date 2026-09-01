# ADR 0096 — A malformed claim costs the claim, not the section

**Status.** Accepted
**Date.** 2026-09-01
**Extends.** ADR 0057, which decided that code may narrow a billed reply rather than
discard it, and named the two repairs. This adds the third.
**Required by.** Roadmap §2.1, diagnosed from the MSFT run's exported record.

## Context

Eight of eighteen sections failed to draft on the run that finally produced a diagnosis.
The standing hypothesis was a thin evidence pack; the record refutes it — every section
was dealt 25–43 facts, 3–9 excerpts and 11–29 calculations, and the run held 780
calculations and 153 claims. Nothing starved.

**Four of the eight died on one rule**, and it is the rule the wire format cannot carry.
`ProposedClaim` requires that a numeric claim names exactly one figure and carries at
least one citation. That is a relation *between* fields; JSON Schema has no way to state
it, so the schema that reaches the API requires only `statement` and `kind`. The server's
constrained decoder is therefore free to emit `{"kind": "numeric"}` with nulls, and it
does — 23 of the run's 70 model calls were rejected after they had been paid for.

The rule was a Pydantic `model_validator`, so it raised during the parse. That is the
part that made it fatal rather than expensive:

- a raise means the reply never becomes an object;
- no object means `last_candidate` is never set;
- no candidate means ADR 0057's salvage has nothing to narrow.

So a section lost its content entirely — four of them recorded zero bytes — and one
malformed claim took a dozen sound claims and a finished draft down with it. The writer's
own prompt already says this about *length* bounds: "a reply that overruns them is thrown
away after it has been paid for." Nobody applied the reasoning to the claim rule.

This is the same blast radius, for the same reason, that `RedTeamChallenge.cites_nothing`
was moved out of a schema to stop: *one weak objection out of six cost the five good ones
beside it.*

## Decision

**The cross-field claim rule is read by the caller, not raised by the schema.**
`ProposedClaim.malformed_reason` returns what is wrong, or `None`.

**A malformed claim is a refusal like any other.** `validate_draft` reports it per claim,
so a retry is told exactly which claims to fix, in the same list as every other problem.

**The salvage drops it, and what rested on it goes too.** The claim repair runs *before*
the numeral repair, deliberately: a dropped claim's statement no longer covers the
numerals it covered, so the sentences that stood on it fail the numeral rule and are
removed by the repair that already exists. The section keeps nothing it can no longer
support.

**Cover comes only from claims that stand up.** `validate_draft` builds its covered set
from numeric claims with no malformed reason, so a claim about to be dropped cannot lend
lineage to a figure on its way out.

### What is not weakened

Nothing reaches a record. A malformed claim is dropped before `record_draft_claims` runs,
and the `claims` table's own check constraint — a numeric claim names exactly one figure —
is untouched and still the last word. The salvage declines unless the narrowed draft
passes **full** revalidation, exactly as ADR 0057 requires, so this can only turn a refused
draft into a conforming one.

## Consequences

### Accepted costs

- **A dropped claim is a citation the reader does not get.** Where its statement covered a
  numeral, the sentence goes with it and the existing "sentences were removed" disclosure
  covers the visible loss. Where it covered none, the loss is a footnote, disclosed on the
  section's own record and not on the front page.
- **The front coverage notice keeps its two counters**, shortened and pruned. A third was
  considered and declined: the reader-visible consequence of a dropped claim is the removed
  sentence, which `pruned` already counts, and a counter for an invisible change would be
  noise on the page that exists to name what a reader should not miss.
- **A section can now publish with fewer claims than the model proposed.** That is the
  point, and it is strictly better than publishing nothing.

### What this buys

A run stops losing whole sections to a rule the API cannot be asked to honour. On the
diagnosed run that is four sections of eight.

### What it does not decide

**Whether the claim contract should be expressible on the wire at all.** A discriminated
union on `kind` would let the decoder enforce the rule and make a malformed claim
unrepresentable rather than merely survivable. That is the better fix and it depends on
vendor support this platform has not established; it wants the live contract suite, not an
offline assumption. Until then, this record is what stops the failure being fatal.
