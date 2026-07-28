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

# Everything, including the browser tests.
#
# Two processes, not one. Playwright's synchronous API drives an asyncio loop on the main
# thread and keeps it running for the life of its session fixture, so every asyncio-based
# fixture that runs after a browser test in the same process fails with "Runner.run()
# cannot be called from a running event loop". Splitting the run is the fix; the
# alternative is a suite whose result depends on collection order.
test-all: test test-e2e

# Run the test suite with coverage.
test-cov:
    uv run pytest --ignore=tests/e2e --cov --cov-report=term-missing

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
