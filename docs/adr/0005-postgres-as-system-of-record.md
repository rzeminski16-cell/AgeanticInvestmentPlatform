# 5. PostgreSQL is the system of record; the schema enforces what it can

- **Status:** Accepted
- **Date:** 2026-07-27

## Context

The platform's central claim is that a report can be audited and reproduced: every figure
traces to a formula, every fact to a hashed source, every decision to a person and a
moment. That claim is only worth as much as the store behind it.

Three properties are needed, and they are not the same property:

1. **Durability** — a run's history survives crashes, restarts and the passage of months.
2. **Integrity** — records that should be impossible cannot be written, by *any* writer,
   not merely by the application.
3. **Provability** — after the fact, it can be demonstrated that the record was not
   altered.

The application is not the only thing that will ever write to this database. Scripts,
migrations, an ad-hoc `psql` session at 11pm, and eventually a second service all will.
Any rule enforced only in Python is a rule those writers do not have.

## Decision

**PostgreSQL is the system of record, and constraints that can be expressed in the schema
are expressed in the schema.**

### Native enums, not check-constrained strings

Status columns use native PostgreSQL enum types. An invalid status is rejected by the
database, so a typo in a maintenance script cannot put a job into a state the workflow
engine has never heard of.

The deliberate exception: **legal state transitions are not enforced in the database.** A
`CHECK` cannot see the previous value, and a trigger would hide business rules somewhere a
reader of the workflow code would never look. The database's job is to reject values that
are not statuses at all; the service layer owns which transitions are allowed.

### Domain constraints as CHECK constraints

Investment horizon between 1 and 240 months. Budgets strictly positive. Currency codes
exactly three characters. Portfolio weights within [0, 1]. `finished_at` never earlier
than `started_at`.

Each of these is also validated at the API boundary, and that duplication is intentional.
The API validation produces a good error message; the CHECK constraint makes the bad value
unrepresentable. A portfolio weight of 800% written by a script would otherwise silently
poison every portfolio-impact calculation downstream, and by the time it surfaced the
provenance trail would say the number was legitimate.

### `Decimal` for money, `TIMESTAMPTZ` for time

`NUMERIC`, never `float`: binary floating point cannot represent 0.1, and a research
platform that silently rounds cash flows is worthless. Every timestamp carries a timezone,
so "when did this run" remains answerable across daylight-saving boundaries.

### Retries append; they do not overwrite

`job_steps` is unique on `(job_id, step_key, attempt)`. A retry increments `attempt` and
writes a new row. A step that succeeded on its third attempt is a materially different
audit story from one that succeeded immediately, and that difference is exactly what you
need when a provider turns out to have been flaky.

### The audit log is hash-chained

Each `audit_events` row stores `this_hash = sha256(prev_hash || canonical_json(payload))`.
Altering any record invalidates every record after it, so a single edit cannot be made to
look consistent without rewriting the entire remainder of the log.

This makes tampering **detectable, not impossible** — anyone with write access can rewrite
rows. Detectability is the achievable property, and it is the one that matters when the
question is "can I trust this record of what happened".

Canonical serialisation is what makes it work: sorted keys, minimal separators, `Decimal`
rendered exactly rather than through a float. Without that, two records with identical
content would hash differently, verification would fail spuriously, and the natural
response would be to stop trusting the verifier rather than the data — the worst possible
outcome for an integrity control.

### Foreign keys chosen per relationship, not by default

- `research_requests → users`: **CASCADE**. Deleting a user removes their work.
- `jobs → research_plans`: **RESTRICT**. A plan a job ran against must not be deletable
  while the job survives, or the run loses the record of what it executed.
- `audit_events.job_id` / `request_id`: **no foreign key at all**. An audit record must
  outlive the thing it describes, including a deleted request. A foreign key here would
  quietly delete exactly the entries most worth keeping.

### A model change that is not migrated fails the build

A test compares the live schema against the ORM metadata and fails on any difference. When
first written it immediately caught two real defects: foreign-key columns had inherited
`server_default=gen_random_uuid()` from the primary-key type alias — so a missing FK would
have produced a random dangling id rather than an error — and `users.email` was `String`
in the model but `CITEXT` in the migration.

Neither would have failed at write time. Both would have surfaced much later as data that
should have been impossible.

## Consequences

- Invalid data is rejected regardless of which client writes it.
- Constraints are duplicated between Pydantic and the schema. Accepted deliberately:
  different jobs, different failure modes.
- Migrations must be written and reviewed for every model change. The drift test makes
  forgetting a build failure rather than a production incident.
- Tests need a real PostgreSQL. They run against a separate `aer_test` database inside a
  rolled-back transaction, and skip with a clear reason when no server is reachable, so
  `uv run pytest` still works on a machine with nothing running.
- Append-only on `audit_events` is currently a convention plus a hash chain, **not** a
  database permission. Revoking UPDATE and DELETE from the application role needs a
  separate migration role to exist first; it is recorded as a TODO in migration `0001` and
  belongs to the deployment phase.

## Alternatives considered

**Enforce everything in the application, keep the schema permissive.** Simpler migrations
and friendlier error messages. Rejected: it assumes the application is the only writer,
which is false for the whole life of the project. It also puts the guarantee in the layer
most likely to be bypassed at exactly the moment someone is in a hurry.

**Event sourcing.** A natural fit for an audit-first system, and genuinely attractive.
Rejected as disproportionate: it would mean projections for every read, and the audit
requirement is already met by a hash-chained log alongside ordinary tables. Revisit only
if the audit requirements outgrow what a chained log can express.

**Store the audit log outside the database** (append-only file, external service). Better
tamper resistance, because the application could be denied delete rights entirely.
Rejected for the MVP: it gives up transactional consistency between an action and its
audit record, which would allow a run to succeed while its audit entry was lost — a worse
failure than the one it prevents. The database-permission approach reaches most of the
same benefit without that trade.

**SQLite.** Already rejected in ADR 0004 for the storage engine generally. Worth noting
here that it would additionally have cost native enums, `JSONB`, `CITEXT` and real
`NUMERIC`, each of which is doing work in this schema.
