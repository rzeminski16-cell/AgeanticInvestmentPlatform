"""Migration integrity.

The drift check below is the important one. It compares the live schema against the ORM
models and fails if they disagree. When it was first written it immediately caught two
real bugs: foreign-key columns had inherited ``server_default=gen_random_uuid()`` from the
primary-key type alias, and ``users.email`` was declared ``String`` in the model but
``CITEXT`` in the migration.

Both are the kind of defect that never fails at write time — they surface much later as a
dangling reference or a duplicate account. Without a check like this, the model and the
migration drift apart silently, and the first symptom is production data that should have
been impossible.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import aer.db.models  # noqa: F401 -- populates Base.metadata
from aer.db.base import Base
from tests.db_fixtures import TEST_USER_AGENT

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def run_alembic(url: str, action: str, revision: str) -> None:
    previous_url = os.environ.get("AER_DATABASE_URL")
    previous_agent = os.environ.get("AER_HTTP_USER_AGENT")
    os.environ["AER_DATABASE_URL"] = url
    os.environ["AER_HTTP_USER_AGENT"] = TEST_USER_AGENT
    try:
        config = alembic_config(url)
        getattr(command, action)(config, revision)
    finally:
        for key, value in (
            ("AER_DATABASE_URL", previous_url),
            ("AER_HTTP_USER_AGENT", previous_agent),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestMigrationScripts:
    def test_there_is_exactly_one_head(self):
        # Two heads mean someone branched without merging; Alembic will refuse to upgrade
        # and the error it gives is not obvious. Catching it here is much cheaper.
        script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused/unused"))
        assert len(script.get_heads()) == 1

    def test_every_revision_has_a_downgrade(self):
        script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused/unused"))
        for revision in script.walk_revisions():
            source = Path(revision.path).read_text(encoding="utf-8")
            assert "def downgrade()" in source, f"{revision.revision} has no downgrade"
            body = source.split("def downgrade()", 1)[1]
            assert "pass" not in body.split("\n")[1:3], (
                f"{revision.revision} has an empty downgrade; a migration that cannot be "
                "reversed cannot be tested by round-trip"
            )


class TestSchemaMatchesModels:
    async def test_no_drift_between_models_and_migrations(self, db_engine):
        """The live schema must match Base.metadata exactly. See the module docstring."""

        def _compare(connection) -> list:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            return compare_metadata(context, Base.metadata)

        async with db_engine.connect() as connection:
            differences = await connection.run_sync(_compare)

        # Alembic reports the alembic_version table as an extra; it is not part of the
        # application model and is expected.
        meaningful = [
            diff
            for diff in differences
            if not (
                isinstance(diff, tuple)
                and len(diff) >= 2
                and diff[0] == "remove_table"
                and getattr(diff[1], "name", None) == "alembic_version"
            )
        ]
        assert not meaningful, f"schema drift detected: {meaningful}"


class TestRoundTrip:
    def test_downgrade_to_base_then_upgrade_again(self, database_url):
        """A migration that cannot be reversed cannot be trusted to be re-applied.

        Uses a throwaway database so a failure part-way through cannot leave the shared
        test schema in a state that breaks every other test.
        """
        roundtrip_url = database_url.replace("/aer_test", "/aer_roundtrip")

        async def recreate() -> None:
            base, _, name = roundtrip_url.rpartition("/")
            engine = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
            try:
                async with engine.connect() as conn:
                    await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                    await conn.execute(text(f'CREATE DATABASE "{name}"'))
            finally:
                await engine.dispose()

        async def count_public_tables() -> int:
            engine = create_async_engine(roundtrip_url)
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables"
                            " WHERE table_schema = 'public'"
                        )
                    )
                    return int(result.scalar_one())
            finally:
                await engine.dispose()

        async def count_enum_types() -> int:
            engine = create_async_engine(roundtrip_url)
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_type t"
                            " JOIN pg_namespace n ON n.oid = t.typnamespace"
                            " WHERE n.nspname = 'public' AND t.typtype = 'e'"
                        )
                    )
                    return int(result.scalar_one())
            finally:
                await engine.dispose()

        asyncio.run(recreate())

        run_alembic(roundtrip_url, "upgrade", "head")
        assert asyncio.run(count_public_tables()) == 8  # 7 application tables + alembic_version
        assert asyncio.run(count_enum_types()) == 6

        run_alembic(roundtrip_url, "downgrade", "base")
        # Only alembic_version survives, and every enum type is cleaned up. A leaked enum
        # would make the next upgrade fail with "type already exists".
        assert asyncio.run(count_public_tables()) == 1
        assert asyncio.run(count_enum_types()) == 0

        run_alembic(roundtrip_url, "upgrade", "head")
        assert asyncio.run(count_public_tables()) == 8
        assert asyncio.run(count_enum_types()) == 6
