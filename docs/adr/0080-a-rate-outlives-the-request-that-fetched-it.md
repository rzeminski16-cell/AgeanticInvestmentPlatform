# ADR 0080 — A rate outlives the request that fetched it

**Status.** Accepted
**Date.** 2026-08-23
**Amends.** ADR 0078, in one column. Everything else that record decided stands: the table,
the two dates, the idempotent recorder, the point-in-time read, and the rule that a rate a
person typed is an attestation rather than a row here.
**Required by.** The purge. ADR 0078 was written before its column met
`services/requests.py`, and the two disagree.

## Context

ADR 0078 gave `fx_rates.source_document_id` a `NOT NULL` and an `ON DELETE RESTRICT`,
copying `financial_facts` and stating the reason plainly: on a rate, a nullable source
document *is* the hand-typed number the record exists to displace.

The reasoning is right and the column it produced is wrong, because a fact and a rate behave
oppositely when a request is deleted.

`purge_request` removes a request and everything derived from it, walking `CASCADE` edges —
"the path the database would itself propagate a delete along". `financial_facts` has no such
path: it is pinned to its source document by a `RESTRICT`. So it is taken **by policy**, as
`FACTS_FOLLOW_THEIR_DOCUMENT`, and the policy is correct — a revenue line extracted from a
filing fetched for one company's research is that research, and keeping it after the request
is gone would leave a fact nothing explains.

**A rate is not that.** The euro's reference rate for 28 June is not about the company that
happened to be under research when it was fetched. The portfolio needs it every day the book
is open. A second research request on an unrelated company needs the same row. A published
report's calculation lineage cites it, and that report is immutable. Deleting one research
request must not remove an exchange rate from the platform.

Which left `fx_rates` in the position `tests/test_request_removal.py` was written to catch:
outside the purge scope, and pinned to something inside it by a `NOT NULL RESTRICT`. The
purge would refuse — correctly, and permanently. The operator's only recourse would be to
delete a rate they must keep, so the request would simply never be purgeable. That test
found this before a row existed, which is what it is for.

## Decision

**`fx_rates.source_document_id` becomes nullable with `ON DELETE SET NULL`, and the row
carries the artefact's hash in a `NOT NULL` column of its own.**

Two halves, and the second is what makes the first safe.

### The pointer may be lost

Nullable and `SET NULL` is the shape `macro_observations` and `price_bars` already have, and
for the same reason: external data acquired under one run and used by every other. A rate
outlives the request that fetched it exactly as a closing price does.

### The hash may not

`artefact_sha256` is `NOT NULL`, sixty-four lowercase hex characters, and it is the digest
of the bytes the rate was parsed from.

**This keeps ADR 0078's guarantee more completely than the column it replaces.** That record
wanted the schema to refuse a rate with no publication behind it. A `NOT NULL` pointer did
that at insert and then stopped: after the purge nulled it — or, under the original shape,
after a `SET NULL` anywhere — the row would carry no evidence claim at all. A hash cannot be
produced for bytes nobody fetched, cannot be invented by an operator typing a rate they
remember, and does not degrade. The door ADR 0078 was closing stays closed, and stays closed
afterwards.

It is deliberately **not** a foreign key to `artefacts.sha256`. The artefact row is
collectable (`aer gc-artefacts`) and its payload is purgeable (ADR 0031); the claim this
column makes is about the bytes, not about whether this platform still holds them. A digest
that outlives its row is the strongest form of "where did this come from?" there is —
anybody with the same response can check it — and a foreign key would make the weaker,
local fact the constraint.

## Consequences

**A purge works, and takes nothing it should not.** A research request that acquired rates
purges cleanly; the rates stay; the documents go; each surviving row keeps a hash naming
what it was parsed from.

**A rate's lineage node degrades honestly.** Before a purge it names the document — URL,
provider, licence, retrieval time. After one it names the digest. "Show me that response" is
lost and "what were these numbers taken from" is not, which is the same trade `price_bars`
made under ADR 0031 and is stated here so a reader of the node knows which they are looking
at.

**`financial_facts` is still the only table taken by policy**, and the test that says so
goes on saying it. `fx_rates` leaves that position rather than joining it.

**One decision in ADR 0078 is superseded and the rest is not.** The `NOT NULL` on the
document pointer, and the sentence explaining it, are replaced by this record. Every other
claim that record makes — including that a hand-typed rate is an attestation and never an
`fx_rates` row — holds unchanged, and this record strengthens rather than relaxes it.
