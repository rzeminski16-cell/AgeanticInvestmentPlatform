# 8. Evidence is content-addressed, and artefact rows are immutable in the database

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

The central claim of this platform is that a report can be audited: every figure traces to
a formula, every fact to a source document, and that source document can be re-read months
later to confirm it says what the report claims it says.

That claim rests entirely on the storage layer. If a stored filing can change — by
tampering, by bit rot, by a well-meaning script, by being overwritten with a newer version
of the same URL — then every citation resting on it becomes unverifiable, and worse,
*silently* unverifiable. A citation that verifies against altered evidence looks exactly
like one that verifies against the original.

Three questions had to be settled: how artefacts are named, how they are written, and what
stops them changing afterwards.

## Decision

### Address by content, not by name

An artefact's identity is the SHA-256 of its own bytes. Files live at
`<root>/<aa>/<bb>/<full-sha256>`.

Four properties follow, none of which had to be separately built:

- **Deduplication is free.** The same filing fetched twice is one file and one row.
- **Tampering is detectable.** The address *is* the digest, so a file that no longer
  hashes to its own name has been altered. `verify(sha256)` re-reads from disk and
  recomputes; it never caches, because a cached integrity check is a check performed once
  and assumed forever.
- **Overwriting is meaningless.** Different content is a different address. There is no
  operation that could replace an artefact with different bytes, because such an operation
  has nowhere to write them.
- **Path traversal is structurally impossible.** The only thing ever interpolated into a
  storage path is a value validated against `^[0-9a-f]{64}$`. `../../etc/passwd` never
  reaches a path join. Sanitising a bad path afterwards is a game of catching every
  encoding; refusing to construct one is not.

The two-level fan-out is not decoration: a single directory with tens of thousands of
entries makes every lookup slow on most filesystems.

### Write atomically, then read back

Writes go to `<root>/tmp/<uuid>.part`, are `fsync`ed, and are moved into place with
`os.replace`, which is atomic within a filesystem. A reader therefore sees either nothing
or the whole artefact, even if the process is killed mid-write.

The `fsync` before the rename is the part that is easy to skip and expensive to omit:
without it the rename can reach the disk before the data does, so a crash in between
leaves a correctly named file holding the wrong bytes — corruption that looks exactly like
valid evidence.

After the move, the file is read back and re-hashed. **This proves less than it appears
to**, and it is worth being precise: a read immediately after a write usually comes from
the page cache, so it does not prove the bytes reached the platter. What it does prove is
that the streaming and hashing code did not disagree with itself. Catching actual disk
corruption is the job of `verify()` called later, after the cache has moved on.

### The size cap is enforced *while* streaming

`put_stream` checks the running total as each chunk arrives and abandons the write partway
if it exceeds `AER_MAX_ARTEFACT_BYTES`. A limit checked only after the response has fully
arrived is not a limit; it is a report. This is the defence against a decompression bomb
and against a response that never ends.

Nothing reaches a content address until the digest is known, so an abandoned stream leaves
at most one file in `tmp`, which is cleaned up on the spot and again by `prune_temp_files`
if the process died first.

### Immutability is a database trigger, not a convention

`artefacts` has a `BEFORE UPDATE` trigger that raises. Task 6's specification allowed
either a trigger or a documented TODO; the trigger was chosen for the reason recorded in
ADR 0005 — the application is not the only thing that will ever write to this database.
Scripts, migrations and an ad-hoc `psql` session all will, and a rule enforced only in
Python is a rule those writers do not have.

The trigger raises rather than silently returning `OLD`. A no-op would make the UPDATE
appear to succeed while changing nothing, and the caller would carry on believing the edit
had landed — worse than a refusal.

**DELETE is deliberately left possible.** Retention, erasure and a mistakenly fetched
document are all legitimate reasons to remove an artefact, and a table nothing can ever be
deleted from is a table that eventually forces someone to disable the protection
wholesale. There is no delete path in the service layer, so it cannot happen by accident,
and `source_documents.artefact_id` is `ON DELETE RESTRICT` so an artefact still cited by a
provenance record cannot be removed while that record stands.

### Content and provenance are separate tables

An `Artefact` is bytes. A `SourceDocument` is the *story* of those bytes — the URL, the
publisher, the date, the licence, whether robots allowed it. Two fetches of the same PDF
share one artefact and produce two source documents, because they happened at different
times and possibly under different terms.

Separating them keeps the audit trail honest. Bytes are identical or they are not;
provenance is a set of claims about those bytes, and claims are the thing that can be
wrong.

### A source that cannot be dated is quarantined, not discarded

Under point-in-time rules, a document with no establishable publication date is recorded
with `quarantined = true` and `quarantine_reason = 'no_publication_date'`.

This is the cheapest place to stop look-ahead bias, and the only place where the decision
is still obvious — a month later nobody remembers why a particular document had no date. A
document that cannot be dated cannot be shown to have existed before the request's as-of
date, so it cannot honestly support a claim made as at that date. It might be from last
week; it might be from after the quarter under analysis, in which case a report citing it
would quietly be using information nobody had at the time.

It is **kept**, because discarding it would erase the record of what the run looked at.
"We saw this and refused to use it" is a more useful audit trail than silence, and the
refusal is written to the hash-chained audit log so it survives the process that decided
it.

The database refuses a quarantine with no reason and a reason with no quarantine. Neither
state means anything, and a flag nobody can act on is worse than no flag.

## Consequences

### Accepted costs

- **Every write is hashed twice** — once streaming in, once reading back. At the scale
  this platform operates (a few hundred megabytes per report), that is not worth
  optimising away.
- **`verify()` is O(file size)** and is deliberately not cached. Bulk verification of a
  whole store will need to be a scheduled job rather than something done inline.
- **An artefact row cannot be corrected.** A wrong `media_type` means inserting a new row
  and leaving the old one, or a deliberate delete-and-reinsert. That is the intended
  friction.

### A bug this design surfaced

The concurrent-store recovery path — one writer wins the unique constraint on `sha256`,
the loser reads the winner's row — was written with `session.add()` *outside* the
savepoint that wraps the flush. Rolling back to a savepoint restores the session to its
state at that point, so an object added beforehand stays pending, and the next autoflush
retried the same doomed INSERT outside any savepoint, poisoning the whole transaction.

It was invisible for a while because ten `asyncio.gather`ed writers reliably serialise:
the first commits before the second looks, so the recovery branch never ran. The
outcome-level test passed against broken code. Forcing the interleaving deterministically —
making one session's first lookup return `None`, then letting the real insert, violation
and recovery run — is what exposed it. Both tests are kept: one asserts the outcome under
whatever timing occurs, the other exercises the branch.

### Explicitly out of scope

No S3 or MinIO backend. `ArtefactStore` is a `Protocol` and `storage_key_for` returns a
backend-relative key rather than a path, so adding one later is an implementation rather
than a refactor — but adding it now would be a second untested code path for a deployment
that does not exist.
