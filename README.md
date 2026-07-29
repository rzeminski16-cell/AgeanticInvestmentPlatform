# Ageiantic Equity Research Platform

A local-first, auditable equity research platform for UK and US listed equities.

It produces **one institutional-style research report at a time**, under explicit human
approval, with every number traceable to a formula and every fact traceable to a hashed
source document.

> **This is a personal research tool. It is not regulated investment advice.** Nothing it
> produces is a recommendation to buy, sell or hold any security. Ratings are non-binding
> personal views, and every generated report carries this disclaimer.

## Status

**Phase 1 complete — the vertical slice runs end to end.** A research request becomes a
costed plan you approve, a filing fetched from SEC EDGAR and hashed, point-in-time facts, a
traced calculation, a drafted report you approve, and a frozen Markdown document in which
every figure carries a footnote that resolves to either the formula that produced it or the
archived bytes it came from.

It is a *slice*, not a finished product: one document, a handful of facts, one calculation,
two sections. Every one of those is deliberately thin. What is complete is the **chain** —
request → plan → approval → acquisition → extraction → calculation → draft → approval →
cited report — and the machinery around it: the approval gates, the budget cap, the cost
meter, resumability after a crash, and a report whose sections are database rows rather
than code.

Phase 2 widens the sources; Phase 3 deepens the analysis. See `docs/PLAN.md` for the full
plan and `docs/adr/` for the decisions taken so far.

## What it does (target state)

1. You complete a structured research request in a local web GUI.
2. The system proposes a research plan, the sources it intends to use, a cost estimate, a
   runtime estimate and the known risks. **You approve it before anything is spent.**
3. It acquires primary sources — SEC EDGAR, FCA National Storage Mechanism, Companies
   House, issuer investor-relations material, licensed end-of-day prices, official macro
   statistics — hashing and archiving every byte.
4. It extracts, normalises and calculates: financial history, earnings quality, a
   driver-based discounted cash flow, comparable companies, historical multiples,
   scenarios and sensitivities. **All arithmetic is ordinary Python, unit-tested.**
5. Language models plan, interpret, compare, red-team and write — but never produce a
   number and never assert an uncited fact.
6. It validates: citation accuracy, temporal compliance, numerical consistency, source
   coverage, completeness.
7. You review the draft alongside the validation results and the red-team's bear case,
   then approve. Only then does it render an immutable PDF, a Markdown archive and
   Obsidian knowledge notes.

You can also add **your own report sections**, written as natural-language *skill files*,
so the analysis reflects your views rather than a fixed template.

## The design principle

**Deterministic Python owns every number and every fact. The language model owns
planning, interpretation, comparison, adversarial challenge and writing.**

An unconstrained model asked to "research Microsoft" produces fluent, plausible, partly
fabricated prose with invented figures and mismatched citations. Everything in this
architecture exists to make that outcome structurally impossible rather than merely
discouraged. See `docs/adr/0003-deterministic-code-owns-numbers-and-facts.md`.

## Requirements

- **Python 3.12**
- [**uv**](https://docs.astral.sh/uv/) for dependency management
- **Docker Desktop** (from Phase 1 onward, for PostgreSQL and Redis)
- Optionally [**just**](https://github.com/casey/just) as a task runner

## Setup (Windows, PowerShell)

```powershell
# 1. Install uv (once)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Clone and enter the repository
git clone https://github.com/rzeminski16-cell/AgeianticEquityResearchPlatform.git
cd AgeianticEquityResearchPlatform

# 3. Install the pinned Python and sync dependencies
uv python install 3.12
uv sync --all-groups

# 4. Install the git hooks
uv run pre-commit install

# 5. Configure
copy .env.example .env
#    Then edit .env and set AER_HTTP_USER_AGENT. Everything else has a working
#    default; add API keys as and when you need the providers they unlock.

# 6. Start Postgres and Redis
docker compose up -d
docker compose ps          # both services should report healthy

# 7. Apply the schema and create your user
uv run alembic upgrade head
uv run aer seed-user --email you@example.com

# 8. Verify
uv run pytest
uv run ruff check .
uv run mypy

# 9. Run it
uv run aer serve
#    Then open http://127.0.0.1:8000
```

macOS and Linux are identical apart from the uv installer and `cp` instead of `copy`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Configuration

All settings are read from `AER_*` environment variables, or from `.env`. Every one is
documented in `.env.example`, which is complete enough that copying it and filling in a
single value gives you a working configuration.

**`AER_HTTP_USER_AGENT` is the only required setting.** It has no default because the SEC
mandates a descriptive User-Agent identifying the operator as a condition of using its
APIs — a shared placeholder would get everyone using it blocked together. Set it to a real
name and a contact address you monitor.

`AER_SECRET_KEY` signs CSRF tokens. Leave it blank locally and one is generated per
process; the only consequence is that a form left open across a restart needs reloading.
It becomes **required** when `AER_APP_ENV=production`, where an ephemeral key would differ
between workers and change on every deploy — startup refuses to continue without it.

API keys are deliberately *not* required at startup. A missing key fails at the point the
provider is used, with a message naming the variable to set, so you are never blocked on
credentials for a service you are not using yet.

Inspect the effective configuration at any time with `just config` — secrets render masked.

### Local infrastructure

```bash
just up        # start Postgres and Redis
just health    # pg_isready + redis ping
just psql      # psql shell on the dev database
just logs      # follow service logs
just down      # stop, keeping data
just down-hard # stop and DELETE all data
```

Every published port binds to `127.0.0.1` only. Docker bypasses host firewalls when
publishing ports, so a plain `5432:5432` would expose the database to whatever network you
are on. MinIO is available but not started by default: `docker compose --profile
objectstore up -d`.

## Everyday commands

With `just`:

| Command | What it does |
|---|---|
| `just setup` | Sync dependencies and install git hooks |
| `just serve` | Run the web server |
| `just dev` | Run the web server with auto-reload |
| `just worker` | Run the background worker that executes research runs |
| `just seed-user you@example.com` | Create the local user (idempotent) |
| `just lint` | Lint and check formatting |
| `just fix` | Apply lint fixes and format |
| `just typecheck` | Run mypy |
| `just test` | Run the test suite (excludes the browser tests) |
| `just test-e2e` | Run the browser tests (needs Chromium and PostgreSQL) |
| `just test-all` | Both, as two processes — see the note under **Testing** |
| `just ci` | Everything CI runs, in the same order |
| `just hooks` | Run every pre-commit hook over the whole tree |
| `just css` | Rebuild the Tailwind stylesheet (needs Node) |

Without `just`, read the `justfile` — every recipe is a one-line `uv run ...` command.

## The web application

```bash
uv run aer serve              # 127.0.0.1:8000 by default
uv run aer serve --reload     # auto-reload while developing
uv run aer version            # what build am I running?
```

A run happens in the worker, so both processes need to be up:

```bash
uv run aer serve              # the GUI and API
uv run arq aer.worker.WorkerSettings   # the worker that executes runs
```

| Endpoint | Purpose |
|---|---|
| `GET /` | Landing page. Renders even with the database down, and says what is wrong |
| `GET /requests` | Your research requests |
| `GET /requests/new` | The research request form |
| `GET /requests/{id}` | One request, and the button that starts a run |
| `POST /runs` | Start a run (form post; redirects to the console) |
| `GET /runs/{id}` | **The run console.** Live progress, or a meta refresh without JavaScript |
| `GET /runs/{id}/plan` | **Gate 1.** The plan, its sources, its cost and its risks |
| `GET /runs/{id}/review` | **Gate 2.** The drafted report, exactly as it will be stored |
| `GET /reports/{id}` | A finished report, its hash, and a link to the archived bytes |
| `GET /healthz` | **Liveness.** Always 200 while the process can answer; touches nothing external |
| `GET /readyz` | **Readiness.** 200 when Postgres and Redis both answer, 503 with a per-dependency breakdown otherwise |
| `GET /docs` | Interactive API documentation (disabled when `AER_APP_ENV=production`) |

The JSON API mirrors the GUI exactly, because both call the same service functions:

| Endpoint | Purpose |
|---|---|
| `POST /api/requests` | Create a request. 201 with a `Location` header |
| `GET /api/requests` | List requests, most recent first |
| `GET /api/requests/{id}` | Read one request |
| `POST /api/runs` | Start a run. 202; the run happens in the worker |
| `GET /api/runs/{id}` | A run's status, steps and spend |
| `GET /api/runs/{id}/events` | Server-sent events: progress until the run ends |
| `GET /api/runs/{id}/draft` | What gate 2 decides on, and the hash an approval must carry |
| `POST /api/runs/{id}/gates/{gate}/decide` | Approve or reject at a gate |
| `GET /api/plans/for-run/{id}` | What gate 1 shows, and its hash |
| `GET /api/reports/for-run/{id}` | The report a run produced |
| `GET /api/reports/{id}/download` | The **archived** Markdown, with its digest in a header |
| `GET /api/calculations/{id}` | One calculation: formula, inputs, sources, code version |

### Approving is a decision about something specific

Both gates show a payload and a hash of exactly that payload, and an approval must carry
the hash back. If what the run produced changed between the page being served and the
button being pressed, the hashes differ and the workflow refuses to continue — an approval
of something else is not an approval of this. The page and the run build that payload from
**the same function**, so "what was shown" and "what was approved" cannot come apart.

Approving never executes anything inline. It records the decision, commits, and enqueues:
a gate approval that ran the remaining steps inside the request would hold a browser
connection open for the length of a research run, and would abandon it if the tab closed.

Every response carries an `X-Request-ID`, and the same id appears in every log line for
that request and in the body of every error — so an error you can see is an error you can
trace. Errors are returned as
[RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457) with a stable
machine-readable `code`.

Unexpected exceptions return a generic message and the request id, never an internal
message or a stack trace; the full traceback goes to the log. Deliberate errors return
their message, because "run `aer seed-user`" is the entire value of that error.

### Front end

Server-rendered Jinja2 templates, styled with Tailwind, progressively enhanced with HTMX —
see `docs/adr/0006-server-rendered-htmx-gui.md`. **The compiled stylesheet and the vendored
HTMX are committed**, so a checkout runs immediately and CI needs no Node toolchain. Node
is required only to change the styling:

```bash
npm install        # once
just css           # rebuild src/aer/web/static/css/app.css
just watch-css     # rebuild continuously while editing templates
```

Nothing is loaded from a CDN. The application is local-first and must work with no
internet connection, and a third-party script on a page that can reach your database and
your provider credentials is a supply-chain risk taken for convenience.

## Repository layout

```
src/aer/            application package
  version.py        build identity (version + git SHA), recorded on every calculation
  errors.py         error hierarchy; every error has a stable machine-readable code
  logging.py        structured JSON logging with secret redaction
  config.py         typed settings; secrets never render, all problems reported at once
  cli.py            `aer serve`, `aer version`, `aer seed-user`
  core/             correctness core: pure, side-effect free, mypy --strict
    enums.py        domain vocabulary, rendered as native PostgreSQL enums
    concepts.py     canonical financial concepts and the filer tags that mean them
    hashing.py      canonical serialisation and audit hash chaining
    schemas/facts.py  RawFact: one reported number, and when it was reported
  calc/             the calculation kernel: pure, unit-safe, mypy --strict
    units.py        Quantity = value + unit + source; incompatible units raise
    engine.py       @traced: records the formula, inputs, sources and code version
    basic.py        growth, CAGR, ratio, margin, weighted average, YoY series
  db/               engine, session management, and ORM models
  storage/          content-addressed artefact store; the evidence substrate
    protocol.py     the ArtefactStore interface: no delete, no update, no move
    local.py        sha256 addressing, atomic writes, integrity verification
  fetch/            the ONLY component that makes outbound network requests
    ssrf.py         resolve, validate every address, refuse anything not public
    transport.py    connects only to validated addresses; closes the rebinding gap
    policy.py       per-provider allowlist, rate, licence note
    robots.py       robots.txt compliance; a disallow is a refusal
    limits.py       Redis token bucket and circuit breaker, shared across workers
    client.py       SafeFetcher: the pipeline, and what archives every response
  sources/          data-source adapters: one package per publisher
    base.py         the SourceAdapter protocol: resolve, discover, extract
    sec/tickers.py  ticker and exchange to CIK, refusing to guess an ambiguity
    sec/submissions.py  the filing index; checks the parallel arrays are parallel
    sec/companyfacts.py every XBRL fact ever tagged, as exact decimals
    sec/pit.py      point-in-time selection: what was known, as at a date
    sec/client.py   EDGAR endpoints, URL construction and pacing
  providers/        model providers: the seam that makes the suite free to run
    protocol.py     two operations: structured completion, and token counting
    router.py       role -> model; no call site names a model
    costs.py        usage -> money, by category, in Decimal, with the FX rate on the row
    anthropic.py    the ONLY module permitted to import the vendor SDK
    fake.py         scripted answers, plausible token counts, zero spend
  agents/           agents: route, call, archive both payloads, meter
    base.py         everything an agent must not have to remember
    planner.py      proposes a plan; states no figure and asserts no fact
  workflow/         the step runner and the workflows built on it
    engine.py       idempotent, resumable, budget-checked before each step
    workflows/vertical_slice_v1.py   request -> plan -> gates -> cited report
  sections/         sections are rows, not code
    registry.py     which sections apply, in what order, pinned per run
    render.py       Markdown from a JSON Schema; citation is a field name
  render/markdown.py  the document: header, sections, footnotes, sources, disclaimer
  services/         business operations: requests, artefacts, provenance, facts,
                    calculations, approvals (gate order and payload hashes), runs
  runtime.py        assembles the service bundle both processes share
  queue.py          enqueueing a run, from the web process
  worker.py         the arq worker: where a research run actually executes
  api/              HTTP layer
    app.py          create_app() factory; lifespan owns the engine and Redis client
    deps.py         session, settings and current-user dependencies
    errors.py       Problem Details responses; what may and may not be returned
    middleware.py   request id, access logging, timing
    security.py     signed CSRF tokens
    sse.py          live run progress, polled from committed state
    routes/         JSON API routers
  web/              server-rendered GUI
    pages.py        run console, both gate pages, the report
    templates/      Jinja2; the disclaimer lives in the shell, not in pages
    static/         committed build output and vendored libraries
    styles/         Tailwind source (compiled to static/css/app.css)
migrations/         Alembic migrations; the schema's only source of truth
tests/              test suite; runs with no network access and no model spend
docs/
  PLAN.md           the full research, architecture and build plan
  adr/              architecture decision records
docker-compose.yml  Postgres, Redis, and MinIO under the `objectstore` profile
package.json        build-time only: compiles the stylesheet. Not needed to run the app.
.env.example        every setting, documented
```

### Database

```bash
just migrate          # apply all pending migrations
just migrate-status   # current revision and available heads
just revision "add foo table"   # autogenerate from model changes
just migrate-down     # roll back one revision
```

The schema is enforced in PostgreSQL, not only in Python: native enums, CHECK constraints
on domain rules, `NUMERIC` for money, and `TIMESTAMPTZ` everywhere. The application is not
the only thing that will ever write to this database, so a rule that lives only in
application code is a rule those other writers do not have. See
`docs/adr/0005-postgres-as-system-of-record.md`.

A test compares the live schema against the ORM models and **fails the build on any
drift**, so a model change that was never migrated cannot reach production.

### Evidence storage

Every byte the platform fetches is stored under `AER_ARTEFACT_ROOT`, addressed by the
SHA-256 of its own content:

```
var/artefacts/<aa>/<bb>/<full-sha256>
```

That one decision buys deduplication, tamper detection and verifiable citations at once —
the address *is* the digest, so a file that no longer hashes to its own name has been
altered. Writes are atomic (temp file, `fsync`, rename), the size cap is enforced while
streaming rather than afterwards, and **artefact rows are made immutable by a database
trigger** rather than by convention.

Provenance lives separately in `source_documents`: the URL, publisher, publication date,
licence note and robots status of each acquisition. Two fetches of the same PDF share one
artefact and get two provenance records, because they happened at different times and
possibly under different terms.

**A source whose publication date cannot be established is quarantined** when the request
is in point-in-time mode — kept, so the record of what was seen survives, but flagged so
nothing can cite it. See `docs/adr/0008-content-addressed-immutable-artefacts.md`.

### Network egress

`src/aer/fetch/` is the only component permitted to make outbound requests, and every
control sits on that one door: a per-provider allowlist, robots.txt compliance, a
Redis-backed token bucket shared by every worker, a circuit breaker, retries with full
jitter, a streaming byte cap, and content-type sniffing that never trusts the header.

**No agent-callable tool anywhere in this system takes a URL.** An agent asks for a *kind*
of source; deterministic adapter code decides which URL that means. Text hidden in a
fetched filing can instruct as loudly as it likes, because no tool exists that would carry
it out. That is a property of what is *absent* from the tool surface, so it is stated in
the module docstring where someone about to add the missing tool will read it.

SSRF protection resolves each hostname once, validates **every** address it returns, and
then connects only to a validated address — carrying the real hostname in the `Host` header
and the TLS SNI, so certificate checking still works. Letting the HTTP client resolve the
name a second time is what DNS rebinding exploits. Every redirect hop is re-validated from
scratch. See `docs/adr/0009-network-egress-is-deterministic-and-guarded.md`.

No fetch test touches the real network: everything runs against `respx`, and a fixture
replaces `socket.socket` so a test that reaches out fails instead of succeeding quietly.

### Point-in-time data

A company's FY2020 revenue has more than one true value. The FY2020 annual report states
one figure; the FY2022 report may state a different one for the same year, after a
restatement. Both are true; they differ in *when they were said*. Research performed as at
a date in 2021 must use the first, because the second did not exist.

Taking "the latest value" instead is look-ahead bias, and it fails **silently** — nothing
raises, no figure looks implausible, and the analysis simply looks better than reality.

SEC EDGAR carries the filing date on every fact, which makes the correct answer computable:

> Group facts by concept, unit, period end and fiscal period. Discard every fact filed
> after the as-of date. From what remains, choose the one filed **latest**.

`aer.sources.sec.pit` implements exactly that, as a pure function with an exhaustive test
suite, and returns a **partition** rather than a filtered list: every input fact appears
once, in `chosen` or in `rejected` with a reason. "Why is this figure not in the report?"
is asked about every report, and a filtered list cannot answer it.

Only the `as_reported` basis is implemented. Asking for `restated` raises. See
`docs/adr/0010-point-in-time-is-selection-not-filtering.md` and
`docs/data-sources/sec-edgar.md`.

### Calculations

`src/aer/calc/` owns every number. **No language model may produce a figure that bypasses
it** — a discounted cash flow is forty lines of Python with unit tests, not a reasoning
task, and putting arithmetic in prose is the most common way systems like this produce
confidently wrong numbers.

A value is never a bare number. It is a `Quantity`: an exact `Decimal`, a unit, and a
source.

```python
eps = revenue / share_count  # USD / shares  ->  USD/shares
revenue + market_cap_in_gbp  # raises UnitMismatchError
```

Units are dimensional vectors, so `USD/USD` is dimensionless, `USD/shares` composes, and a
growth rate times a revenue is a revenue — all of it from the arithmetic rather than from a
table of legal combinations. Currencies never convert implicitly; `convert()` needs a rate
whose own unit proves it is the right way up, **and** a source on that rate.

Every calculation goes through `@traced`, which **refuses any input it cannot account for**:

```python
cagr(context, start=revenue_2017, end=revenue_2022, years=5)
# records: formula, function_ref, code_version, each input with its unit and
#          source id, the parameters, and the output with its unit
```

A `Quantity` with no source raises. A bare `Decimal` raises. A refused call records
nothing. The result carries a source pointing at its own record, so calculations chain and
`GET /api/calculations/{id}` can resolve the lineage down to the facts and assumptions the
figure ultimately rests on — reporting any reference that no longer resolves rather than
hiding it.

`Decimal` throughout, at 34 digits, with division-by-zero and invalid-operation trapped.
Rounding happens once, at presentation. See
`docs/adr/0011-calculations-are-unit-safe-and-traced.md`.

### Model calls

Every call goes through a provider, a router and a meter — see
`docs/adr/0012-model-provider-abstraction.md`.

**A role picks the model; no call site names one.** `AER_MODEL_ROUTES` is JSON, so moving
source triage from Sonnet to Haiku — roughly a thirtyfold difference on a step that runs
dozens of times per report — is a configuration edit. A role with no route **raises**
rather than falling back, because a silent default is how a run costs thirty times what
was expected while looking entirely normal.

**Usage is priced by category and stored in `Decimal`.** Input, output, cache read and
cache write have ratios spanning an order of magnitude; a meter that treated them alike
would misreport a cached run in the direction that flatters the platform. The USD→GBP rate
is written on each row rather than applied and forgotten, so last month's costs stay
reconcilable when the rate changes. An unknown model is priced at the dearest known one:
an overstatement pauses a run for a decision, an understatement spends money nobody agreed
to.

**The cap is checked before a step runs, not after.** A run that would exceed its ceiling
stops in `BUDGET_EXCEEDED` having called nothing; the test for it asserts the provider's
call count is zero.

**Only `aer/providers/anthropic.py` may import the vendor SDK**, and it does so inside a
function. A test parses every file under `src/` to confirm it, and a second test imports
the application in a subprocess and checks the SDK never loaded.

### Report sections are rows, not code

A section is a row in `section_definitions`: a key, a version, a position, and a JSON
Schema that the renderer walks to produce Markdown. There is no section enum, no section
list, and no per-section branch anywhere in `src/` — enforced by a test that reads the
source tree.

That is not tidiness. Phase 4 lets you author a section in a natural-language skill file,
and a section defined that way has nobody to write its template. If rendering needed one,
the feature would be impossible to add later rather than merely unbuilt.

Adding a section is an `INSERT`:

```sql
INSERT INTO section_definitions (key, version, origin, title, position, required,
                                 output_contract, evidence_policy, token_budget,
                                 allowed_tools, applicability)
VALUES ('competitive_position', 1, 'builtin', 'Competitive Position', 150, true,
        '{"type":"object","properties":{"commentary":{"type":"string","title":"Commentary"}}}',
        '{"min_sources":1,"requires_primary":true}', 2000, '{}', '{}');
```

`position` is `NUMERIC` and sparse (100, 200), so 150 slots in without renumbering
anything. `tests/test_report_sections.py::TestAThirdSection` does exactly this and asserts
the rendered report gains a third section, in the right place, with footnote numbering
still correct across the whole document, **with no code change**. See
`docs/adr/0013-report-sections-are-data-not-code.md`.

`output_contract` is stored as `json`, not `jsonb` — the only such column in the schema.
`jsonb` discards key order, reordering by key length then bytewise, which silently replaced
a section author's declared field order with an artefact of the storage engine. The order
is part of the contract, so the column keeps the text exactly as written.

### Approval gates and resumability

A run stops at each gate and records an approval carrying the hash of exactly what was
displayed. Approving twice is refused; approving gate 2 before gate 1 is refused; an
approval recorded against different content does not open the gate.

Steps are idempotent by stored outcome. A worker that dies mid-run resumes from the first
incomplete step — the planner is not asked twice, the filing is not fetched twice — because
a step that already succeeded returns its stored output instead of executing. There is a
test that kills a run after acquisition and asserts the fetch count does not change.

### Pending migrations announce themselves

New code often needs new tables, and forgetting `uv run alembic upgrade head` used to
produce the worst kind of failure: the process started cleanly, `/readyz` reported ready,
and one page returned an opaque 500 whose only clue was a stack trace.

`aer.db.schema_check` compares `Base.metadata` against the live schema and reports what is
missing, in three places: a `schema.out_of_date` warning at start-up, a banner on the
landing page, and a `schema` entry in `/readyz` that turns a stale schema into a 503 naming
the missing objects. It is derived from the models rather than from a revision constant, so
there is nothing to keep in step — a table added to the models and forgotten in a migration
is caught by the same check.

It is a warning at start-up and never a refusal. The landing page is deliberately built to
render with the database down and say what is wrong; an application that would not start
because of a pending migration would take away the one page that could have told you.

### What may change after the fact

**A request is editable and deletable until a run has left something behind.** Not when a
run starts — when it produces a report, gathers evidence, spends money, or records a
decision at a gate. Those are the things an edit would falsify and a deletion would
destroy, and `immutable_reason` names whichever one applies rather than saying "editing is
disabled". Editing is a whole-payload replace through the same validation a creation goes
through, so a rule cannot be dodged by creating something valid and then editing it.
Deleting anything with evidence or a report behind it is refused outright — those cascade
away with the request and the hashed bytes would be left orphaned on disk.

**Spend is the deliberate exception, and fixing it was a schema change rather than a rule
change.** `costs` used to cascade away with the request through three separate references,
so deleting a request erased its cost history — and a monthly cap you can get under by
deleting what you spent it on is not a cap. Migration 0009 makes those references
`SET NULL`, following the pattern `audit_events` already uses so a record outlives what it
describes. The ledger is now append-only in effect, and spend no longer has to block
deletion to be protected.

**One report per request, not one job.** A cancelled or failed run produced no report, so
starting again supersedes it with a new job; a live run or one that produced a report is
returned instead. Superseding never resurrects the old job: the row says it finished, and a
cancelled job still carries its cancellation, so the engine would stop it again on its first
step.

**A run can be cancelled, but not interrupted.** Cancelling records a request in
`job_cancellations`; the engine reads it before each step and stops. A step already in
flight — a model call, a filing being fetched — runs to completion, because abandoning it
would throw away work already paid for while recording a stop time that never happened. The
console shows both moments: when you asked, and when it actually stopped.

The separate table is not tidiness. `runs.execute` sets `jobs.status = RUNNING` and the
worker commits once, at the end, so Postgres holds that row's lock for the run's whole
lifetime; a cancel that wrote to `jobs` would block for exactly as long as cancelling
remained useful. That was measured with two `psql` sessions before the design was chosen.
See `docs/adr/0014-what-may-change-after-the-fact.md`.

## Testing

```bash
uv run pytest --ignore=tests/e2e     # default suite: no network, no model spend
uv run pytest tests/e2e              # browser tests (Chromium + PostgreSQL)
just test-all                        # both, as two processes
uv run pytest --cov                  # with coverage
uv run pytest -m integration         # database tests only
uv run pytest -m "not integration"   # skip anything needing PostgreSQL
```

**The browser tests must run in their own pytest process.** Playwright's synchronous API
drives an asyncio loop on the main thread and keeps it running for the life of its session
fixture, so any asyncio-based fixture that runs after a browser test in the same process
fails with "Runner.run() cannot be called from a running event loop". `just test-all` is
therefore two commands rather than one `pytest` invocation.

The whole vertical slice — plan, both gates, budget guard, acquisition, calculation,
rendered report — runs in the default suite against a fake provider and a stubbed EDGAR
client, so it costs nothing and needs no network. That is the entire reason the provider
abstraction exists: a suite that spent money would be a suite nobody ran.

The browser tests drive a real Chromium against a real uvicorn server on an ephemeral
port. They exist to catch what an in-process HTTP client structurally cannot — a form
field the server never receives, a submit button outside the form, an HTMX response the
browser silently discards. Two genuine bugs found that way are recorded in
`docs/adr/0007-request-validation-boundaries.md`.

If Chromium was installed outside Playwright's own cache, point at it with
`PLAYWRIGHT_CHROMIUM_PATH`; `/opt/pw-browsers/chromium` is picked up automatically.

Tests that would make real, billable model calls are marked `live_llm` and never run by
default.

Database tests run against a separate `aer_test` database, inside a transaction that is
rolled back afterwards, so they never touch your development data. If PostgreSQL is not
running they **skip with the reason** rather than failing, so `uv run pytest` still works
on a machine with nothing started. Point them elsewhere with `AER_TEST_DATABASE_URL`.

### Verifying it by hand

`docs/manual-verification.md` is a checklist for everything the suite structurally cannot
prove: Docker Compose, the pages as a person sees them, the guards provoked deliberately,
and one real run against the real SEC and a real model call. Most of it costs nothing; the
one section that spends money says so and says roughly how much.

## Contributing

See `CONTRIBUTING.md` for the workflow and the definition of done, and `CLAUDE.md` for the
conventions that govern how code in this repository is written.

## Licence

MIT — see `LICENSE`. This is provisional while the project is personal; revisit before any
public distribution, and note that some data providers restrict redistribution of their
content independently of this repository's licence.
