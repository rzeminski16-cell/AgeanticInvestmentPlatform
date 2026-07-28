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

# Run the test suite (no network, no model spend).
test:
    uv run pytest

# Run the test suite with coverage.
test-cov:
    uv run pytest --cov --cov-report=term-missing

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
