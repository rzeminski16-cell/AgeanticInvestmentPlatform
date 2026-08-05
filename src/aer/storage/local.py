"""Local filesystem artefact store.

Layout: ``<root>/<aa>/<bb>/<full-sha256>``, where ``aa`` and ``bb`` are the first two
byte-pairs of the digest. The fan-out is not decoration — a single directory holding tens
of thousands of entries makes every lookup and every ``ls`` slow on most filesystems, and
two levels of 256 gives 65,536 buckets, which is more than this platform will ever need.

Three properties this module is responsible for, in order of how much damage their
absence would do:

1. **No partial file ever appears at a content address.** Writes go to a temporary file,
   are flushed to disk, and are moved into place with :func:`os.replace`, which is atomic
   within a filesystem. A reader therefore sees either nothing or the whole artefact,
   even if the process is killed mid-write. A half-written filing sitting at a name that
   claims to be its hash is undetectable corruption.
2. **The digest is computed from what was actually written.** Hashing happens while
   streaming, and the file is read back afterwards to confirm the two agree.
3. **A caller cannot escape the root.** Every path is built from a validated hex digest,
   never from anything a caller supplies directly.

**Concurrent writes of the same content deduplicate rather than collide**, and the two
platforms make that harder than it sounds. Two adapters fetching the same filing at once
is the ordinary case; on POSIX the racing renames simply overwrite each other with
identical bytes, while Windows refuses a rename whose destination another writer holds
open. :meth:`_finalise_sync` treats a refusal with the artefact already present as the
duplicate it is, because under content addressing the destination's name *is* the digest
of what was about to be written.

All blocking I/O runs in a worker thread. A 50 MiB read on the event loop would stall
every other request in the process, and the fetch layer will be storing exactly that size.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from pathlib import Path
from typing import IO, Final

import structlog

from aer.errors import IntegrityError, ValidationError
from aer.storage.protocol import StoredArtefact

_log = structlog.get_logger("aer.storage.local")

__all__ = ["LocalArtefactStore", "is_valid_sha256"]

# Exactly 64 lowercase hex characters. This is the only thing ever interpolated into a
# storage path, which is what makes traversal impossible rather than merely unlikely.
_SHA256_PATTERN: Final = re.compile(r"\A[0-9a-f]{64}\Z")

_FANOUT_WIDTH: Final = 2
_TEMP_DIRNAME: Final = "tmp"
_READ_CHUNK_BYTES: Final = 1024 * 1024


def is_valid_sha256(value: str) -> bool:
    """Whether ``value`` is a well-formed lowercase SHA-256 hex digest."""
    return bool(_SHA256_PATTERN.match(value))


class LocalArtefactStore:
    """An :class:`~aer.storage.protocol.ArtefactStore` backed by a local directory."""

    def __init__(self, root: Path, *, max_bytes: int) -> None:
        self._root = root.expanduser().resolve(strict=False)
        self._max_bytes = max_bytes
        self._temp_dir = self._root / _TEMP_DIRNAME

    @property
    def root(self) -> Path:
        return self._root

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    # -- Addressing ----------------------------------------------------------------------

    def path_for(self, sha256: str) -> Path:
        """Return the absolute path for a content address.

        Raises:
            ValidationError: If ``sha256`` is not a well-formed digest. Validating the
                *shape of the input* is what makes traversal impossible rather than
                unlikely: ``../../../etc/passwd`` never reaches a path join, because it is
                not 64 hexadecimal characters. Sanitising a bad path afterwards is a game
                of catching every encoding; refusing to build one is not.
        """
        digest = sha256.strip().lower()
        if not is_valid_sha256(digest):
            message = "A content address must be 64 lowercase hexadecimal characters."
            # The offending value is deliberately not echoed back. It is
            # attacker-controlled, and a rejected path fragment reflected into a log line
            # or an error body turns a traversal attempt into a log-injection one.
            raise ValidationError(message, context={"given_length": len(sha256)})
        return self._root / self.storage_key_for(digest)

    def storage_key_for(self, sha256: str) -> str:
        """The path relative to the store root, as recorded on the artefact row."""
        digest = sha256.strip().lower()
        if not is_valid_sha256(digest):
            message = "A content address must be 64 lowercase hexadecimal characters."
            raise ValidationError(message, context={"given_length": len(sha256)})
        return f"{digest[:_FANOUT_WIDTH]}/{digest[_FANOUT_WIDTH : _FANOUT_WIDTH * 2]}/{digest}"

    # -- Writing -------------------------------------------------------------------------

    async def put_bytes(self, data: bytes) -> StoredArtefact:
        """Store ``data``, deduplicating against what is already present."""
        if len(data) > self._max_bytes:
            raise self._too_large(len(data))
        return await asyncio.to_thread(self._store_sync, (data,))

    async def put_stream(self, chunks: AsyncIterable[bytes]) -> StoredArtefact:
        """Store a stream without ever holding all of it in memory.

        The cap is enforced **while** the stream is consumed, so an oversized or endless
        response is abandoned partway rather than after it has all arrived. A limit that
        is only checked at the end is not a limit; it is a report.

        Nothing reaches a content address until the digest is known, so an abandoned
        stream leaves at most one file in ``tmp`` — which is removed here, and again by
        :meth:`prune_temp_files` if the process died before it could be.
        """
        temporary = await asyncio.to_thread(self._new_temp_file)
        digest = hashlib.sha256()
        size = 0
        try:
            handle = await asyncio.to_thread(temporary.open, "wb")
            try:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise self._too_large(size)
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
                await asyncio.to_thread(_flush_to_disk, handle)
            finally:
                await asyncio.to_thread(handle.close)

            return await asyncio.to_thread(self._finalise_sync, temporary, digest.hexdigest(), size)
        except BaseException:
            await asyncio.to_thread(temporary.unlink, True)
            raise

    def _store_sync(self, chunks: Iterable[bytes]) -> StoredArtefact:
        """Write, hash, and move into place. Runs entirely in a worker thread."""
        temporary = self._new_temp_file()
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
                _flush_to_disk(handle)
            return self._finalise_sync(temporary, digest.hexdigest(), size)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _finalise_sync(self, temporary: Path, sha256: str, size: int) -> StoredArtefact:
        """Move a completed temporary file to its content address and verify it."""
        destination = self.path_for(sha256)

        if destination.exists():
            # Identical content is already stored. Its bytes are by definition the bytes
            # just written, so there is nothing to do — and nothing is overwritten, which
            # is the property the acceptance criteria ask for.
            temporary.unlink(missing_ok=True)
            return StoredArtefact(sha256=sha256, size_bytes=size, was_new=False)

        destination.parent.mkdir(parents=True, exist_ok=True)

        # Atomic within a filesystem: a reader sees the old state or the new one, never a
        # partial file. `replace` rather than `rename` because it is defined to overwrite
        # on Windows too, and a racing writer of *identical* content is harmless by
        # construction -- both wrote the same bytes.
        #
        # The `exists` check above is a time-of-check/time-of-use race, and deliberately
        # not the only defence. Ten writers of the same filing all see "not there" and all
        # proceed to here. On POSIX every rename then succeeds and the last one wins, to no
        # ill effect. On Windows `MoveFileEx` refuses with ERROR_ACCESS_DENIED while another
        # writer holds the destination open -- including the winner, reading it back to
        # verify -- so the losers raised instead of deduplicating quietly.
        try:
            temporary.replace(destination)
        except OSError:
            if not destination.exists():
                raise
            # Somebody else got there first. Under content addressing that is not a
            # conflict: the destination's *name* is the digest of the bytes just written,
            # so whatever is there is what this call was going to write. Reported as a
            # duplicate, exactly like the check above.
            temporary.unlink(missing_ok=True)
            return StoredArtefact(sha256=sha256, size_bytes=size, was_new=False)

        _fsync_directory(destination.parent)

        actual = _digest_file(destination)
        if actual != sha256:
            # What is on disk is not what was hashed: a filesystem fault, or a bug here.
            # Either way the artefact cannot be trusted, and leaving it would mean a
            # citation could later "verify" against corrupt evidence.
            destination.unlink(missing_ok=True)
            message = (
                "An artefact did not match its own digest immediately after being "
                "written, and has been removed. The storage volume may be faulty."
            )
            raise IntegrityError(
                message, context={"expected_sha256": sha256, "actual_sha256": actual}
            )

        return StoredArtefact(sha256=sha256, size_bytes=size, was_new=True)

    # -- Reading -------------------------------------------------------------------------

    async def read(self, sha256: str) -> bytes:
        """The artefact's bytes, **after confirming they still hash to their own address.**

        Threat T8's control, and it belongs here rather than at each call site. A
        content-addressed store whose reads are unchecked is a store where an edited file goes
        on being served under the name of the original — and every consumer downstream, the
        citation verifier included, would then be checking evidence against a document that is
        no longer the document that was archived.

        Verifying on read rather than leaving it to :meth:`verify` is the difference between a
        control and a facility. A caller who has to remember is a caller who one day does not.

        The cost is one SHA-256 pass over bytes already in memory, at hundreds of megabytes a
        second. Against a run that parses the document and calls a model about it, it does not
        register.

        Raises:
            IntegrityError: The artefact is missing, or its bytes have changed.
        """
        path = self.path_for(sha256)
        try:
            data: bytes = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise self._missing(sha256) from exc

        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            message = (
                f"The artefact stored as {sha256} now hashes to {actual}. Its bytes have "
                "changed since they were archived, so it is not the document anything citing "
                "it was checked against. Nothing here can repair that; the file has to be "
                "re-fetched."
            )
            raise IntegrityError(
                message, context={"expected_sha256": sha256, "actual_sha256": actual}
            )

        return data

    async def open(self, sha256: str) -> AsyncIterator[bytes]:
        """Yield the artefact in chunks, so a large file never has to fit in memory.

        **Does not verify.** The digest is only known after the last chunk, by which point the
        caller has acted on the first — so a check here would report a tampered file too late
        to matter. Anything a claim will rest on uses :meth:`read`.
        """
        path = self.path_for(sha256)
        try:
            handle = await asyncio.to_thread(path.open, "rb")
        except FileNotFoundError as exc:
            raise self._missing(sha256) from exc
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, _READ_CHUNK_BYTES)
                if not chunk:
                    return
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def exists(self, sha256: str) -> bool:
        return await asyncio.to_thread(self.path_for(sha256).is_file)

    # -- Erasure ---------------------------------------------------------------------------

    async def purge(self, sha256: str) -> int:
        """Erase a payload under a licence obligation. Returns the bytes freed.

        Satisfies :class:`aer.storage.retention.PurgeableStore`, which is a **separate**
        protocol from :class:`~aer.storage.protocol.ArtefactStore` — so a caller holding the
        ordinary store interface cannot reach this, however much it might want to. See
        ADR 0031.

        Idempotent: an artefact that is already gone frees nothing and is not an error. A
        retention sweep must be safe to re-run, and the obligation is that the bytes are
        absent rather than that this call was the one that removed them.

        The empty fan-out directories are left behind. Removing them would race another
        writer storing an artefact with the same first bytes, and an empty directory costs
        an inode.
        """
        path = self.path_for(sha256)

        def _remove() -> int:
            try:
                freed = path.stat().st_size
            except FileNotFoundError:
                return 0
            path.unlink(missing_ok=True)
            return freed

        freed = await asyncio.to_thread(_remove)
        if freed:
            _log.info("artefact.purged", sha256=sha256, bytes_freed=freed)
        return freed

    async def verify(self, sha256: str) -> int:
        """Confirm a stored artefact still hashes to its own address, and return its size.

        This is what catches silent corruption — bit rot, a bad restore, an edit by hand.
        It re-reads from disk every time rather than caching a result, because a cached
        integrity check is a check performed once and then assumed forever.

        Raises:
            IntegrityError: If the artefact is missing or altered. It raises rather than
                returning a boolean, because a caller who has to remember to check a
                return value is a caller who will one day forget, and the consequence
                here is a report resting on evidence that has changed.
        """
        path = self.path_for(sha256)
        if not await asyncio.to_thread(path.is_file):
            raise self._missing(sha256)

        actual = await asyncio.to_thread(_digest_file, path)
        if actual != sha256.strip().lower():
            message = (
                "A stored artefact no longer matches its content address. Any report "
                "citing it rests on evidence that has since changed."
            )
            raise IntegrityError(
                message, context={"expected_sha256": sha256, "actual_sha256": actual}
            )
        return await asyncio.to_thread(lambda: path.stat().st_size)

    # -- Maintenance ---------------------------------------------------------------------

    def ensure_directories(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

    def prune_temp_files(self) -> int:
        """Remove abandoned partial writes and return how many were deleted.

        Only ever touches ``<root>/tmp``. Nothing under a content address can be a
        candidate: a file only arrives there once its digest is known, so a partial file
        cannot be sitting at one.
        """
        if not self._temp_dir.is_dir():
            return 0
        removed = 0
        for leftover in self._temp_dir.glob("*.part"):
            with contextlib.suppress(OSError):
                leftover.unlink()
                removed += 1
        return removed

    # -- Internals -----------------------------------------------------------------------

    def _new_temp_file(self) -> Path:
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        return self._temp_dir / f"{uuid.uuid4().hex}.part"

    def _too_large(self, size: int) -> ValidationError:
        message = (
            f"The artefact is {size:,} bytes, above the {self._max_bytes:,} byte limit. "
            "Raise AER_MAX_ARTEFACT_BYTES if this is genuinely expected; the cap exists "
            "to stop a decompression bomb or a runaway download filling the disk."
        )
        return ValidationError(message, context={"size_bytes": size, "max_bytes": self._max_bytes})

    @staticmethod
    def _missing(sha256: str) -> IntegrityError:
        message = (
            "No artefact is stored at this content address. Either the store has been "
            "pruned, or it is pointed at a different directory from the one that holds it."
        )
        return IntegrityError(message, context={"sha256": sha256})


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _flush_to_disk(handle: IO[bytes]) -> None:
    """Flush and fsync before the rename.

    Without the fsync the rename can reach the disk before the data does, so a crash in
    between leaves a correctly named file holding the wrong bytes — corruption that looks
    exactly like valid evidence.
    """
    handle.flush()
    os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Persist the directory entry itself, not just the file's contents.

    Without this the rename can be lost in a crash even though the data survived, leaving
    an artefact that exists on disk at no address. Not supported on every platform, so a
    failure here is tolerated rather than fatal.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover -- platform dependent
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover -- not supported on every filesystem
        pass
    finally:
        os.close(fd)
