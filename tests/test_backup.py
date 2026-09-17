"""Backing up both halves, and proving the copy can be read back.

Gap A10. The risk in a backup feature is not that it fails loudly — it is that it succeeds
quietly and produces something unusable, which nobody discovers until the day it is needed.
So the bulk of what follows damages a backup in a specific way and expects the damage to be
found, and the restore test really does restore, into a scratch database, and reads the
rows back out.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from aer.config import Settings
from aer.core.enums import GateKind, JobStatus, UserRole
from aer.db.models import Report, User
from aer.services import backup as backup_module
from aer.services.audit_verify import verify_audit_chain
from aer.services.backup import (
    ARTEFACT_INDEX_NAME,
    DATABASE_DUMP_NAME,
    MANIFEST_NAME,
    BackupError,
    create_backup,
    read_manifest,
    restore_backup,
    verify_backup,
)
from aer.services.retention import verify_store
from aer.services.run_replay import replay_run
from aer.storage.local import LocalArtefactStore
from tests.api_fixtures import build_app, client_for
from tests.db_fixtures import TEST_DATABASE_URL, run_async
from tests.request_fixtures import research_request
from tests.run_fixtures import Driver, to_final_gate
from tests.workflow_fixtures import AS_OF_DATE, DEFAULT_PER_RUN_BUDGET_GBP

pytestmark = pytest.mark.skipif(
    shutil.which("pg_dump") is None,
    reason="the backup commands shell out to the PostgreSQL client tools",
)


def _artefact_store(root: Path) -> Path:
    """A store laid out the way LocalArtefactStore lays one out: fanned out by digest."""
    root.mkdir(parents=True, exist_ok=True)
    for body in (b"first artefact", b"second artefact", b"third artefact"):
        digest = hashlib.sha256(body).hexdigest()
        path = root / digest[:2] / digest[2:4] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return root


def _take(tmp_path: Path, *, name: str = "backup") -> tuple[Path, Path]:
    store = _artefact_store(tmp_path / "store")
    destination = tmp_path / name
    create_backup(
        database_url=TEST_DATABASE_URL,
        artefact_root=store,
        destination=destination,
        schema_revision="0027",
    )
    return destination, store


class TestTakingOne:
    def test_a_backup_holds_both_halves_and_verifies(self, tmp_path: Path) -> None:
        destination, _ = _take(tmp_path)

        assert (destination / DATABASE_DUMP_NAME).is_file()
        assert (destination / ARTEFACT_INDEX_NAME).is_file()
        assert (destination / MANIFEST_NAME).is_file()

        report = verify_backup(destination)

        assert report.is_sound, report.problems
        assert report.checked == 3

    def test_the_manifest_records_what_it_will_be_checked_against(self, tmp_path: Path) -> None:
        destination, _ = _take(tmp_path)

        manifest = read_manifest(destination)

        assert manifest.schema_revision == "0027"
        assert manifest.artefact_count == 3
        assert manifest.database_bytes > 0
        assert len(manifest.database_sha256) == 64

    def test_writing_over_an_existing_backup_is_refused(self, tmp_path: Path) -> None:
        """Half of one backup and half of another is neither, and fails only on restore."""
        destination, store = _take(tmp_path)

        with pytest.raises(BackupError, match="Refusing to write over it"):
            create_backup(
                database_url=TEST_DATABASE_URL,
                artefact_root=store,
                destination=destination,
                schema_revision="0027",
            )

    def test_partial_writes_are_not_copied(self, tmp_path: Path) -> None:
        """`tmp` holds bytes with no content address yet, which could never be verified."""
        store = _artefact_store(tmp_path / "store")
        (store / "tmp").mkdir()
        (store / "tmp" / "half-written").write_bytes(b"incomplete")

        destination = tmp_path / "backup"
        manifest = create_backup(
            database_url=TEST_DATABASE_URL,
            artefact_root=store,
            destination=destination,
            schema_revision="0027",
        )

        assert manifest.artefact_count == 3
        assert verify_backup(destination).is_sound

    def test_an_empty_store_still_produces_a_verifiable_backup(self, tmp_path: Path) -> None:
        """A fresh install has no artefacts; its database is still worth keeping."""
        empty = tmp_path / "empty"
        empty.mkdir()
        destination = tmp_path / "backup"

        create_backup(
            database_url=TEST_DATABASE_URL,
            artefact_root=empty,
            destination=destination,
            schema_revision="0027",
        )

        assert verify_backup(destination).is_sound


class TestTheCredentialNeverReachesACommandLine:
    """`ps` is world-readable. A password in argv is a worse leak than one in a log."""

    def test_the_password_goes_in_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_run(command: list[str], password: object, *, what: str) -> None:
            captured["command"] = command
            captured["password"] = password

        monkeypatch.setattr(backup_module, "_run", fake_run)
        backup_module._dump_database(
            "postgresql+asyncpg://aer:hunter2@127.0.0.1:5432/aer",  # pragma: allowlist secret
            tmp_path / "unused.dump",
        )

        assert "hunter2" not in " ".join(captured["command"])
        assert captured["password"] == "hunter2"

    def test_the_environment_carries_it_and_the_arguments_do_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def fake_subprocess_run(command: list[str], **kwargs: Any) -> Any:
            seen["command"] = command
            seen["env"] = kwargs["env"]

            class Completed:
                returncode = 0
                stderr = ""

            return Completed()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
        backup_module._run(["pg_dump", "--host", "localhost"], "hunter2", what="pg_dump")

        assert seen["env"]["PGPASSWORD"] == "hunter2"
        assert not any("hunter2" in part for part in seen["command"])


class TestDamageIsFound:
    """Each of these is a backup that looks fine in a directory listing."""

    def test_a_corrupted_artefact_is_caught(self, tmp_path: Path) -> None:
        destination, _ = _take(tmp_path)
        stored = next((destination / "artefacts").rglob("*"))
        while stored.is_dir():
            stored = next(stored.rglob("*"))
        stored.write_bytes(b"something else entirely")

        report = verify_backup(destination)

        assert not report.is_sound
        assert any("does not hash to its name" in p for p in report.problems)

    def test_a_missing_artefact_is_caught(self, tmp_path: Path) -> None:
        destination, _ = _take(tmp_path)
        files = [p for p in (destination / "artefacts").rglob("*") if p.is_file()]
        files[0].unlink()

        report = verify_backup(destination)

        assert not report.is_sound
        assert any("listed but not present" in p for p in report.problems)

    def test_an_unlisted_extra_file_is_caught(self, tmp_path: Path) -> None:
        """The index and the tree disagreeing makes "nothing is missing" unprovable."""
        destination, _ = _take(tmp_path)
        (destination / "artefacts" / "stowaway").write_bytes(b"not in the index")

        report = verify_backup(destination)

        assert not report.is_sound
        assert any("not in its index" in p for p in report.problems)

    def test_a_truncated_dump_is_caught_by_size(self, tmp_path: Path) -> None:
        destination, _ = _take(tmp_path)
        dump = destination / DATABASE_DUMP_NAME
        dump.write_bytes(dump.read_bytes()[:-500])

        report = verify_backup(destination)

        assert not report.is_sound
        assert any("bytes" in p for p in report.problems)

    def test_a_dump_altered_without_changing_its_length_is_caught_by_hash(
        self, tmp_path: Path
    ) -> None:
        """The case a size check alone would wave through."""
        destination, _ = _take(tmp_path)
        dump = destination / DATABASE_DUMP_NAME
        raw = bytearray(dump.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        dump.write_bytes(bytes(raw))

        report = verify_backup(destination)

        assert not report.is_sound
        assert any("does not match the hash" in p for p in report.problems)

    def test_an_edited_index_is_caught(self, tmp_path: Path) -> None:
        destination, _ = _take(tmp_path)
        index = destination / ARTEFACT_INDEX_NAME
        index.write_text(index.read_text().replace("\n", "\n", 1) + "0" * 64 + " ghost\n")

        report = verify_backup(destination)

        assert not report.is_sound
        assert any("index does not match the hash" in p for p in report.problems)

    def test_a_directory_that_is_not_a_backup_says_so(self, tmp_path: Path) -> None:
        (tmp_path / "random").mkdir()

        with pytest.raises(BackupError, match="does not look like a backup"):
            verify_backup(tmp_path / "random")

    def test_an_unreadable_manifest_says_so(self, tmp_path: Path) -> None:
        destination, _ = _take(tmp_path)
        (destination / MANIFEST_NAME).write_text("{not json")

        with pytest.raises(BackupError, match="not valid JSON"):
            verify_backup(destination)


class TestRestoring:
    """The half that makes the other half a backup rather than a copy."""

    @staticmethod
    def _scratch_url() -> str:
        base, _, _ = TEST_DATABASE_URL.rpartition("/")
        return f"{base}/aer_restore_probe"

    @staticmethod
    def _admin_execute(statement: str) -> None:
        base, _, _ = TEST_DATABASE_URL.rpartition("/")

        async def run() -> None:
            engine = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
            try:
                async with engine.connect() as connection:
                    await connection.execute(text(statement))
            finally:
                await engine.dispose()

        run_async(run())

    @pytest.fixture
    def scratch_database(self) -> Any:
        self._admin_execute("DROP DATABASE IF EXISTS aer_restore_probe")
        self._admin_execute("CREATE DATABASE aer_restore_probe")
        yield self._scratch_url()
        self._admin_execute("DROP DATABASE IF EXISTS aer_restore_probe")

    def test_a_restored_database_has_the_rows_the_backup_held(
        self, tmp_path: Path, scratch_database: str
    ) -> None:
        """Restored into a *different* database, so the source cannot be what is read back.

        The reference data the migrations seed is the thing to count: it is committed, so
        `pg_dump` sees it, and it is not something the restore could invent.
        """
        destination, _ = _take(tmp_path)

        restore_backup(
            directory=destination,
            database_url=scratch_database,
            artefact_root=tmp_path / "restored-store",
        )

        async def count() -> int:
            engine = create_async_engine(scratch_database)
            try:
                async with engine.connect() as connection:
                    result = await connection.execute(
                        text("SELECT count(*) FROM section_definitions")
                    )
                    return int(result.scalar_one())
            finally:
                await engine.dispose()

        assert run_async(count()) > 0

    def test_the_artefacts_come_back_too(self, tmp_path: Path, scratch_database: str) -> None:
        """A database restored beside an empty store is citations into nothing."""
        destination, store = _take(tmp_path)
        restored_store = tmp_path / "restored-store"

        restore_backup(
            directory=destination,
            database_url=scratch_database,
            artefact_root=restored_store,
        )

        original = sorted(p.name for p in store.rglob("*") if p.is_file())
        recovered = sorted(p.name for p in restored_store.rglob("*") if p.is_file())
        assert recovered == original

    def test_an_unverifiable_backup_is_not_restored(
        self, tmp_path: Path, scratch_database: str
    ) -> None:
        """One bad backup must not be allowed to become two."""
        destination, _ = _take(tmp_path)
        dump = destination / DATABASE_DUMP_NAME
        dump.write_bytes(dump.read_bytes()[:-500])

        with pytest.raises(BackupError, match="does not verify"):
            restore_backup(
                directory=destination,
                database_url=scratch_database,
                artefact_root=tmp_path / "restored-store",
            )


@pytest.mark.integration
class TestARunSurvivesTheRoundTrip:
    """Phase 1.8. The recovery path, exercised on a run rather than on an empty schema.

    Everything above proves the copy is faithful to the bytes. Invariant 1's guarantee is
    about more than bytes: a restored database beside a restored store has to *work* — the
    artefacts still hash to their names, the audit chain still links, and a finished run
    still reproduces from its own record. This drives a real fake-scene run to an approved
    report, backs both halves up, restores them into a different database and a different
    store, and asks the restored side the three questions the by-hand procedure asks
    (`docs/developers/testing-by-hand.md` §17). The delivery plan records the first time
    it was run by hand; this keeps it run.
    """

    @pytest.fixture
    async def restored_database(self) -> AsyncIterator[str]:
        """A second, empty database on the same server: the restore's target.

        Created and dropped here rather than through the module's sync helper, because
        this test is itself async and cannot spin a second loop on the same thread.
        """
        base, _, _ = TEST_DATABASE_URL.rpartition("/")
        name = "aer_restore_roundtrip"
        admin = create_async_engine(
            f"{base}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool
        )
        try:
            async with admin.connect() as connection:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
            yield f"{base}/{name}"
            async with admin.connect() as connection:
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            await admin.dispose()

    @pytest.fixture
    def enqueued(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def record(redis: Any, job_id: uuid.UUID) -> str:
            return f"task-{job_id}"

        monkeypatch.setattr("aer.api.routes.runs.enqueue_run", record)
        monkeypatch.setattr("aer.web.pages.enqueue_run", record)

    @pytest.fixture
    async def committed(self, db_engine: Any) -> dict[str, Any]:
        factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
        async with factory() as session:
            user = User(
                email="roundtrip@example.invalid", display_name="Round trip", role=UserRole.OWNER
            )
            session.add(user)
            await session.flush()
            request = research_request(
                user_id=user.id,
                company_name="Microsoft Corporation",
                ticker="MSFT",
                exchange="NASDAQ",
                as_of_date=AS_OF_DATE,
                base_currency="USD",
                reporting_currency="USD",
                investment_horizon_months=12,
                max_cost_gbp=DEFAULT_PER_RUN_BUDGET_GBP,
            )
            session.add(request)
            await session.commit()
            return {"user": user, "request": request}

    @pytest.fixture
    async def api(
        self,
        api_settings: Settings,
        db_engine: Any,
        fake_redis: Any,
        committed: dict[str, Any],
        enqueued: None,
    ) -> Any:
        async for client in client_for(build_app(api_settings, engine=db_engine, redis=fake_redis)):
            yield client

    async def test_a_finished_run_reproduces_from_the_restored_copy(
        self,
        api: Any,
        db_engine: Any,
        api_settings: Settings,
        committed: dict[str, Any],
        tmp_path: Path,
        restored_database: str,
    ) -> None:
        driver = Driver(db_engine, api_settings)
        job_id = await to_final_gate(api, committed["request"].id, driver)
        await driver.approve(job_id, gate=GateKind.FINAL, step="revise")
        assert await driver.advance(job_id) is JobStatus.SUCCEEDED

        # What the source holds, so the restored side is compared against a number rather
        # than against "something".
        async with db_engine.connect() as connection:
            revision = str(await connection.scalar(text("SELECT version_num FROM alembic_version")))
            calculations = int(await connection.scalar(text("SELECT count(*) FROM calculations")))
            audit_rows = int(await connection.scalar(text("SELECT count(*) FROM audit_events")))
        assert calculations > 0, "the driven run recorded calculations"

        destination = tmp_path / "backup"
        manifest = create_backup(
            database_url=TEST_DATABASE_URL,
            artefact_root=api_settings.artefact_root,
            destination=destination,
            schema_revision=revision,
        )
        assert manifest.artefact_count > 0, "the driven run archived artefacts"
        assert verify_backup(destination).is_sound

        restored_root = tmp_path / "restored-artefacts"
        assert restore_backup(
            directory=destination, database_url=restored_database, artefact_root=restored_root
        ).is_sound

        engine = create_async_engine(restored_database, poolclass=NullPool)
        try:
            store = LocalArtefactStore(restored_root, max_bytes=api_settings.max_artefact_bytes)
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                integrity = await verify_store(session, store)
                chain = await verify_audit_chain(session)
                replay = await replay_run(session, store, job_id=job_id, settings=api_settings)
                restored_calculations = int(
                    await session.scalar(text("SELECT count(*) FROM calculations"))
                )
                report = await session.scalar(select(Report).where(Report.job_id == job_id))
        finally:
            await engine.dispose()

        # The three questions §17 asks of a restored run, answered from the restored side
        # alone: the store it was handed, the database it was pointed at.
        assert integrity.is_sound, (integrity.corrupt, integrity.missing)
        assert 0 < integrity.checked <= manifest.artefact_count
        assert chain.is_sound, chain.reason
        assert chain.checked == chain.total == audit_rows
        assert replay.reproduces, replay.problems()
        assert replay.calculations_checked == calculations
        assert replay.model_calls_checked > 0
        assert restored_calculations == calculations
        assert report is not None
        assert report.immutable
