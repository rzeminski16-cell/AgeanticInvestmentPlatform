# Manual verification

Everything below is something the automated suite cannot prove on its own — because it
needs Docker, or a real browser you are looking at, or the real SEC, or a real model call
that costs real money.

Work through it in order. Each check says what to run, what you should see, and — where it
matters — **what would mean something is wrong**. A check that "seems fine" is not a pass;
compare against the stated expectation.

Commands are PowerShell. On macOS or Linux they are identical apart from `copy` → `cp`.

**Cost warning.** Sections 1–6 and 9–12 spend nothing. **Section 7 makes one real model
call and costs a few pence** (one Opus 5 planner call, typically £0.03–£0.06). Section 8
depends on it. Nothing else in this document reaches a paid API.

---

## 1. Local infrastructure

This is the one acceptance criterion from Task 2 that has never been verified, because
image pulls are blocked in the sandbox this was built in.

```powershell
docker compose up -d
docker compose ps
```

**Expect:** `postgres` and `redis` both `running` and `healthy`.

```powershell
docker compose ps --format json
```

**Expect:** ports bound to `127.0.0.1:5432` and `127.0.0.1:6379`.

**Wrong:** any `0.0.0.0:` binding. That would expose your database to your whole network.

```powershell
docker compose exec postgres pg_isready -U aer -d aer     # -> accepting connections
docker compose exec redis redis-cli ping                   # -> PONG
```

The object store is behind a profile and should **not** have started:

```powershell
docker compose ps minio        # -> nothing
docker compose --profile objectstore up -d
docker compose ps minio        # -> now running
docker compose --profile objectstore down
```

---

## 2. Setup from a clean checkout

The point of this one is that `.env.example` is sufficient — that a new machine needs one
value filled in, not a scavenger hunt.

```powershell
copy .env.example .env
```

Edit `.env` and set **only** `AER_HTTP_USER_AGENT`, to a real name and a contact address
you actually monitor — for example `Ageiantic Research rzeminski16@gmail.com`. The SEC
requires this and will block a generic one.

```powershell
uv sync --all-groups
uv run alembic upgrade head
uv run aer seed-user --email you@example.com
uv run aer version
```

**Expect:** migrations run to `0007`, the user is created, and `aer version` prints a
version and a git SHA.

```powershell
uv run aer seed-user --email you@example.com
```

**Expect:** it says the user already exists and changes nothing. Running it twice must be
safe.

### Secrets do not leak into the configuration dump

```powershell
uv run just config
```

or, without `just`:

```powershell
uv run python -c "from aer.config import load_settings; print(load_settings().model_dump_json(indent=2))"
```

**Expect:** every key field renders as `**********`.

**Wrong:** any actual key value visible. Put a fake key in `AER_ANTHROPIC_API_KEY`
temporarily and re-run if you want to see the masking work rather than assume it.

---

## 3. The static gates and the test suite

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --ignore=tests/e2e
uv run pytest tests/e2e
```

**Expect:** clean, clean, `Success: no issues found in 121 source files`, then
**1210 passed** and **27 passed**.

Run the two pytest commands **separately**, not as one `pytest`. Playwright's synchronous
API keeps an event loop running on the main thread for the life of its session, so any
async fixture that follows a browser test in the same process fails with
`Runner.run() cannot be called from a running event loop`. `just test-all` does the split
for you.

The e2e run needs Chromium. If Playwright has not downloaded one:

```powershell
uv run playwright install chromium
```

---

## 4. The tests actually test something

A suite that passes is worth nothing if it would also pass with the code broken. Break
each of these, watch it fail, then undo it with `git checkout <file>`.

| Break this | Where | Expect |
|---|---|---|
| Change `if self._budget is not None` to `if False and ...` | `src/aer/workflow/engine.py` | 4 failures in `TestTheBudgetGuard` |
| Add `import anthropic` at the top of `src/aer/runtime.py` | — | 2 failures in `TestTheImportBoundary` |
| Filter the section loop to the two known keys in `render_markdown` | `src/aer/render/markdown.py` | 5 failures in `TestAThirdSection` |
| Change `/reports/` back to `reports/` | `.gitignore` | 1 failure in `TestNothingUnderSrcIsIgnored` |
| Wrap the sleep in `contextlib.suppress(asyncio.CancelledError)` | `src/aer/api/sse.py` | 1 failure in `test_a_disconnected_reader_ends_the_stream` |

**Wrong:** any of these still passing. That means the guard it protects is not actually
being checked.

---

## 5. The web application, by eye

```powershell
uv run aer serve
```

Open <http://127.0.0.1:8000>.

- [ ] The landing page renders, and the **"Not investment advice"** badge is beside the
      product name at the top — not buried in the footer.
- [ ] `/healthz` returns 200.
- [ ] `/readyz` returns 200 with both `postgres` and `redis` reported.
- [ ] Stop Redis (`docker compose stop redis`), reload `/readyz`: **503**, and the body
      names Redis specifically rather than saying "something is wrong". Start it again.
- [ ] Stop Postgres, reload `/`: the page still renders and tells you the database is
      unreachable and how to start it. **Wrong:** a blank 500. Start it again.
- [ ] `/docs` renders the API documentation.
- [ ] Set `AER_APP_ENV=production` in `.env`, restart, reload `/docs`: **404**. Put it
      back to `development`.

### Every page carries the disclaimer

Visit each of `/`, `/requests`, `/requests/new`, and any request detail page. The footer
disclaimer must be on all of them. It lives in the page shell precisely so a page cannot
ship without it.

### The form works without JavaScript

Disable JavaScript in your browser (DevTools → Settings → Debugger → Disable JavaScript),
then submit `/requests/new` with a deliberately invalid value — an as-of date in the
future.

- [ ] The page comes back with an inline error naming the field.
- [ ] **Everything else you typed is still there.** Losing a page of carefully written
      focus questions to a validation error is a real failure, not a cosmetic one.
- [ ] Correct it and submit: you land on the request's detail page.

Re-enable JavaScript and repeat. The behaviour should be identical apart from the page not
reloading.

---

## 6. Request validation

Create requests through `/requests/new` and confirm each of these is **refused with an
explanation**, not silently accepted:

- [ ] An as-of date in the future.
- [ ] A fund by ticker: `SPY` on `NYSE`. The exchange is supported, so this must be
      refused on the *fund* rule and say so. A fund has no revenue and no margins; the
      whole analysis is a category error.
- [ ] A fund by name: company name `iShares Core MSCI World UCITS ETF`, ticker `IWDA`,
      exchange `LSE`.
- [ ] An investment trust: company name `Scottish Mortgage Investment Trust plc`, ticker
      `SMT`, exchange `LSE`.
- [ ] An OTC venue — pick `AQSE` in the exchange dropdown if it is offered, or post
      `"exchange": "OTCQX"` to `/api/requests`. The error should say the venue is OTC,
      not merely "unsupported".
- [ ] A malformed ISIN — change one character of `US5949181045` so the check digit fails.

In each case check the message names **which** rule refused it. "Not supported" tells you
nothing you can act on.

And confirm these are **normalised** rather than rejected:

- [ ] `msft` becomes `MSFT`, `nasdaq` becomes `NASDAQ`.
- [ ] A URL in "excluded sources" becomes a bare domain.
- [ ] A portfolio weight of `2.5%` round-trips as exactly `0.025` — check the detail page.
      **Wrong:** `0.024999999`. A weight that moves in the third decimal place because it
      passed through a float is a number you cannot reconcile.

---

## 7. A real research run

**This is the highest-value check in the document, and the only one that spends money.**

Two things happen here for the first time outside a test: a real Anthropic call, and the
SEC parsers meeting real EDGAR data. The fixtures in this repository were *constructed to
the documented API shapes*, not recorded from EDGAR — the sandbox this was built in cannot
reach `sec.gov`. So this run is the first evidence that the parsers handle the real thing.

### Set up

Put a real key in `.env`:

```
AER_ANTHROPIC_API_KEY=sk-ant-...
```

Confirm `AER_HTTP_USER_AGENT` is a real name and contact. EDGAR blocks generic ones.

You need **both** processes. Two terminals:

```powershell
uv run aer serve
```

```powershell
uv run arq aer.worker.WorkerSettings
```

**Expect** the worker to log `worker.started`. Without it the run is queued and nothing
happens — which is itself worth seeing once, so you recognise the symptom.

### Run it

1. Create a request for **Microsoft Corporation / MSFT / NASDAQ**, as-of date
   **2022-06-30**, point-in-time **on**, max spend **£2.50**.
2. On the request page, click **Start the run**.

- [ ] You land on the run console. It shows the steps as they complete and the spend
      rising.
- [ ] Within a minute or so it stops at **Waiting for you**.

**Wrong:** the console sits at `QUEUED` forever. That means the worker is not running or
cannot reach Redis.

### Gate 1

Click **Review the plan**.

- [ ] The plan summary describes what the run intends to do.
- [ ] The sources table names `sec_edgar` at tier `T1_REGULATORY`.
- [ ] **The plan contains no financial figures.** The planner is instructed never to state
      one, because every number in the report must come from deterministic code. If you
      see "revenue of approximately $198bn" in the plan summary, that is a real finding —
      tell me.
- [ ] Known risks are listed.
- [ ] The cost shown is what the planning call actually cost, in pence.

Click **Approve and continue**. You return to the console and the run resumes.

### Gate 2

- [ ] The run reaches **Waiting for you** again. Click **Review the draft**.
- [ ] The preview is the whole document, with a header, sections, footnote markers and a
      sources table.
- [ ] Approve it.

### The report

- [ ] The console shows a **View the report** button. Click it.
- [ ] The badge says **Approved and frozen**.
- [ ] Every figure in the report carries a footnote marker.
- [ ] Each footnote resolves to either a formula and a code version, or a URL, a retrieval
      date and a source tier.
- [ ] The word **"Unresolved citation"** does not appear anywhere. If it does, a footnote
      is pointing at something that no longer exists — tell me.
- [ ] The disclaimer is at the top and the bottom.
- [ ] Click **Download the archived Markdown**. It downloads.

### Sanity-check the number

For MSFT as at 2022-06-30 the report should show revenue compounding at roughly **16–17%**
between the earliest and latest filed periods available at that date.

**Wrong, and important:** a figure computed from revenue of **142,000,000,000** for FY2020.
That is Microsoft's *restated* FY2020 figure, filed in 2022 — after the as-of date. The
admissible figure is **143,015,000,000**, filed in 2020. If the restated number appears,
point-in-time enforcement has failed and the report contains look-ahead bias.

---

## 8. Verify the evidence yourself

The claim this platform makes is that every number traces to a hashed artefact. Check it
by hand rather than taking the report's word for it.

### The archived bytes are the bytes

Take the twelve-character digest from the report's **Sources** table, then:

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT sha256, size_bytes, storage_key FROM artefacts WHERE sha256 LIKE '<paste>%';"
```

The `storage_key` column is the path under `AER_ARTEFACT_ROOT`: the first two characters of
the digest, the next two, then the digest in full — `4e/ec/4eec429d…`. Hash the file:

```powershell
Get-FileHash -Algorithm SHA256 var\artefacts\4e\ec\4eec429d19b627e5...
```

- [ ] The hash equals the filename and equals the database row.

**Wrong:** any mismatch. That means stored evidence has been altered or corrupted.

### The report you downloaded is the report that was approved

```powershell
Get-FileHash -Algorithm SHA256 research-2022-06-30-xxxxxxxx.md
```

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT content_hash, immutable, approved_at FROM reports;"
```

The download also carries an `X-Artefact-SHA256` response header (visible in DevTools →
Network) which must match the artefact's digest.

- [ ] `immutable` is `true` and `approved_at` is set.

### The calculation records how it was made

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT function_ref, formula, output_value, output_unit, code_version FROM calculations;"
```

- [ ] The formula is there, and `code_version` is the git SHA the run used.

Then fetch `/api/calculations/{id}` in your browser and confirm the lineage resolves down
to the financial facts.

### The costs add up

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT category, model, units, amount_usd, amount_gbp, fx_rate FROM costs;"
```

- [ ] There is a separate row per category, each with the FX rate on it.
- [ ] The GBP total matches the spend shown on the console.
- [ ] The model is the one your `AER_MODEL_ROUTES` sends `planner` to.

### The point-in-time filter did its job

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT concept, period_end, value, filed_date FROM financial_facts WHERE concept = 'revenue' ORDER BY period_end;"
```

- [ ] **No row has a `filed_date` after 2022-06-30.**

---

## 9. The controls, deliberately provoked

These prove the guards fire. None of them spends money.

### The budget cap stops a run before it spends

Create a request with **max spend £0.01** — below what the planner step is projected to
cost. Start it.

- [ ] The console shows **Stopped on budget**.
- [ ] `SELECT * FROM costs WHERE job_id = '...'` returns **nothing**. The cap must stop the
      run *before* the call, not after paying for it.

### An approval cannot be transferred to a different plan

On a run waiting at gate 1, open DevTools, find the hidden `payload_hash` input, change one
character, and submit.

- [ ] The run does not proceed. It stays waiting.

**Wrong:** the run continuing. That would mean the gate accepts an approval of content
nobody was shown.

### A gate cannot be approved twice, or out of order

With a run waiting at gate 1:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/runs/<job-id>/gates/FINAL/decide" -H "Content-Type: application/json" -d "{\"decision\":\"APPROVED\",\"payload_hash\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}"
```

- [ ] 422, with a message saying the FINAL gate cannot be decided while PLAN has not been
      reached.

Approve gate 1 properly, then approve it again through the API.

- [ ] 422, saying it was already approved.

### CSRF

```powershell
curl.exe -X POST "http://127.0.0.1:8000/runs/<job-id>/gates/PLAN" -d "decision=APPROVED&payload_hash=..."
```

- [ ] 403, and no approval row is created. This matters more than it looks: the app has no
      authentication and listens on loopback, so any page in any browser tab could
      otherwise commission spending on your behalf.

### A killed worker resumes rather than repeating

Start a run, approve gate 1, and **kill the worker with Ctrl-C while it is acquiring**.
Restart it and re-approve nothing — the run resumes on its own next enqueue, or start a
second run of the same request to nudge it.

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT step_key, attempt, status FROM job_steps ORDER BY sequence;"
```

- [ ] All eight steps present, all `SUCCEEDED`, and **exactly eight rows** — a resumed run
      must not create a second row for a step it already did.
- [ ] `plan` and `acquire` show `attempt = 0`. Those are the expensive ones: the model call
      and the EDGAR fetch. A resumed run returns their stored output without executing them.

`gate_plan` and `gate_final` will show `attempt = 1` or more, and that is correct — a gate
is entered, pauses, and is entered again after you approve. Being re-entered is what
"resuming" means. It costs nothing.

```powershell
docker compose exec postgres psql -U aer -d aer -c "SELECT agent_role, model, input_tokens, output_tokens FROM agent_runs;"
```

- [ ] Exactly **one** planner row. The planner must not be paid for twice.

### Secrets never reach the log

With `AER_LOG_JSON=true`, run anything that logs and search the output for your API key.

- [ ] It does not appear. Redaction is by field name *and* by value shape, so even a key
      pasted into a URL is masked.

---

## 10. Sections are rows, not code

This is the property Phase 4's user-authored sections depend on. Prove it on your own
machine with an `INSERT` and no code change.

```powershell
docker compose exec postgres psql -U aer -d aer
```

```sql
INSERT INTO section_definitions
  (key, version, origin, title, position, required, output_contract,
   evidence_policy, token_budget, allowed_tools, applicability)
VALUES
  ('competitive_position', 1, 'builtin', 'Competitive Position', 150, true,
   '{"type":"object","title":"Competitive Position","required":["commentary"],
     "properties":{"commentary":{"type":"string","title":"Commentary"},
                   "observations":{"type":"array","title":"Observations",
                                   "items":{"type":"string"}}}}'::json,
   '{"min_sources":1,"requires_primary":true}'::jsonb,
   2000, '{}', '{}'::jsonb);
```

Start a **new** run and take it through both gates.

- [ ] The report has three sections.
- [ ] **Competitive Position** sits between Executive Summary and Historical Financial
      Analysis — position 150, between 100 and 200.
- [ ] Its sub-headings are **Commentary** and **Observations**, which came from the JSON
      Schema you just wrote. No template exists for this section.
- [ ] Footnote numbering is still continuous from 1 with no gaps or duplicates.
- [ ] You changed no Python.

Confirm the existing report is unaffected — a run pins the sections it started with:

- [ ] Re-open the earlier report. It still has two sections.

Clean up if you like:

```sql
DELETE FROM section_definitions WHERE key = 'competitive_position';
```

(If a report has already been rendered against it, the delete is refused. That is correct:
a definition a report was built from must not vanish underneath it.)

### Declared field order survives

```sql
SELECT output_contract::text FROM section_definitions WHERE key = 'executive_summary';
```

- [ ] The properties read `thesis`, then `key_points`, then `key_risks` — the order they
      were declared in.

**Wrong:** `thesis, key_risks, key_points`. That is keys sorted by length, which is what
JSONB does, and it means the column type has regressed to `jsonb`.

---

## 11. The console under real conditions

- [ ] Start a run and watch the console. Steps update **without you reloading**.
- [ ] Disable JavaScript and reload while a run is in progress: the page still shows the
      current state and refreshes itself every 5 seconds.
- [ ] Navigate away from the console mid-run, then check the worker and server logs. The
      event stream should stop. **Wrong:** the server continuing to poll the database for a
      page nobody is looking at.
- [ ] Open the console for a finished run: no auto-refresh, and a **View the report**
      button.

---

## 12. Things that should be impossible

- [ ] Open a run belonging to nobody: `/runs/00000000-0000-0000-0000-000000000000` → a
      page saying it is not available, **404**, and no stack trace.
- [ ] `/api/runs/<random-uuid>` → 404 with a machine-readable `code`, not a 500.
- [ ] Force an error and check the response body contains a request id and **no internal
      message and no traceback** — while the full traceback *is* in the server log.
- [ ] Every response carries an `X-Request-ID`, and that same id appears in the log lines
      for that request.

---

## What is not covered here, and why

**Restart resilience of the queue.** arq's own persistence is not something this project
tests; a run interrupted by a machine restart is recovered by re-enqueueing it, not
automatically.

**Retention and safe deletion.** Not built. `docs/PLAN.md` has it in Phase 2. There is
currently no supported way to delete a report and its evidence.

**Audit-chain verification on a schedule.** `aer.core.hashing.verify_chain` exists and is
tested, but nothing runs it periodically yet. You can call it by hand over the
`audit_events` table if you want to confirm the chain is intact.

**Anything beyond one US filing.** UK filings, prices, macro data, peers, valuation and
the red-team pass are Phase 2 and 3. The slice deliberately does one of everything.

---

## If something fails

Note which check, what you saw, and what the logs said. A failure in section 7 or 8 is the
most interesting kind — those are the paths that have never met real data or a real model,
and they are where I would expect the first genuine defect to be.
