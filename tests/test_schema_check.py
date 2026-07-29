"""The "have you run the migrations?" probe.

Written after a real failure: a database one migration behind started cleanly, answered
``SELECT 1``, reported ready, and then returned an opaque 500 from the one page that
touched the new table. Every test here drops something real and asks the inspector, rather
than asserting against a fake — a stubbed inspector would only prove the assertion agrees
with itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aer.db.models import JobCancellation, ResearchRequest
from aer.db.schema_check import SchemaDrift, schema_drift

pytestmark = pytest.mark.integration


@pytest.fixture
async def probe_session(api_engine):
    """A plain session, not the transactional one.

    ``db_session`` holds an open transaction for the length of a test. A ``DROP TABLE``
    issued on another connection would block on it rather than failing, which turns a
    wrong assertion into a hung suite.
    """
    factory = async_sessionmaker(bind=api_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def restore_schema(api_engine):
    """Put back whatever a test dropped, whether it passed or not."""
    yield
    async with api_engine.begin() as connection:
        await connection.run_sync(JobCancellation.__table__.create, checkfirst=True)
        await connection.execute(
            text(
                "ALTER TABLE research_requests "
                "ADD COLUMN IF NOT EXISTS resolved boolean NOT NULL DEFAULT false"
            )
        )


async def drop_table(engine, name: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))


async def drop_column(engine, table: str, column: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}"))


class TestAMigratedDatabase:
    async def test_reports_no_drift(self, probe_session):
        drift = await schema_drift(probe_session)

        assert drift.is_clean
        assert drift.missing_tables == ()
        assert drift.missing_columns == ()

    async def test_it_actually_looked_at_something(self, probe_session):
        # Without this, an empty `Base.metadata` — the models never imported — would make
        # every other test in this file pass while checking nothing at all.
        from aer.db.base import Base  # noqa: PLC0415 -- the point is the import's effect

        assert len(Base.metadata.sorted_tables) > 10


class TestAMissingTable:
    async def test_it_is_reported_by_name(self, api_engine, probe_session, restore_schema):
        await drop_table(api_engine, "job_cancellations")

        drift = await schema_drift(probe_session)

        assert not drift.is_clean
        assert "job_cancellations" in drift.missing_tables

    async def test_the_message_names_the_table_and_the_fix(
        self, api_engine, probe_session, restore_schema
    ):
        await drop_table(api_engine, "job_cancellations")

        message = (await schema_drift(probe_session)).as_message()

        assert "job_cancellations" in message
        assert "alembic upgrade head" in message

    async def test_its_columns_are_not_also_listed(self, api_engine, probe_session, restore_schema):
        # A missing table has every column missing. Listing them all would bury the one
        # fact that matters under twenty that follow from it.
        await drop_table(api_engine, "job_cancellations")

        drift = await schema_drift(probe_session)

        assert drift.missing_columns == ()


class TestAMissingColumn:
    async def test_it_is_reported_as_table_dot_column(
        self, api_engine, probe_session, restore_schema
    ):
        # Migration 0002 added exactly this column, so this is a real migration's shape and
        # not a hypothetical one.
        await drop_column(api_engine, "research_requests", "resolved")

        drift = await schema_drift(probe_session)

        assert drift.missing_tables == ()
        assert "research_requests.resolved" in drift.missing_columns

    async def test_the_table_is_not_reported_as_missing(
        self, api_engine, probe_session, restore_schema
    ):
        await drop_column(api_engine, "research_requests", "resolved")

        drift = await schema_drift(probe_session)

        assert ResearchRequest.__tablename__ not in drift.missing_tables


class TestTheMessage:
    def test_a_clean_schema_says_so(self):
        assert "matches" in SchemaDrift().as_message()

    def test_it_names_both_kinds_at_once(self):
        message = SchemaDrift(("alpha",), ("beta.gamma",)).as_message()

        assert "alpha" in message
        assert "beta.gamma" in message

    def test_a_long_list_is_truncated(self):
        # A probe response that dumps ninety column names is one nobody reads.
        names = tuple(f"table_{index}" for index in range(20))

        message = SchemaDrift(names).as_message()

        assert "and 15 more" in message
        assert "table_19" not in message
