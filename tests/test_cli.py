"""The command line.

``seed-user`` gets the most attention because it is the one command that writes. It is
documented as idempotent, and "idempotent" is a claim that is true until someone adds a
second insert; the test that runs it twice is what keeps it true.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from aer.cli import _research_tables, app
from aer.core.enums import UserRole
from aer.db.models import AuditEvent, ResearchRequest, User
from aer.version import version

runner = CliRunner()


def invoke_ok(args: list[str]):
    """Invoke a command and insist it succeeded.

    ``CliRunner`` captures any exception into ``result.exception`` instead of raising, so
    a plain ``runner.invoke(...)`` followed by a database assertion can pass for the wrong
    reason: the command crashed, wrote nothing, and the assertion was checking a
    pre-existing state. Re-raising here turns that into the failure it is.
    """
    result = runner.invoke(app, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def cli_env(settings_env, tmp_path, database_url):
    """A CLI environment pointed at the migrated test database."""
    settings_env.setenv("AER_DATABASE_URL", database_url)
    settings_env.setenv("AER_ARTEFACT_ROOT", str(tmp_path / "artefacts"))
    settings_env.setenv("AER_SECRET_KEY", "test-signing-key-not-a-real-one")
    return settings_env


async def _truncate(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            # users CASCADEs into requests; audit_events carries no foreign keys by
            # design, so it has to be named explicitly.
            await connection.execute(text("TRUNCATE users, audit_events RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def clean_user_tables(cli_env, database_url):
    """Empty the tables ``seed-user`` writes to, before and after each test.

    ``db_session`` cannot isolate these tests. It works by holding an outer transaction
    that is rolled back at teardown, but ``seed-user`` opens its own engine and really
    commits — so its writes are invisible to that transaction and outlive it, and the two
    would contend for locks on the same rows.

    Truncating on both sides rather than only afterwards: a test that inherits rows from
    a previous one is a test whose result depends on execution order, and that is a
    failure you debug at the wrong file.
    """
    asyncio.run(_truncate(database_url))
    yield
    asyncio.run(_truncate(database_url))


@pytest.fixture
def read_scalar(clean_user_tables, database_url):
    """Read one value back from the database, synchronously.

    Every test below is deliberately synchronous. ``aer serve``-style commands are sync
    entry points that own their event loop — ``seed-user`` calls ``asyncio.run`` — and
    calling one from inside an ``async def`` test raises "cannot be called from a running
    event loop". Typer's ``CliRunner`` swallows that into ``result.exception``, so the
    test does not fail; it passes against a command that did nothing at all.
    """

    def read(statement):
        async def run():
            engine = create_async_engine(database_url)
            try:
                async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                    return await session.scalar(statement)
            finally:
                await engine.dispose()

        return asyncio.run(run())

    return read


class TestVersion:
    def test_prints_the_build_identity(self, settings_env):
        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert version() in result.stdout

    def test_verbose_names_each_component(self, settings_env):
        result = runner.invoke(app, ["version", "--verbose"])

        assert result.exit_code == 0
        assert "version:" in result.stdout
        assert "git sha:" in result.stdout

    def test_works_without_configuration(self, monkeypatch):
        # No AER_HTTP_USER_AGENT set. Asking what build you are running must not require
        # a working configuration -- that is the first thing you check when configuration
        # is the thing that is broken.
        monkeypatch.delenv("AER_HTTP_USER_AGENT", raising=False)
        assert runner.invoke(app, ["version"]).exit_code == 0


class TestConfigurationFailure:
    def test_a_missing_required_setting_exits_2_and_names_it(self, monkeypatch):
        monkeypatch.delenv("AER_HTTP_USER_AGENT", raising=False)
        result = runner.invoke(app, ["seed-user", "--email", "someone@example.invalid"])

        assert result.exit_code == 2
        assert "AER_HTTP_USER_AGENT" in result.output


@pytest.mark.integration
class TestSeedUser:
    def test_creates_the_user(self, clean_user_tables):
        result = invoke_ok(["seed-user", "--email", "analyst@example.invalid"])

        assert result.exit_code == 0
        assert "Created user" in result.stdout

    def test_is_idempotent(self, clean_user_tables):
        first = invoke_ok(["seed-user", "--email", "repeat@example.invalid"])
        second = invoke_ok(["seed-user", "--email", "repeat@example.invalid"])

        assert first.exit_code == 0
        assert second.exit_code == 0
        assert "already exists" in second.stdout

    def test_a_second_run_creates_no_second_row(self, read_scalar):
        email = "once@example.invalid"
        invoke_ok(["seed-user", "--email", email])
        invoke_ok(["seed-user", "--email", email])

        count = read_scalar(select(func.count()).select_from(User).where(User.email == email))
        assert count == 1

    def test_email_matching_is_case_insensitive(self, read_scalar):
        # The column is CITEXT. Two rows differing only by case would be two accounts for
        # one person, and every ownership check would then depend on how they typed it.
        invoke_ok(["seed-user", "--email", "Mixed@Example.invalid"])
        result = invoke_ok(["seed-user", "--email", "mixed@example.invalid"])

        assert "already exists" in result.stdout
        count = read_scalar(
            select(func.count()).select_from(User).where(User.email == "MIXED@EXAMPLE.INVALID")
        )
        assert count == 1

    def test_the_display_name_defaults_to_the_local_part(self, read_scalar):
        invoke_ok(["seed-user", "--email", "jane.smith@example.invalid"])

        user = read_scalar(select(User).where(User.email == "jane.smith@example.invalid"))
        assert user.display_name == "jane.smith"

    def test_an_explicit_name_and_role_are_used(self, read_scalar):
        invoke_ok(
            [
                "seed-user",
                "--email",
                "viewer@example.invalid",
                "--name",
                "Read Only",
                "--role",
                "viewer",
            ],
        )

        user = read_scalar(select(User).where(User.email == "viewer@example.invalid"))
        assert user.display_name == "Read Only"
        assert user.role is UserRole.VIEWER

    def test_creation_is_recorded_in_the_audit_log(self, read_scalar):
        # An account created outside the audit trail is a gap in the record of who
        # approved what, which is the record this whole system exists to keep.
        invoke_ok(["seed-user", "--email", "audited@example.invalid"])

        event = read_scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "user.created")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["email"] == "audited@example.invalid"
        assert event.actor == "cli"
        assert event.this_hash

    def test_an_invalid_role_is_rejected(self, clean_user_tables):
        result = runner.invoke(
            app, ["seed-user", "--email", "x@example.invalid", "--role", "superuser"]
        )
        assert result.exit_code != 0


class TestResearchTablesAreWalkedNotListed:
    """What ``reset-research`` clears comes from the mapping, not from a list someone
    maintains. A hand-written list is how a "clean" database keeps one run's rows."""

    def test_the_walk_is_transitive(self):
        """Two hops out. ``financial_facts`` reaches a request only through
        ``source_documents``, so a walk that stops at direct children leaves the facts
        behind and the next run reads a company's history from a request that is gone."""
        tables = _research_tables()

        assert "research_requests" in tables
        assert "jobs" in tables
        assert "source_documents" in tables
        assert "financial_facts" in tables
        assert "sensitivity_cells" in tables

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("financial_facts", "source_documents"),
            ("citations", "extractions"),
            ("claims", "calculations"),
            ("jobs", "research_plans"),
            ("job_steps", "jobs"),
            ("research_plans", "research_requests"),
        ],
    )
    def test_a_restricting_child_is_emptied_before_its_parent(self, first, second):
        """Each pair is a deliberate ``RESTRICT``: evidence must not vanish from under the
        report that cites it. Reverse either and the delete stops halfway, refused."""
        tables = _research_tables()

        assert tables.index(first) < tables.index(second)

    def test_what_was_never_part_of_a_run_survives(self):
        tables = _research_tables()

        for kept in ("users", "skills", "skill_versions", "audit_events", "artefacts", "companies"):
            assert kept not in tables


@pytest.fixture
def clean_research_tables(cli_env, database_url):
    """Empty everything ``reset-research`` touches, before and after.

    Same reason as ``clean_user_tables``: the command opens its own engine and really
    commits, so ``db_session``'s rollback cannot reach it.
    """
    asyncio.run(_truncate(database_url))
    yield
    asyncio.run(_truncate(database_url))


def _seed_request(database_url: str) -> None:
    """One committed research request, as a run would leave behind."""

    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                user = User(
                    email="reset@example.invalid", display_name="Reset", role=UserRole.OWNER
                )
                session.add(user)
                await session.flush()
                session.add(
                    ResearchRequest(
                        user_id=user.id,
                        company_name="Contoso Corporation",
                        ticker="CTSO",
                        exchange="NASDAQ",
                        as_of_date=date(2023, 1, 1),
                        base_currency="USD",
                        investment_horizon_months=12,
                        max_cost_gbp="2.50",
                        portfolio_context={},
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


class TestResetResearch:
    def test_the_requests_go_and_the_account_stays(self, read_scalar, database_url):
        """The distinction the whole command turns on: a run is disposable, the person
        who asked for it is not."""
        _seed_request(database_url)

        invoke_ok(["reset-research", "--yes"])

        assert read_scalar(select(func.count()).select_from(ResearchRequest)) == 0
        assert read_scalar(select(func.count()).select_from(User)) == 1

    def test_declining_deletes_nothing(self, read_scalar, database_url):
        """A destructive command that acts on silence is a destructive command that acts
        by accident."""
        _seed_request(database_url)

        result = runner.invoke(app, ["reset-research"], input="n\n")

        assert result.exit_code != 0
        assert read_scalar(select(func.count()).select_from(ResearchRequest)) == 1

    def test_an_empty_history_is_not_an_error(self, clean_research_tables):
        result = invoke_ok(["reset-research"])

        assert "nothing to do" in result.output

    def test_the_wipe_is_recorded_in_the_audit_log(self, read_scalar, database_url):
        """The trail outlives what it describes, or it is not a trail."""
        _seed_request(database_url)

        invoke_ok(["reset-research", "--yes"])

        event = read_scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "research.reset")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        assert event is not None
        assert event.payload["rows"] >= 1
        assert "research_requests" in event.payload["tables"]
