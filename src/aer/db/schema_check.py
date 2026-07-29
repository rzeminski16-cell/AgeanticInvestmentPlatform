"""Does the database have the schema this code expects?

A pending migration is the second most common local failure after "Postgres is not
running", and until this existed it was by far the worse of the two to diagnose: the
process started cleanly, ``/readyz`` reported ok, and then one page returned an opaque 500
whose only clue was in a stack trace. "You forgot to migrate" is a question the application
can answer for itself, so it should.

**Derived from the models, not from a constant.** The comparison is against
``Base.metadata``, which is exactly what the ORM will try to query. There is no revision
number to bump and nothing to keep in step — a table added to the models and forgotten in a
migration is caught by the same check as a migration nobody ran.

**Only missing things count.** A table or column present in the database and absent from
the models is not drift in the sense that matters here: nothing will try to select it. The
question being asked is narrower and more useful than "does the schema match?" — it is
"will the queries this code issues fail?".

The check does not compare types, defaults or constraints. That is what
``tests/test_migrations.py::TestSchemaMatchesModels`` is for, using Alembic's own
autogenerate comparison against a freshly migrated database. This is a runtime probe, and a
runtime probe that reported a type mismatch as an outage would be wrong more often than
right.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from aer.db.base import Base
from aer.errors import AerError

# Imported for the side effect of registering every mapper on `Base.metadata`. Without it
# the comparison below would run against an empty set of tables and cheerfully report that
# nothing is missing.
from aer.db import models as _models  # noqa: F401  # isort: skip

__all__ = ["SchemaDrift", "SchemaOutOfDateError", "schema_drift"]

# How many names to list before saying "and N more". A probe response that dumps ninety
# column names is one nobody reads.
_MAX_LISTED = 5


class SchemaOutOfDateError(AerError):
    """The database is missing something the models expect.

    Raised only by the readiness probe, which turns it into a 503 naming the missing
    objects and the command that fixes them.
    """

    code = "schema_out_of_date"
    http_status = 503


@dataclass(frozen=True, slots=True)
class SchemaDrift:
    """What the models expect and the database does not have."""

    missing_tables: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.missing_tables and not self.missing_columns

    def as_message(self) -> str:
        """One sentence naming what is missing and how to fix it.

        Names the objects rather than only the count: "run the migrations" is advice, but
        "``job_cancellations`` is missing" is the thing that tells you *which* migration you
        skipped and whether you are looking at the problem you think you are.
        """
        if self.is_clean:
            return "The database schema matches the models."

        parts: list[str] = []
        if self.missing_tables:
            parts.append(f"tables {_listed(self.missing_tables)}")
        if self.missing_columns:
            parts.append(f"columns {_listed(self.missing_columns)}")

        return (
            f"The database is missing {' and '.join(parts)}. This almost always means a "
            "migration has not been run: `uv run alembic upgrade head`."
        )


def _listed(names: tuple[str, ...]) -> str:
    shown = ", ".join(names[:_MAX_LISTED])
    remaining = len(names) - _MAX_LISTED
    return shown if remaining <= 0 else f"{shown} and {remaining} more"


def _drift(connection: Connection) -> SchemaDrift:
    inspector = inspect(connection)
    present = set(inspector.get_table_names())

    missing_tables: list[str] = []
    missing_columns: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            missing_tables.append(table.name)
            # No column check for a table that is not there: it would report every column
            # as missing and bury the one fact that matters.
            continue
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        missing_columns.extend(
            f"{table.name}.{column.name}" for column in table.columns if column.name not in actual
        )

    return SchemaDrift(tuple(sorted(missing_tables)), tuple(sorted(missing_columns)))


async def schema_drift(session: AsyncSession) -> SchemaDrift:
    """Compare the models against the live schema.

    Raises whatever the driver raises if the database is unreachable — distinguishing "the
    schema is behind" from "there is no database" is the caller's job, and both callers
    report them differently.
    """
    return await session.run_sync(lambda sync_session: _drift(sync_session.connection()))
