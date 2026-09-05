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
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from aer.config import Settings, load_settings
from aer.core.enums import JobStatus, Provider, UserRole
from aer.db.engine import create_engine, create_session_factory
from aer.db.models import AuditEvent, Job, User
from aer.errors import AerError
from aer.logging import configure_logging, get_logger
from aer.obsidian import ObsidianExportError, export_report
from aer.queue import discard_queued_runs
from aer.services.acceptance import AcceptanceReadout, acceptance_readout
from aer.services.audit_verify import ChainReport, verify_audit_chain
from aer.services.backup import (
    create_backup,
    restore_backup,
    verify_backup,
)
from aer.services.curation import Worksheet, curation_worksheet, render_worksheet
from aer.services.knowledge import KnowledgeStats, knowledge_stats
from aer.services.lessons import LessonCandidate, recurring_lessons
from aer.services.retention import (
    GarbageCollected,
    IntegrityReport,
    PurgeOutcome,
    collect_garbage,
    licensed_providers,
    purge_provider,
    purgeable_artefacts,
    verify_store,
)
from aer.services.run_replay import RunReplay, replay_run
from aer.services.gates import Reseal
from aer.services.step_diagnostic import RunDiagnostic, StepDiagnostic, run_diagnostic
from aer.storage.local import LocalArtefactStore
from aer.version import build_identity, git_sha, version

__all__ = ["app", "main"]

app = typer.Typer(
    name="aer",
    help="Tracework Invest. A personal research tool, not investment advice.",
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


@app.command(name="acceptance")
def acceptance_command(
    job_id: Annotated[uuid.UUID, typer.Argument(help="The finished run to measure.")],
) -> None:
    """Measure one finished run against the P11 acceptance requirements.

    The deterministic half of `docs/archive/polish-phase-1.md` P11: every check is a read of
    what the run recorded — sections, citations, the evaluation gate's verdicts, the
    issuers the report actually cites, the front page, the spend — printed beside its
    requirement so the diff is the output. Exits non-zero when a requirement fails.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        readout = asyncio.run(_acceptance(settings, job_id=job_id))
    except AerError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.secho(f"Run {readout.job_id} — {readout.subject}", fg=typer.colors.CYAN, bold=True)
    for check in readout.checks:
        if check.passed is None:
            flag, colour = "·", typer.colors.WHITE
        elif check.passed:
            flag, colour = "PASS", typer.colors.GREEN
        else:
            flag, colour = "FAIL", typer.colors.RED
        typer.secho(f"  [{flag}] {check.name}", fg=colour, bold=check.passed is False)
        typer.echo(f"        required: {check.required}")
        typer.echo(f"        measured: {check.measured}")

    if not readout.passed:
        typer.secho("The run does not meet the acceptance requirements.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("Every requirement holds.", fg=typer.colors.GREEN)


async def _acceptance(settings: Settings, *, job_id: uuid.UUID) -> AcceptanceReadout:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await acceptance_readout(session, job_id=job_id)
    finally:
        await engine.dispose()


@app.command(name="knowledge")
def knowledge_command() -> None:
    """Print what the platform knows: size, shape, coverage, freshness, vault health.

    The same figures the `/knowledge` page and `/api/knowledge` report, from the same
    service — three surfaces over one measurement, so a number quoted from the terminal
    and one read off the page cannot disagree.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    stats = asyncio.run(_knowledge(settings))

    size, shape = stats.size, stats.shape
    typer.secho("Size", fg=typer.colors.CYAN, bold=True)
    typer.echo(
        f"  {size.companies} compan(ies): {size.researched} researched, {size.stubs} stub(s)"
    )
    typer.echo(
        f"  {size.approved_reports} approved report(s), {size.industries} industr(ies), "
        f"{size.theme_nodes} theme(s), {size.catalyst_nodes} catalyst(s), "
        f"{size.sources} source(s)"
    )

    typer.secho("Shape", fg=typer.colors.CYAN, bold=True)
    typer.echo(
        f"  {shape.edges} competitor edge(s) over {shape.components} component(s); "
        f"largest {shape.largest_component}, isolated {shape.isolated}, "
        f"mean degree {shape.mean_degree}"
    )

    typer.secho("Coverage", fg=typer.colors.CYAN, bold=True)
    typer.echo(
        f"  {stats.coverage.researched_ratio} of the graph researched; "
        f"{stats.coverage.unclassified} unclassified; "
        f"{stats.coverage.single_member_industries} single-member industr(ies)"
    )

    if stats.accuracy.drivers:
        typer.secho("Assumption accuracy", fg=typer.colors.CYAN, bold=True)
        for driver in stats.accuracy.drivers:
            typer.echo(
                f"  {driver.name.replace('_', ' ')}: mean absolute delta "
                f"{driver.mean_absolute_delta} over {driver.measured} measured run(s)"
            )

    freshness = stats.freshness
    typer.secho("Freshness", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  research spans {freshness.oldest or '—'} to {freshness.newest or '—'}")
    if freshness.stale:
        typer.secho(f"  {len(freshness.stale)} stale:", fg=typer.colors.YELLOW)
        for row in freshness.stale:
            typer.echo(f"    {row.ticker}: {row.days_since} days since {row.newest_as_of}")
    if freshness.closed_windows:
        typer.secho(
            f"  {len(freshness.closed_windows)} catalyst window(s) closed, "
            "nothing recorded about what happened:",
            fg=typer.colors.YELLOW,
        )
        for catalyst in freshness.closed_windows:
            typer.echo(f"    {catalyst.ticker}: {catalyst.label} ({catalyst.expected_timing})")

    vault = stats.vault
    typer.secho("Vault", fg=typer.colors.CYAN, bold=True)
    if not vault.configured:
        typer.echo("  no vault configured, so nothing is projected")
    typer.echo(
        f"  {vault.exported_reports} report(s) exported, {vault.recorded_files} file(s) recorded"
    )
    if vault.unexported:
        typer.secho(
            f"  {len(vault.unexported)} approved report(s) never exported — "
            "knowledge the map does not have",
            fg=typer.colors.YELLOW,
        )
    if vault.drifted:
        typer.secho(
            f"  {len(vault.drifted)} file(s) under the vault root that no export wrote:",
            fg=typer.colors.YELLOW,
        )
        for relative in vault.drifted:
            typer.echo(f"    {relative}")


async def _knowledge(settings: Settings) -> KnowledgeStats:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await knowledge_stats(session, settings=settings)
    finally:
        await engine.dispose()


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

    # The queue points at rows that no longer exist (gap A57). The worker discards such a
    # job quietly now, but leaving twenty of them queued means twenty pointless wake-ups
    # on the next start, so they go with the rows they name.
    discarded = asyncio.run(_discard_queued(settings))
    if discarded:
        typer.echo(f"Discarded {discarded:,} queued run(s) that no longer exist.")


async def _discard_queued(settings: Settings) -> int:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        return await discard_queued_runs(redis)
    finally:
        await redis.aclose()


def _research_tables() -> tuple[str, ...]:
    """Every table holding part of a run, in the order it must be emptied.

    Walked from ``work_orders`` through the foreign keys rather than listed by hand, so a
    table added next month is included without anyone remembering to add it here — and a
    hand-written list that has gone stale is how a "clean" database keeps one run's rows.

    **From the run root, not from the mandate.** Since ADR 0072's fourth step, ``approvals``
    and ``plan_skill_pins`` reach a run only through ``work_orders``; a walk that still
    started at ``research_requests`` would leave both behind, and a "clean" database would
    still hold every approval anybody had ever given.

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

    reached = {"work_orders"}
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


@app.command(name="verify-artefacts")
def verify_artefacts() -> None:
    """Re-read every archived artefact and check it still hashes to its name.

    Invariant 1 is a claim in the present tense: "every externally derived fact traces to a
    hashed artefact" holds only while the artefact still matches its hash. The store checks
    the digest on every read, so rot is caught the moment something needs the document —
    this catches it while there is still a backup to restore from.

    Exits 1 on any corrupt or missing artefact, so it can be a cron line.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    report = asyncio.run(_verify_artefacts(settings))
    if report.is_sound:
        typer.secho(f"{report.checked:,} artefact(s) checked, all intact.", fg=typer.colors.GREEN)
        return

    for sha256 in report.corrupt:
        typer.secho(f"  corrupt  {sha256}", fg=typer.colors.RED, err=True)
    for sha256 in report.missing:
        typer.secho(f"  missing  {sha256}", fg=typer.colors.RED, err=True)
    typer.secho(
        f"{report.intact:,} of {report.checked:,} intact. "
        f"{len(report.corrupt)} corrupt, {len(report.missing)} missing.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command(name="backup")
def backup(
    to: Annotated[Path, typer.Option("--to", help="Directory to write the backup into.")],
) -> None:
    """Copy the database and the artefact store into one directory.

    Both halves or neither: the database holds every claim, calculation and citation, and
    the store holds the bytes those citations point at. A database restored beside an empty
    store is a set of citations into nothing.

    Refuses to write over an existing backup, and verifies what it just wrote before
    reporting success — an unread backup is not a backup.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    revision = asyncio.run(_schema_revision(settings))
    manifest = create_backup(
        database_url=settings.database_url,
        artefact_root=settings.artefact_root,
        destination=to,
        schema_revision=revision,
    )
    report = verify_backup(to)
    if not report.is_sound:
        for problem in report.problems:
            typer.secho(f"  {problem}", fg=typer.colors.RED, err=True)
        typer.secho(
            "The backup was written but does not verify. Treat it as unusable.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    megabytes = (manifest.database_bytes + manifest.artefact_bytes) / 1_048_576
    typer.secho(
        f"Backed up to {to}: schema {manifest.schema_revision}, "
        f"{manifest.artefact_count:,} artefact(s), {megabytes:,.1f} MiB, verified.",
        fg=typer.colors.GREEN,
    )


@app.command(name="verify-backup")
def verify_backup_command(
    directory: Annotated[Path, typer.Option("--from", help="The backup directory to check.")],
) -> None:
    """Re-hash a backup and check it against its own manifest.

    Touches no database, so it can be run wherever the backup lives — which is where a
    restore is usually first attempted, and where finding out the copy is bad is still
    cheap. Exits 1 on any problem, so it can be a cron line.
    """
    report = verify_backup(directory)
    if report.is_sound:
        assert report.manifest is not None
        typer.secho(
            f"{directory} verifies: {report.checked:,} artefact(s) and the database dump "
            f"match the manifest taken at {report.manifest.created_at}.",
            fg=typer.colors.GREEN,
        )
        return

    for problem in report.problems:
        typer.secho(f"  {problem}", fg=typer.colors.RED, err=True)
    typer.secho(
        f"{len(report.problems)} problem(s). This backup should not be restored from.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command(name="restore")
def restore(
    directory: Annotated[Path, typer.Option("--from", help="The backup directory to restore.")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation prompt. For scripts.")
    ] = False,
) -> None:
    """Put a backup back. **This drops and rebuilds the target database.**

    The backup is verified first and the restore refuses if it does not check out:
    restoring an unverified copy over a working database is how one bad backup becomes two.

    Artefacts are copied in rather than replacing the store, because they are
    content-addressed — a file already present under a digest *is* the file in the backup.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    target = make_url(settings.database_url)
    if not yes:
        typer.secho(
            f"This will DROP and rebuild every table in {target.database} on "
            f"{target.host}:{target.port}.",
            fg=typer.colors.YELLOW,
        )
        typer.confirm("Restore over it?", abort=True)

    report = restore_backup(
        directory=directory,
        database_url=settings.database_url,
        artefact_root=settings.artefact_root,
    )
    typer.secho(
        f"Restored {directory} into {target.database}: "
        f"{report.checked:,} artefact(s) verified on the way in.",
        fg=typer.colors.GREEN,
    )


@app.command(name="replay-run")
def replay_run_command(
    job_id: Annotated[uuid.UUID, typer.Argument(help="The run to reproduce.")],
) -> None:
    """Re-derive everything a run produced, from what the run wrote down.

    Fetches nothing and calls no model: every leg is re-checked against stored rows and
    archived bytes, so this costs nothing and gives the same answer in a year as today.

    Four legs, each able to fail on its own — the calculations replay from their own
    records, the citations still find their excerpts in the artefacts, the artefacts still
    read back by hash, and every model call still has both halves of its exchange archived.

    Exits 1 if any of them no longer holds.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    report = asyncio.run(_replay_run(settings, job_id=job_id))
    if report.reproduces:
        typer.secho(
            f"Run {job_id} reproduces: {report.calculations_checked:,} calculation(s), "
            f"{report.citations_checked:,} citation(s), {report.artefacts_checked:,} "
            f"artefact(s) and {report.model_calls_checked:,} model call(s) all still hold.",
            fg=typer.colors.GREEN,
        )
        return

    for problem in report.problems():
        typer.secho(f"  {problem}", fg=typer.colors.RED, err=True)
    typer.secho(
        f"Run {job_id} does not reproduce: {len(report.problems())} of "
        f"{report.checked:,} checked thing(s) no longer hold.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command(name="verify-audit")
def verify_audit() -> None:
    """Walk the audit log and check every record still links to the one before it.

    The chain has been written on every event since Task 3 and read back by nothing, which
    buys the cost of tamper-evidence without the benefit: the property is not "the rows are
    linked", it is "somebody would notice".

    What it catches is a row edited, deleted, inserted or reordered in place. What it cannot
    catch is a rewrite of the whole table with every hash recomputed — that needs the
    database to refuse UPDATE and DELETE, which waits on a migration role of its own.

    Exits 1 on a break, so it can be a cron line beside `verify-artefacts`.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    report = asyncio.run(_verify_audit(settings))
    if report.is_empty:
        typer.echo("The audit log is empty; nothing to verify.")
        return
    if report.is_sound:
        typer.secho(
            f"{report.checked:,} audit event(s) checked, the chain is intact.",
            fg=typer.colors.GREEN,
        )
        return

    typer.secho(
        f"The audit chain breaks at event {report.broken_at_id}: {report.reason}.",
        fg=typer.colors.RED,
        err=True,
    )
    typer.secho(
        f"{report.checked:,} of {report.total:,} event(s) verified before the break. "
        "Every event after it is unverifiable, which is the chain working, not a second "
        "fault.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command(name="gc-artefacts")
def gc_artefacts(
    delete: Annotated[
        bool, typer.Option("--delete", help="Actually remove them. Reports only without it.")
    ] = False,
) -> None:
    """Remove archived bytes that nothing in the database points at.

    Every reference to an artefact is ``RESTRICT``, so an artefact with no referrer is one
    no citation, report or agent run can reach — deleting it takes nothing away from
    invariant 1, because no fact traces to it. They accumulate honestly: `reset-research`
    clears the runs and leaves the content-addressed bytes, which is the right order.

    Reports by default. A sweep that deleted on its first invocation is one somebody runs
    once by accident.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    outcome = asyncio.run(_gc_artefacts(settings, delete=delete))
    if not outcome.found:
        typer.echo("No unreferenced artefacts; nothing to do.")
        return

    megabytes = outcome.reclaimable_bytes / 1_048_576
    if outcome.deleted:
        typer.secho(
            f"Removed {outcome.found:,} artefact(s), freeing {megabytes:,.1f} MiB.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo(
            f"{outcome.found:,} unreferenced artefact(s) holding {megabytes:,.1f} MiB. "
            "Re-run with --delete to remove them."
        )


@app.command(name="purge-licensed")
def purge_licensed(
    provider: Annotated[
        str, typer.Option("--provider", help="Which licensed feed. Currently only 'eodhd'.")
    ],
    reason: Annotated[
        str,
        typer.Option(
            "--reason",
            help="The obligation being honoured: which agreement, which date, which clause.",
        ),
    ],
    actor: Annotated[
        str, typer.Option("--actor", help="Who is doing this. Recorded on every purge row.")
    ] = "operator",
    yes: Annotated[
        bool, typer.Option("--yes", help="Skip the confirmation. For scripts, not for haste.")
    ] = False,
) -> None:
    """Delete every stored payload from one licensed provider, and record that it happened.

    **The command a terminated subscription needs, and the reason ADR 0030's route 2 is
    coherent.** EODHD's agreement requires every copy deleted within a month of the
    subscription ending, and an immutable archive cannot honour that — so the payload was
    separated from the provenance (ADR 0031) and the purge path was built. It then sat with
    no caller, which meant the obligation could be honoured only by somebody writing Python
    at a REPL against a live database.

    What goes is the bytes. What stays is everything that makes the deletion defensible:
    the artefact row, its hash and size, every source document pointing at it, every
    citation resolved against it, and an ``artefact_purges`` row naming the reason, the
    actor and the terms in force at the time. A citation into a purged payload can still be
    shown to *have been* verified on a date against a hash — and can never be re-verified,
    which is a real loss and is stated rather than engineered around.

    Refuses any provider whose material is retained permanently. Asking to purge the SEC is
    a question with one safe answer.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    chosen = _licensed_provider_or_exit(provider)
    if not reason.strip():
        typer.secho(
            "A purge needs a stated reason. 'Licence' is not one; name the obligation — "
            "which agreement, which date, which clause. Somebody reads it in two years "
            "when a citation will not resolve.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    outstanding = asyncio.run(_purgeable_count(settings, chosen))
    if not outstanding:
        typer.echo(f"No {chosen.value} payloads are stored; nothing to do.")
        return

    typer.secho(
        f"This will permanently delete {outstanding:,} {chosen.value} payload(s).",
        fg=typer.colors.YELLOW,
    )
    typer.echo("Citations over them will remain, and will never be re-verifiable.")
    if not yes and not typer.confirm("Delete them?"):
        typer.echo("Nothing was deleted.")
        raise typer.Exit(code=1)

    outcome = asyncio.run(_purge_licensed(settings, chosen, reason=reason, actor=actor))
    megabytes = outcome.bytes_freed / 1_048_576
    typer.secho(
        f"Purged {outcome.purged:,} artefact(s) from {chosen.value}, "
        f"freeing {megabytes:,.1f} MiB. Every deletion is recorded in artefact_purges.",
        fg=typer.colors.GREEN,
    )


def _licensed_provider_or_exit(name: str) -> Provider:
    """Resolve a provider name, refusing anything with no deletion obligation."""
    allowed = {found.value: found for found in licensed_providers()}
    chosen = allowed.get(name.strip().lower())
    if chosen is not None:
        return chosen

    typer.secho(
        f"{name!r} is not a licensed provider. Only a provider whose terms oblige deletion "
        f"may be purged, which is currently {', '.join(sorted(allowed)) or 'none of them'}. "
        "Everything else is retained permanently, and a filing erased is a report that can "
        "no longer be checked.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


async def _purgeable_count(settings: Settings, provider: Provider) -> int:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return len(await purgeable_artefacts(session, provider=provider))
    finally:
        await engine.dispose()


async def _purge_licensed(
    settings: Settings, provider: Provider, *, reason: str, actor: str
) -> PurgeOutcome:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            outcome = await purge_provider(
                session, _store_for(settings), provider=provider, reason=reason, actor=actor
            )
            await session.commit()
            return outcome
    finally:
        await engine.dispose()


@app.command(name="lessons")
def lessons_command(
    all_notes: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Include classes seen in only one run, not just the recurring ones.",
        ),
    ] = False,
) -> None:
    """Print what the critique loop keeps having to revise, grouped by class (ADR 0091).

    Counted from the revision notes across runs; a class met in two or more runs is a
    candidate lesson. The platform never acts on one: making a lesson standing guidance
    means authoring a methodology skill — versioned, pinned at gate 1, additive-only —
    which is the operator's act, on the operator's judgement.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    candidates = asyncio.run(_lessons(settings, minimum_jobs=1 if all_notes else 2))

    if not candidates:
        scope = "any run" if all_notes else "more than one run"
        typer.echo(f"No challenge class has provoked the critique loop in {scope}.")
        return

    for candidate in candidates:
        marker = "recurring" if candidate.recurring else "seen once"
        typer.secho(
            f"{candidate.scope}/{candidate.dimension} — {candidate.jobs} run(s), "
            f"{candidate.notes} note(s) — {marker}",
            fg=typer.colors.YELLOW if candidate.recurring else typer.colors.WHITE,
            bold=candidate.recurring,
        )
        for statement in candidate.latest_statements:
            typer.echo(f"    {statement}")
    typer.echo(
        "\nA recurring class becomes standing guidance only as a methodology skill you "
        "author and enable; the platform records, and never teaches itself."
    )


async def _lessons(settings: Settings, *, minimum_jobs: int) -> list[LessonCandidate]:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await recurring_lessons(session, minimum_jobs=minimum_jobs)
    finally:
        await engine.dispose()


@app.command(name="curation-worksheet")
def curation_worksheet_command(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the worksheet here instead of to the terminal."),
    ] = None,
    top: Annotated[
        int | None,
        typer.Option("--top", help="Only the highest-ranked rows. A sitting, not the list."),
    ] = None,
) -> None:
    """Prepare the concept-map curation worksheet (roadmap §2.8).

    A55 is 175 concepts and 110 segment tags the map cannot place, and it has survived
    several passes because it is judgement over accounting semantics rather than a code
    change. What this does is prepare the sitting: read what every run's extract step
    already recorded, aggregate it, rank it by the largest share of a mapped line any run
    saw, and write a worksheet with a column to fill in.

    **It decides nothing.** The output is a document you edit; turning what you write into
    alias-table entries is a separate, deliberate act. Tags already refused (§2.7) are
    listed apart and are not up for decision.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    worksheet = asyncio.run(_curation_worksheet(settings, limit=top))

    if not worksheet.rows:
        typer.echo(
            f"No unplaced tags in {worksheet.runs_read} recorded run(s). Either every tag "
            "mapped, or no run has reached the extract step yet."
        )
        return

    document = render_worksheet(worksheet)
    if out is None:
        typer.echo(document)
        return
    out.write_text(document, encoding="utf-8")
    typer.secho(
        f"{len(worksheet.rows)} tag(s) to decide about, from {worksheet.runs_read} run(s) → {out}",
        fg=typer.colors.GREEN,
    )


async def _curation_worksheet(settings: Settings, *, limit: int | None) -> Worksheet:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await curation_worksheet(session, limit=limit)
    finally:
        await engine.dispose()


@app.command(name="monitor")
def monitor_command(
    thesis_id: Annotated[
        uuid.UUID | None,
        typer.Option("--thesis", help="One thesis to read. Omit for every open thesis."),
    ] = None,
) -> None:
    """Run the thesis monitor in this process: one pass per open thesis (roadmap §3.6).

    The same pass the worker runs — same routing, same budget, same services — for a
    scheduler that has a terminal and no queue. Each pass reads the premises that carry a
    threshold against what has been filed since their last reading, spends against the
    per-run cap and the month's, and stops with a finding rather than pausing if a call
    would breach either (ADR 0078). Exits 1 if any pass stopped.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        stopped = asyncio.run(_monitor(settings, thesis_id=thesis_id))
    except AerError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error
    if stopped:
        raise typer.Exit(code=1)


async def _monitor(settings: Settings, *, thesis_id: uuid.UUID | None) -> bool:
    from aer.api.deps import current_user_or_none  # noqa: PLC0415 -- one query, one place
    from aer.runtime import build_services  # noqa: PLC0415 -- constructs the provider
    from aer.services import theses as thesis_service  # noqa: PLC0415
    from aer.services import thesis_monitor  # noqa: PLC0415
    from aer.services.configuration import effective_settings  # noqa: PLC0415

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    any_stopped = False
    try:
        async with factory() as session:
            user = await current_user_or_none(session)
            if user is None:
                message = (
                    "No user exists. Create one with: uv run aer seed-user --email you@example.com"
                )
                raise AerError(message)
            if thesis_id is None:
                theses = await thesis_monitor.theses_to_monitor(session, user_id=user.id)
            else:
                one = await thesis_service.thesis_of(session, thesis_id, user_id=user.id)
                if one is None:
                    message = f"No thesis {thesis_id}."
                    raise AerError(message, context={"thesis_id": str(thesis_id)})
                theses = [one]
            if not theses:
                typer.secho("No open thesis to monitor.", fg=typer.colors.YELLOW)
                return False

            resolved = await effective_settings(session, settings)
            services = build_services(resolved, redis=redis)
            for thesis in theses:
                outcome = await thesis_monitor.run_monitor(
                    session,
                    settings=resolved,
                    provider=services.provider,
                    router=services.router,
                    store=services.store,
                    user=user,
                    thesis=thesis,
                )
                any_stopped = any_stopped or outcome.stopped
                colour = typer.colors.RED if outcome.stopped else typer.colors.GREEN
                typer.secho(
                    f"{thesis.title}: {outcome.read} read, {outcome.nothing_new} nothing new, "
                    f"{outcome.unobservable} unobservable, {len(outcome.findings)} "
                    f"finding(s), £{outcome.spend_gbp:.4f}"
                    + (" — STOPPED at a cost ceiling" if outcome.stopped else ""),
                    fg=colour,
                )
    finally:
        await redis.aclose()
        await engine.dispose()
    return any_stopped


@app.command(name="queue")
def queue_command(
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit", help="At most this many runs. Omit for as many as the budget affords."
        ),
    ] = None,
) -> None:
    """Commission the next companies on the watchlist the standing budget affords (§3.10).

    The queue in the order followed, each entry turned into an ordinary research request
    as at today with the per-run cap, and its run started — to stop at gate one for you,
    as every research run does. Stops at the first entry the standing budget cannot
    afford and exits 1 if anything was left queued for that reason (ADR 0107).
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        short = asyncio.run(_queue(settings, limit=limit))
    except AerError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error
    if short:
        raise typer.Exit(code=1)


async def _queue(settings: Settings, *, limit: int | None) -> bool:
    from aer.api.deps import current_user_or_none  # noqa: PLC0415 -- one query, one place
    from aer.queue import enqueue_run  # noqa: PLC0415
    from aer.services import watchlist as watchlist_service  # noqa: PLC0415
    from aer.services.configuration import effective_settings  # noqa: PLC0415

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with factory() as session:
            user = await current_user_or_none(session)
            if user is None:
                message = (
                    "No user exists. Create one with: uv run aer seed-user --email you@example.com"
                )
                raise AerError(message)
            resolved = await effective_settings(session, settings)
            drain = await watchlist_service.commission_next(
                session, settings=resolved, user=user, limit=limit
            )
            await session.commit()
            for row, job in drain.commissioned:
                queued = await enqueue_run(redis, job.id)
                typer.secho(
                    f"{row.entry.listing}: commissioned as at {row.as_of_date.isoformat()}, "
                    f"run {job.id}"
                    + ("" if queued is not None else " — NOT QUEUED, start it by hand"),
                    fg=typer.colors.GREEN if queued is not None else typer.colors.YELLOW,
                )
            for reason in drain.skipped:
                typer.secho(f"Skipped {reason}", fg=typer.colors.YELLOW)
            if not drain.commissioned and not drain.stopped and not drain.skipped:
                typer.secho("Nothing queued on the watchlist.", fg=typer.colors.YELLOW)
            if drain.stopped:
                typer.secho(
                    f"Stopped with {drain.left} left in the queue: {drain.stopped}",
                    fg=typer.colors.RED,
                )
    finally:
        await redis.aclose()
        await engine.dispose()
    return bool(drain.stopped)


@app.command(name="diagnose")
def diagnose_command(
    job_id: Annotated[uuid.UUID, typer.Argument(help="The run to read back.")],
    step_key: Annotated[
        str | None, typer.Argument(help="One step to show in full. Omit for the whole run.")
    ] = None,
) -> None:
    """Print a run's per-step diagnostic, from what each step already recorded.

    Roadmap §3.15's readout, ADR 0090. Status, attempts, timing, cost, the recorded error,
    the step's stored output and every model call's tokens and archived payload hashes —
    all reads, no fetch, no model call, no spend. Exits 1 when the run has failed, so a
    script can tell a broken run from a waiting one.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        readout = asyncio.run(_run_diagnostic(settings, job_id=job_id))
    except AerError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if step_key is not None:
        step = readout.step(step_key)
        if step is None:
            hint = "not reached yet" if step_key in readout.not_reached else "not a recorded step"
            typer.secho(
                f"Run {job_id} has no record of {step_key!r} ({hint}).", fg=typer.colors.RED
            )
            raise typer.Exit(code=1)
        _print_step_detail(step)
        return

    _print_run_diagnostic(readout)
    if readout.status is JobStatus.FAILED:
        raise typer.Exit(code=1)


@app.command(name="step")
def step_command(
    job_id: Annotated[uuid.UUID, typer.Argument(help="The run to advance by one step.")],
) -> None:
    """Execute exactly one step of a run, in this terminal, then pause and diagnose.

    The deliberate half of resumption (roadmap §3.15, ADR 0090). Turns the job's step mode
    on — so the run pauses after every executed step wherever it executes, the worker
    included — runs the next incomplete step here with the same services the worker would
    build, and prints the step's diagnostic before anything else can spend. Steps that
    already succeeded are skipped for free; a FAILED run is first resumed, which appends
    the audit event that decision requires.

    Real steps spend real money: the provider, the fetcher and the budget guard are all
    the production ones.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        asyncio.run(_step_once(settings, job_id=job_id))
    except AerError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error


@app.command(name="reseal")
def reseal_command(
    job_id: Annotated[uuid.UUID, typer.Argument(help="The run whose final gate to re-seal.")],
    reason: Annotated[
        str, typer.Option(help="Why the seal is being moved; recorded in the audit chain.")
    ] = "re-sealed from the terminal",
) -> None:
    """Re-derive the final gate's seal from the run's own record.

    For a run stopped with "what this run sealed and what the review page shows have
    drifted apart". The seal moves to the payload as the record now stands — it adds
    nothing — and the audit chain records the move. Whether the recorded approval then
    matches is printed, not assumed: if it does, `aer resume` continues the run; if it does
    not, the approval was of older content and a second decision is refused by design.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        outcome = asyncio.run(_reseal(settings, job_id=job_id, reason=reason))
    except AerError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if not outcome.changed:
        typer.echo(
            f"The {outcome.gate.value} seal already matches the record "
            f"({outcome.current_hash[:12]}); nothing moved."
        )
    else:
        typer.secho(
            f"Re-sealed {outcome.gate.value}: {outcome.previous_hash[:12]} -> "
            f"{outcome.current_hash[:12]}.",
            fg=typer.colors.GREEN,
        )
    if outcome.approval_matches is None:
        typer.echo("No decision has been recorded at this gate yet.")
    elif outcome.approval_matches:
        typer.secho(
            f"The recorded approval matches the seal. Continue with: uv run aer resume {job_id}",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            "The recorded approval does not match the seal: it was taken over older content. "
            "A second decision is refused by design, so this run cannot be released; start "
            "the request again.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


@app.command(name="resume")
def resume_command(
    job_id: Annotated[uuid.UUID, typer.Argument(help="The run to hand back to the worker.")],
    reason: Annotated[
        str | None, typer.Option(help="Why the run is being resumed; recorded in the audit chain.")
    ] = None,
    keep_step_mode: Annotated[
        bool,
        typer.Option(
            "--keep-step-mode",
            help="Leave step mode on, so the worker executes one step and pauses again.",
        ),
    ] = False,
) -> None:
    """Re-enqueue the same job — the supported way to continue after a terminal failure.

    §2.3's resolution (ADR 0090). The decision to continue is appended to the audit chain
    with who, when and the state it resumed from; nothing the run recorded about itself is
    rewritten, and the engine skips every step that already succeeded. Refused for a run
    that succeeded, was cancelled, or is running now — the message says which and why.

    Step mode is turned off unless ``--keep-step-mode`` is passed: resuming means "carry
    on", and a run that paused again after one step would not be carrying on.
    """
    settings = _settings_or_exit()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    try:
        queued = asyncio.run(
            _resume(settings, job_id=job_id, reason=reason, keep_step_mode=keep_step_mode)
        )
    except AerError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if queued:
        typer.secho(f"Run {job_id} is queued; the worker will continue it.", fg=typer.colors.GREEN)
        return
    typer.secho(
        f"Run {job_id} is recorded as resumed, but the queue is unreachable. Start Redis "
        "and run this again, or step it inline with: aer step",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=1)


async def _run_diagnostic(settings: Settings, *, job_id: uuid.UUID) -> RunDiagnostic:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await run_diagnostic(session, job_id=job_id)
    finally:
        await engine.dispose()


async def _step_once(settings: Settings, *, job_id: uuid.UUID) -> None:
    """One stepped execution: resume if failed, arm step mode, run one step, diagnose."""
    from aer.api.deps import current_user_or_none  # noqa: PLC0415 -- one query, one place
    from aer.runtime import build_services  # noqa: PLC0415 -- constructs the provider
    from aer.services import runs as run_service  # noqa: PLC0415
    from aer.services.configuration import effective_settings  # noqa: PLC0415
    from aer.services.resume import resume_run, set_step_mode  # noqa: PLC0415

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                message = f"No run {job_id}."
                raise AerError(message, context={"job_id": str(job_id)})
            actor = await current_user_or_none(session)
            if actor is None:
                message = (
                    "No user exists. Create one with: uv run aer seed-user --email you@example.com"
                )
                raise AerError(message)

            if job.status is JobStatus.SUCCEEDED:
                typer.secho(
                    f"Run {job_id} already succeeded; nothing left to step.", fg=typer.colors.GREEN
                )
                return
            if job.status is JobStatus.FAILED:
                # The §2.3 decision applies to a stepped continuation too: continuing a
                # failed run is a recorded choice, whoever executes the next step.
                await resume_run(session, job=job, actor=actor, reason="stepped from the CLI")
            await set_step_mode(session, job=job, actor=actor, enabled=True)
            await session.commit()

            # The same read the worker makes, so a stepped run is the run — same routing,
            # same budget, same services (ADR 0050).
            resolved = await effective_settings(session, settings)
            services = build_services(resolved, redis=redis)

            executed_after = datetime.now(UTC)
            error_message: str | None = None
            try:
                await run_service.execute(
                    session,
                    job=job,
                    settings=resolved,
                    provider=services.provider,
                    store=services.store,
                    sec_client=services.sec_client,
                    fetcher=services.fetcher,
                    session_factory=factory,
                )
            except Exception as failure:
                error_message = str(failure)
            await session.commit()

            readout = await run_diagnostic(session, job_id=job_id)

        _print_stepped_outcome(readout, since=executed_after, error_message=error_message)
        if error_message is not None:
            raise typer.Exit(code=1)
    finally:
        await redis.aclose()
        await engine.dispose()


async def _reseal(settings: Settings, *, job_id: uuid.UUID, reason: str) -> Reseal:
    from aer.api.deps import current_user_or_none  # noqa: PLC0415 -- one query, one place
    from aer.services.gates import reseal_final_gate  # noqa: PLC0415

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                message = f"No run {job_id}."
                raise AerError(message, context={"job_id": str(job_id)})
            actor = await current_user_or_none(session)
            if actor is None:
                message = (
                    "No user exists. Create one with: uv run aer seed-user --email you@example.com"
                )
                raise AerError(message)
            outcome = await reseal_final_gate(session, job=job, actor=actor, reason=reason)
            await session.commit()
            return outcome
    finally:
        await engine.dispose()


async def _resume(
    settings: Settings, *, job_id: uuid.UUID, reason: str | None, keep_step_mode: bool
) -> bool:
    """Record the resume, then enqueue. Returns whether the queue accepted it."""
    from aer.api.deps import current_user_or_none  # noqa: PLC0415 -- one query, one place
    from aer.queue import enqueue_run  # noqa: PLC0415
    from aer.services.resume import resume_run, set_step_mode  # noqa: PLC0415

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                message = f"No run {job_id}."
                raise AerError(message, context={"job_id": str(job_id)})
            actor = await current_user_or_none(session)
            if actor is None:
                message = (
                    "No user exists. Create one with: uv run aer seed-user --email you@example.com"
                )
                raise AerError(message)

            await resume_run(session, job=job, actor=actor, reason=reason)
            if not keep_step_mode:
                await set_step_mode(session, job=job, actor=actor, enabled=False)
            await session.commit()

        return await enqueue_run(redis, job_id) is not None
    finally:
        await redis.aclose()
        await engine.dispose()


# How much of a stored value the run readout shows. Enough to recognise the content;
# `aer diagnose <run> <step>` prints the full record for the step that matters.
_VALUE_PREVIEW_CHARS = 100

_STATUS_COLOURS = {
    JobStatus.SUCCEEDED: typer.colors.GREEN,
    JobStatus.FAILED: typer.colors.RED,
    JobStatus.CANCELLED: typer.colors.RED,
}


def _status_colour(status: JobStatus) -> str:
    return _STATUS_COLOURS.get(status, typer.colors.YELLOW)


def _print_run_diagnostic(readout: RunDiagnostic) -> None:
    step_mode = "on" if readout.step_mode else "off"
    typer.secho(
        f"Run {readout.job_id} — {readout.workflow_version} @ {readout.code_version[:12]} — "
        f"{readout.status.value} — £{readout.spend_gbp:.4f} spent — step mode {step_mode}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    for step in readout.steps:
        elapsed = f"{step.elapsed_seconds:.1f}s" if step.elapsed_seconds is not None else "—"
        calls = f"  {len(step.exchanges)} model call(s)" if step.exchanges else ""
        typer.secho(
            f"  [{step.status.value}] {step.key}  attempt {step.attempts}  {elapsed}  "
            f"£{step.cost_gbp:.4f}{calls}",
            fg=_status_colour(step.status),
        )
        if step.error:
            code = step.error.get("code", "error")
            typer.echo(f"      {code}: {step.error.get('message', '')}")
    for key in readout.not_reached:
        typer.secho(f"  [NOT REACHED] {key}", fg=typer.colors.WHITE)
    if readout.next_step is not None:
        typer.echo(f"  next: {readout.next_step}")


def _print_step_detail(step: StepDiagnostic) -> None:
    elapsed = f"{step.elapsed_seconds:.1f}s" if step.elapsed_seconds is not None else "not recorded"
    typer.secho(
        f"{step.key} — {step.status.value} — attempt {step.attempts} — {elapsed} — "
        f"£{step.cost_gbp:.4f}",
        fg=_status_colour(step.status),
        bold=True,
    )
    if step.started_at is not None:
        typer.echo(f"  started:  {step.started_at.isoformat()}")
    if step.finished_at is not None:
        typer.echo(f"  finished: {step.finished_at.isoformat()}")
    if step.error:
        typer.secho("  error:", fg=typer.colors.RED, bold=True)
        for key, value in step.error.items():
            typer.echo(f"    {key}: {value}")
    if step.output:
        typer.echo("  output:")
        for key, value in step.output.items():
            typer.echo(f"    {key}: {_preview(value)}")
    for exchange in step.exchanges:
        tokens = f"{exchange.input_tokens or 0} in / {exchange.output_tokens or 0} out"
        typer.echo(
            f"  model call: {exchange.agent_role} — {exchange.model}"
            f"{f' ({exchange.effort})' if exchange.effort else ''} — {tokens}"
            f" — stop: {exchange.stop_reason or 'unrecorded'}"
        )
        if exchange.request_sha256:
            typer.echo(f"    request archived:  {exchange.request_sha256}")
        if exchange.response_sha256:
            typer.echo(f"    response archived: {exchange.response_sha256}")


def _preview(value: object) -> str:
    text_value = repr(value)
    if len(text_value) <= _VALUE_PREVIEW_CHARS:
        return text_value
    return text_value[:_VALUE_PREVIEW_CHARS] + "…"


def _print_stepped_outcome(
    readout: RunDiagnostic, *, since: datetime, error_message: str | None
) -> None:
    """What one `aer step` did: the steps this invocation touched, then where things stand."""
    touched = [
        step for step in readout.steps if step.started_at is not None and step.started_at >= since
    ]
    if not touched:
        typer.echo("No step needed to execute; every reachable step had already succeeded.")
    for step in touched:
        _print_step_detail(step)

    guidance = {
        JobStatus.PAUSED: (
            "Paused deliberately (step mode). Continue with `aer step`, or hand it back "
            "to the worker with `aer resume`."
        ),
        JobStatus.AWAITING_APPROVAL: (
            "Waiting at a gate. Decide it in the console; with step mode on, the run "
            "pauses again after the gate's step."
        ),
        JobStatus.BUDGET_EXCEEDED: (
            "Stopped at a budget ceiling. The refusal above names which; raise it or "
            "reject the run."
        ),
        JobStatus.SUCCEEDED: "Run complete.",
        JobStatus.FAILED: "The step failed; the record above is the diagnostic.",
    }
    line = guidance.get(readout.status, readout.status.value)
    colour = _status_colour(readout.status)
    if error_message is not None and readout.status is not JobStatus.FAILED:
        # The exception outran the record — say both rather than trusting either alone.
        typer.secho(f"Execution raised: {error_message}", fg=typer.colors.RED)
    typer.secho(line, fg=colour, bold=True)


async def _schema_revision(settings: Settings) -> str:
    """The migration the database is currently at, recorded in the backup manifest.

    A restore into a codebase expecting a different schema is the failure a manifest can
    warn about, and it cannot warn about what it did not write down.
    """
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            return str(revision) if revision else "unknown"
    finally:
        await engine.dispose()


async def _replay_run(settings: Settings, *, job_id: uuid.UUID) -> RunReplay:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await replay_run(session, _store_for(settings), job_id=job_id, settings=settings)
    finally:
        await engine.dispose()


async def _verify_audit(settings: Settings) -> ChainReport:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await verify_audit_chain(session)
    finally:
        await engine.dispose()


async def _verify_artefacts(settings: Settings) -> IntegrityReport:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return await verify_store(session, _store_for(settings))
    finally:
        await engine.dispose()


async def _gc_artefacts(settings: Settings, *, delete: bool) -> GarbageCollected:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            outcome = await collect_garbage(session, _store_for(settings), dry_run=not delete)
            if delete:
                await session.commit()
            return outcome
    finally:
        await engine.dispose()


def _store_for(settings: Settings) -> LocalArtefactStore:
    return LocalArtefactStore(settings.artefact_root, max_bytes=settings.max_artefact_bytes)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
