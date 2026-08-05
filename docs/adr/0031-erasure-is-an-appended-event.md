# ADR 0031 — Erasure is an appended event, and a capability the storage path does not have

**Status.** Accepted
**Date.** 2026-08-05
**Amends.** ADR 0008, which made artefacts immutable and gave the store no delete path.
**Required by.** ADR 0030 route 2 — the operator keeps the EODHD personal plan and builds for
internal use, which makes the deletion clause live.

## Context

ADR 0008 built an artefact store with no `delete`, no `update` and no `move`, and said why:
an artefact's address is the hash of its content, so one that changes is a different artefact
and one that vanishes takes a report's evidence with it. It also left a door open, in the
protocol's own docstring:

> Retention and erasure, when they arrive, will be a deliberate operation with its own audit
> trail, not a method on this interface.

They have arrived. EODHD's subscription agreement obliges the subscriber to destroy **every
copy** of the data within a month of the subscription ending, and may ask for confirmation. An
immutable, no-delete store is precisely a store that cannot comply.

**This is not EODHD's doing.** `docs/PLAN.md`'s risk register T16 already called for a
retention policy — artefacts referenced by an immutable report never deleted, unreferenced
artefacts collected after 90 days, soft-delete plus an audit event — and it was never built. A
licensed source is simply the first one that makes it load-bearing rather than prudent.

## Decision

### The bytes are separable from the provenance, and only the bytes go

A purge removes the payload from the backend. The `artefacts` row, its SHA-256, its size, its
media type, its storage key, every `source_documents` row pointing at it and every citation
resolved against it all survive untouched.

So the provenance chain stays answerable — *which* bytes, from *where*, at *what* time,
hashing to *what*, verified by *which* method on *which* date. The one question that stops
being answerable is "show me those bytes again".

### Erasure is recorded as a row, not as a flag

`artefacts` rejects every UPDATE by database trigger, and that is invariant 1 expressed in the
schema rather than in a service somebody has to remember. Marking a row purged would have
meant relaxing that trigger to permit *some* columns to change, turning a rule anybody can
state into a rule anybody has to read carefully.

`artefact_purges` is append-only, one row per artefact, `RESTRICT` on the foreign key so that
deleting an artefact cannot take the explanation with it. It carries the reason in words, the
actor, the bytes freed, and **the licence note as it stood at acquisition** — a purge has to be
defensible against the terms in force when the data arrived, not against today's.

### Erasure is a capability, not a convention

`aer.storage.retention.PurgeableStore` is a **separate protocol** from `ArtefactStore`. A
service wired with the ordinary store cannot delete anything — not because it politely does
not, but because the type it holds has no method for it. Exactly one module asks for the
narrower interface, and a test asserts that `ArtefactStore` still has no `purge`, `delete`,
`remove`, `update` or `move`.

This is the same shape as `ValuationMandate` in ADR 0029: a capability you must be handed
rather than a rule you must remember.

### Three refusals

1. **A `PERMANENT` provider is never purged.** Filings and official statistics have no
   deletion obligation and invariant 1 has the opposite one. The retention class lives on the
   `FetchPolicy`, beside the licence note it comes from, so a paid feed added without
   classifying it fails a test rather than creating data with a silent expiry date.
2. **A purge with no stated reason is refused.** "Licence" is not a reason; "the EODHD
   subscription ended on 2027-03-01 and the agreement requires deletion within a month" is,
   and it is what somebody reads when a citation will not resolve.
3. **Purging twice is refused**, in the service and by a unique constraint.

## What is lost

**A citation into a purged artefact can never be re-verified.** It can be shown to *have been*
verified — against a named hash, on a date, by a recorded method — and the excerpt cannot be
checked against the document again, because the document is gone.

That is a genuine reduction in what this platform promises, and it is stated here rather than
engineered around, because the alternative was not keeping the bytes: it was not having the
source at all. A report resting on licensed data is a report whose evidence has a shelf life,
and a reader is better served by knowing that than by a verification that quietly stops
happening.

The mitigation is scope. Only EODHD is `LICENSED`. Every filing, every registry document,
every official statistic and every macro vintage remains permanent and re-verifiable for ever.

## Consequences

**A near-miss worth recording.** The first implementation classified `SEC_EDGAR` as `LICENSED`
by accident — a find-and-replace that matched two policies where it should have matched one.
That would have made every filing purgeable. `test_the_public_sources_are_all_permanent`
caught it on the first run, which is the argument for testing what must *not* happen rather
than only what should.

**Cloud storage and backups are unresolved.** EODHD's terms say "subscriber's premises" and do
not define whether a controlled cloud environment qualifies, nor whether backups, replicas and
disaster-recovery copies count as copies. This platform is local-first, so the immediate answer
is a local disk; anything else needs the written agreement ADR 0030 describes.

**The 90-day garbage collection in T16 is still not built.** This ADR covers erasure under a
licence obligation only. Unreferenced artefacts from failed runs still accumulate, and that is
a separate piece of work with a different justification.
