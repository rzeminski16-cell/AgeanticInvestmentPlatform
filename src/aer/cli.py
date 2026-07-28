"""Command line entry points.

Three commands, each doing exactly one thing that is awkward to do any other way: start
the server with logging already configured, print what build you are running, and create
the local user.

Every command loads settings through :func:`aer.config.load_settings`, so a
misconfiguration is reported once, in full, by the same code path the server uses — not
as a different error from each command.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated

import typer
import uvicorn
from sqlalchemy import select

from aer.config import Settings, load_settings
from aer.core.enums import UserRole
from aer.db.engine import create_engine, create_session_factory
from aer.db.models import AuditEvent, User
from aer.errors import AerError
from aer.logging import configure_logging, get_logger
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


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
