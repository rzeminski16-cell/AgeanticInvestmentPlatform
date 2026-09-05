# One-off operator scripts

Not part of the application. These are read-only SQL, for questions the interface does not
answer. Run them through the recipes below rather than by hand — they go through the
container, so no local `psql` is needed, and the output is written by `psql` rather than by
the shell.

## Diagnosing a run that did not draft (roadmap §2.1)

Two steps. Neither writes anything to the database.

```sh
just runs                    # 1. Find the run. Twenty most recent, with how many
                             #    sections did not generate.
just diagnose-run <the-uuid> # 2. Export it, to run-diagnosis.json in this directory.
```

**Why not a `psql` one-liner.** This file used to give one, against `$DATABASE_URL`, and it
could not be followed on the machine that needed it. Three reasons, each of which the
recipes avoid:

- **There may be no local `psql`.** The database runs in a container (`docker-compose.yml`),
  and installing a client to read it is a detour.
- **`AER_DATABASE_URL` is not a `psql` connection string.** It carries the SQLAlchemy
  dialect — `postgresql+asyncpg://…` — which `psql` does not reject: it ignores the scheme
  and tries a local socket, so the failure reads as "is the server running?" rather than as
  "wrong URL". And `$DATABASE_URL` is not a variable this platform sets at all.
- **`>` does not mean the same thing in every shell.** Windows PowerShell writes UTF-16
  through it, which corrupts the JSON. `just diagnose-run` has `psql` write the file itself.

If you would rather run it by hand, this is what the recipe does:

```sh
docker compose cp scripts/export-run-diagnosis.sql postgres:/tmp/export.sql
docker compose exec postgres psql -U aer -d aer -At \
  -v job_id=<the-uuid> -o /tmp/run-diagnosis.json -f /tmp/export.sql
docker compose cp postgres:/tmp/run-diagnosis.json run-diagnosis.json
```

The export is one JSON object: the job, the mandate, every step with its `attempt` count, one
row per model call with its token counts and stop reason, every section with its status,
confidence and stated reason, and the size of the evidence pack it had to work from.

**It leaves out the things you would not want to hand over.** No artefact bytes, so no fetched
filings and no model prompts or responses — only the references to them. No section prose, only
its length. No user row, no attestations, no transactions, no portfolio. That is a default
rather than a guarantee: read the file before you send it.
