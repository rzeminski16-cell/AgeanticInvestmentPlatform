# Ageiantic Equity Research Platform

A local-first, auditable equity research platform for UK and US listed equities.

It produces **one institutional-style research report at a time**, under explicit human
approval, with every number traceable to a formula and every fact traceable to a hashed
source document.

> **This is a personal research tool. It is not regulated investment advice.** Nothing it
> produces is a recommendation to buy, sell or hold any security. Ratings are non-binding
> personal views, and every generated report carries this disclaimer.

## Status

**Phase 0 — foundation.** The repository scaffold, tooling, development conventions, local
infrastructure and typed configuration exist. The application itself does not yet. See
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

# 7. Verify
uv run pytest
uv run ruff check .
uv run mypy
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
| `just lint` | Lint and check formatting |
| `just fix` | Apply lint fixes and format |
| `just typecheck` | Run mypy |
| `just test` | Run the test suite |
| `just ci` | Everything CI runs, in the same order |
| `just hooks` | Run every pre-commit hook over the whole tree |

Without `just`, read the `justfile` — every recipe is a one-line `uv run ...` command.

## Repository layout

```
src/aer/            application package
  version.py        build identity (version + git SHA), recorded on every calculation
  errors.py         error hierarchy; every error has a stable machine-readable code
  logging.py        structured JSON logging with secret redaction
  config.py         typed settings; secrets never render, all problems reported at once
  core/             correctness core: pure, side-effect free, mypy --strict
tests/              test suite; runs with no network access and no model spend
docs/
  PLAN.md           the full research, architecture and build plan
  adr/              architecture decision records
docker-compose.yml  Postgres, Redis, and MinIO under the `objectstore` profile
.env.example        every setting, documented
```

## Testing

```bash
uv run pytest                 # default suite: no network, no model spend
uv run pytest --cov           # with coverage
uv run pytest -m integration  # requires Docker Compose services (later phases)
```

Tests that would make real, billable model calls are marked `live_llm` and never run by
default.

## Contributing

See `CONTRIBUTING.md` for the workflow and the definition of done, and `CLAUDE.md` for the
conventions that govern how code in this repository is written.

## Licence

MIT — see `LICENSE`. This is provisional while the project is personal; revisit before any
public distribution, and note that some data providers restrict redistribution of their
content independently of this repository's licence.
