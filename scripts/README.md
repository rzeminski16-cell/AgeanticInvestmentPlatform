# One-off operator scripts

Not part of the application. These are read-only SQL you run against your own database with
`psql`, for questions the interface does not answer.

## Diagnosing a run that did not draft (roadmap §2.1)

Two steps. Neither writes anything.

```sh
# 1. Find the run. Twenty most recent, with how many sections did not generate.
psql "$DATABASE_URL" -f scripts/list-runs.sql

# 2. Export it, by the job id from step 1.
psql "$DATABASE_URL" -v job_id=<the-uuid> -At \
  -f scripts/export-run-diagnosis.sql > run-diagnosis.json
```

The export is one JSON object: the job, the mandate, every step with its `attempt` count, one
row per model call with its token counts and stop reason, every section with its status,
confidence and stated reason, and the size of the evidence pack it had to work from.

**It leaves out the things you would not want to hand over.** No artefact bytes, so no fetched
filings and no model prompts or responses — only the references to them. No section prose, only
its length. No user row, no attestations, no transactions, no portfolio. That is a default
rather than a guarantee: read the file before you send it.
