# 4. Postgres and Redis, running locally, configured through one typed object

- **Status:** Accepted
- **Date:** 2026-07-27

## Context

The platform needs a system of record, a job queue, and a way to configure both. Three
requirements shape the choice:

1. **Local-first.** The research runs on the operator's own machine. Filings, extracted
   financials, draft theses and API credentials stay there by default. This is a privacy
   and control decision, not a cost one.
2. **Deployable later without redesign.** The same code must eventually run on a server
   for multiple users, so nothing may depend on being single-process or single-machine.
3. **Auditable.** Every run records its steps, costs, sources and calculations, and those
   records must survive crashes and be queryable months later.

## Decision

**PostgreSQL 16 and Redis 7, in Docker Compose, with all published ports bound to
`127.0.0.1`, configured through a single typed `Settings` object.**

### Why Postgres

The data model is relational and heavily cross-referenced: a claim cites a source
document, which stores an artefact, which was fetched during a job step, which belongs to
a run, which produced a report. Foreign keys and transactions are the point, not an
overhead. It also gives us `JSONB` for semi-structured extraction payloads, native enums,
`CHECK` constraints for domain rules, and — later — `pgvector` for prior-research
retrieval. One engine covers all of it.

### Why Redis

ARQ needs a broker. Redis also serves as the shared rate-limit token bucket across
workers, which matters because SEC EDGAR's fair-access limit applies per IP, not per
process. Run state deliberately does **not** live in Redis — it lives in Postgres, in
`job_steps` — so losing Redis costs an in-flight queue entry, never an audit trail.

### Why Docker Compose rather than native installs

Development is on Windows. Native Postgres on Windows is a per-machine adventure, and the
whole point is that a fresh checkout works the same everywhere. Compose also pins exact
versions, so a Postgres upgrade is a reviewed change rather than something that happens to
a developer's laptop.

### Ports bound to loopback

Every published port is written `127.0.0.1:5432:5432`, not `5432:5432`. Docker manipulates
the host firewall directly when publishing ports, so the short form exposes the service to
the entire local network regardless of the operator's firewall settings. On untrusted wifi
that is a database open to the world. The loopback prefixes are load-bearing.

### One typed configuration object

`aer.config.Settings` reads `AER_*` environment variables and is the only place
configuration is interpreted. Four properties it guarantees:

- **Secrets never render.** Credentials are `SecretStr`, which masks in `repr()` and
  `str()`. A stray f-string in a log line cannot leak a key.
- **Every problem is reported at once**, named by environment variable, so configuring a
  fresh machine takes one pass rather than one run per mistake.
- **Construction has no side effects.** `ensure_directories()` is explicit and called at
  startup; merely importing a module never creates directories.
- **Only `http_user_agent` is required.** Provider keys are optional at startup and
  asserted at the point of use, so a missing EODHD key never blocks work on SEC ingestion.

### The development database password

`docker-compose.yml` defaults `POSTGRES_PASSWORD` to `aer_local_dev`. A committed
credential is normally indefensible; here it is deliberate and bounded:

- the port is reachable only from loopback,
- the database holds no production data and no credentials,
- and `docker compose up -d` must work immediately on a fresh checkout, which is an
  acceptance criterion for the local environment.

It is overridable via `AER_POSTGRES_PASSWORD`, is deliberately non-secret-looking so it is
never mistaken for a real credential, and carries an inline comment saying it must be
replaced for any non-local deployment. Recording it here rather than leaving it implicit
is the point: this is a considered trade-off, not an oversight, and a future reviewer
should be able to tell the difference.

## Consequences

- One `docker compose up -d` gives an identical environment on any machine.
- Moving to a server changes connection strings, not code.
- Docker Desktop becomes a prerequisite for development. Acceptable — it was already
  needed for the eventual deployment story.
- Postgres is heavier than the workload strictly requires at one report per week. The
  cost is a few hundred megabytes of RAM; the benefit is never migrating the data model.
- Because `Settings` construction is pure, tests can build configurations freely without
  temporary directories or cleanup — which is what makes the configuration test suite
  cheap enough to be thorough.

## Alternatives considered

**SQLite.** Genuinely tempting for a single-user local application: no daemon, no Docker,
a single file to back up. Rejected on three counts — no `JSONB` for extraction payloads,
weak concurrent-write behaviour under an async worker pool alongside a web process, and a
migration to Postgres later would touch the data model, the queries and the tests at once.
The cost of Postgres is paid up front and never again.

**Postgres for the queue as well (`SKIP LOCKED`), dropping Redis.** One fewer service, and
attractive. Rejected because ARQ is already the chosen orchestrator and expects Redis, and
because the shared rate-limit bucket wants a fast in-memory store rather than a table
being hammered by every outbound request.

**Native installs, no Docker.** Fewer moving parts on the developer's machine, at the cost
of version drift and Windows-specific setup pain. Rejected: reproducibility across
machines is worth more than avoiding one prerequisite.

**Reading `os.environ` directly where needed.** Simplest possible thing. Rejected because
it makes the three guarantees above impossible: credentials end up in logs, defaults drift
apart between modules, and there is no single place to add validation later.
