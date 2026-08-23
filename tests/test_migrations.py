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
import logging
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Enum as SaEnum
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


@pytest.mark.integration
class TestMigrationsDoNotDisturbTheHost:
    def test_running_migrations_leaves_existing_loggers_enabled(self, database_url):
        """Applying a migration must not silence the application's loggers.

        ``migrations/env.py`` calls ``logging.config.fileConfig``, whose
        ``disable_existing_loggers`` argument defaults to **True** — it disables every
        logger that already exists. Migrations run in-process here and would in any
        future migrate-on-startup path, so the default turns "upgrade the schema" into
        "stop logging", with no error and nothing in the output to explain it.

        This failure is invisible: a disabled logger does not raise, it returns quietly.
        The only way it stays fixed is a test that looks.
        """
        canary = logging.getLogger("aer.test.canary")
        assert not canary.disabled

        run_alembic(database_url, "upgrade", "head")

        assert not canary.disabled
        assert not logging.getLogger("aer.config").disabled


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


class TestEnumLabelsMatchTheModels:
    """The drift the check above cannot see, and it cost a slice to find out.

    ``compare_metadata`` compares tables, columns, types and server defaults. It does not
    compare the *labels* of an enum type — so ``Provider.ECB`` sat in the Python enum from
    the day the ECB adapter was written while the Postgres type never learned the value,
    and every one of these tests passed. The first thing that tried to write an ECB source
    document failed with ``invalid input value for enum provider``, at runtime, in a
    migration two years of revisions later (ADR 0078, revision 0052).

    Only one direction is a fault. A label the model can write and the database does not
    have is an INSERT that will fail; a label the database has and the model no longer
    writes is the residue of a downgrade, which Postgres cannot remove and which harms
    nothing — revisions 0016 and 0025 both say so.
    """

    async def test_every_value_a_model_can_write_exists_in_the_database(self, db_engine):
        wanted: dict[str, set[str]] = {}
        for table in Base.metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type, SaEnum) and column.type.name:
                    wanted.setdefault(column.type.name, set()).update(column.type.enums)

        async with db_engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT t.typname, e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid"
                )
            )
        held: dict[str, set[str]] = {}
        for type_name, label in rows:
            held.setdefault(type_name, set()).add(label)

        missing = {
            name: sorted(labels - held.get(name, set()))
            for name, labels in wanted.items()
            if labels - held.get(name, set())
        }

        assert not missing, f"enum values the schema cannot store: {missing}"


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

        # Derived from the models rather than hardcoded, so adding a table does not mean
        # editing this test -- and so a table the migration forgot still fails it.
        expected_tables = len(Base.metadata.tables) + 1  # + alembic_version
        expected_enums = len(
            {
                column.type.name
                for table in Base.metadata.tables.values()
                for column in table.columns
                if isinstance(column.type, SaEnum)
            }
        )

        asyncio.run(recreate())

        run_alembic(roundtrip_url, "upgrade", "head")
        assert asyncio.run(count_public_tables()) == expected_tables
        assert asyncio.run(count_enum_types()) == expected_enums

        run_alembic(roundtrip_url, "downgrade", "base")
        # Only alembic_version survives, and every enum type is cleaned up. A leaked enum
        # would make the next upgrade fail with "type already exists".
        assert asyncio.run(count_public_tables()) == 1
        assert asyncio.run(count_enum_types()) == 0

        run_alembic(roundtrip_url, "upgrade", "head")
        assert asyncio.run(count_public_tables()) == expected_tables
        assert asyncio.run(count_enum_types()) == expected_enums
