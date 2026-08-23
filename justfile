# Task runner. Install `just` from https://github.com/casey/just, or read the recipes and
# run the underlying commands directly -- nothing here is magic.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Show available recipes.
default:
    @just --list

# First-time setup: sync dependencies, install git hooks, create .env.
setup:
    uv sync --all-groups
    uv run pre-commit install
    @echo "Now copy .env.example to .env and set AER_HTTP_USER_AGENT."

# Start Postgres and Redis in the background.
up:
    docker compose up -d
    @echo "Waiting for services to report healthy..."
    docker compose ps

# Stop the services, keeping data volumes.
down:
    docker compose down

# Stop the services and DELETE all data. Destroys the local database.
down-hard:
    docker compose down -v

# Follow service logs.
logs *args:
    docker compose logs -f {{args}}

# Open a psql shell on the development database.
psql:
    docker compose exec postgres psql -U aer -d aer

# Open a redis-cli shell.
redis:
    docker compose exec redis redis-cli

# Check that the infrastructure is actually reachable.
health:
    docker compose exec postgres pg_isready -U aer -d aer
    docker compose exec redis redis-cli ping

# Run the web server on the configured host and port (default 127.0.0.1:8000).
serve *args:
    uv run aer serve {{args}}

# Run the web server with auto-reload, for development.
dev:
    uv run aer serve --reload

# Run the background worker. A research run happens here, not in an HTTP request: the web
# process only enqueues, so nothing useful happens until this is running too.
worker:
    uv run arq aer.worker.WorkerSettings

# Create the single local user. Idempotent.
seed-user email:
    uv run aer seed-user --email "{{email}}"

# Delete every research request and everything derived from one. Asks first. Keeps the
# accounts, the authored skills, the artefacts and the audit log; cached evidence goes.
reset-research:
    uv run aer reset-research

# Copy the database and the artefact store into one directory, and verify what was
# written before reporting success. Both halves or neither: a database restored beside an
# empty store is a set of citations into nothing.
backup destination:
    uv run aer backup --to {{destination}}

# Re-hash a backup against its own manifest. Touches no database, so it can be run
# wherever the backup lives. Exits non-zero on any problem, so it can be a cron line.
verify-backup source:
    uv run aer verify-backup --from {{source}}

# Put a backup back. DESTRUCTIVE: drops and rebuilds every table. Verifies the backup
# first and refuses if it does not check out. Asks before it does anything.
restore source:
    uv run aer restore --from {{source}}

# Re-derive everything a run produced from what the run wrote down. Fetches nothing and
# calls no model. Exits non-zero if any leg no longer holds.
replay-run job_id:
    uv run aer replay-run {{job_id}}

# Walk the audit log and check every record still links to the one before it. Exits
# non-zero on a break, so it can be a cron line beside verify-artefacts.
verify-audit:
    uv run aer verify-audit

# Re-read every archived artefact and confirm it still hashes to its name. Exits non-zero
# on anything corrupt or missing, so it can be a cron line.
verify-artefacts:
    uv run aer verify-artefacts

# Report archived bytes that nothing in the database points at. Reports only; pass
# --delete to actually remove them.
gc-artefacts *ARGS:
    uv run aer gc-artefacts {{ARGS}}

# Delete every stored payload from a licensed provider, under a stated obligation. Asks
# first. Keeps the artefact rows, the citations and the lineage; only the bytes go, and
# every deletion is recorded in artefact_purges. ADR 0030, ADR 0031.
purge-licensed provider reason:
    uv run aer purge-licensed --provider "{{provider}}" --reason "{{reason}}"

# Rebuild the Tailwind stylesheet. Needs Node; the OUTPUT is committed, so CI does not.
css:
    npm run build:css

# Rebuild the stylesheet continuously while editing templates.
watch-css:
    npm run watch:css

# Copy vendored JavaScript out of node_modules. Run after `npm install` or `npm update`.
# Record the version and SHA-256 in the commit message; see src/aer/web/static/README.md.
vendor-js:
    cp node_modules/htmx.org/dist/htmx.min.js src/aer/web/static/vendor/htmx.min.js
    @echo "htmx.min.js updated. Record its version and hash in the commit message."

# Apply all pending migrations.
migrate:
    uv run alembic upgrade head

# Roll back one migration.
migrate-down:
    uv run alembic downgrade -1

# Roll back every migration. Destroys all data.
migrate-base:
    uv run alembic downgrade base

# Show the current revision and the available heads.
migrate-status:
    uv run alembic current --verbose
    uv run alembic heads

# Create a new migration from the difference between the models and the database.
# Always read the generated file before committing it: autogenerate is a first draft,
# not an answer. It cannot see data migrations, and it guesses at renames.
revision message:
    uv run alembic revision --autogenerate -m "{{message}}"

# Create an empty migration, for data changes autogenerate cannot infer.
revision-empty message:
    uv run alembic revision -m "{{message}}"

# Lint and check formatting (does not modify files).
lint:
    uv run ruff check .
    uv run ruff format --check .

# Apply lint fixes and formatting.
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Static type checking. No path argument: the packages come from pyproject.toml, so
# every caller (here, pre-commit, CI) checks exactly the same set.
typecheck:
    uv run mypy

# Run the test suite (no network, no model spend). Excludes the browser tests.
test:
    uv run pytest --ignore=tests/e2e

# Run the browser tests. Needs PostgreSQL and Chromium; slower than the rest combined.
test-e2e:
    uv run pytest tests/e2e

# The wire contract, against the real API. **Costs money** -- a fraction of a penny, on the
# cheap models, for answers a few tokens long.
#
# Deliberately outside `test` and outside `ci`. What it buys is the one question the offline
# suite cannot ask: the fake provider is an alternative implementation of the protocol, not a
# fake transport, so it never sees a payload and cannot notice when the API stops accepting
# one. A deprecated field reached a live report that way, and the Batches API validates at
# result-fetch time, so it failed an hour and five pounds into the run rather than at the
# first request. Run this before a live run; it answers in under a minute.
test-live:
    uv run pytest -m live_llm

# Everything, including the browser tests.
#
# Two processes, not one. Playwright's synchronous API drives an asyncio loop on the main
# thread and keeps it running for the life of its session fixture, so every asyncio-based
# fixture that runs after a browser test in the same process fails with "Runner.run()
# cannot be called from a running event loop". Splitting the run is the fix; the
# alternative is a suite whose result depends on collection order.
test-all: test test-e2e

# Run the suite with the test files in a different order, to find tests that only pass
# because of what ran before them.
#
# `just test` always runs the files in the same order, so a test coupled to another file's
# committed rows or module-level state passes for ever and fails the first time anything
# moves. Two have been found that way: a leaked `Agent` subclass, and an artefact row a
# fixture committed and did not truncate. Both were invisible in the default order.
#
# Takes a seed so a failure is reproducible -- `just test-shuffled 20260811` runs the exact
# ordering that found the second one. Omit it for a fresh order each time; the seed used is
# printed either way, so a red run can always be repeated.
test-shuffled seed="":
    uv run python -m tests.shuffled {{seed}}

# Run the test suite with coverage.
test-cov:
    uv run pytest --ignore=tests/e2e --cov --cov-report=term-missing

# The eight blocking metrics from docs/archive/PLAN.md section 2.10, on their own, together with
# the thirty golden calculations they lean on.
#
# Inside `test` as well — the gate is ordinary pytest. Runnable alone because "did the
# platform's guarantees move?" is a question worth being able to ask in ten seconds without
# reading two thousand dots.
eval:
    uv run pytest tests/test_evaluation_gate.py tests/test_eval_metrics.py tests/test_eval_replay.py tests/test_calc_golden.py

# Everything CI runs, in the same order.
ci: lint typecheck test

# Run every pre-commit hook against the whole tree.
hooks:
    uv run pre-commit run --all-files

# Print the build identity (version and git SHA).
version:
    uv run python -c "from aer import build_identity; print(build_identity())"

# Print the effective configuration. Secrets render masked.
config:
    uv run python -c "from aer.config import load_settings; print(load_settings().model_dump_json(indent=2))"
