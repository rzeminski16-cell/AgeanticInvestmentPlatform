"""Command line entry points.

Each does exactly one thing that is awkward to do any other way: start the server with
logging already configured, print what build you are running, create the local user,
project a report into Obsidian, and clear the research history on a development machine.

Every command loads settings through :func:`aer.config.load_settings`, so a
misconfiguration is reported once, in full, by the same code path the server uses — not
as a different error from each command.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Sequence
from typing import Annotated

import typer
import uvicorn
from sqlalchemy import select, text

from aer.config import Settings, load_settings
from aer.core.enums import UserRole
from aer.db.engine import create_engine, create_session_factory
from aer.db.models import AuditEvent, User
from aer.errors import AerError
from aer.logging import configure_logging, get_logger
from aer.obsidian import ObsidianExportError, export_report
from aer.version import build_identity, git_sha, version

__all__ = ["app", "main"]

app = typer.Typer(
    name="aer",
    help="Ageiantic Equity Research Platform. A personal research tool, not investment advice.",
    no_args_is_help=True,
    add_completion=False,
)

_log = get_logger("aer.cli")


def _settings_or_exit() -> Settings:
    """Load settings, or print the problem and exit 2.

    Exit 2 rather than 1: this is a usage error the operator must fix, and separating it
    from a runtime failure means a wrapper script can tell "you configured it wrong" from
    "it broke".
    """
    try:
        return load_settings()
    except AerError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


# Named `show_version` rather than `version`, which would shadow the imported
# `aer.version.version` it calls.
@app.command(name="version")
def show_version(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Include the full SHA.")] = False,
) -> None:
    """Print the build identity."""
    if verbose:
        typer.echo(f"version: {version()}")
        typer.echo(f"git sha: {git_sha() or '(unavailable)'}")
        typer.echo(f"python:  {sys.version.split()[0]}")
    else:
        typer.echo(build_identity())


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Override AER_BIND_HOST.")] = None,
    port: Annotated[int | None, typer.Option(help="Override AER_BIND_PORT.")] = None,
    reload: Annotated[
        bool, typer.Option(help="Reload on source changes. Development only.")
    ] = False,
) -> None:
    """Run the web server."""
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    bind_host = host or settings.bind_host
    bind_port = port or settings.bind_port

    uvicorn.run(
        # Passed as an import string rather than an instance so that --reload works: the
        # reloader runs the application in a subprocess and can only reach it by name.
        "aer.api.app:bootstrap",
        factory=True,
        host=bind_host,
        port=bind_port,
        reload=reload,
        # Uvicorn's own logging config would install handlers that bypass the redacting
        # formatter configured above. Disabling it leaves every record going through the
        # single pipeline in aer.logging.
        log_config=None,
        access_log=False,
    )


@app.command(name="seed-user")
def seed_user(
    email: Annotated[str, typer.Option(help="The account's email address.")],
    name: Annotated[
        str | None, typer.Option(help="Display name. Defaults to the local part.")
    ] = None,
    role: Annotated[UserRole, typer.Option(help="Access level.")] = UserRole.OWNER,
) -> None:
    """Create the local user, or report the existing one.

    Idempotent: running it twice is not an error and does not create a second account.
    Email comparison is case-insensitive in the database (the column is ``CITEXT``), so
    ``Jane@Example.com`` and ``jane@example.com`` are the same person.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    created, user_email = asyncio.run(_seed_user(settings, email=email, name=name, role=role))

    if created:
        typer.secho(f"Created user {user_email} ({role.value}).", fg=typer.colors.GREEN)
    else:
        typer.echo(f"User {user_email} already exists; nothing to do.")


async def _seed_user(
    settings: Settings,
    *,
    email: str,
    name: str | None,
    role: UserRole,
) -> tuple[bool, str]:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            existing = await session.scalar(select(User).where(User.email == email))
            if existing is not None:
                return False, existing.email

            user = User(
                email=email,
                display_name=name or email.split("@", 1)[0],
                role=role,
            )
            session.add(user)
            await session.flush()

            # The audit log records who exists and when they appeared. A user account
            # created outside the trail is a gap in the record of who approved what.
            previous = await session.scalar(
                select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
            )
            session.add(
                AuditEvent.create_linked(
                    actor="cli",
                    event_type="user.created",
                    payload={"user_id": str(user.id), "email": user.email, "role": role.value},
                    previous=previous,
                )
            )
            await session.commit()
            return True, user.email
    finally:
        await engine.dispose()


@app.command(name="export-obsidian")
def export_obsidian(
    report_id: Annotated[uuid.UUID, typer.Argument(help="The approved report to export.")],
) -> None:
    """Project one approved report into the configured Obsidian vault.

    Nothing exports automatically: this command, and the button on the report page, are
    the only ways a note reaches the vault — and only for a report somebody approved.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        files = asyncio.run(_export_obsidian(settings, report_id=report_id))
    except ObsidianExportError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.secho(f"Exported {len(files)} file(s):", fg=typer.colors.GREEN)
    for relative in files:
        typer.echo(f"  {relative}")


async def _export_obsidian(settings: Settings, *, report_id: uuid.UUID) -> list[str]:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            record = await export_report(session, settings=settings, report_id=report_id)
            files = list(record.files)
            await session.commit()
            return files
    finally:
        await engine.dispose()


@app.command(name="reset-research")
def reset_research(
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation. For scripts, not for haste.")
    ] = False,
) -> None:
    """Delete every research request and everything derived from one.

    For starting over on a development machine. What survives is what was never part of a
    run: the user accounts, the authored skills, the section and sector definitions, the
    content-addressed artefacts, and the audit log — which gains an entry recording this,
    because wiping the history is itself an act the trail should hold.

    Cached evidence does **not** survive, and cannot: ``source_documents`` carries the
    request that fetched it, so a document outliving its request would be a row pointing at
    nothing. The next run re-fetches from SEC EDGAR, which costs a few seconds and no money.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    tables = _research_tables()
    counts = asyncio.run(_row_counts(settings, tables))
    populated = {name: count for name, count in counts.items() if count}

    if not populated:
        typer.echo("No research history to remove; nothing to do.")
        return

    typer.secho("This will permanently delete:", fg=typer.colors.YELLOW)
    for name, count in sorted(populated.items(), key=lambda row: (-row[1], row[0])):
        typer.echo(f"  {count:>9,}  {name}")
    if not yes and not typer.confirm("Delete all of it?"):
        typer.echo("Nothing was deleted.")
        raise typer.Exit(code=1)

    removed = sum(populated.values())
    asyncio.run(_reset_research(settings, tables, rows=removed))
    typer.secho(
        f"Removed {removed:,} row(s) across {len(populated)} table(s).", fg=typer.colors.GREEN
    )


def _research_tables() -> tuple[str, ...]:
    """Every table holding part of a research request, in the order it must be emptied.

    Walked from ``research_requests`` through the foreign keys rather than listed by hand,
    so a table added next month is included without anyone remembering to add it here — and
    a hand-written list that has gone stale is how a "clean" database keeps one run's rows.

    **Deepest first, and that is not tidiness.** Several of these references are
    ``RESTRICT`` on purpose — a citation pins the extraction it quotes, a claim pins the
    calculation behind its number — so evidence cannot be deleted out from under the report
    that cites it. Nothing may go before the rows that point at it, which is exactly
    SQLAlchemy's own dependency sort, reversed.
    """
    from aer.db.base import Base  # noqa: PLC0415 -- import cost belongs to this command alone

    parents = {
        name: {fk.column.table.name for fk in table.foreign_keys}
        for name, table in Base.metadata.tables.items()
    }

    reached = {"research_requests"}
    while True:
        grown = {name for name, refs in parents.items() if refs & reached} | reached
        if grown == reached:
            break
        reached = grown

    ordered = [table.name for table in reversed(Base.metadata.sorted_tables)]
    return tuple(name for name in ordered if name in reached)


async def _row_counts(settings: Settings, tables: Sequence[str]) -> dict[str, int]:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return {
                name: int(await session.scalar(text(f'SELECT count(*) FROM "{name}"')) or 0)  # noqa: S608
                for name in tables
            }
    finally:
        await engine.dispose()


async def _reset_research(settings: Settings, tables: Sequence[str], *, rows: int) -> None:
    """Empty each table in turn, in one transaction, and record that it happened.

    ``DELETE`` rather than ``TRUNCATE``, for two reasons that both matter here.
    ``TRUNCATE`` needs an exclusive lock on every table at once, so it deadlocks against
    anything merely reading — a browser tab left open on the runs list is enough. And its
    ``CASCADE`` overrides the schema's declared delete semantics wholesale, including the
    ``RESTRICT`` rules that exist to stop evidence being deleted out from under a report.
    Walking the dependency order respects them instead: if one day a reference cannot be
    honoured, this fails saying which, rather than steamrollering it.

    ``rows`` is the count the operator was shown and agreed to, carried in rather than
    re-counted, so the audit entry records what was confirmed rather than what a second
    query happened to find.
    """
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            for name in tables:
                await session.execute(text(f'DELETE FROM "{name}"'))  # noqa: S608

            previous = await session.scalar(
                select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
            )
            session.add(
                AuditEvent.create_linked(
                    actor="cli",
                    event_type="research.reset",
                    payload={"tables": list(tables), "rows": rows},
                    previous=previous,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
