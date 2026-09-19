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

from collections.abc import Mapping, Sequence
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

import aer.db.models  # noqa: F401 -- importing is what registers the tables on the metadata
from aer.db.base import Base

__all__ = [
    "PARTLY_SEEDED",
    "SEEDED_BY_MIGRATIONS",
    "STARVED_PROBE_KEY",
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

STARVED_PROBE_KEY: Final = "starved_probe"
"""The one section definition a fixture adds that claims to be part of the spine.

``tests.workflow_fixtures.seed_starved_section`` writes it, and it declares
``origin='builtin'`` because that is what it stands in for — a section of the spine the
run owes and cannot evidence. The schema admits no third origin (a ``skill`` row must
name a skill), so nothing distinguishes it from the migration's eighteen except its name,
and the name is therefore where the cleanup has to know it. Imported by the fixture that
writes it, so the two cannot drift apart.
"""

PARTLY_SEEDED: Final[dict[str, str]] = {
    # The spine is `origin = 'builtin'` — with one exception, which is a fixture's own.
    "section_definitions": f"origin <> 'builtin' OR key = '{STARVED_PROBE_KEY}'",
}
"""Seeded tables that a *run* also writes to, and the rows in them that are not reference.

Skipping such a table wholesale leaves the run's own rows behind, and
``section_definitions`` carries a ``RESTRICT`` reference to ``skills``: after any run that
pinned a custom-section skill, emptying ``skills`` was a foreign-key violation rather than
a cleanup, and the next test inherited a section definition whose skill had gone. So the
table is visited in its proper place in the order and emptied of everything the predicate
matches — which leaves exactly what a fresh database has. Naming the table explicitly
still empties all of it.

The starved probe is caught by the same predicate for the same reason from the other
side: it is a required section every run after it would owe and fail, and every test that
counts eighteen sections would count nineteen. Both are the same rule — *reference data is
a property of rows, and this table's rows are not all of one kind*.

Found on 19 September 2026, by the first journey row to enable a skill and then reset.
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
        # A partly-seeded table stays in the order: its reference rows are kept by the
        # predicate in `PARTLY_SEEDED`, not by skipping the table.
        return tuple(
            name for name in ordered if name not in SEEDED_BY_MIGRATIONS or name in PARTLY_SEEDED
        )

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

    Looks before it deletes. Every engine fixture empties the database on the way in (see
    `tests/db_fixtures.py`), and nearly every test finds it already empty: asking is one
    round trip where the deletes are one per table — about a millisecond against
    twenty-five, over two thousand tests.
    """
    order = deletion_order(names)
    # A caller naming a table asked for all of it — including the spine. The predicates
    # only guard the default sweep.
    kept = {} if names is not None else PARTLY_SEEDED
    async with engine.begin() as connection:
        # A test that wedges here should say so quickly rather than hanging the suite,
        # which is the failure mode A17 is about.
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        if not await _holds_rows(connection, order, kept):
            return
        for name in order:
            await connection.execute(text(_deletion(name, kept)))


def _deletion(name: str, kept: Mapping[str, str]) -> str:
    where = kept.get(name)
    # Both halves come from the metadata and from the constant above, never from a caller.
    return f'DELETE FROM "{name}"' + (f" WHERE {where}" if where else "")  # noqa: S608


async def _holds_rows(
    connection: AsyncConnection, tables: Sequence[str], kept: Mapping[str, str]
) -> bool:
    """Whether any of ``tables`` has a deletable row in it, asked in one statement.

    The predicate is applied here too: without it a partly-seeded table always reports
    rows — the spine is always there — and the "already empty" fast path, which is what
    keeps two thousand tests to one round trip each, would never be taken.
    """
    if not tables:
        return False
    probe = " OR ".join(
        f'EXISTS (SELECT 1 FROM "{name}"'  # noqa: S608 -- from the metadata
        + (f" WHERE {where}" if (where := kept.get(name)) else "")
        + ")"
        for name in tables
    )
    return bool((await connection.execute(text(f"SELECT {probe}"))).scalar())


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
