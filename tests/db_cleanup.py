"""Emptying the database between tests without taking a lock on the whole schema.

Gap A17. Twenty-six test modules cleaned up with ``TRUNCATE … RESTART IDENTITY CASCADE``,
and that statement is the wrong tool here for two independent reasons.

**It needs an ``ACCESS EXCLUSIVE`` lock on every table it names, all at once.** Anything
merely *reading* one of them blocks it, and it blocks anything reading them — so a suite
that mixes the transactional fixtures with a command opening its own engine and really
committing has two transactions each holding what the other wants. That is gap A17's
deadlock, and it does not announce itself: the run simply stops, and the timeout does not
fire because nothing has timed out.

**And ``CASCADE`` overrides the schema's declared delete semantics wholesale**, including
the ``RESTRICT`` rules that exist so evidence cannot be deleted out from under a report
that cites it. A cleanup that quietly defeats those is a cleanup that would let a genuine
ordering bug pass, because the test tore down a state the application could never reach.

So: ``DELETE``, in reverse dependency order, in one transaction. It takes row locks rather
than table locks, it honours every foreign key as declared, and the order comes from the
metadata rather than from a list somebody has to remember to update — which is the same
reasoning `aer.services.requests` applies when it works out what a purge owns.

The sequence reset that ``RESTART IDENTITY`` gave is not reproduced, and nothing wanted it:
every primary key in this schema is a UUID.

**Reference data the migrations install is preserved.** Deleting everything is not the same
as resetting to a fresh database: a fresh database has the eighteen-section spine and the
sector profiles in it, because migrations 0023 and 0014 put them there. A cleanup that
removed those would leave the schema in a state no deployment has ever been in, and the
next test to resolve a section would fail for a reason nowhere near its own code — which is
exactly what happened the first time this was written. See :data:`SEEDED_BY_MIGRATIONS`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import aer.db.models  # noqa: F401 -- importing is what registers the tables on the metadata
from aer.db.base import Base

__all__ = [
    "SEEDED_BY_MIGRATIONS",
    "delete_all",
    "deletion_order",
    "empty_the_database",
]

SEEDED_BY_MIGRATIONS: Final[frozenset[str]] = frozenset(
    {
        # Migration 0023: the eighteen-section spine every report is built from.
        "section_definitions",
        # Migration 0014: the specialist sector profiles the classifier resolves against.
        "sector_profiles",
    }
)
"""Tables a migration fills, which emptying the database must leave alone.

Not test data. These arrive with the schema, every deployment has them, and a suite that
deleted them would be testing a state that cannot exist. Naming one explicitly in a call
still empties it, for the rare test that wants to prove behaviour when the spine is absent.
"""


def deletion_order(names: Sequence[str] | None = None) -> tuple[str, ...]:
    """Table names in an order that never violates a foreign key.

    ``Base.metadata.sorted_tables`` is dependency-sorted with parents first, so reversing
    it puts children first — which is exactly the order rows may be removed in.

    The metadata is only populated by importing the model modules, which is why this file
    imports `aer.db.models` for its side effect: without it `sorted_tables` is empty and a
    cleanup would silently delete nothing at all.

    Args:
        names: Restrict to these tables, keeping the safe order. A name the metadata does
            not carry is refused rather than skipped: a typo would otherwise leave a table
            full and the test that depended on it empty would fail somewhere else entirely.
            Naming a seeded table explicitly does empty it; the default does not.
    """
    ordered = [table.name for table in reversed(Base.metadata.sorted_tables)]
    if names is None:
        return tuple(name for name in ordered if name not in SEEDED_BY_MIGRATIONS)

    wanted = {name.strip() for name in names if name.strip()}
    unknown = wanted - set(ordered)
    if unknown:
        message = (
            f"No table named {', '.join(sorted(unknown))} exists in the metadata. A cleanup "
            "naming a table that is not there empties nothing and says nothing."
        )
        raise LookupError(message)
    return tuple(name for name in ordered if name in wanted)


async def delete_all(engine: AsyncEngine, names: Sequence[str] | None = None) -> None:
    """Empty the named tables — or every table — in one transaction.

    Row locks rather than an exclusive lock on the schema, so this does not deadlock
    against a fixture holding a read, and every declared foreign key is honoured on the
    way down.
    """
    order = deletion_order(names)
    async with engine.begin() as connection:
        # A test that wedges here should say so quickly rather than hanging the suite,
        # which is the failure mode A17 is about.
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        for name in order:
            await connection.execute(text(f'DELETE FROM "{name}"'))  # noqa: S608 -- from metadata


async def empty_the_database(database_url: str, names: Sequence[str] | None = None) -> None:
    """:func:`delete_all` for a caller holding a URL rather than an engine.

    Disposes the engine it opens. The CLI suites need this shape because the command under
    test owns its own engine and the fixture cannot borrow it.
    """
    engine: Any = create_async_engine(database_url)
    try:
        await delete_all(engine, names)
    finally:
        await engine.dispose()
