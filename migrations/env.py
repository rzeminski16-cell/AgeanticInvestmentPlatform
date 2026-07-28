"""Alembic environment.

The database URL comes from application settings rather than ``alembic.ini``. Keeping a
connection string out of a committed file removes an easy way to leak a credential, and
means migrations and the application can never disagree about which database they mean.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the models package is what populates Base.metadata. Without it, autogenerate
# would compare the database against an empty schema and cheerfully propose dropping every
# table.
import aer.db.models  # noqa: F401
from aer.config import load_settings
from aer.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", load_settings().database_url)


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column type changes. Without this a NUMERIC(10,2) -> NUMERIC(10,4) change
        # is silently missed, which on a money column is a correctness bug.
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting, for review or manual application."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
