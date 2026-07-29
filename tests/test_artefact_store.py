"""The content-addressed store.

No database here — the store is deliberately ignorant of one. What is being tested is the
handling of bytes: that the address is the digest, that nothing partial ever appears at an
address, that tampering is detected, and that a caller cannot talk their way out of the
root directory.

The tampering tests matter most. Every other property fails loudly the first time it is
wrong; a corrupt artefact fails silently, and a report resting on one looks exactly like a
report resting on good evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from aer.errors import IntegrityError, ValidationError
from aer.storage.local import LocalArtefactStore, is_valid_sha256
from aer.storage.protocol import ArtefactStore

PAYLOAD = b"Annual Report 2026. Revenue was 1,234."
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def store(tmp_path) -> LocalArtefactStore:
    return LocalArtefactStore(tmp_path / "artefacts", max_bytes=4096)


async def stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def stored_files(store: LocalArtefactStore) -> list:
    """Every file under the root except abandoned temporaries."""
    return [path for path in store.root.rglob("*") if path.is_file() and path.parent.name != "tmp"]


class TestAddressing:
    def test_the_address_is_the_digest_of_the_content(self, store):
        assert store.path_for(PAYLOAD_SHA256).name == PAYLOAD_SHA256

    def test_files_fan_out_two_levels(self, store):
        # A single directory with tens of thousands of entries makes every lookup slow on
        # most filesystems.
        relative = store.path_for(PAYLOAD_SHA256).relative_to(store.root)

        assert relative.parts == (PAYLOAD_SHA256[:2], PAYLOAD_SHA256[2:4], PAYLOAD_SHA256)

    def test_the_storage_key_is_relative_to_the_root(self, store):
        # Relative so that moving the artefact directory does not invalidate every row.
        key = store.storage_key_for(PAYLOAD_SHA256)

        assert not key.startswith("/")
        assert store.root / key == store.path_for(PAYLOAD_SHA256)

    def test_an_uppercase_digest_is_accepted_and_normalised(self, store):
        assert store.path_for(PAYLOAD_SHA256.upper()) == store.path_for(PAYLOAD_SHA256)

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../../etc/passwd",
            "..%2f..%2fetc%2fpasswd",
            "/etc/passwd",
            "a" * 63,  # too short
            "a" * 65,  # too long
            "g" * 64,  # not hexadecimal
            "",
            "abc/../../..",
            "\x00" * 64,
        ],
    )
    def test_a_path_cannot_escape_the_root(self, store, hostile):
        # Validating the *shape of the input* rather than sanitising a path afterwards:
        # sanitising is a game of catching every encoding, refusing to build a path is not.
        with pytest.raises(ValidationError):
            store.path_for(hostile)

    def test_the_rejected_value_is_not_echoed_back(self, store):
        # A rejected path fragment reflected into a log line or an error body turns a
        # traversal attempt into a log-injection one.
        with pytest.raises(ValidationError) as excinfo:
            store.path_for("../../etc/passwd")

        assert "etc/passwd" not in str(excinfo.value)
        assert "etc/passwd" not in repr(excinfo.value.context)

    def test_is_valid_sha256_agrees_with_hashlib(self):
        assert is_valid_sha256(hashlib.sha256(b"anything").hexdigest())
        assert not is_valid_sha256(hashlib.sha256(b"anything").hexdigest().upper())


class TestWriting:
    async def test_it_satisfies_the_protocol(self, store):
        assert isinstance(store, ArtefactStore)

    async def test_storing_bytes_returns_their_digest(self, store):
        result = await store.put_bytes(PAYLOAD)

        assert result.sha256 == PAYLOAD_SHA256
        assert result.size_bytes == len(PAYLOAD)
        assert result.was_new is True

    async def test_the_bytes_land_at_their_address(self, store):
        await store.put_bytes(PAYLOAD)

        assert store.path_for(PAYLOAD_SHA256).read_bytes() == PAYLOAD

    async def test_storing_the_same_bytes_twice_writes_one_file(self, store):
        first = await store.put_bytes(PAYLOAD)
        second = await store.put_bytes(PAYLOAD)

        assert first.sha256 == second.sha256
        assert second.was_new is False
        assert len(stored_files(store)) == 1

    async def test_different_bytes_are_different_artefacts(self, store):
        a = await store.put_bytes(b"one")
        b = await store.put_bytes(b"two")

        assert a.sha256 != b.sha256
        assert len(stored_files(store)) == 2

    async def test_no_temporary_file_survives_a_successful_write(self, store):
        await store.put_bytes(PAYLOAD)

        assert list((store.root / "tmp").glob("*.part")) == []

    async def test_a_stream_produces_the_same_address_as_the_whole_payload(self, store):
        result = await store.put_stream(stream(PAYLOAD[:10], PAYLOAD[10:20], PAYLOAD[20:]))

        assert result.sha256 == PAYLOAD_SHA256
        assert result.size_bytes == len(PAYLOAD)

    async def test_a_stream_deduplicates_against_an_existing_artefact(self, store):
        await store.put_bytes(PAYLOAD)
        result = await store.put_stream(stream(PAYLOAD))

        assert result.was_new is False
        assert len(stored_files(store)) == 1


class TestConcurrency:
    async def test_ten_simultaneous_writes_of_identical_content_produce_one_file(self, store):
        # Two adapters fetching the same filing at once is the ordinary case, not an edge
        # case. Exactly one write can win the rename; the rest must be harmless.
        results = await asyncio.gather(*(store.put_bytes(PAYLOAD) for _ in range(10)))

        assert {result.sha256 for result in results} == {PAYLOAD_SHA256}
        assert len(stored_files(store)) == 1
        assert store.path_for(PAYLOAD_SHA256).read_bytes() == PAYLOAD

    async def test_simultaneous_writes_of_different_content_all_survive(self, store):
        payloads = [f"filing {index}".encode() for index in range(10)]
        results = await asyncio.gather(*(store.put_bytes(item) for item in payloads))

        assert len({result.sha256 for result in results}) == 10
        assert len(stored_files(store)) == 10

    async def test_a_lost_rename_race_is_reported_as_a_duplicate(self, store, monkeypatch):
        """Losing the rename must deduplicate, not raise.

        This is a real failure and not a hypothetical one. The existence check before the
        rename is a time-of-check/time-of-use race: ten writers of the same filing all see
        "not there" and all proceed. POSIX renames simply overwrite, so the loss is
        invisible. Windows' ``MoveFileEx`` refuses with ERROR_ACCESS_DENIED while another
        writer holds the destination open, so the losers used to raise ``PermissionError``
        — and two adapters fetching the same filing at once is the ordinary case.

        Simulated rather than raced, because the race only loses on Windows and this must
        be covered everywhere.
        """

        def losing_replace(self, target):
            # Exactly the state Windows leaves behind: the winner's file is in place, and
            # this writer's rename is refused.
            destination = Path(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(PAYLOAD)
            message = "[WinError 5] Access is denied"
            raise PermissionError(5, message)

        monkeypatch.setattr(Path, "replace", losing_replace)

        result = await store.put_bytes(PAYLOAD)

        assert result.was_new is False
        assert result.sha256 == PAYLOAD_SHA256
        assert store.path_for(PAYLOAD_SHA256).read_bytes() == PAYLOAD
        # And the loser tidied up after itself.
        assert list((store.root / "tmp").glob("*.part")) == []

    async def test_a_rename_that_fails_with_nothing_at_the_address_still_raises(
        self, store, monkeypatch
    ):
        """The permissive branch must not swallow a genuine failure.

        Without this, "the destination exists" and "the rename failed for a reason that
        matters" would be indistinguishable, and a full disk or a permissions problem
        would be reported as a successful deduplication of a file that is not there.
        """

        def failing_replace(self, target):
            message = "[WinError 5] Access is denied"
            raise PermissionError(5, message)

        monkeypatch.setattr(Path, "replace", failing_replace)

        with pytest.raises(PermissionError):
            await store.put_bytes(PAYLOAD)

        assert stored_files(store) == []
        assert list((store.root / "tmp").glob("*.part")) == []


class TestSizeCap:
    async def test_an_oversized_payload_is_rejected(self, store):
        with pytest.raises(ValidationError, match="above the"):
            await store.put_bytes(b"x" * (store.max_bytes + 1))

    async def test_nothing_is_written_when_a_payload_is_rejected(self, store):
        with pytest.raises(ValidationError):
            await store.put_bytes(b"x" * (store.max_bytes + 1))

        assert stored_files(store) == []
        assert list((store.root / "tmp").glob("*.part")) == []

    async def test_a_payload_exactly_at_the_cap_is_accepted(self, store):
        result = await store.put_bytes(b"x" * store.max_bytes)
        assert result.size_bytes == store.max_bytes

    async def test_an_oversized_stream_is_abandoned_partway(self, store):
        # The point of enforcing the cap while consuming rather than afterwards: a
        # decompression bomb or an endless response must not be fully received first.
        consumed = 0

        async def endless() -> AsyncIterator[bytes]:
            nonlocal consumed
            while True:
                consumed += 1024
                yield b"x" * 1024

        with pytest.raises(ValidationError):
            await store.put_stream(endless())

        assert consumed <= store.max_bytes + 1024
        assert stored_files(store) == []

    async def test_an_abandoned_stream_leaves_no_temporary_file(self, store):
        async def failing() -> AsyncIterator[bytes]:
            yield b"partial"
            message = "the connection dropped"
            raise ConnectionError(message)

        with pytest.raises(ConnectionError):
            await store.put_stream(failing())

        assert list((store.root / "tmp").glob("*.part")) == []
        assert stored_files(store) == []


class TestIntegrity:
    async def test_an_intact_artefact_verifies(self, store):
        await store.put_bytes(PAYLOAD)

        assert await store.verify(PAYLOAD_SHA256) == len(PAYLOAD)

    async def test_a_tampered_artefact_is_detected(self, store):
        await store.put_bytes(PAYLOAD)
        store.path_for(PAYLOAD_SHA256).write_bytes(b"Revenue was 9,999.")

        with pytest.raises(IntegrityError) as excinfo:
            await store.verify(PAYLOAD_SHA256)

        assert excinfo.value.context["expected_sha256"] == PAYLOAD_SHA256
        assert excinfo.value.context["actual_sha256"] != PAYLOAD_SHA256

    async def test_a_single_flipped_byte_is_detected(self, store):
        # The realistic corruption case. A whole-file replacement is obvious; one bad bit
        # from a failing disk is what a hash is actually for.
        await store.put_bytes(PAYLOAD)
        path = store.path_for(PAYLOAD_SHA256)
        content = bytearray(path.read_bytes())
        content[5] ^= 0x01
        path.write_bytes(bytes(content))

        with pytest.raises(IntegrityError):
            await store.verify(PAYLOAD_SHA256)

    async def test_a_truncated_artefact_is_detected(self, store):
        await store.put_bytes(PAYLOAD)
        path = store.path_for(PAYLOAD_SHA256)
        path.write_bytes(path.read_bytes()[:-1])

        with pytest.raises(IntegrityError):
            await store.verify(PAYLOAD_SHA256)

    async def test_verifying_a_missing_artefact_raises(self, store):
        # Rather than returning False. A caller who has to remember to check a boolean is
        # a caller who will one day forget, and the cost here is a report resting on
        # evidence that is not there.
        with pytest.raises(IntegrityError, match="No artefact is stored"):
            await store.verify(PAYLOAD_SHA256)

    async def test_verification_reads_from_disk_every_time(self, store):
        # A cached integrity check is a check performed once and assumed forever.
        await store.put_bytes(PAYLOAD)
        assert await store.verify(PAYLOAD_SHA256)

        store.path_for(PAYLOAD_SHA256).write_bytes(b"changed")
        with pytest.raises(IntegrityError):
            await store.verify(PAYLOAD_SHA256)


class TestReading:
    async def test_read_returns_the_stored_bytes(self, store):
        await store.put_bytes(PAYLOAD)

        assert await store.read(PAYLOAD_SHA256) == PAYLOAD

    async def test_reading_a_missing_artefact_raises(self, store):
        with pytest.raises(IntegrityError):
            await store.read(PAYLOAD_SHA256)

    async def test_open_yields_the_whole_artefact_in_order(self, store):
        await store.put_bytes(PAYLOAD)

        chunks = [chunk async for chunk in store.open(PAYLOAD_SHA256)]

        assert b"".join(chunks) == PAYLOAD

    async def test_opening_a_missing_artefact_raises(self, store):
        with pytest.raises(IntegrityError):
            [chunk async for chunk in store.open(PAYLOAD_SHA256)]

    async def test_exists_reflects_what_is_stored(self, store):
        assert await store.exists(PAYLOAD_SHA256) is False
        await store.put_bytes(PAYLOAD)
        assert await store.exists(PAYLOAD_SHA256) is True

    async def test_exists_does_not_verify(self, store):
        # Deliberately cheap: a presence check that hashed the file would make listing a
        # run's evidence quadratic in its size.
        await store.put_bytes(PAYLOAD)
        store.path_for(PAYLOAD_SHA256).write_bytes(b"tampered")

        assert await store.exists(PAYLOAD_SHA256) is True


class TestMaintenance:
    def test_pruning_removes_only_temporary_files(self, store):
        store.ensure_directories()
        (store.root / "tmp" / "abandoned.part").write_bytes(b"partial")
        keep = store.root / "aa"
        keep.mkdir(parents=True)
        (keep / "not-a-temp-file").write_bytes(b"evidence")

        assert store.prune_temp_files() == 1
        assert (keep / "not-a-temp-file").exists()

    def test_pruning_an_empty_store_is_harmless(self, store):
        assert store.prune_temp_files() == 0
