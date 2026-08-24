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
import re
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

    def test_the_docstring_header_agrees_with_the_revision_it_declares(self):
        """The header Alembic prints must be the revision Alembic runs.

        `alembic current --verbose` and `alembic history` render the module docstring, so a
        stale ``Revision ID:`` line is a wrong answer to "what am I on?" delivered by the
        tool an operator uses to find out. The variables below it are what actually runs, so
        nothing breaks and nothing complains — the two just quietly disagree.

        They disagreed for four revisions after the 2026-08-23 merge renumbered one branch's
        chain onto the other's: `revision` and `down_revision` were rewritten and the headers
        were not, so 0057 introduced itself as 0054.
        """
        script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused/unused"))
        for revision in script.walk_revisions():
            source = Path(revision.path).read_text(encoding="utf-8")
            header = re.search(r"^\s*Revision ID: (\S+)\s*$", source, re.M)
            if header:
                assert header.group(1) == revision.revision, (
                    f"{revision.path} declares revision {revision.revision} but its docstring "
                    f"header says {header.group(1)}. `alembic current --verbose` prints the "
                    "header, so the two must agree."
                )
            revises = re.search(r"^\s*Revises: (\S*)\s*$", source, re.M)
            if revises:
                expected = revision.down_revision or ""
                assert revises.group(1) in (expected, "") or expected == "", (
                    f"{revision.path} revises {expected!r} but its docstring header says "
                    f"{revises.group(1)!r}."
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
    migration two years of revisions later (ADR 0082, revision 0052).

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

    def test_a_seed_downgrade_refuses_when_a_report_still_cites_it(self, database_url):
        """The round-trip above proves the chain reverses on an **empty** schema.

        That is the weaker half of the property, and the half that never fails. Six revisions
        seed a ``section_definitions`` row and delete it again on the way down, while
        ``report_sections.section_definition_id`` is ``ON DELETE RESTRICT`` on purpose — so on
        a database that has produced a report, that delete cannot succeed at all.

        Found by hand in August 2026: a full rollback on a used database stopped on revision
        0050 with a bare ``ForeignKeyViolationError`` naming a constraint, which tells an
        operator nothing about what to do next. What is asserted here is which of the two
        answers they get, and that the refusal lifts once the citing rows are gone.
        """
        guard_url = database_url.replace("/aer_test", "/aer_seed_guard")

        async def recreate() -> None:
            base, _, name = guard_url.rpartition("/")
            engine = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
            try:
                async with engine.connect() as conn:
                    await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                    await conn.execute(text(f'CREATE DATABASE "{name}"'))
            finally:
                await engine.dispose()

        async def seed_a_report_section() -> None:
            """The shortest chain that makes a *realistic* report cite the row 0050 seeds.

            The job needs a ``request_id`` and not only a ``work_order_id``. Revision 0054's
            downgrade deletes any job without one — "a row created after this migration ran,
            with nowhere to put it in the old shape" — so a job carrying only a work order is
            swept away three revisions before 0050 is reached, taking its report sections with
            it on the cascade, and the guard below would never be tested. A work order and its
            research request share an id (ADR 0072), so they are seeded as one.
            """
            engine = create_async_engine(guard_url)
            try:
                async with engine.begin() as conn:
                    user = (
                        await conn.execute(
                            text(
                                "INSERT INTO users (email, display_name) "
                                "VALUES ('guard@example.invalid', 'Guard') RETURNING id"
                            )
                        )
                    ).scalar_one()
                    request = (
                        await conn.execute(
                            text(
                                "INSERT INTO research_requests (user_id, company_name, "
                                "ticker, exchange, as_of_date, base_currency, "
                                "investment_horizon_months) VALUES (:user, 'Guard plc', "
                                "'GRD', 'LSE', DATE '2026-01-01', 'GBP', 12) RETURNING id"
                            ),
                            {"user": user},
                        )
                    ).scalar_one()
                    await conn.execute(
                        text(
                            "INSERT INTO work_orders (id, user_id, as_of_date) "
                            "VALUES (:id, :user, DATE '2026-01-01')"
                        ),
                        {"id": request, "user": user},
                    )
                    job = (
                        await conn.execute(
                            text(
                                "INSERT INTO jobs (work_order_id, request_id, "
                                "workflow_version, code_version) "
                                "VALUES (:id, :id, 'vertical_slice_v1', 'test') RETURNING id"
                            ),
                            {"id": request},
                        )
                    ).scalar_one()
                    written = await conn.execute(
                        text(
                            "INSERT INTO report_sections (job_id, section_definition_id, "
                            "section_key, position) "
                            "SELECT :job, sd.id, sd.key, 1 FROM section_definitions sd "
                            "WHERE sd.key = 'validation_disagreements' "
                            "AND sd.origin = 'builtin' AND sd.version = 3"
                        ),
                        {"job": job},
                    )
                    assert written.rowcount == 1, (
                        "revision 0050 seeds validation_disagreements v3; if this inserted "
                        "nothing, the seed moved and this test is no longer about it"
                    )
            finally:
                await engine.dispose()

        async def clear_report_sections() -> None:
            engine = create_async_engine(guard_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("DELETE FROM report_sections"))
            finally:
                await engine.dispose()

        asyncio.run(recreate())
        run_alembic(guard_url, "upgrade", "head")
        asyncio.run(seed_a_report_section())

        with pytest.raises(RuntimeError) as refusal:
            run_alembic(guard_url, "downgrade", "0049")

        message = str(refusal.value)
        assert "validation_disagreements" in message, message
        assert "reset-research" in message, (
            "the refusal has to name the remedy -- that is the whole difference between it "
            f"and the foreign-key error it replaces: {message}"
        )

        # And it is a guard, not a wall: with nothing citing the row, the same downgrade runs.
        asyncio.run(clear_report_sections())
        run_alembic(guard_url, "downgrade", "0049")
        run_alembic(guard_url, "upgrade", "head")
