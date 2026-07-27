# Task runner. Install `just` from https://github.com/casey/just, or read the recipes and
# run the underlying commands directly -- nothing here is magic.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Show available recipes.
default:
    @just --list

# First-time setup: sync dependencies and install git hooks.
setup:
    uv sync --all-groups
    uv run pre-commit install

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
