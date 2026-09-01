"""The command line.

``seed-user`` gets the most attention because it is the one command that writes. It is
documented as idempotent, and "idempotent" is a claim that is true until someone adds a
second insert; the test that runs it twice is what keeps it true.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from aer.cli import _research_tables, app
from aer.core.enums import Provider, SourceTier, UserRole
from aer.db.models import (
    Artefact,
    ArtefactPurge,
    AuditEvent,
    ResearchRequest,
    SourceDocument,
    User,
)
from aer.fetch.policy import DEFAULT_POLICIES, RetentionClass
from aer.storage.local import LocalArtefactStore
from aer.version import version
from tests.db_cleanup import empty_the_database
from tests.request_fixtures import research_request

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
    """Empty everything these commands touch, without an exclusive lock (gap A17).

    ``TRUNCATE … CASCADE`` is what this used to be, and it is the statement the gap is
    about: it needs an ``ACCESS EXCLUSIVE`` lock on every table at once, so it deadlocks
    against the transactional fixtures the moment a command opens its own engine and really
    commits — which is exactly what the commands under test do. It also overrode the
    schema's ``RESTRICT`` rules wholesale, so a cleanup could reach a state the application
    itself is forbidden to.

    Deleting in dependency order takes row locks and honours every foreign key. See
    `tests/db_cleanup.py`.
    """
    await empty_the_database(database_url)


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
                    research_request(
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


# -- The maintenance sweeps ------------------------------------------------------------------


async def _truncate_artefacts(database_url: str) -> None:
    """The same, for the sweeps. See :func:`_truncate` for why it is not a ``TRUNCATE``."""
    await empty_the_database(database_url)


@pytest.fixture
def clean_artefacts(cli_env, database_url):
    """Empty the artefact table around each sweep test.

    Same reason as ``clean_user_tables``: these commands open their own engine and really
    commit, so ``db_session``'s outer transaction cannot isolate them.
    """
    asyncio.run(_truncate_artefacts(database_url))
    yield
    asyncio.run(_truncate_artefacts(database_url))


def _seed_artefact(database_url: str, root: Path, payload: bytes) -> str:
    """One committed artefact with its bytes really on disk. Returns the hash."""
    store = LocalArtefactStore(root, max_bytes=1_048_576)

    async def run() -> str:
        stored = await store.put_bytes(payload)
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                session.add(
                    Artefact(
                        sha256=stored.sha256,
                        media_type="application/json",
                        size_bytes=stored.size_bytes,
                        storage_key=store.storage_key_for(stored.sha256),
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()
        return stored.sha256

    return asyncio.run(run())


class TestVerifyArtefacts:
    def test_an_intact_store_passes(self, clean_artefacts, database_url, tmp_path):
        _seed_artefact(database_url, tmp_path / "artefacts", b'{"filed":"2024-07-30"}')

        result = invoke_ok(["verify-artefacts"])

        assert "all intact" in result.output

    def test_a_rotted_artefact_fails_the_command(self, clean_artefacts, database_url, tmp_path):
        """It exits non-zero so it can be a cron line. A sweep whose only output is a log
        message is a sweep nobody reads."""
        root = tmp_path / "artefacts"
        sha256 = _seed_artefact(database_url, root, b'{"filed":"2024-07-30"}')
        LocalArtefactStore(root, max_bytes=1_048_576).path_for(sha256).write_bytes(b"edited")

        result = runner.invoke(app, ["verify-artefacts"])

        assert result.exit_code == 1
        assert sha256 in result.output

    def test_an_empty_store_is_sound(self, clean_artefacts):
        result = invoke_ok(["verify-artefacts"])

        assert "0 artefact(s) checked" in result.output


class TestGcArtefacts:
    def test_it_reports_without_deleting(
        self, clean_artefacts, read_scalar, database_url, tmp_path
    ):
        _seed_artefact(database_url, tmp_path / "artefacts", b'{"left":"over"}')

        result = invoke_ok(["gc-artefacts"])

        assert "Re-run with --delete" in result.output
        assert read_scalar(select(func.count()).select_from(Artefact)) == 1

    def test_delete_removes_the_row_and_the_bytes(
        self, clean_artefacts, read_scalar, database_url, tmp_path
    ):
        """The commit is the assertion. Without it the session rolls back on close and the
        bytes are gone while the row survives — a store the next sweep reports as missing
        and a citation nothing can resolve."""
        root = tmp_path / "artefacts"
        sha256 = _seed_artefact(database_url, root, b'{"left":"over"}')

        result = invoke_ok(["gc-artefacts", "--delete"])

        assert "Removed 1 artefact(s)" in result.output
        assert read_scalar(select(func.count()).select_from(Artefact)) == 0
        assert not LocalArtefactStore(root, max_bytes=1_048_576).path_for(sha256).is_file()

    def test_nothing_to_collect_is_not_an_error(self, clean_artefacts):
        result = invoke_ok(["gc-artefacts", "--delete"])

        assert "nothing to do" in result.output


# -- The licensed purge ------------------------------------------------------------------------


def _seed_licensed_artefact(database_url: str, root: Path, payload: bytes) -> str:
    """A committed EODHD acquisition: bytes on disk, artefact row, source document."""
    store = LocalArtefactStore(root, max_bytes=1_048_576)

    async def run() -> str:
        stored = await store.put_bytes(payload)
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
                user = User(email="purge@example.invalid", display_name="P", role=UserRole.OWNER)
                session.add(user)
                await session.flush()
                request = research_request(
                    user_id=user.id,
                    company_name="Microsoft Corporation",
                    ticker="MSFT",
                    exchange="NASDAQ",
                    as_of_date=date(2023, 1, 1),
                    base_currency="USD",
                    investment_horizon_months=12,
                    max_cost_gbp="2.50",
                    portfolio_context={},
                )
                artefact = Artefact(
                    sha256=stored.sha256,
                    media_type="application/json",
                    size_bytes=stored.size_bytes,
                    storage_key=store.storage_key_for(stored.sha256),
                )
                session.add_all([request, artefact])
                await session.flush()
                session.add(
                    SourceDocument(
                        work_order_id=request.id,
                        artefact_id=artefact.id,
                        url="https://eodhd.com/api/eod/MSFT.US",
                        title="MSFT end-of-day prices",
                        provider=Provider.EODHD,
                        source_tier=SourceTier.T4_LICENSED_MARKET,
                        retrieved_at=datetime.now(UTC),
                        licence_note=DEFAULT_POLICIES[Provider.EODHD].licence_note,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()
        return stored.sha256

    return asyncio.run(run())


REASON = "The EODHD subscription ended 2027-03-01; the agreement requires deletion in a month."


class TestPurgeLicensed:
    """The caller `purge_provider` never had.

    ADR 0030's route 2 is only coherent if the subscription can lapse and the agreement can
    be complied with. The machinery for that existed and had no entry point at all, so the
    obligation could be honoured only by writing Python at a REPL against a live database.
    """

    def test_it_deletes_the_payload_and_keeps_the_record(
        self, clean_artefacts, read_scalar, database_url, tmp_path
    ):
        root = tmp_path / "artefacts"
        sha256 = _seed_licensed_artefact(database_url, root, b'{"close": 446.95}')

        result = invoke_ok(["purge-licensed", "--provider", "eodhd", "--reason", REASON, "--yes"])

        assert "Purged 1 artefact(s)" in result.output
        assert not LocalArtefactStore(root, max_bytes=1_048_576).path_for(sha256).is_file()
        # Everything that makes the deletion defensible survives.
        assert read_scalar(select(func.count()).select_from(Artefact)) == 1
        assert read_scalar(select(func.count()).select_from(ArtefactPurge)) == 1

    def test_the_purge_row_carries_the_reason_and_the_actor(
        self, clean_artefacts, read_scalar, database_url, tmp_path
    ):
        """Somebody reads this in two years when a citation will not resolve."""
        _seed_licensed_artefact(database_url, tmp_path / "artefacts", b'{"close": 446.95}')

        invoke_ok(
            [
                "purge-licensed",
                "--provider",
                "eodhd",
                "--reason",
                REASON,
                "--actor",
                "compliance",
                "--yes",
            ]
        )

        row = read_scalar(select(ArtefactPurge).limit(1))
        assert row.reason == REASON
        assert row.actor == "compliance"
        assert row.licence_note

    def test_a_permanent_provider_is_refused(self, clean_artefacts):
        """Asking to purge the SEC is a question with one safe answer."""
        result = runner.invoke(
            app, ["purge-licensed", "--provider", "sec_edgar", "--reason", REASON, "--yes"]
        )

        assert result.exit_code == 2
        assert "not a licensed provider" in result.output

    def test_the_refusal_says_which_providers_are_purgeable(self, clean_artefacts):
        result = runner.invoke(
            app, ["purge-licensed", "--provider", "sec_edgar", "--reason", REASON, "--yes"]
        )

        assert "eodhd" in result.output

    def test_a_blank_reason_is_refused_before_anything_is_deleted(
        self, clean_artefacts, read_scalar, database_url, tmp_path
    ):
        """ "Licence" is not a reason. Name the obligation."""
        _seed_licensed_artefact(database_url, tmp_path / "artefacts", b'{"close": 446.95}')

        result = runner.invoke(
            app, ["purge-licensed", "--provider", "eodhd", "--reason", "   ", "--yes"]
        )

        assert result.exit_code == 2
        assert read_scalar(select(func.count()).select_from(ArtefactPurge)) == 0

    def test_declining_the_confirmation_deletes_nothing(
        self, clean_artefacts, read_scalar, database_url, tmp_path
    ):
        root = tmp_path / "artefacts"
        sha256 = _seed_licensed_artefact(database_url, root, b'{"close": 446.95}')

        result = runner.invoke(
            app, ["purge-licensed", "--provider", "eodhd", "--reason", REASON], input="n\n"
        )

        assert result.exit_code != 0
        assert LocalArtefactStore(root, max_bytes=1_048_576).path_for(sha256).is_file()
        assert read_scalar(select(func.count()).select_from(ArtefactPurge)) == 0

    def test_the_confirmation_says_what_is_lost(self, clean_artefacts, database_url, tmp_path):
        """A citation into a purged payload can never be re-verified, and an operator
        should be told that before agreeing rather than after."""
        _seed_licensed_artefact(database_url, tmp_path / "artefacts", b'{"close": 446.95}')

        result = runner.invoke(
            app, ["purge-licensed", "--provider", "eodhd", "--reason", REASON], input="n\n"
        )

        assert "never be re-verifiable" in result.output

    def test_nothing_stored_is_not_an_error(self, clean_artefacts):
        result = invoke_ok(["purge-licensed", "--provider", "eodhd", "--reason", REASON])

        assert "nothing to do" in result.output


class TestEveryLicensedFeedHasAWayOut:
    """The invariant ADR 0030's route 2 rests on.

    A provider whose terms oblige deletion, with no command that can perform it, is data
    with an expiry date and no way to meet it. That was the state `purge_provider` was in
    for as long as it had no caller, and it is the state a second paid feed would silently
    create — so the check is over the policy table rather than over the one provider that
    happens to be in it today.
    """

    def test_the_command_accepts_every_provider_the_policies_call_licensed(self, clean_artefacts):
        licensed = [
            provider
            for provider, policy in DEFAULT_POLICIES.items()
            if policy.retention is RetentionClass.LICENSED
        ]
        assert licensed, "the check is vacuous if nothing is licensed"

        for provider in licensed:
            result = invoke_ok(["purge-licensed", "--provider", provider.value, "--reason", REASON])
            assert "nothing to do" in result.output, provider.value

    def test_a_permanent_provider_is_refused_for_every_one_of_them(self, clean_artefacts):
        """The other half. A command that accepted everything would be a way to erase a
        filing, which is the opposite of what invariant 1 asks for."""
        permanent = [
            provider
            for provider, policy in DEFAULT_POLICIES.items()
            if policy.retention is RetentionClass.PERMANENT
        ]
        assert permanent

        for provider in permanent:
            result = runner.invoke(
                app,
                ["purge-licensed", "--provider", provider.value, "--reason", REASON, "--yes"],
            )
            assert result.exit_code == 2, provider.value


class TestKnowledge:
    def test_it_reports_an_empty_graph_without_crashing(self, cli_env):
        """The formatting is the only part of this command no other surface exercises.

        An empty graph is the interesting case: every date is ``None`` and every list is
        empty, which is exactly where a format string reaches for an attribute that is not
        there. The figures themselves are asserted in `tests/test_knowledge_stats.py`.
        """
        result = invoke_ok(["knowledge"])

        assert "Size" in result.output
        assert "Shape" in result.output
        assert "Coverage" in result.output
        assert "Freshness" in result.output
        assert "Vault" in result.output
