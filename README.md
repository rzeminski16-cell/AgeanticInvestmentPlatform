# Ageiantic Equity Research Platform

A local-first, auditable equity research platform for UK and US listed equities.

It produces **one institutional-style research report at a time**, under explicit human
approval, with every number traceable to a formula and every fact traceable to a hashed
source document.

> **This is a personal research tool. It is not regulated investment advice.** Nothing it
> produces is a recommendation to buy, sell or hold any security. Ratings are non-binding
> personal views, and every generated report carries this disclaimer.

## Status

**Phase 1 — foundation.** The repository scaffold, tooling, conventions, local
infrastructure, typed configuration, database schema, web application shell and the
**research request** — form, API, validation and universe rules — all exist.
`uv run aer serve` starts a working server; you can create and read requests through the
GUI or the API. Source acquisition, analysis and report generation do not exist yet. See
`docs/PLAN.md` for the full plan and `docs/adr/` for the decisions taken so far.

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
| `just seed-user you@example.com` | Create the local user (idempotent) |
| `just lint` | Lint and check formatting |
| `just fix` | Apply lint fixes and format |
| `just typecheck` | Run mypy |
| `just test` | Run the test suite (excludes the browser tests) |
| `just test-e2e` | Run the browser tests (needs Chromium and PostgreSQL) |
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

| Endpoint | Purpose |
|---|---|
| `GET /` | Landing page. Renders even with the database down, and says what is wrong |
| `GET /requests` | Your research requests |
| `GET /requests/new` | The research request form |
| `GET /requests/{id}` | One request |
| `GET /healthz` | **Liveness.** Always 200 while the process can answer; touches nothing external |
| `GET /readyz` | **Readiness.** 200 when Postgres and Redis both answer, 503 with a per-dependency breakdown otherwise |
| `GET /docs` | Interactive API documentation (disabled when `AER_APP_ENV=production`) |

The JSON API mirrors the GUI exactly, because both call the same service functions:

| Endpoint | Purpose |
|---|---|
| `POST /api/requests` | Create a request. 201 with a `Location` header |
| `GET /api/requests` | List requests, most recent first |
| `GET /api/requests/{id}` | Read one request |

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
    hashing.py      canonical serialisation and audit hash chaining
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
  services/         business operations: requests, artefacts, provenance
  api/              HTTP layer
    app.py          create_app() factory; lifespan owns the engine and Redis client
    deps.py         session, settings and current-user dependencies
    errors.py       Problem Details responses; what may and may not be returned
    middleware.py   request id, access logging, timing
    security.py     signed CSRF tokens
    routes/         JSON API routers
  web/              server-rendered GUI
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

## Testing

```bash
uv run pytest --ignore=tests/e2e     # default suite: no network, no model spend
uv run pytest tests/e2e              # browser tests (Chromium + PostgreSQL)
uv run pytest --cov                  # with coverage
uv run pytest -m integration         # database tests only
uv run pytest -m "not integration"   # skip anything needing PostgreSQL
```

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

## Contributing

See `CONTRIBUTING.md` for the workflow and the definition of done, and `CLAUDE.md` for the
conventions that govern how code in this repository is written.

## Licence

MIT — see `LICENSE`. This is provisional while the project is personal; revisit before any
public distribution, and note that some data providers restrict redistribution of their
content independently of this repository's licence.
